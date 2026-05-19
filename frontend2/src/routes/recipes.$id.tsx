import { createFileRoute, useParams } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export const Route = createFileRoute("/recipes/$id")({
  component: RecipeEditorPage,
});

function RecipeEditorPage(): JSX.Element {
  const { id } = useParams({ from: "/recipes/$id" });
  const qc = useQueryClient();

  const recipe = useQuery({
    queryKey: ["recipe", id],
    queryFn: () => api.recipes.get(id),
  });

  const [text, setText] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (recipe.data) {
      setText(JSON.stringify(recipe.data.content, null, 2));
      setError(null);
    }
  }, [recipe.data]);

  const save = useMutation({
    mutationFn: (content: Record<string, unknown>) =>
      api.recipes.patch(id, { content }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recipe", id] });
      qc.invalidateQueries({ queryKey: ["recipes"] });
    },
  });

  const toggleStar = useMutation({
    mutationFn: () => api.recipes.patch(id, { starred: !recipe.data?.starred }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recipe", id] });
      qc.invalidateQueries({ queryKey: ["recipes"] });
    },
  });

  function onSave() {
    try {
      const parsed = JSON.parse(text);
      setError(null);
      save.mutate(parsed);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (recipe.isLoading) return <p className="text-slate-400">Loading…</p>;
  if (!recipe.data) return <p className="text-slate-400">Not found.</p>;

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold font-mono">{recipe.data.name}</h1>
          <p className="text-xs text-slate-500">v{recipe.data.version} · updated {recipe.data.updated_at.slice(0, 19)}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => toggleStar.mutate()}
            className="px-2 py-1 rounded text-xs hover:bg-elevated/60"
            title="Star this recipe"
          >
            {recipe.data.starred ? "★" : "☆"}
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={save.isPending}
            className="px-3 py-1.5 rounded bg-accent text-slate-950 text-xs font-semibold disabled:opacity-40"
          >
            {save.isPending ? "saving…" : "save (bumps version)"}
          </button>
        </div>
      </header>

      <section className="glass p-3">
        <textarea
          value={text}
          onChange={(e) => setText(e.currentTarget.value)}
          spellCheck={false}
          className="w-full h-[60vh] bg-slate-900/80 text-slate-100 font-mono text-sm
                     p-3 rounded border border-border resize-y outline-none focus:border-accent/60"
        />
        {error && <p className="text-red-400 text-xs mt-2">JSON: {error}</p>}
        {save.error && <p className="text-red-400 text-xs mt-2">{(save.error as Error).message}</p>}
      </section>
    </div>
  );
}
