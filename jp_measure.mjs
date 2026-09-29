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
const DEFINING = new Set([
  "FunctionDeclaration",
  "FunctionExpression",
  "ArrowFunctionExpression",
  "ClassExpression",
  "ClassDeclaration",
]);

const linter = new Linter({ configType: "flat" });

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
}

function answer(doc, code = 0) {
  process.stdout.write(`${JSON.stringify(doc)}\n`);
  process.exit(code);
}

// A function's own name, from what it is bound to.
const NAMED_BY = {
  VariableDeclarator: (p) => (p.id.type === "Identifier" ? p.id.name : null),
  MethodDefinition: (p) => p.key.name ?? p.key.value,
  Property: (p) => p.key.name ?? p.key.value,
  AssignmentExpression: (p, source) => source.getText(p.left),
};

function nameOf(node, source) {
  if (node.id) return node.id.name;
  const named = NAMED_BY[node.parent?.type]?.(node.parent, source);
  return named ?? `(anonymous@${node.loc.start.line}:${node.loc.start.column})`;
}

// A top-level statement that runs, or null for one that only declares.
function running(statement) {
  let s = statement;
  if (s.type === "ImportDeclaration" || s.type === "ExportAllDeclaration")
    return null;
  if (s.type.startsWith("Export")) s = s.declaration;
  if (!s || DEFINING.has(s.type)) return null;
  const onlyDefines =
    s.type === "VariableDeclaration" &&
    s.declarations.every((d) => d.init && DEFINING.has(d.init.type));
  return onlyDefines ? null : s;
}

// A rule of our own that measures nothing: it records each function's name,
// range and lines, and the top-level statements, as ESLint walks the tree.
function spansRule(collected) {
  return {
    create(context) {
      const names = [];
      const enter = (node) => {
        const method = node.parent?.type === "MethodDefinition";
        const cls = method
          ? `${node.parent.parent.parent.id?.name ?? "(class)"}.`
          : "";
        const own = nameOf(node, context.sourceCode);
        const name = names.length ? `${names.at(-1)}.${own}` : cls + own;
        names.push(name);
        // A method's head is its key, before the function's own range.
        const start = method ? node.parent.range[0] : node.range[0];
        collected.funcs.push({
          name,
          range: [start, node.range[1]],
          lines: [node.loc.start.line, node.loc.end.line],
        });
      };
      const leave = () => names.pop();
      return {
        Program(program) {
          for (const s of program.body.map(running).filter(Boolean)) {
            collected.top.push({
              range: s.range,
              lines: [s.loc.start.line, s.loc.end.line],
            });
          }
        },
        FunctionDeclaration: enter,
        "FunctionDeclaration:exit": leave,
        FunctionExpression: enter,
        "FunctionExpression:exit": leave,
        ArrowFunctionExpression: enter,
        "ArrowFunctionExpression:exit": leave,
      };
    },
  };
}

function lint(code, sourceType, rules, collected) {
  const plugins = collected
    ? { jp: { rules: { spans: spansRule(collected) } } }
    : {};
  return linter.verify(code, [
    { languageOptions: { ecmaVersion: "latest", sourceType }, plugins, rules },
  ]);
}

// The functions and top level of SRC, as a module or else as a script.
function collect(src) {
  let firstError = null;
  for (const sourceType of ["module", "script"]) {
    const collected = { funcs: [], top: [] };
    const fatal = lint(
      src,
      sourceType,
      { "jp/spans": "error" },
      collected,
    ).find((m) => m.fatal);
    if (!fatal) return { sourceType, ...collected };
    firstError ??= fatal.message;
  }
  return answer({ error: firstError }, 2);
}

function withTopLevel(src, top) {
  if (!top.length) return { measured: src, syntheticLine: 0 };
  const head = src.replace(/\s+$/, "");
  const body = top.map((t) => src.slice(t.range[0], t.range[1])).join("\n");
  return {
    measured: `${head}\n\nasync function ${SYNTHETIC}() {\n${body}\n}\n`,
    syntheticLine: head.split("\n").length + 2,
  };
}

function offsets(code) {
  const starts = [0];
  for (let i = 0; i < code.length; i++)
    if (code[i] === "\n") starts.push(i + 1);
  return (line, column) => starts[line - 1] + column - 1;
}

// Which function a report at POS belongs to: the innermost holding it.
function holderOf(funcs, pos) {
  const holding = funcs.filter((f) => f.range[0] <= pos && pos <= f.range[1]);
  return holding.reduce((a, b) => (a && a.range[0] > b.range[0] ? a : b), null);
}

// Whose complexity report M is. The synthetic function's own head is the top
// level; a function inside it is a copy of one already measured where it
// stands, so it belongs to nobody.
function ownerOf(m, syntheticLine, funcs, at) {
  if (!syntheticLine || m.line < syntheticLine) {
    return holderOf(funcs, at(m.line, m.column))?.name;
  }
  return m.line === syntheticLine ? TOP_LEVEL : null;
}

function complexities({ measured, syntheticLine }, sourceType, funcs) {
  const at = offsets(measured);
  const cx = new Map();
  for (const m of lint(measured, sourceType, { complexity: ["error", 0] })) {
    if (m.fatal) answer({ error: `measuring: ${m.message}` }, 2);
    const n = Number(/complexity of (\d+)/.exec(m.message)?.[1]);
    const owner = n ? ownerOf(m, syntheticLine, funcs, at) : null;
    if (owner) cx.set(owner, n);
  }
  return cx;
}

function report(src, { funcs, top }, cx) {
  const out = {};
  for (const f of funcs)
    out[f.name] = [
      f.lines[1] - f.lines[0] + 1,
      cx.get(f.name) ?? 1,
      f.lines[0],
    ];
  if (top.length) {
    const size = top.reduce((s, t) => s + t.lines[1] - t.lines[0] + 1, 0);
    out[TOP_LEVEL] = [size, cx.get(TOP_LEVEL) ?? 1, top[0].lines[0]];
  }
  const lines = src.split("\n").length - (src.endsWith("\n") ? 1 : 0);
  return { lines, funcs: out };
}

async function main() {
  const src = await readStdin();
  const collected = collect(src);
  const cx = complexities(
    withTopLevel(src, collected.top),
    collected.sourceType,
    collected.funcs,
  );
  answer(report(src, collected, cx));
}

await main();
