<?php

declare(strict_types=1);

namespace Demo;

$x = $argv[1] ?? 'a';
if ($x === 'a') {
    echo 1;
} elseif ($x === 'b') {
    echo 2;
}

$f = (static fn(int $n): int => $n > 1 ? 1 : 2);

function top(int $a): int
{
    if ($a) {
        return 1;
    }

    return 2;
}

final class K
{
    public function m(int $b): int
    {
        foreach ([1, 2] as $i) {
            if ($i === $b) {
                return $i;
            }
        }

        return 0;
    }
}
