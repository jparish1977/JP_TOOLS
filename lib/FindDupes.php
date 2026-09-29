<?php

/**
 * JP_TOOLS/lib/FindDupes.php
 * The find-dupes command. It reads the command line, wires FileScanner's
 * adapters to what it asks for (METHODOLOGY 3: this is the composition root),
 * runs the mode it names and hands the result to the output. find-dupes.php
 * is its entry point.
 */

declare(strict_types=1);

namespace JpTools;

use InvalidArgumentException;
use Iteration8\Utilities\FileScanner\Cache\FilesystemCache;
use Iteration8\Utilities\FileScanner\Cache\MemoryCache;
use Iteration8\Utilities\FileScanner\Cache\SqliteCache;
use Iteration8\Utilities\FileScanner\Contract\HashCacheInterface;
use Iteration8\Utilities\FileScanner\Contract\OutputInterface;
use Iteration8\Utilities\FileScanner\DuplicateFinder;
use Iteration8\Utilities\FileScanner\DuplicateGroup;
use Iteration8\Utilities\FileScanner\Filesystem\LocalFilesystem;
use Iteration8\Utilities\FileScanner\Hasher\NativeHasher;
use Iteration8\Utilities\FileScanner\Output\ConsoleOutput;
use Iteration8\Utilities\FileScanner\Output\JsonOutput;
use Iteration8\Utilities\FileScanner\Scanner;

class FindDupes
{
    private const HELP = <<<HELP
        Usage:
          php find-dupes.php <directory>                  Find duplicates within a directory
          php find-dupes.php <source> <target>            Compare two directories

        Options:
          --json                Output results as JSON
          --json-file <path>    Write JSON results to file
          --verbose, -v         Verbose output
          --algo <algo>         Hash algorithm (default: md5, also: sha256, sha1)
          --cache <dir>         Cache hashes to this directory
          --ignore <dirs>       Comma-separated directories to ignore
          --workers N, -w N     Parallel scan processes for --stream mode
          --db <path>           Persistent SQLite hash database (hash once, skip unchanged files)
          --stream              Streaming mode --scan to DB, query for dupes (requires --db)
          --help, -h            Show this help

        HELP;

    /** Flags that switch an option on. */
    private const SWITCHES = ['--json' => 'json', '--verbose' => 'verbose', '-v' => 'verbose',
        '--stream' => 'stream'];

    /** Flags that take the next argument as their value. */
    private const VALUED = ['--json-file' => 'json-file', '--algo' => 'algo', '--cache' => 'cache',
        '--ignore' => 'ignore', '--workers' => 'workers', '-w' => 'workers', '--db' => 'db'];

    private bool $json = false;

    private bool $verbose = false;

    private bool $stream = false;

    private bool $help = false;

    private string $algo = 'md5';

    private ?string $cachePath = null;

    private ?string $jsonFile = null;

    private ?string $db = null;

    private int $workers = 0;

    /** @var list<string> */
    private array $ignore = [];

    /** @var list<string> */
    private array $positional = [];

    /**
     * @param string $script find-dupes.php itself, which --workers runs once per directory
     */
    public function __construct(private readonly string $script)
    {
    }

    /**
     * @param list<string> $argv
     */
    public function run(array $argv): int
    {
        try {
            $this->parse(array_slice($argv, 1));
        } catch (InvalidArgumentException $invalidArgumentException) {
            fwrite(STDERR, "Error: {$invalidArgumentException->getMessage()}\n");
            return 1;
        }

        if ($this->help) {
            echo self::HELP;
            return 0;
        }

        if ($this->positional === []) {
            fwrite(STDERR, "Error: at least one directory is required. Use --help for usage.\n");
            return 1;
        }

        return $this->scan();
    }

    /**
     * Options and directories from ARGS. --help stops here, whatever follows it.
     *
     * @param list<string> $args
     */
    private function parse(array $args): void
    {
        while ($args !== []) {
            $arg = array_shift($args);
            if (isset(self::SWITCHES[$arg])) {
                $this->switchOn(self::SWITCHES[$arg]);
            } elseif (isset(self::VALUED[$arg])) {
                $this->setValue(self::VALUED[$arg], $this->valueOf($args, $arg));
            } elseif ($arg === '--help' || $arg === '-h') {
                $this->help = true;
                return;
            } else {
                $this->positional[] = $arg;
            }
        }
    }

