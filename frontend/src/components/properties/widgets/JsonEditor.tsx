import { useEffect, useMemo, useRef, useState } from "react";
import { Lock } from "lucide-react";

/** JSON editor with syntax highlighting, line numbers, override-aware
 *  spans, and line-mapped error indicators.
 *
 *  Architecture: a textarea overlays a syntax-highlighted preview.
 *  The textarea is *visible-but-transparent text* — only its caret +
 *  selection are seen; the preview renders the colors. The two surfaces
 *  share font / line-height / padding / tab-size so the caret tracks
 *  exactly. Long lines do NOT wrap (`whitespace: pre` + `overflow-x:
 *  auto`) — wrapping caused cursor desync in the previous version.
 *
 *  Override accent: a top-level key whose value differs from the
 *  baseline gets an accent left-border on EVERY line of its value
 *  (handles nested objects/arrays). We compute the brace/bracket depth
 *  per line during a single pass and mark every line that's inside
 *  (or starts) an overridden top-level key.
 *
 *  Parse errors: when JSON.parse throws, we extract the `position` from
 *  the message ("Unexpected token X in JSON at position N"), map it to
 *  line/column, and surface both in the error banner. A red arrow tick
 *  appears in the gutter on that line. */
export type JsonEditorProps = {
  value: Record<string, unknown>;
  baseline?: Record<string, unknown> | null;
  readOnly?: boolean;
  onChange?: (parsed: Record<string, unknown>) => void;
  onError?: (msg: string | null) => void;
};

type LineMeta = {
  /** Top-level key that owns this line (null for braces / blank). */
  ownerKey: string | null;
  /** True iff this line's owner key is an override. */
  isOverride: boolean;
  /** First line of an overridden block — shows the trailing comment. */
  isFirstOfOverride: boolean;
  /** Baseline value to show in the comment (only when isFirstOfOverride). */
  baselineRepr: string | null;
};

export function JsonEditor({ value, baseline, readOnly, onChange, onError }: JsonEditorProps) {
  // Source of truth for text is the textarea. value sync only fires
  // when the parent's value changes AND our current text doesn't
  // already parse to the same shape — avoids cursor jumps when the
  // user is mid-edit and the parent slider re-publishes.
  const initial = useMemo(() => JSON.stringify(value, null, 2), []); // eslint-disable-line
  const [text, setText] = useState(initial);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [errorLine, setErrorLine] = useState<number | null>(null);
  const ta = useRef<HTMLTextAreaElement | null>(null);

  // Re-sync from parent only when their value diverges from ours.
  useEffect(() => {
    let textParses: unknown = null;
    try { textParses = JSON.parse(text); } catch { textParses = null; }
    if (textParses !== null && JSON.stringify(textParses) === JSON.stringify(value)) {
      return;
    }
    setText(JSON.stringify(value, null, 2));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleChange = (next: string) => {
    setText(next);
    try {
      const parsed = JSON.parse(next);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("Top-level value must be an object");
      }
      setErrorMsg(null);
      setErrorLine(null);
      onError?.(null);
      onChange?.(parsed);
    } catch (e) {
      const raw = e instanceof Error ? e.message : String(e);
      setErrorMsg(raw);
      // Extract `position N` from common JSON error messages
      const posMatch = raw.match(/position (\d+)/);
      if (posMatch) {
        const pos = parseInt(posMatch[1], 10);
        const before = next.slice(0, pos);
        const line = before.split("\n").length;
        setErrorLine(line);
      } else {
        setErrorLine(null);
      }
      onError?.(raw);
    }
  };

  // Compute per-line metadata in one pass. Tracks brace/bracket depth
  // to identify which top-level key "owns" each line.
  const lineMeta = useMemo(() => {
    const overrideKeys = new Set<string>();
    if (baseline) {
      try {
        const parsed = JSON.parse(text);
        for (const k of Object.keys(parsed)) {
          const a = JSON.stringify(parsed[k]);
          const b = JSON.stringify((baseline as Record<string, unknown>)[k]);
          if (a !== b) overrideKeys.add(k);
        }
      } catch { /* parse error — no override accents until fixed */ }
    }

    const lines = text.split("\n");
    const meta: LineMeta[] = [];
    let depth = 0;            // current brace+bracket depth
    let currentOwner: string | null = null;  // top-level key on the active block
    let blockStarted = false;  // becomes true on the line where ownerKey appears

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      // Detect a top-level key declaration: at depth === 1 right after
      // any leading whitespace, a `"key":` pattern starts a new block.
      // Top-level keys live at depth 1 because the surrounding `{` at
      // line 0 raises depth to 1.
      const keyMatch = depth === 1 ? line.match(/^\s*"([^"]+)":/) : null;
      if (keyMatch) {
        currentOwner = keyMatch[1];
        blockStarted = true;
      } else if (depth === 0) {
        currentOwner = null;
      }

      const isOverride = currentOwner !== null && overrideKeys.has(currentOwner);
      const isFirstOfOverride = isOverride && blockStarted;
      const baselineRepr =
        isFirstOfOverride && baseline
          ? JSON.stringify((baseline as Record<string, unknown>)[currentOwner!])
          : null;

      meta.push({ ownerKey: currentOwner, isOverride, isFirstOfOverride, baselineRepr });
      blockStarted = false;

      // Update depth AFTER recording this line's metadata so the line
      // containing the opening brace is part of its owner's block.
      for (const ch of line) {
        if (ch === "{" || ch === "[") depth++;
        else if (ch === "}" || ch === "]") depth--;
      }
      // If depth returned to 1 after this line, the previous owner's
      // block has ended.
      if (depth <= 1) {
        // currentOwner stays so any sibling-line interpretation is
        // consistent until the next keyMatch.
      }
    }
    return meta;
  }, [text, baseline]);

  const lines = text.split("\n");
  const lineCount = lines.length;
  const gutterWidth = Math.max(2, String(lineCount).length) * 8 + 16; // approx px

  return (
    <div className="relative font-mono text-[11px] leading-[1.55]">
      {errorMsg && (
        <div className="px-3 py-1 text-warning text-[10px] bg-warning/10 border-b border-warning/30 flex items-center gap-2">
          <span className="font-medium">JSON parse error</span>
          {errorLine !== null && (
            <span className="font-mono text-warning/70">line {errorLine}</span>
          )}
          <span className="truncate flex-1 text-warning/80" title={errorMsg}>
            {errorMsg}
          </span>
        </div>
      )}

      {readOnly && (
        <div className="px-3 py-1 text-text-muted text-[10px] bg-elevated/40 border-b border-border flex items-center gap-1.5">
          <Lock size={10} />
          <span>Read-only — duplicate this recipe to edit</span>
        </div>
      )}

      <div className="relative">
        {/* Gutter + highlight overlay. Pointer-events disabled so the
            textarea beneath receives clicks. */}
        <pre
          aria-hidden
          className="absolute inset-0 m-0 whitespace-pre overflow-hidden pointer-events-none"
          style={{ paddingLeft: gutterWidth + 12, paddingTop: 12, paddingRight: 12, paddingBottom: 12 }}
        >
          {lines.map((line, i) => {
            const m = lineMeta[i];
            const overrideClass = m?.isOverride
              ? "bg-accent/5 border-l-2 border-accent -ml-1 pl-1"
              : "";
            return (
              <div
                key={i}
                className={overrideClass + " relative"}
              >
                {highlightLine(line)}
                {m?.isFirstOfOverride && m.baselineRepr !== null && (
                  <span className="text-warning/80 text-[10px] ml-2">
                    {" "}// was: {truncateRepr(m.baselineRepr, 36)}
                  </span>
                )}
              </div>
            );
          })}
        </pre>

        {/* Gutter — line numbers, sits visually on top of the overlay
            but doesn't take pointer events. */}
        <div
          aria-hidden
          className="absolute inset-y-0 left-0 pointer-events-none text-text-muted/50 text-right select-none"
          style={{ width: gutterWidth, paddingTop: 12, paddingRight: 8 }}
        >
          {lines.map((_, i) => (
            <div
              key={i}
              className={
                "leading-[1.55] " +
                (errorLine === i + 1 ? "text-warning font-semibold" : "")
              }
            >
              {i + 1}
            </div>
          ))}
        </div>

        <textarea
          ref={ta}
          value={text}
          readOnly={readOnly}
          onChange={(e) => handleChange(e.target.value)}
          spellCheck={false}
          wrap="off"
          className={
            "relative block w-full min-h-[280px] bg-transparent text-transparent " +
            "caret-text-primary resize-y selection:bg-accent/25 focus:outline-none " +
            "whitespace-pre overflow-auto " +
            (readOnly ? "cursor-not-allowed" : "")
          }
          style={{
            paddingLeft: gutterWidth + 12,
            paddingTop: 12,
            paddingRight: 12,
            paddingBottom: 12,
            tabSize: 2,
          }}
          aria-label="Recipe JSON"
        />
      </div>
    </div>
  );
}

