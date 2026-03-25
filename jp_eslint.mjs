#!/usr/bin/env node
/**
 * JP_TOOLS ESLint runner — uses programmatic API so arbitrary file paths work
 * regardless of where the config file lives.
 *
 * Usage: node jp_eslint.mjs <path>
 * Output: JSON array of ESLint result objects (same as --format json)
 *
 * Auto-detects sourceType: files with import/export → "module", others → "script".
 * Lints each file individually to ensure correct sourceType per file.
 */
import { ESLint } from "eslint";
import html from "eslint-plugin-html";
import { resolve, dirname, extname } from "path";
import { existsSync, statSync, readFileSync, readdirSync } from "fs";

const [target] = process.argv.slice(2);
if (!target) {
  console.error(JSON.stringify({ error: "Usage: node jp_eslint.mjs <path>" }));
  process.exit(2);
}

const absTarget = resolve(target);
if (!existsSync(absTarget)) {
  console.error(JSON.stringify({ error: `Not found: ${absTarget}` }));
  process.exit(2);
}

// Detect whether a JS file uses ES module syntax
function isModuleFile(filePath) {
  if (extname(filePath) === ".mjs") return true;
  if (extname(filePath) === ".cjs") return false;
  try {
    const src = readFileSync(filePath, "utf8");
    return /\b(import\s+|export\s+(default\s+|const\s+|let\s+|var\s+|function\s+|class\s+|\{))/m.test(src);
  } catch {
    return false;
  }
}

// Collect lintable files from a directory
function collectFiles(targetPath) {
  const stat = statSync(targetPath);
  if (stat.isFile()) return [targetPath];
  const files = [];
  const skip = new Set(["node_modules", "vendor", ".git", "dist", "build"]);
  function walk(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = resolve(dir, entry.name);
      if (entry.isDirectory()) {
        if (!skip.has(entry.name)) walk(full);
      } else if (/\.(m?js|ts|jsx|tsx|html?)$/.test(entry.name)) {
        files.push(full);
      }
    }
  }
  walk(targetPath);
  return files;
}

// Common rules — quality + correctness
const commonRules = {
  // Correctness
  "no-undef":              "error",
  "no-unused-vars":        ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
  "eqeqeq":                ["error", "always", { null: "ignore" }],
  "no-eval":               "error",
  "no-implied-eval":       "error",
  "no-new-func":           "error",
  "no-shadow":             "warn",
  "no-redeclare":          "error",

  // Quality
  "complexity":            ["warn", 25],
  "max-depth":             ["warn", 5],
  "max-lines-per-function": ["warn", { max: 200, skipBlankLines: true, skipComments: true }],
  "max-params":            ["warn", 6],
  "no-magic-numbers":      ["warn", { ignore: [-1, 0, 1, 2], ignoreArrayIndexes: true, ignoreDefaultValues: true }],

  // Style
  "prefer-const":          "warn",
  "no-var":                "warn",
  "no-console":            ["warn", { allow: ["warn", "error"] }],
  "object-shorthand":      "warn",
  "prefer-template":       "warn",
};

// Browser globals
const browserGlobals = {
  window: "readonly", document: "readonly", console: "readonly",
  fetch: "readonly", localStorage: "readonly", sessionStorage: "readonly",
  setTimeout: "readonly", clearTimeout: "readonly",
  setInterval: "readonly", clearInterval: "readonly",
  URL: "readonly", Blob: "readonly", FileReader: "readonly",
  Image: "readonly", requestAnimationFrame: "readonly",
  alert: "readonly", confirm: "readonly", prompt: "readonly",
  navigator: "readonly", location: "readonly",
  performance: "readonly", HTMLElement: "readonly",
  HTMLCanvasElement: "readonly", WebGLRenderingContext: "readonly",
  Event: "readonly", MouseEvent: "readonly", KeyboardEvent: "readonly",
};

// Node/module globals
const moduleGlobals = {
  process: "readonly", console: "readonly",
  setTimeout: "readonly", clearTimeout: "readonly",
  setInterval: "readonly", clearInterval: "readonly",
  URL: "readonly",
};

// Lint a single file with the correct config
async function lintFile(filePath) {
  const isHtml = /\.html?$/.test(filePath);
  const isModule = !isHtml && isModuleFile(filePath);
  const dir = dirname(filePath);

  const config = [];

  if (isHtml) {
    config.push({
      plugins: { html },
      files: ["**"],
      languageOptions: {
        ecmaVersion: 2022,
        sourceType: "script",
        globals: browserGlobals,
      },
      rules: commonRules,
    });
  } else {
    config.push({
      files: ["**"],
      languageOptions: {
        ecmaVersion: 2022,
        sourceType: isModule ? "module" : "script",
        globals: isModule ? moduleGlobals : browserGlobals,
      },
      rules: commonRules,
    });
  }

  const eslint = new ESLint({
    cwd: dir,
    overrideConfigFile: true,
    overrideConfig: config,
  });

  return eslint.lintFiles([filePath]);
}

// Main
const files = collectFiles(absTarget);
const allResults = [];

for (const f of files) {
  try {
    const results = await lintFile(f);
    allResults.push(...results);
  } catch (err) {
    allResults.push({
      filePath: f,
      messages: [{ ruleId: null, severity: 2, message: err.message, line: 0, column: 0 }],
      errorCount: 1,
      warningCount: 0,
      fixableErrorCount: 0,
      fixableWarningCount: 0,
    });
  }
}

console.log(JSON.stringify(allResults));