    /**
     * The argument after FLAG, or a usage error when FLAG was the last argument.
     *
     * @param list<string> $args
     */
    private function valueOf(array &$args, string $flag): string
    {
        $value = array_shift($args);
        if ($value === null) {
            throw new InvalidArgumentException("{$flag} needs a value. Use --help for usage.");
        }

        return $value;
    }

    private function switchOn(string $option): void
    {
        match ($option) {
            'json' => $this->json = true,
            'verbose' => $this->verbose = true,
            default => $this->stream = true,
        };
    }

    private function setValue(string $option, string $value): void
    {
        match ($option) {
            'json-file' => [$this->json, $this->jsonFile] = [true, $value],
            'algo' => $this->algo = $value,
            'cache' => $this->cachePath = $value,
            'ignore' => $this->ignore = array_merge($this->ignore, explode(',', $value)),
            'workers' => $this->workers = (int) $value,
            default => $this->db = $value,
        };
    }

    private function output(): OutputInterface
    {
        return $this->json ? new JsonOutput($this->jsonFile) : new ConsoleOutput($this->verbose);
    }

    private function cache(): HashCacheInterface
    {
        if ($this->db) {
            return new SqliteCache($this->db);
        }

        if ($this->cachePath) {
            return new FilesystemCache($this->cachePath);
        }

        return new MemoryCache();
    }

    // --- Wire the adapters (this is where they get swapped), then run ---
    private function scan(): int
    {
        $output = $this->output();
        $cache = $this->cache();
        $hasher = new NativeHasher($this->algo);
        $scanner = new Scanner(new LocalFilesystem(), $hasher, $output, $cache);
        foreach ($this->ignore as $pattern) {
            $scanner->ignore(trim($pattern));
        }

        $start = microtime(true);
        if (!$this->stream) {
            $output->results($this->inMemory($scanner, $hasher, $output, $start));
            return 0;
        }

        // Streaming mode: scan to DB, query DB for dupes. No memory accumulation.
        if ($this->db === null || !$cache instanceof SqliteCache) {
            fwrite(STDERR, "Error: --stream requires --db <path>\n");
            return 1;
        }

        $output->results($this->streamed($scanner, $cache, $hasher, $output, $this->db, $start));
        return 0;
    }

    /**
     * One directory: the duplicates within it. Two: a comparison.
     *
     * @return array<string, mixed>
     */
    private function inMemory(Scanner $scanner, NativeHasher $hasher, OutputInterface $output, float $start): array
    {
        $finder = new DuplicateFinder($scanner, $output);
        if (count($this->positional) === 1) {
            $groups = $finder->findDuplicates($this->positional[0]);
            $stats = $scanner->getStats();
            return [
                'mode' => 'duplicates',
                'directory' => $this->positional[0],
                'algorithm' => $hasher->algorithm(),
                'files_scanned' => $stats['files_scanned'],
                'files_hashed' => $stats['files_hashed'],
                'cache_hits' => $stats['cache_hits'],
                'duplicate_groups' => count($groups),
                'total_wasted_bytes' => array_sum(array_map(fn(DuplicateGroup $g): int => $g->wastedBytes(), $groups)),
                'elapsed_seconds' => round(microtime(true) - $start, 2),
                'groups' => array_map(fn(DuplicateGroup $g): array => $g->toArray(), $groups),
                'timestamp' => date('c'),
            ];
        }

        $result = $finder->compare($this->positional[0], $this->positional[1]);
        $stats = $scanner->getStats();
        return array_merge($result->toArray(), [
            'mode' => 'compare',
            'algorithm' => $hasher->algorithm(),
            'files_scanned' => $stats['files_scanned'],
            'files_hashed' => $stats['files_hashed'],
            'cache_hits' => $stats['cache_hits'],
            'elapsed_seconds' => round(microtime(true) - $start, 2),
            'timestamp' => date('c'),
        ]);
    }

