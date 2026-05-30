export type CellKind = "sequence" | "model";

/** Coerce an arbitrary label into a valid cell / run name: replace any run of
 *  disallowed characters with a single underscore, then trim leading/trailing
 *  underscores. Composed-recipe names use a "·" separator
 *  (e.g. "earthquake·watermelon") which is NOT in the allowed
 *  [A-Za-z0-9_.-] set — without this the CellRef ctor throws
 *  ("invalid cell name") and the backend's run_name regex 422s the run. */
export function sanitizeCellName(raw: string): string {
  return raw.replace(/[^A-Za-z0-9_.\-]+/g, "_").replace(/^_+|_+$/g, "");
}

/** Typed reference to a cell displayed in the SPA viewport.
 *
 *  Cells are identified by a "wire format" — a single string with a
 *  kind prefix, e.g. `sequence:cluster_6_15` or `model:tower_01`. The
 *  frontend store used to carry the kind and name as a separate
 *  `{ kind, name }` object and stringify them on demand; that left every
 *  call site free to invent its own concatenation / parsing, and one of
 *  them inevitably stored the bare name where wire format was expected
 *  (or vice versa). `CellRef` is the single place that handles the
 *  round-trip so the bug becomes a type error.
 *
 *  Instances are intentionally immutable + value-equal via `wire` so
 *  zustand selectors that gate on `activeCell` don't re-fire on every
 *  render. Use the static parsers when accepting external strings;
 *  construct directly when you already know kind + name. */
export class CellRef {
  constructor(public readonly kind: CellKind, public readonly name: string) {
    if (!/^[A-Za-z0-9_.\-]+$/.test(name)) {
      throw new Error(`invalid cell name: ${name}`);
    }
  }
  get wire(): string { return `${this.kind}:${this.name}`; }
  static parseWire(s: string): CellRef {
    const ix = s.indexOf(":");
    if (ix < 0) throw new Error(`not a wire-format cell: ${s}`);
    const kind = s.slice(0, ix);
    if (kind !== "sequence" && kind !== "model") {
      throw new Error(`unknown cell kind: ${kind}`);
    }
    return new CellRef(kind, s.slice(ix + 1));
  }
  static tryParseWire(s: string | null | undefined): CellRef | null {
    if (!s) return null;
    try { return CellRef.parseWire(s); } catch { return null; }
  }
  /** Stable identity key for selectors / React keys. Same kind+name →
   *  same string, so referential equality on the wrapping object isn't
   *  needed to keep zustand's shallow compare happy. */
  get key(): string { return this.wire; }
  equals(other: CellRef | null | undefined): boolean {
    return !!other && other.kind === this.kind && other.name === this.name;
  }
}
