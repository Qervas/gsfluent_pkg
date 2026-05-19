import { Link, createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const Route = createFileRoute("/recipes/")({
  component: RecipesListPage,
});

function RecipesListPage(): JSX.Element {
  const q = useQuery({ queryKey: ["recipes"], queryFn: () => api.recipes.list() });
  const recipes = q.data?.items ?? [];

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Recipes</h1>
          <p className="text-xs text-slate-500">Sim recipes — JSON editable, version history retained.</p>
        </div>
        <Link
          to="/recipes/new"
          className="px-3 py-1.5 rounded bg-accent text-slate-950 text-xs font-semibold"
        >
          + New recipe
        </Link>
      </header>

      {q.isLoading && <p className="text-slate-400">Loading…</p>}
      {recipes.length === 0 ? (
        <div className="glass p-8 text-center text-slate-400">
          No recipes yet. Click <strong>New recipe</strong> to start.
        </div>
      ) : (
        <ul className="divide-y divide-border/40">
          {recipes.map((r) => (
            <li key={r.id} className="py-3">
              <Link
                to="/recipes/$id"
                params={{ id: r.id }}
                className="flex items-center justify-between hover:bg-elevated/30 rounded px-2 -mx-2"
              >
                <div>
                  <h3 className="font-mono text-sm">{r.starred ? "★ " : ""}{r.name}</h3>
                  <p className="text-xs text-slate-500">
                    v{r.version} · updated {r.updated_at.slice(0, 19)}
                  </p>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
