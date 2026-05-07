import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

const PROVENANCE_KEY = "_provenance";
const NOTE_KEY = "_note";

export function ProvenanceFooter() {
  const activeRecipeData = useStore((s) => s.activeRecipeData);
  const activeRecipeName = useStore((s) => s.activeRecipeName);

  const provenance = activeRecipeData?.[PROVENANCE_KEY] as
    | { based_on?: string; saved_at?: string }
    | undefined;

  // Source preset to diff against: explicit `based_on` if present, else
  // the recipe's own name (i.e. user is editing a built-in directly).
  const basedOn =
    provenance?.based_on ??
    (activeRecipeName?.startsWith("★ ") ? activeRecipeName.slice(2) : activeRecipeName);

  const { data: source } = useQuery({
    queryKey: ["recipe", basedOn],
    queryFn: () => (basedOn ? api.recipes.get(basedOn) : Promise.resolve(null)),
    enabled: !!basedOn,
  });

  if (!activeRecipeData) {
    return <div className="text-text-muted text-xs py-1">(no recipe loaded)</div>;
  }

  // No provenance and source isn't loaded yet → assume built-in.
  if (!provenance && !source) {
    return <div className="text-text-secondary py-1">Built-in preset.</div>;
  }

  const diffs = source ? countDiffs(source.data, activeRecipeData) : 0;

  return (
    <div className="text-text-secondary py-1 space-y-0.5">
      <div>
        Based on <span className="text-accent">{basedOn ?? "(unknown)"}</span>
        {source && diffs > 0 && (
          <>
            {" "}· <span className="text-warning">{diffs} edit{diffs === 1 ? "" : "s"}</span>
          </>
        )}
      </div>
      {provenance?.saved_at && (
        <div className="text-text-muted">saved {provenance.saved_at}</div>
      )}
    </div>
  );
}

function countDiffs(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): number {
  let n = 0;
  const keys = new Set([...Object.keys(a ?? {}), ...Object.keys(b ?? {})]);
  for (const k of keys) {
    if (k === PROVENANCE_KEY || k === NOTE_KEY) continue;
    if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) n++;
  }
  return n;
}
