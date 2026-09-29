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
 *   php find-dupes.php ~/backup/old-laptop
 *   php find-dupes.php ~/backup/old-laptop ~/backup/old-ssd --json
 *   php find-dupes.php ~/Pictures --cache ~/.hash-cache --algo sha256
 *
 * The entry point only: the command is JpTools\FindDupes (lib/FindDupes.php).
 */

declare(strict_types=1);

ini_set('memory_limit', '1G');

$autoload = __DIR__ . '/vendor/autoload.php';
if (!is_file($autoload)) {
    fwrite(STDERR, "vendor/autoload.php missing; run: composer install\n");
    exit(1);
}

require_once $autoload;

use Iteration8\Utilities\FileScanner\Scanner;
use JpTools\FindDupes;

if (!class_exists(Scanner::class)) {
    fwrite(STDERR, <<<'TXT'
        find-dupes.php needs the iteration8/utilities library, which lives in a private repo.
        Every other JP_TOOLS tool works without it.

        If you have access, add it with:
          composer config repositories.iteration8-utilities vcs https://github.com/jparish1977/iteration8-utilities.git
          composer require iteration8/utilities:dev-master

        TXT);
    exit(1);
}

exit((new FindDupes(__FILE__))->run($argv));
