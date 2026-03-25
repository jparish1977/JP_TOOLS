#!/usr/bin/env node
/**
 * JP_TOOLS ESLint runner — uses programmatic API so arbitrary file paths work
 * regardless of where the config file lives.
 *
 * Usage: node jp_eslint.mjs <path>
 * Output: JSON array of ESLint result objects (same as --format json)
 *
 * Auto-detects sourceType: files with import/export → "module", others → "script".
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
  } catch (_e) {
    return false;
  }
}

// Collect JS files to determine which are modules vs scripts
function collectJsFiles(targetPath) {
  const stat = statSync(targetPath);
  if (stat.isFile()) return [targetPath];
  const files = [];
  for (const entry of readdirSync(targetPath, { withFileTypes: true, recursive: true })) {
    if (entry.isFile() && /\.(m?js|ts|jsx|tsx)$/.test(entry.name)) {
      files.push(resolve(entry.parentPath || targetPath, entry.name));
    }
  }
  return files;
}

const jsFiles = collectJsFiles(absTarget);
const scriptFiles = jsFiles.filter(f => !isModuleFile(f));
const moduleFiles = jsFiles.filter(f => isModuleFile(f));

// cwd = target dir so ESLint's base path includes the file
const cwd = statSync(absTarget).isDirectory() ? absTarget : dirname(absTarget);

// Common rules for all JS
const commonRules = {
  "no-undef":              "error",
  "no-unused-vars":        ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
  "eqeqeq":                ["error", "always", { null: "ignore" }],
  "no-eval":               "error",
  "no-implied-eval":       "error",
  "prefer-const":          "warn",
  "no-var":                "warn",
  "no-console":            ["warn", { allow: ["warn", "error"] }],
  "object-shorthand":      "warn",
  "prefer-template":       "warn",
};

// Browser globals for script-mode files
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

// Build file-specific glob patterns for script files
const scriptGlobs = scriptFiles.map(f => f.replace(/\\/g, "/"));
const moduleGlobs = moduleFiles.map(f => f.replace(/\\/g, "/"));

const overrideConfig = [
  // HTML plugin
  {
    plugins: { html },
    files: ["**/*.html"],
  },
  // HTML inline scripts
  {
    files: ["**/*.html"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: browserGlobals,
    },
    rules: commonRules,
  },
];

// Module JS files
if (moduleGlobs.length > 0) {
  overrideConfig.push({
    files: moduleGlobs,
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: moduleGlobals,
    },
    rules: commonRules,
  });
}

// Script JS files (no import/export)
if (scriptGlobs.length > 0) {
  overrideConfig.push({
    files: scriptGlobs,
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: browserGlobals,
    },
    rules: commonRules,
  });
}

const eslint = new ESLint({
  cwd,
  overrideConfigFile: true,
  overrideConfig,
});

try {
  const results = await eslint.lintFiles([absTarget]);
  console.log(JSON.stringify(results));
} catch (err) {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
}
