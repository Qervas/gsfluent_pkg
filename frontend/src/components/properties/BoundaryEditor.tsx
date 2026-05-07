import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { BoundaryRow } from "./BoundaryRow";

type BC = { type: string; [k: string]: unknown };

export function BoundaryEditor() {
  const { data: schemas, isLoading } = useQuery({
    queryKey: ["bc_schemas"],
    queryFn: api.schemas.boundaries,
  });
  const data = useStore((s) => s.activeRecipeData);
  const name = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);

  if (!data || !name) return null;
  if (isLoading || !schemas) {
    return <div className="text-text-muted text-xs py-1">Loading BC schemas…</div>;
  }

  const bcs: BC[] = Array.isArray(data.boundary_conditions)
    ? (data.boundary_conditions as BC[])
    : [];

  const setBcs = (next: BC[]) => {
    setActiveRecipe(name, { ...data, boundary_conditions: next });
  };

  const addBC = () => {
    // Default new BC to the first available type, with that type's defaults
    // pre-filled.
    const types = Object.keys(schemas);
    if (types.length === 0) return;
    const t = types[0];
    const fresh: BC = { type: t };
    for (const f of schemas[t] ?? []) {
      // Schema defaults are already JSON-serializable.
      fresh[f.name] = f.default;
    }
    setBcs([...bcs, fresh]);
  };

  return (
    <div className="space-y-2">
      {bcs.map((bc, i) => (
        <BoundaryRow
          key={i}
          bc={bc}
          schemas={schemas}
          onChange={(next) =>
            setBcs(bcs.map((x, j) => (j === i ? next : x)))
          }
          onDelete={() => setBcs(bcs.filter((_, j) => j !== i))}
        />
      ))}
      <button
        onClick={addBC}
        className="w-full flex items-center justify-center gap-1 py-1 text-xs text-accent hover:bg-elevated rounded"
      >
        <Plus size={12} /> Add boundary
      </button>
    </div>
  );
}
