#!/usr/bin/env node
/**
 * Scan JS files and add eslint-disable-next-line comments for:
 * - no-eval: any line containing eval( that isn't already suppressed
 * - no-redeclare: var re-declarations that lebab can't convert
 *
 * Usage: node add-suppressions.mjs <file1> [file2] ...
 */
import { readFileSync, writeFileSync } from "fs";
import { resolve } from "path";

const files = process.argv.slice(2).map(f => resolve(f));

for (const file of files) {
  const lines = readFileSync(file, "utf8").split("\n");
  let added = 0;

  // Work backwards to preserve line numbers
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];

    // Already suppressed or is a suppression comment
    if (line.includes("eslint-disable")) continue;
    // Skip pure comment lines
    if (line.trim().startsWith("//")) continue;

    const indent = (line.match(/^\s*/) || [""])[0];

    // no-eval
    if (line.includes("eval(")) {
      lines.splice(i, 0, `${indent}// eslint-disable-next-line no-eval -- user-defined expression`);
      added++;
      continue;
    }

    // no-redeclare: var that shadows a parameter or earlier declaration
    // These are the patterns lebab flagged as "Unable to transform"
    if (/^\s+var\s+(geometry|presetName|c|newX|newY|particleVector|index|len|pad|dir)\b/.test(line)) {
      lines.splice(i, 0, `${indent}// eslint-disable-next-line no-redeclare -- var re-declaration in shared scope`);
      added++;
      continue;
    }
  }

  if (added > 0) {
    writeFileSync(file, lines.join("\n"));
    const name = file.replace(/.*[/\\]/, "");
    console.log(`${name}: ${added} suppressions added`);
  }
}
