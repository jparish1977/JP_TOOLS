#!/usr/bin/env php
<?php
/**
 * JP_TOOLS/find-dupes.php
 * Find duplicate files or compare directories using pluggable hash/cache/output backends.
 *
 * Usage:
 *   php find-dupes.php <directory>                          Find dupes within a directory
 *   php find-dupes.php <source-dir> <target-dir>            Compare two directories
 *   php find-dupes.php <directory> --json                   Output as JSON
 *   php find-dupes.php <directory> --algo sha256            Use SHA-256 instead of MD5
 *   php find-dupes.php <directory> --cache ./hash-cache     Cache hashes to disk
 *   php find-dupes.php <directory> --ignore vendor,dist     Ignore directories
 *
 * Examples:
 *   php find-dupes.php ~/saved/from-surface-pro7
 *   php find-dupes.php ~/saved/from-surface-pro7 ~/saved/sa510 --json
 *   php find-dupes.php ~/Pictures --cache ~/.hash-cache --algo sha256
 */

declare(strict_types=1);

ini_set('memory_limit', '1G');

require_once __DIR__ . '/vendor/autoload.php';

use Iteration8\Utilities\FileScanner\Cache\FilesystemCache;
use Iteration8\Utilities\FileScanner\Cache\MemoryCache;
use Iteration8\Utilities\FileScanner\Cache\SqliteCache;
use Iteration8\Utilities\FileScanner\DuplicateFinder;
use Iteration8\Utilities\FileScanner\Filesystem\LocalFilesystem;
use Iteration8\Utilities\FileScanner\Hasher\NativeHasher;
use Iteration8\Utilities\FileScanner\Output\ConsoleOutput;
use Iteration8\Utilities\FileScanner\Output\JsonOutput;
use Iteration8\Utilities\FileScanner\Scanner;

// --- Parse arguments ---
$args = $argv;
array_shift($args); // remove script name

$positional = [];
$options = [
    'json' => false,
    'verbose' => false,
    'algo' => 'md5',
    'cache' => null,
    'ignore' => [],
    'json-file' => null,
    'workers' => 0,
    'db' => null,
    'stream' => false,
];

while ($args) {
    $arg = array_shift($args);
    switch ($arg) {
        case '--json':
            $options['json'] = true;
            break;
        case '--json-file':
            $options['json'] = true;
            $options['json-file'] = array_shift($args);
            break;
        case '--verbose':
        case '-v':
            $options['verbose'] = true;
            break;
        case '--algo':
            $options['algo'] = array_shift($args);
            break;
        case '--cache':
            $options['cache'] = array_shift($args);
            break;
        case '--ignore':
            $ignoreStr = array_shift($args);
            $options['ignore'] = array_merge($options['ignore'], explode(',', $ignoreStr));
            break;
        case '--workers':
        case '-w':
            $options['workers'] = (int) array_shift($args);
            break;
        case '--db':
            $options['db'] = array_shift($args);
            break;
        case '--stream':
            $options['stream'] = true;
            break;
        case '--help':
        case '-h':
            echo <<<HELP
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
              --stream              Streaming mode — scan to DB, query for dupes (requires --db)
              --help, -h            Show this help

            HELP;
            exit(0);
        default:
            $positional[] = $arg;
    }
}

if (empty($positional)) {
    fwrite(STDERR, "Error: at least one directory is required. Use --help for usage.\n");
    exit(1);
}

// --- Assemble components (this is where adapters get swapped) ---

$filesystem = new LocalFilesystem();
$hasher = new NativeHasher($options['algo']);

$output = $options['json']
    ? new JsonOutput($options['json-file'])
    : new ConsoleOutput($options['verbose']);

if ($options['db']) {
    $cache = new SqliteCache($options['db']);
} elseif ($options['cache']) {
    $cache = new FilesystemCache($options['cache']);
} else {
    $cache = new MemoryCache();
}

$scanner = new Scanner($filesystem, $hasher, $output, $cache);

if (!empty($options['ignore'])) {
    foreach ($options['ignore'] as $pattern) {
        $scanner->ignore(trim($pattern));
    }
}

$finder = new DuplicateFinder($scanner, $output);

// --- Run ---

$start = microtime(true);

