import { useEffect, useMemo, useRef, useState } from "react";

/** JSON editor with override-aware highlighting.
 *
 *  Approach: textarea overlays a syntax-highlighted preview. We avoid
 *  CodeMirror (~150 KB) for now in favor of a simple textarea-on-top-of-
 *  preview pattern. Trade-off: no bracket matching / autocomplete; the
 *  user-facing payload is small enough (~50 fields) that this is fine.
 *
 *  Override accents: lines whose JSON key matches an override key get an
 *  accent left-border + a trailing comment showing the baseline value.
 *  Pure presentational — the *content* of the text is just the effective
 *  config.
 *
 *  Sync: parent owns the effective object. We render its prettified
 *  JSON. On every keystroke we try to parse: on success, we diff vs
 *  baseline and emit an `onChange(parsed)` with the new effective
 *  (parent recomputes overrides from this). On parse error we surface
 *  `onError(msg)` and the parent disables Run. */
export type JsonEditorProps = {
  value: Record<string, unknown>;
  baseline?: Record<string, unknown> | null;
  readOnly?: boolean;
  onChange?: (parsed: Record<string, unknown>) => void;
  onError?: (msg: string | null) => void;
};

export function JsonEditor({ value, baseline, readOnly, onChange, onError }: JsonEditorProps) {
  const initial = useMemo(() => JSON.stringify(value, null, 2), [value]);
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const ta = useRef<HTMLTextAreaElement | null>(null);

  // When the *parent's* value changes (e.g., the user dragged a slider
  // in Form mode), re-sync the editor text. Don't overwrite if our text
  // already parses to the same value (avoids cursor jumps mid-typing).
  useEffect(() => {
    const same = (() => {
      try {
        return JSON.stringify(JSON.parse(text)) === JSON.stringify(value);
      } catch { return false; }
    })();
    if (!same) setText(JSON.stringify(value, null, 2));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleChange = (next: string) => {
    setText(next);
    try {
      const parsed = JSON.parse(next);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        throw new Error("Top-level value must be an object");
      }
      setError(null);
      onError?.(null);
      onChange?.(parsed);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      onError?.(msg);
    }
  };

  // Lines that override the baseline get a left-border accent. Build a
  // set of override keys for fast lookup during rendering.
  const overrideKeys = useMemo(() => {
    if (!baseline) return new Set<string>();
    const out = new Set<string>();
    try {
      const parsed = JSON.parse(text);
      for (const k of Object.keys(parsed)) {
        const a = JSON.stringify(parsed[k]);
        const b = JSON.stringify((baseline as Record<string, unknown>)[k]);
        if (a !== b) out.add(k);
      }
    } catch {}
    return out;
  }, [text, baseline]);

  const lines = text.split("\n");
  // Match `"key":` at the start of a line (ignoring indent) to figure
  // out which key a given source line belongs to.
  const keyOfLine = (line: string): string | null => {
    const m = line.match(/^\s*"([^"]+)":/);
    return m ? m[1] : null;
  };

  return (
    <div className="relative font-mono text-[11px] leading-[1.5]">
      {error && (
        <div className="px-3 py-1 text-warning text-[10px] bg-warning/10 border-b border-warning/30">
          JSON parse error: {error}
        </div>
      )}
      <div className="relative">
        {/* Highlighted overlay (visual only). Behind the textarea. */}
        <pre
          aria-hidden
          className="absolute inset-0 m-0 p-3 whitespace-pre-wrap pointer-events-none text-text-secondary"
        >
          {lines.map((line, i) => {
            const k = keyOfLine(line);
            const isOverride = k !== null && overrideKeys.has(k);
            const baselineValue =
              k !== null && baseline
                ? (baseline as Record<string, unknown>)[k]
                : undefined;
            return (
              <div
                key={i}
                className={
                  isOverride
                    ? "bg-accent/5 border-l-2 border-accent pl-1 -ml-1"
                    : ""
                }
              >
                {line}
                {isOverride && baselineValue !== undefined && (
                  <span className="text-warning text-[10px] ml-2">
                    // override (recipe: {JSON.stringify(baselineValue)})
                  </span>
                )}
              </div>
            );
          })}
        </pre>
        <textarea
          ref={ta}
          value={text}
          readOnly={readOnly}
          onChange={(e) => handleChange(e.target.value)}
          spellCheck={false}
          className="relative w-full min-h-[260px] p-3 bg-transparent text-transparent caret-text-primary resize-y selection:bg-accent/20 focus:outline-none"
          aria-label="Recipe JSON"
        />
      </div>
    </div>
  );
}
