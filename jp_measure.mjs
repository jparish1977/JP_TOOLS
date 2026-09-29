#!/usr/bin/env node
/**
 * JP_TOOLS JS measure for the smells arm (jpcheck/measure.py).
 *
 * Reads a JS source on stdin. Prints JSON: {"lines": N, "funcs": {name:
 * [lines, complexity, first line]}}, or {"error": why} with exit 2 when the
 * source does not parse.
 *
 * Complexity is ESLint's own `complexity` rule, with its maximum at 0 so it
 * reports every function. It does not measure code outside functions, so the
 * top-level statements are copied into a synthetic function appended to the
 * source, measured as "(top level)". ESLint reports at a function's head (the
 * `=>` of an arrow, the key of a method), so each report goes to the innermost
 * function whose range holds it.
 */
import { Linter } from "eslint";

const TOP_LEVEL = "(top level)";
const SYNTHETIC = "__jp_top_level__";

const read = async () => {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
};

const FUNCTIONS = new Set(["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"]);
const DEFINING = new Set([...FUNCTIONS, "ClassExpression", "ClassDeclaration"]);

const linter = new Linter({ configType: "flat" });

function lint(code, sourceType, rules) {
  return linter.verify(code, [{
    languageOptions: { ecmaVersion: "latest", sourceType },
    rules,
    plugins: { jp: { rules: { spans: SPANS_RULE } } },
  }]);
}

// A rule of our own that measures nothing: it records each function's name,
// range and lines, and the top-level statements, as ESLint walks the tree.
const collected = { funcs: [], top: [] };
const SPANS_RULE = {
  create(context) {
    const names = [];
    const nameOf = (node) => {
      const p = node.parent;
      if (node.id) return node.id.name;
      if (p?.type === "VariableDeclarator" && p.id.type === "Identifier") return p.id.name;
      if (p?.type === "MethodDefinition" || p?.type === "Property") {
        return p.key.name ?? p.key.value ?? `@${node.loc.start.line}`;
      }
      if (p?.type === "AssignmentExpression") return context.sourceCode.getText(p.left);
      return `(anonymous@${node.loc.start.line}:${node.loc.start.column})`;
    };
    const enter = (node) => {
      const own = nameOf(node);
      const cls = node.parent?.type === "MethodDefinition"
        ? (node.parent.parent.parent.id?.name ?? "(class)") + "." : "";
      const name = names.length ? `${names.at(-1)}.${own}` : cls + own;
      names.push(name);
      // A method's head is its key, before the function's own range.
      const start = node.parent?.type === "MethodDefinition" ? node.parent.range[0] : node.range[0];
      collected.funcs.push({ name, range: [start, node.range[1]],
                             lines: [node.loc.start.line, node.loc.end.line] });
    };
    const leave = () => names.pop();
    return {
      Program(program) {
        for (let s of program.body) {
          if (s.type === "ImportDeclaration" || s.type === "ExportAllDeclaration") continue;
          if (s.type.startsWith("Export")) {
            if (!s.declaration) continue;
            s = s.declaration;
          }
          if (DEFINING.has(s.type)) continue;
          if (s.type === "VariableDeclaration" && s.declarations.every((d) => d.init && DEFINING.has(d.init.type))) continue;
          collected.top.push({ range: s.range, lines: [s.loc.start.line, s.loc.end.line] });
        }
      },
      FunctionDeclaration: enter, "FunctionDeclaration:exit": leave,
      FunctionExpression: enter, "FunctionExpression:exit": leave,
      ArrowFunctionExpression: enter, "ArrowFunctionExpression:exit": leave,
    };
  },
};

function offsets(code) {
  const starts = [0];
  for (let i = 0; i < code.length; i++) if (code[i] === "\n") starts.push(i + 1);
  return (line, column) => starts[line - 1] + column - 1;
}

const src = await read();
let sourceType = "module";
let errors = lint(src, sourceType, { "jp/spans": "error" }).filter((m) => m.fatal);
if (errors.length) {
  collected.funcs = []; collected.top = [];
  sourceType = "script";
  errors = lint(src, sourceType, { "jp/spans": "error" }).filter((m) => m.fatal);
}
if (errors.length) {
  console.log(JSON.stringify({ error: errors[0].message }));
  process.exit(2);
}
const { funcs, top } = collected;
const head = src.replace(/\s+$/, "");
let measured = src;
let syntheticLine = 0;
if (top.length) {
  const body = top.map((t) => src.slice(t.range[0], t.range[1])).join("\n");
  syntheticLine = head.split("\n").length + 2;
  measured = `${head}\n\nasync function ${SYNTHETIC}() {\n${body}\n}\n`;
}
const at = offsets(measured);
const cx = new Map();
for (const m of lint(measured, sourceType, { complexity: ["error", 0] })) {
  if (m.fatal) {
    console.log(JSON.stringify({ error: `measuring: ${m.message}` }));
    process.exit(2);
  }
  const n = Number(/complexity of (\d+)/.exec(m.message)?.[1]);
  if (m.ruleId !== "complexity" || !n) continue;
  // The synthetic function's own head is the top level; a function inside it
  // is a copy of one already measured where it stands.
  if (syntheticLine && m.line === syntheticLine) { cx.set(TOP_LEVEL, n); continue; }
  if (syntheticLine && m.line > syntheticLine) continue;
  const pos = at(m.line, m.column);
  const holding = funcs.filter((f) => f.range[0] <= pos && pos <= f.range[1]);
  if (!holding.length) continue;
  const inner = holding.reduce((a, b) => (b.range[0] >= a.range[0] ? b : a));
  cx.set(inner.name, n);
}
const out = {};
for (const f of funcs) out[f.name] = [f.lines[1] - f.lines[0] + 1, cx.get(f.name) ?? 1, f.lines[0]];
if (top.length) {
  const size = top.reduce((s, t) => s + t.lines[1] - t.lines[0] + 1, 0);
  out[TOP_LEVEL] = [size, cx.get(TOP_LEVEL) ?? 1, top[0].lines[0]];
}
console.log(JSON.stringify({ lines: src.split("\n").length - (src.endsWith("\n") ? 1 : 0), funcs: out }));