if ($options['stream']) {
    // Streaming mode — scan to DB, query DB for dupes. No memory accumulation.
    if (!($cache instanceof SqliteCache)) {
        fwrite(STDERR, "Error: --stream requires --db <path>\n");
        exit(1);
    }

    // Scan directories — parallel if workers requested, sequential otherwise
    if ($options['workers'] > 0 && count($positional) > 1) {
        $output->info("Spawning " . min($options['workers'], count($positional)) . " parallel scan processes");
        $processes = [];
        $php = PHP_BINARY;
        $script = __FILE__;
        $dbPath = $options['db'];

        foreach ($positional as $dir) {
            $cmd = [$php, $script, $dir, '--db', $dbPath, '--stream', '--algo', $options['algo']];
            foreach ($options['ignore'] as $pattern) {
                $cmd[] = '--ignore';
                $cmd[] = $pattern;
            }

            $proc = proc_open($cmd, [
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ], $pipes);

            if (is_resource($proc)) {
                stream_set_blocking($pipes[1], false);
                stream_set_blocking($pipes[2], false);
                $processes[] = ['proc' => $proc, 'pipes' => $pipes, 'dir' => $dir];
                $output->info("  Started: {$dir}");
            }

            // Respect worker limit
            while (count($processes) >= $options['workers']) {
                foreach ($processes as $i => $p) {
                    $status = proc_get_status($p['proc']);
                    if (!$status['running']) {
                        // Drain output
                        $out = stream_get_contents($p['pipes'][1]);
                        if ($out) {
                            echo $out;
                        }
                        fclose($p['pipes'][1]);
                        fclose($p['pipes'][2]);
                        proc_close($p['proc']);
                        unset($processes[$i]);
                        break;
                    }
                }
                if (count($processes) >= $options['workers']) {
                    usleep(100000); // 100ms
                }
            }
        }

        // Wait for remaining
        foreach ($processes as $p) {
            $out = stream_get_contents($p['pipes'][1]);
            if ($out) {
                echo $out;
            }
            fclose($p['pipes'][1]);
            fclose($p['pipes'][2]);
            proc_close($p['proc']);
        }
    } else {
        foreach ($positional as $dir) {
            $scanner->scanToCache($dir);
        }
    }

    $elapsed = round(microtime(true) - $start, 2);
    $stats = $scanner->getStats();

    // Query dupes from DB
    $dupeGroups = $cache->findDuplicateHashes();
    $totalWasted = 0;
    $groups = [];
    foreach ($dupeGroups as $hash => $files) {
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
    usort($groups, fn($a, $b) => $b['wasted_bytes'] <=> $a['wasted_bytes']);

    $dbStats = $cache->stats();
    $output->info("DB stats: {$dbStats['total']} files, {$dbStats['unique_hashes']} unique hashes, {$dbStats['duplicate_hashes']} duplicate hashes");

    $resultData = [
        'mode' => 'stream',
        'directories' => $positional,
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
} elseif (count($positional) === 1) {
    // Single directory — find dupes within
    $groups = $finder->findDuplicates($positional[0]);

    $elapsed = round(microtime(true) - $start, 2);
    $stats = $scanner->getStats();

    $resultData = [
        'mode' => 'duplicates',
        'directory' => $positional[0],
        'algorithm' => $hasher->algorithm(),
        'files_scanned' => $stats['files_scanned'],
        'files_hashed' => $stats['files_hashed'],
        'cache_hits' => $stats['cache_hits'],
        'duplicate_groups' => count($groups),
        'total_wasted_bytes' => array_sum(array_map(fn($g) => $g->wastedBytes(), $groups)),
        'elapsed_seconds' => $elapsed,
        'groups' => array_map(fn($g) => $g->toArray(), $groups),
        'timestamp' => date('c'),
    ];
} else {
    // Two directories — compare
    $result = $finder->compare($positional[0], $positional[1]);

    $elapsed = round(microtime(true) - $start, 2);
    $stats = $scanner->getStats();

    $resultData = array_merge($result->toArray(), [
        'mode' => 'compare',
        'algorithm' => $hasher->algorithm(),
        'files_scanned' => $stats['files_scanned'],
        'files_hashed' => $stats['files_hashed'],
        'cache_hits' => $stats['cache_hits'],
        'elapsed_seconds' => $elapsed,
        'timestamp' => date('c'),
    ]);
}

$output->results($resultData);
