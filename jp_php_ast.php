<?php

/**
 * JP_TOOLS PHP syntax tree for the smells arm (jpcheck/measure.py).
 *
 * Usage: php jp_php_ast.php AUTOLOAD < source.php
 *
 * Reads a PHP source on stdin and parses it with nikic/php-parser, loaded from
 * AUTOLOAD (a Composer vendor/autoload.php). Prints the tree as JSON, the same
 * document `php-parse -j` prints, and exits 0. A source that does not parse
 * prints the parser's message on stderr and exits 2. Any other exit is the
 * runner failing, never a finding about the source.
 *
 * php-parse -j is not used because it encodes without a flag for invalid
 * UTF-8: a string literal such as "\x89PNG" decodes to a byte that is not
 * UTF-8, json_encode fails, and it prints nothing and still exits 0. Here such
 * a byte becomes U+FFFD. The measure reads node types and lines, never a
 * string's value, so the substitution changes nothing it uses.
 */

declare(strict_types=1);

require $argv[1];

$parser = (new PhpParser\ParserFactory())->createForNewestSupportedVersion();
try {
    $stmts = $parser->parse((string) file_get_contents('php://stdin'));
} catch (PhpParser\Error $error) {
    fwrite(STDERR, "Syntax error: {$error->getMessage()}\n");
    exit(2);
}

echo json_encode($stmts, JSON_PRETTY_PRINT | JSON_INVALID_UTF8_SUBSTITUTE | JSON_THROW_ON_ERROR), "\n";
