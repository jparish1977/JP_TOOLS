#!/usr/bin/env node
/**
 * Downgrade const → let inside functions that contain eval().
 *
 * eval() can assign to any local variable in its enclosing scope.
 * Tools like lebab and eslint --fix can't see inside eval strings,
 * so they incorrectly promote let → const. This script reverses that
 * for any function body containing eval().
 *
 * Usage: node fix-eval-const.mjs <file1> [file2] ...
 * Run AFTER lebab and eslint --fix, BEFORE adding suppressions.
 */
import { readFileSync, writeFileSync } from "fs";
import { resolve } from "path";

const files = process.argv.slice(2).map(f => resolve(f));
if (files.length === 0) {
  console.error("Usage: node fix-eval-const.mjs <file1> [file2] ...");
  process.exit(2);
}

for (const file of files) {
  const lines = readFileSync(file, "utf8").split("\n");
  let changes = 0;

  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes("eval(") && !lines[i].trim().startsWith("//")) {
      // Find enclosing function boundaries by brace counting
      let depth = 0;
      let funcStart = i;
      for (let j = i; j >= 0; j--) {
        for (const ch of lines[j]) {
          if (ch === "}") depth++;
          if (ch === "{") depth--;
        }
        if (depth < 0) { funcStart = j; break; }
      }

      depth = 0;
      let funcEnd = i;
      for (let j = funcStart; j < lines.length; j++) {
        for (const ch of lines[j]) {
          if (ch === "{") depth++;
          if (ch === "}") depth--;
        }
        if (depth <= 0 && j > funcStart) { funcEnd = j; break; }
      }

      // Downgrade const → let in this function
      for (let j = funcStart; j <= funcEnd; j++) {
        if (/^\s+const\s/.test(lines[j]) && !lines[j].includes("eslint-disable")) {
          lines[j] = lines[j].replace(/^(\s+)const\s/, "$1let ");
          changes++;
        }
      }
    }
  }

  if (changes > 0) {
    writeFileSync(file, lines.join("\n"));
    const name = file.replace(/.*[/\\]/, "");
    console.log(`${name}: ${changes} const → let in eval-containing functions`);
  }
}
