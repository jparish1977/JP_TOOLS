/**
 * Module resolve hooks registered by jp_node_resolve.mjs.
 *
 * A bare specifier that cannot be resolved from where it is imported is
 * retried as though imported from inside each root in turn. Relative and
 * absolute specifiers, and anything that resolves normally, are untouched,
 * so a package installed in this checkout always wins.
 */
import { pathToFileURL } from "node:url";
import { join } from "node:path";

let roots = [];

export function initialize(data) {
  roots = (data && data.roots) || [];
}

function isBare(specifier) {
  return !/^(\.{0,2}\/|[a-zA-Z][a-zA-Z0-9+.-]*:)/.test(specifier);
}

export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    if (error?.code !== "ERR_MODULE_NOT_FOUND" || !isBare(specifier)) {
      throw error;
    }
    for (const root of roots) {
      const parentURL = pathToFileURL(
        join(root, "jp-tools-resolve-base.mjs"),
      ).href;
      try {
        return await nextResolve(specifier, { ...context, parentURL });
      } catch (retryError) {
        if (retryError?.code !== "ERR_MODULE_NOT_FOUND") {
          throw retryError;
        }
      }
    }
    throw error;
  }
}
