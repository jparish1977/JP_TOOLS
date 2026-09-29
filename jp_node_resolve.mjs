/**
 * Loaded with `node --import` before jp_eslint.mjs or jp_stylelint.mjs, only
 * when this checkout has no node_modules/ of its own (a git worktree).
 *
 * A bare import such as `import { ESLint } from "eslint"` is resolved from the
 * importing file's own directory, so in a worktree it fails even though the
 * checkout the worktree belongs to has the package installed. This registers
 * jp_node_hooks.mjs, which retries such an import from each directory named
 * in JP_TOOLS_NODE_ROOTS (a list of node_modules directories, separated by
 * the platform's path delimiter), in order.
 */
import { register } from "node:module";
import { delimiter } from "node:path";

const roots = (process.env.JP_TOOLS_NODE_ROOTS || "")
  .split(delimiter)
  .filter(Boolean);
register("./jp_node_hooks.mjs", import.meta.url, { data: { roots } });
