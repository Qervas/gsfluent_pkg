import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export function RecipeTree() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["recipes"],
    queryFn: api.recipes.list,
  });
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);

  const onPick = async (name: string) => {
    try {
      const r = await api.recipes.get(name);
      setActiveRecipe(r.name, r.data);
    } catch (e) {
      console.error("failed to load recipe", name, e);
    }
  };

  return (
    <div>
      <div className="text-text-muted text-[10px] uppercase tracking-wider px-2 py-1 mt-2">
        Recipes
      </div>
      {isLoading && (
        <div className="text-text-muted text-xs px-3 py-1">Loading…</div>
      )}
      {data.map((r) => (
        <button
          key={r.name}
          onClick={() => onPick(r.name)}
          className={
            "w-full text-left px-3 py-1 text-xs hover:bg-elevated truncate " +
            (activeRecipeName === r.name ? "text-accent" : "text-text-primary")
          }
        >
          {r.source === "user" ? "★ " : ""}
          {r.name}
        </button>
      ))}
    </div>
  );
}
