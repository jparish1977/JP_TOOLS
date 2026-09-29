#!/usr/bin/env node
/**
 * JP_TOOLS Stylelint runner: lints CSS files and <style> blocks in HTML.
 *
 * Usage: node jp_stylelint.mjs <path>
 * Output: JSON array of stylelint result objects
 */
import stylelint from "stylelint";
import { resolve, extname, dirname, join, delimiter } from "path";
import { existsSync } from "fs";
import { fileURLToPath } from "url";

// stylelint resolves `extends` and `customSyntax` itself, from configBasedir,
// not through node's import resolution. So name the checkout whose
// node_modules holds the shared config: this one, or, in a git worktree that
// has none, the checkout it belongs to (JP_TOOLS_NODE_ROOTS, set by check.py).
const here = dirname(fileURLToPath(import.meta.url));
const bases = [
  here,
  ...(process.env.JP_TOOLS_NODE_ROOTS || "")
    .split(delimiter)
    .filter(Boolean)
    .map((modules) => dirname(modules)),
];
const configBasedir =
  bases.find((base) =>
    existsSync(join(base, "node_modules", "stylelint-config-standard")),
  ) ?? here;

const [target] = process.argv.slice(2);
if (!target) {
  console.error(
    JSON.stringify({ error: "Usage: node jp_stylelint.mjs <path>" }),
  );
  process.exit(2);
}

const absTarget = resolve(target);
if (!existsSync(absTarget)) {
  console.error(JSON.stringify({ error: `Not found: ${absTarget}` }));
  process.exit(2);
}

const isHtml = [".html", ".htm"].includes(extname(absTarget).toLowerCase());

const config = {
  extends: ["stylelint-config-standard"],
  rules: {
    // Relax rules that are noisy for hand-written CSS
    "color-named": null,
    "alpha-value-notation": null,
    "color-function-notation": null,
    "declaration-block-no-redundant-longhand-properties": null,
  },
};

if (isHtml) {
  config.customSyntax = "postcss-html";
}

try {
  const result = await stylelint.lint({
    files: absTarget,
    config,
    configBasedir,
  });
  // Only the fields check.py reads. A whole result object can hold a parser
  // that refers back to itself, which JSON.stringify cannot print.
  const out = result.results.map((r) => ({
    source: r.source,
    warnings: r.warnings.map((w) => ({
      line: w.line,
      column: w.column,
      severity: w.severity,
      rule: w.rule,
      text: w.text,
    })),
  }));
  process.stdout.write(`${JSON.stringify(out)}\n`);
} catch (err) {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
}
