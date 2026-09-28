#!/usr/bin/env node
/**
 * Downgrade const -> let inside functions that contain eval().
 *
 * eval() can assign to any local variable in its enclosing scope.
 * Tools like lebab and eslint --fix can't see inside eval strings,
 * so they incorrectly promote let -> const. This script reverses that
 * for any function body containing eval().
 *
 * The enclosing function is found by counting braces, so a brace inside a
 * string or comment can misplace its bounds.
 *
 * Usage: node fix-eval-const.mjs <file1> [file2] ...
 * Run AFTER lebab and eslint --fix, BEFORE adding suppressions.
 */
import { readFileSync, writeFileSync } from "fs";
import { resolve } from "path";

/** Net brace balance of one line: +1 per `open`, -1 per `close`. */
function balance(line, open, close) {
  let n = 0;
  for (const ch of line) {
    if (ch === open) n++;
    if (ch === close) n--;
  }
  return n;
}

/** The line where the block enclosing line `i` opens. */
function blockStart(lines, i) {
  let depth = 0;
  for (let j = i; j >= 0; j--) {
    depth += balance(lines[j], "}", "{");
    if (depth < 0) return j;
  }
  return i;
}

/** The line where the block opened at `start` closes. */
function blockEnd(lines, start) {
  let depth = 0;
  for (let j = start; j < lines.length; j++) {
    depth += balance(lines[j], "{", "}");
    if (depth <= 0 && j > start) return j;
  }
  return start;
}

/** Rewrite `const` to `let` on lines start..end. Returns how many changed. */
function downgrade(lines, start, end) {
  let changes = 0;
  for (let j = start; j <= end; j++) {
    if (/^\s+const\s/.test(lines[j]) && !lines[j].includes("eslint-disable")) {
      lines[j] = lines[j].replace(/^(\s+)const\s/, "$1let ");
      changes++;
    }
  }
  return changes;
}

function fixFile(file) {
  const lines = readFileSync(file, "utf8").split("\n");
  let changes = 0;
  lines.forEach((line, i) => {
    if (line.includes("eval(") && !line.trim().startsWith("//")) {
      const start = blockStart(lines, i);
      changes += downgrade(lines, start, blockEnd(lines, start));
    }
  });
  if (changes > 0) {
    writeFileSync(file, lines.join("\n"));
    process.stdout.write(
      `${file.replace(/.*[/\\]/, "")}: ${changes} const -> let in eval-containing functions\n`,
    );
  }
}

const files = process.argv.slice(2).map((f) => resolve(f));
if (files.length === 0) {
  console.error("Usage: node fix-eval-const.mjs <file1> [file2] ...");
  process.exit(2);
}
files.forEach(fixFile);
