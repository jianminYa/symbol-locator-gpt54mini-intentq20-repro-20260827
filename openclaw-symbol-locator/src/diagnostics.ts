export type DiagnosticSink = (line: string) => void;

export function emitDiagnostic(sink: DiagnosticSink | undefined, message: string): void {
  const line = `[sl-diag] ${message}`;
  if (sink) sink(line);
  else process.stderr.write(`${line}\n`);
}