/** Tokenize one line of JSON for syntax coloring. Hand-rolled so we
 *  don't pull in Prism. Doesn't validate — just colors. Handles strings
 *  (including escaped quotes), keys (string followed by `:`), numbers,
 *  booleans, null, and punctuation. */
function highlightLine(line: string): React.ReactNode {
  const out: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < line.length) {
    const ch = line[i];
    // String (or key)
    if (ch === '"') {
      let j = i + 1;
      while (j < line.length && line[j] !== '"') {
        if (line[j] === "\\" && j + 1 < line.length) j += 2;
        else j++;
      }
      const lit = line.slice(i, j + 1);
      // Look ahead past whitespace for `:` → it's a key
      let k = j + 1;
      while (k < line.length && /\s/.test(line[k])) k++;
      const isKey = line[k] === ":";
      out.push(
        <span key={key++} className={isKey ? "text-accent" : "text-emerald-300"}>
          {lit}
        </span>,
      );
      i = j + 1;
      continue;
    }
    // Number (integer, float, scientific, negative)
    if (/[-0-9]/.test(ch)) {
      let j = i;
      if (ch === "-") j++;
      while (j < line.length && /[0-9.eE+\-]/.test(line[j])) j++;
      out.push(
        <span key={key++} className="text-amber-300">
          {line.slice(i, j)}
        </span>,
      );
      i = j;
      continue;
    }
    // Keyword: true / false / null
    if (line.slice(i, i + 4) === "true" || line.slice(i, i + 4) === "null") {
      out.push(
        <span key={key++} className="text-violet-300">
          {line.slice(i, i + 4)}
        </span>,
      );
      i += 4;
      continue;
    }
    if (line.slice(i, i + 5) === "false") {
      out.push(
        <span key={key++} className="text-violet-300">
          {line.slice(i, i + 5)}
        </span>,
      );
      i += 5;
      continue;
    }
    // Punctuation / whitespace — passthrough
    out.push(
      <span key={key++} className="text-text-muted/70">
        {ch}
      </span>,
    );
    i++;
  }
  return out;
}

function truncateRepr(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