    /**
     * Every directory into the database, then the duplicates as it holds them.
     *
     * @return array<string, mixed>
     */
    private function streamed(
        Scanner $scanner,
        SqliteCache $cache,
        NativeHasher $hasher,
        OutputInterface $output,
        string $db,
        float $start,
    ): array {
        // Scan directories: parallel if workers requested, sequential otherwise
        if ($this->workers > 0 && count($this->positional) > 1) {
            $this->scanInWorkers($db, $output);
        } else {
            foreach ($this->positional as $dir) {
                $scanner->scanToCache($dir);
            }
        }

        $elapsed = round(microtime(true) - $start, 2);
        $stats = $scanner->getStats();
        [$groups, $totalWasted] = $this->databaseGroups($cache);
        $dbStats = $cache->stats();
        $output->info("DB stats: {$dbStats['total']} files, {$dbStats['unique_hashes']} unique hashes, {$dbStats['duplicate_hashes']} duplicate hashes");

        return [
            'mode' => 'stream',
            'directories' => $this->positional,
            'algorithm' => $hasher->algorithm(),
            'files_scanned' => $stats['files_scanned'],
            'files_hashed' => $stats['files_hashed'],
            'cache_hits' => $stats['cache_hits'],
            'db_total' => $dbStats['total'],
            'db_unique_hashes' => $dbStats['unique_hashes'],
            'duplicate_groups' => count($groups),
            'total_wasted_bytes' => $totalWasted,
            'elapsed_seconds' => $elapsed,
            'groups' => $groups,
            'timestamp' => date('c'),
        ];
    }

    /**
     * The database's duplicate groups, most bytes wasted first, and the bytes wasted in all.
     *
     * @return array{0: list<array<string, mixed>>, 1: int}
     */
    private function databaseGroups(SqliteCache $cache): array
    {
        $totalWasted = 0;
        $groups = [];
        foreach ($cache->findDuplicateHashes() as $hash => $files) {
            if (!empty($files)) {
                $wasted = (int) $files[0]['size'] * (count($files) - 1);
                $totalWasted += $wasted;
                $groups[] = [
                    'hash' => $hash,
                    'count' => count($files),
                    'wasted_bytes' => $wasted,
                    'files' => $files,
                ];
            }
        }

        usort($groups, fn(array $a, array $b): int => $b['wasted_bytes'] <=> $a['wasted_bytes']);
        return [$groups, $totalWasted];
    }

    /**
     * One find-dupes.php --stream per directory, at most --workers at once,
     * each worker's output printed as it finishes.
     */
    private function scanInWorkers(string $db, OutputInterface $output): void
    {
        $output->info("Spawning " . min($this->workers, count($this->positional)) . " parallel scan processes");
        $processes = [];
        foreach ($this->positional as $dir) {
            $worker = $this->startWorker($dir, $db);
            if ($worker !== null) {
                $processes[] = $worker;
                $output->info("  Started: {$dir}");
            }

            // Respect worker limit
            while (count($processes) >= $this->workers) {
                $this->reapOne($processes);
                if (count($processes) >= $this->workers) {
                    usleep(100000); // 100ms
                }
            }
        }

        // Wait for remaining
        foreach ($processes as $worker) {
            $this->drain($worker);
        }
    }

    /**
     * @return array{proc: resource, pipes: array<int, resource>}|null
     */
    private function startWorker(string $dir, string $db): ?array
    {
        $cmd = [PHP_BINARY, $this->script, $dir, '--db', $db, '--stream', '--algo', $this->algo];
        foreach ($this->ignore as $pattern) {
            $cmd[] = '--ignore';
            $cmd[] = $pattern;
        }

        $proc = proc_open($cmd, [
            1 => ['pipe', 'w'],
            2 => ['pipe', 'w'],
        ], $pipes);
        if (!is_resource($proc)) {
            return null;
        }

        stream_set_blocking($pipes[1], false);
        stream_set_blocking($pipes[2], false);
        return ['proc' => $proc, 'pipes' => $pipes];
    }

    /**
     * Drain and close the first worker that has finished, if any has.
     *
     * @param array<int, array{proc: resource, pipes: array<int, resource>}> $processes
     */
    private function reapOne(array &$processes): void
    {
        foreach ($processes as $i => $worker) {
            if (!proc_get_status($worker['proc'])['running']) {
                $this->drain($worker);
                unset($processes[$i]);
                return;
            }
        }
    }

    /**
     * @param array{proc: resource, pipes: array<int, resource>} $worker
     */
    private function drain(array $worker): void
    {
        $out = stream_get_contents($worker['pipes'][1]);
        if ($out) {
            echo $out;
        }

        fclose($worker['pipes'][1]);
        fclose($worker['pipes'][2]);
        proc_close($worker['proc']);
    }
}
