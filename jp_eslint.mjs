#!/usr/bin/env node
/**
 * JP_TOOLS ESLint runner — uses programmatic API so arbitrary file paths work
 * regardless of where the config file lives.
 *
 * Usage: node jp_eslint.mjs <path>
 * Output: JSON array of ESLint result objects (same as --format json)
 */
import { ESLint } from "eslint";
import html from "eslint-plugin-html";
import { resolve, dirname } from "path";
import { existsSync, statSync } from "fs";

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

// cwd = target dir so ESLint's base path includes the file
const cwd = statSync(absTarget).isDirectory() ? absTarget : dirname(absTarget);

const eslint = new ESLint({
  cwd,
  overrideConfigFile: true,   // disable project config lookup
  overrideConfig: [
    {
      plugins: { html },
      files: ["**/*.html"],
    },
    {
      files: ["**/*.mjs", "**/*.js", "**/*.ts", "**/*.jsx", "**/*.tsx"],
      languageOptions: {
        ecmaVersion: 2022,
        sourceType: "module",
        globals: {
          process: "readonly", console: "readonly",
          setTimeout: "readonly", clearTimeout: "readonly",
          setInterval: "readonly", clearInterval: "readonly",
          URL: "readonly",
        },
      },
    },
    {
      files: ["**/*.html"],
      languageOptions: {
        ecmaVersion: 2022,
        sourceType: "script",   // HTML inline scripts are typically non-module
        globals: {
          window: "readonly", document: "readonly", console: "readonly",
          fetch: "readonly", localStorage: "readonly", sessionStorage: "readonly",
          setTimeout: "readonly", clearTimeout: "readonly",
          setInterval: "readonly", clearInterval: "readonly",
          URL: "readonly", Blob: "readonly", FileReader: "readonly",
          Image: "readonly", requestAnimationFrame: "readonly",
          alert: "readonly", confirm: "readonly", prompt: "readonly",
          navigator: "readonly", location: "readonly",
        },
      },
      rules: {
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
      },
    },
  ],
});

try {
  const results = await eslint.lintFiles([absTarget]);
  console.log(JSON.stringify(results));
} catch (err) {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
}
