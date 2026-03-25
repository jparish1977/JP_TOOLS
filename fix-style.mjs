#!/usr/bin/env node
/**
 * Auto-fix object-shorthand, prefer-const, and prefer-template via ESLint.
 * Usage: node fix-style.mjs <file1> [file2] ...
 */
import { ESLint } from "eslint";
import { dirname, resolve } from "path";

const files = process.argv.slice(2).map(f => resolve(f));
if (files.length === 0) {
  console.error("Usage: node fix-style.mjs <file1> [file2] ...");
  process.exit(2);
}

for (const target of files) {
  const eslint = new ESLint({
    cwd: dirname(target),
    fix: true,
    overrideConfigFile: true,
    overrideConfig: [{
      files: ["**"],
      languageOptions: { ecmaVersion: 2022, sourceType: "script" },
      rules: {
        "object-shorthand": "warn",
        "prefer-const": "warn",
        "prefer-template": "warn",
      },
    }],
  });

  const results = await eslint.lintFiles([target]);
  await ESLint.outputFixes(results);
  const msgs = results[0].messages.length;
  const fixed = results[0].fixableWarningCount + results[0].fixableErrorCount;
  if (msgs > 0) {
    console.log(`${target.split(/[/\\]/).pop()}: ${fixed} fixed, ${msgs - fixed} remaining`);
  }
}
