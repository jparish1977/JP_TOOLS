#!/usr/bin/env node
/**
 * JP_TOOLS Stylelint runner — lints CSS files and <style> blocks in HTML.
 *
 * Usage: node jp_stylelint.mjs <path>
 * Output: JSON array of stylelint result objects
 */
import stylelint from "stylelint";
import { resolve, extname } from "path";
import { existsSync } from "fs";

const [target] = process.argv.slice(2);
if (!target) {
  console.error(JSON.stringify({ error: "Usage: node jp_stylelint.mjs <path>" }));
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
    "color-named":               null,
    "alpha-value-notation":      null,
    "color-function-notation":   null,
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
  console.log(JSON.stringify(out));
} catch (err) {
  console.error(JSON.stringify({ error: err.message }));
  process.exit(1);
}
