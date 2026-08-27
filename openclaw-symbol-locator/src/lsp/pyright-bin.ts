// Locate the pyright-langserver JS entrypoint shipped by the `pyright` npm package.
import { createRequire } from "node:module";

const req = createRequire(import.meta.url);

/**
 * Absolute path to `pyright-langserver` bundle entrypoint. Spawn with
 * `node <path> --stdio` to talk LSP.
 */
export function resolvePyrightLangserverBin(): string {
  // package.json bin: { "pyright-langserver": "langserver.index.js" }
  const pkgJsonPath = req.resolve("pyright/package.json");
  const pkg = req("pyright/package.json") as { bin?: Record<string, string> };
  const rel = pkg.bin?.["pyright-langserver"];
  if (!rel) {
    throw new Error("pyright package missing pyright-langserver bin declaration");
  }
  // pkgJsonPath = .../pyright/package.json; resolve rel relative to that dir
  const pkgDir = pkgJsonPath.slice(0, pkgJsonPath.lastIndexOf("/"));
  return `${pkgDir}/${rel}`;
}
