import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const Route = createFileRoute("/recipes")({
  component: RecipesPage,
});

function RecipesPage(): JSX.Element {
  const q = useQuery({ queryKey: ["recipes"], queryFn: () => api.recipes.list() });
  const recipes = q.data?.items ?? [];

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Recipes</h1>
        <p className="text-xs text-slate-500">Sim recipes. Editor lands in Phase 7.</p>
      </header>

      {q.isLoading && <p className="text-slate-400">Loading…</p>}
      {recipes.length === 0 ? (
        <div className="glass p-8 text-center text-slate-400">No recipes yet.</div>
      ) : (
        <ul className="divide-y divide-border/40">
          {recipes.map((r) => (
            <li key={r.id} className="py-3 flex items-center justify-between">
              <div>
                <h3 className="font-mono text-sm">{r.starred ? "★ " : ""}{r.name}</h3>
                <p className="text-xs text-slate-500">v{r.version} · updated {r.updated_at.slice(0, 19)}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
