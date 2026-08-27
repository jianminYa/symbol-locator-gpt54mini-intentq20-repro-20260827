// LSP subset — only what the plugin actually uses.
// Reproduced here to avoid dragging vscode-languageserver-protocol runtime in.

export type LspPosition = {
  line: number; // 0-based
  character: number; // 0-based
};

export type LspRange = {
  start: LspPosition;
  end: LspPosition;
};

export type LspLocation = {
  uri: string;
  range: LspRange;
};

/** LSP SymbolKind enum (subset) */
export const SymbolKind = {
  File: 1,
  Module: 2,
  Namespace: 3,
  Package: 4,
  Class: 5,
  Method: 6,
  Property: 7,
  Field: 8,
  Constructor: 9,
  Enum: 10,
  Interface: 11,
  Function: 12,
  Variable: 13,
  Constant: 14,
  String: 15,
  Number: 16,
  Boolean: 17,
  Array: 18,
  Object: 19,
  Key: 20,
  Null: 21,
  EnumMember: 22,
  Struct: 23,
  Event: 24,
  Operator: 25,
  TypeParameter: 26,
} as const;

export type SymbolKindNumber = (typeof SymbolKind)[keyof typeof SymbolKind];

/** Map numeric SymbolKind to a human-readable string used in tool output. */
export function symbolKindName(kind: number): string {
  const entry = Object.entries(SymbolKind).find(([, v]) => v === kind);
  return entry ? entry[0].toLowerCase() : `kind-${kind}`;
}

/** As returned by `workspace/symbol` (flat, non-hierarchical). */
export type LspWorkspaceSymbol = {
  name: string;
  kind: number;
  containerName?: string;
  location: LspLocation;
};

/**
 * As returned by `textDocument/documentSymbol` (hierarchical variant).
 * pyright always returns the hierarchical form when the client declares support.
 */
export type LspDocumentSymbol = {
  name: string;
  kind: number;
  detail?: string;
  range: LspRange;
  selectionRange: LspRange;
  children?: LspDocumentSymbol[];
};

/** Normalized symbol shape used inside the plugin. */
export type PlainSymbol = {
  name: string;
  kind: number;
  kindName: string;
  file: string; // absolute filesystem path
  line: number; // 1-based, human-friendly
  column: number; // 1-based
  container?: string;
};

export function fileUriToPath(uri: string): string {
  return uri.startsWith("file://") ? decodeURIComponent(uri.slice("file://".length)) : uri;
}

export function pathToFileUri(path: string): string {
  return `file://${encodeURI(path)}`;
}
