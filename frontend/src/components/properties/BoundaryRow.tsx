import { Trash2 } from "lucide-react";
import { SelectInput } from "./widgets/SelectInput";
import { Vec3Input } from "./widgets/Vec3Input";
import { NumberInput } from "./widgets/NumberInput";

type BC = { type: string; [k: string]: unknown };
type FieldSpec = {
  name: string;
  type: "vec3" | "float" | "string";
  default: unknown;
  hint: string;
};

export function BoundaryRow({
  bc,
  schemas,
  onChange,
  onDelete,
}: {
  bc: BC;
  schemas: Record<string, FieldSpec[]>;
  onChange: (next: BC) => void;
  onDelete: () => void;
}) {
  const types = Object.keys(schemas);
  const fields = schemas[bc.type] ?? [];

  const setField = (key: string, v: unknown) => onChange({ ...bc, [key]: v });
  const setType = (newType: string) => {
    const fresh: BC = { type: newType };
    for (const f of schemas[newType] ?? []) {
      fresh[f.name] = f.default;
    }
    onChange(fresh);
  };

  const tuple = (a: unknown): [number, number, number] => {
    const arr = Array.isArray(a) ? a : [];
    return [Number(arr[0] ?? 0), Number(arr[1] ?? 0), Number(arr[2] ?? 0)];
  };

  return (
    <div className="border border-border rounded p-2 space-y-1 bg-canvas">
      <div className="flex items-center gap-2">
        <div className="flex-1">
          <SelectInput
            label="Type"
            value={bc.type}
            options={types}
            onChange={setType}
          />
        </div>
        <button
          onClick={onDelete}
          className="text-error/80 hover:text-error p-0.5"
          aria-label="delete boundary"
          title="Remove this boundary"
        >
          <Trash2 size={12} />
        </button>
      </div>

      {fields.map((f) => {
        const v = bc[f.name] ?? f.default;
        if (f.type === "vec3") {
          return (
            <Vec3Input
              key={f.name}
              label={f.name}
              value={tuple(v)}
              onChange={(nv) => setField(f.name, [nv[0], nv[1], nv[2]])}
              step={0.5}
              hint={f.hint}
            />
          );
        }
        if (f.type === "string") {
          return (
            <SelectInput
              key={f.name}
              label={f.name}
              value={typeof v === "string" ? v : String(v)}
              // Without a fixed enum from the schema, allow the current value
              // as the only option (lets users at least see / preserve it).
              // Phase 4 polish could load enum lists from a richer schema.
              options={[typeof v === "string" ? v : String(v)]}
              onChange={(nv) => setField(f.name, nv)}
              hint={f.hint}
            />
          );
        }
        // float
        return (
          <NumberInput
            key={f.name}
            label={f.name}
            value={Number(v ?? 0)}
            onChange={(n) => setField(f.name, n)}
            step={0.1}
            hint={f.hint}
          />
        );
      })}

      {fields.length === 0 && (
        <div className="text-text-muted text-xs italic">
          No additional fields for this BC type.
        </div>
      )}
    </div>
  );
}
