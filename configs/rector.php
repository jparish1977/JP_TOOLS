<?php

declare(strict_types=1);

use Rector\Config\RectorConfig;
use Rector\Set\ValueObject\LevelSetList;
use Rector\Set\ValueObject\SetList;

/**
 * JP_TOOLS shared Rector config.
 * Safe automated refactoring rules — no breaking changes.
 * Copy/extend into a project's rector.php as needed.
 */
return RectorConfig::configure()
    ->withSets([
        // Upgrade syntax to PHP 8.1 idioms
        LevelSetList::UP_TO_PHP_81,

        // Dead code removal — unused variables, assignments, params
        SetList::DEAD_CODE,

        // Code quality — simplify boolean expressions, redundant ifs, etc.
        SetList::CODE_QUALITY,

        // Coding style consistency
        SetList::CODING_STYLE,

        // Type declarations — add missing param/return types where inferable
        SetList::TYPE_DECLARATION,
    ])

    // Rules that change semantics — off by default, enable per-project
    // ->withSets([SetList::NAMING])

    ->withImportNames(importShortClasses: false);
