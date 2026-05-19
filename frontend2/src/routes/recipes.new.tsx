import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

export const Route = createFileRoute("/recipes/new")({
  component: NewRecipePage,
});

const TEMPLATE = `{
  "material": "fluid",
  "solver": {
    "dt": 1e-4,
    "substeps": 8
  },
  "forces": {
    "gravity": [0, -9.8, 0]
  }
}
`;

function NewRecipePage(): JSX.Element {
  const navigate = useNavigate();
  const [name, setName] = useState<string>("");
  const [text, setText] = useState<string>(TEMPLATE);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => {
      let content: Record<string, unknown>;
      try {
        content = JSON.parse(text);
      } catch (e) {
        throw new Error(`JSON: ${(e as Error).message}`);
      }
      return api.recipes.create({ name: name || "untitled", content });
    },
    onSuccess: (r) => navigate({ to: "/recipes/$id", params: { id: r.id } }),
    onError: (e) => setError((e as Error).message),
  });

  return (
    <div className="space-y-4 max-w-3xl">
      <h1 className="text-xl font-semibold">New recipe</h1>

      <input
        value={name}
        onChange={(e) => setName(e.currentTarget.value)}
        placeholder="name"
        className="block w-full bg-elevated/60 border border-border rounded px-2 py-1.5 text-sm"
      />

      <textarea
        value={text}
        onChange={(e) => setText(e.currentTarget.value)}
        spellCheck={false}
        className="w-full h-[55vh] bg-slate-900/80 text-slate-100 font-mono text-sm
                   p-3 rounded border border-border outline-none focus:border-accent/60"
      />

      {error && <p className="text-red-400 text-xs">{error}</p>}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => create.mutate()}
          disabled={create.isPending || !name}
          className="px-4 py-2 rounded bg-accent text-slate-950 font-semibold text-sm disabled:opacity-40"
        >
          {create.isPending ? "creating…" : "Create"}
        </button>
      </div>
    </div>
  );
}
