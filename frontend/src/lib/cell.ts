export type CellKind = "sequence" | "model";

/** Typed reference to a cell loaded in the viser viewer.
 *
 *  The viser control API speaks "wire format" — a single string with the
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
