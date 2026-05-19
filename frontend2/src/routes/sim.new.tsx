import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";

export const Route = createFileRoute("/sim/new")({
  component: NewRunPage,
});

function NewRunPage(): JSX.Element {
  const navigate = useNavigate();
  const models = useQuery({ queryKey: ["models"], queryFn: () => api.models.list() });
  const recipes = useQuery({ queryKey: ["recipes"], queryFn: () => api.recipes.list() });

  const [name, setName] = useState<string>("");
  const [modelId, setModelId] = useState<string>("");
  const [recipeId, setRecipeId] = useState<string>("");

  const submit = useMutation({
    mutationFn: () =>
      api.runs.submit({
        name: name || `run-${Date.now().toString(36)}`,
        model_id: modelId,
        recipe_id: recipeId || undefined,
      }),
    onSuccess: (run) => navigate({ to: "/runs/$id", params: { id: run.id } }),
  });

  const canSubmit = modelId && (recipeId || false);

  return (
    <div className="space-y-4 max-w-2xl">
      <header>
        <h1 className="text-xl font-semibold">New run</h1>
        <p className="text-xs text-slate-500">
          Pick a model + recipe. The job is queued; you'll land on the run page.
        </p>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (canSubmit) submit.mutate();
        }}
        className="glass p-4 space-y-4"
      >
        <Field label="Name (optional)">
          <input
            value={name}
            onChange={(e) => setName(e.currentTarget.value)}
            placeholder="run-2026-05-19-…"
            className="w-full bg-elevated/60 border border-border rounded px-2 py-1.5 text-sm"
          />
        </Field>

        <Field label="Model" required>
          <select
            value={modelId}
            onChange={(e) => setModelId(e.currentTarget.value)}
            className="w-full bg-elevated/60 border border-border rounded px-2 py-1.5 text-sm"
            required
          >
            <option value="">— select —</option>
            {models.data?.items.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name} ({m.num_gaussians ? `${(m.num_gaussians / 1e6).toFixed(1)}M` : "?"})
              </option>
            ))}
          </select>
        </Field>

        <Field label="Recipe" required>
          <select
            value={recipeId}
            onChange={(e) => setRecipeId(e.currentTarget.value)}
            className="w-full bg-elevated/60 border border-border rounded px-2 py-1.5 text-sm"
            required
          >
            <option value="">— select —</option>
            {recipes.data?.items.map((r) => (
              <option key={r.id} value={r.id}>
                {r.starred ? "★ " : ""}{r.name} (v{r.version})
              </option>
            ))}
          </select>
        </Field>

        {submit.error && (
          <p className="text-red-400 text-xs">{(submit.error as Error).message}</p>
        )}

        <div className="flex justify-end">
          <button
            type="submit"
            disabled={!canSubmit || submit.isPending}
            className="px-4 py-2 rounded bg-accent text-slate-950 font-semibold text-sm disabled:opacity-40"
          >
            {submit.isPending ? "submitting…" : "Submit"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({
  label, required, children,
}: { label: string; required?: boolean; children: React.ReactNode }): JSX.Element {
  return (
    <label className="block">
      <span className="text-xs text-slate-400 uppercase tracking-wider">
        {label}
        {required && <span className="text-red-400 ml-1">*</span>}
      </span>
      <div className="mt-1">{children}</div>
    </label>
  );
}
