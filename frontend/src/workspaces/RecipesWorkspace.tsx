import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, Copy, Save, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Properties } from "@/components/properties/Properties";
import { useStore } from "@/lib/store";

/** Recipes workspace.
 *
 * Mirrors the Sim workspace's RHS Properties panel here so a user can
 * tune material / solver / forces / boundaries on a recipe without
 * leaving the Recipes tab. Selection updates `store.activeRecipe`,
 * which Properties (and every nested panel) already reads from — same
 * single source of truth across both workspaces.
 */
export function RecipesWorkspace() {
  const qc = useQueryClient();
  const { data: recipes = [] } = useQuery({
    queryKey: ["recipes"],
    queryFn: api.recipes.list,
  });

  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const activeRecipeData = useStore((s) => s.activeRecipeData);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);

  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const builtin = recipes.filter((r) => r.source === "builtin");
  const user = recipes.filter((r) => r.source === "user");
  const selected = activeRecipeName;
  const isUser = recipes.find((r) => r.name === selected)?.source === "user";

  const onSelect = async (name: string) => {
    setError(null);
    try {
      const r = await api.recipes.get(name);
      setActiveRecipe(r.name, r.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onDelete = async () => {
    if (!selected || !isUser) return;
    if (!confirm(`Delete user preset "${selected}"?`)) return;
    try {
      await api.recipes.delete(selected);
      qc.invalidateQueries({ queryKey: ["recipes"] });
      setActiveRecipe(null, null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onDuplicate = async () => {
    if (!activeRecipeData) return;
    const name = prompt("Duplicate as preset name:");
    if (!name?.trim()) return;
    try {
      await api.recipes.save(name.trim(), activeRecipeData, selected ?? undefined);
      qc.invalidateQueries({ queryKey: ["recipes"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onRename = async () => {
    if (!selected || !isUser || !activeRecipeData) return;
    const next = prompt(`Rename "${selected}" to:`, selected);
    if (!next?.trim() || next.trim() === selected) return;
    const newName = next.trim();
    try {
      const provenance = (activeRecipeData?._provenance as { based_on?: string } | undefined)?.based_on;
      await api.recipes.save(newName, activeRecipeData, provenance);
      await api.recipes.delete(selected);
      qc.invalidateQueries({ queryKey: ["recipes"] });
      setActiveRecipe(newName, activeRecipeData);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onSaveEdits = async () => {
    if (!selected || !isUser || !activeRecipeData) return;
    setSaving(true);
    setError(null);
    try {
      const provenance = (activeRecipeData?._provenance as { based_on?: string } | undefined)?.based_on;
      await api.recipes.save(selected, activeRecipeData, provenance);
      qc.invalidateQueries({ queryKey: ["recipes"] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const onImport = async () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json";
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      const text = await file.text();
      try {
        const data = JSON.parse(text);
        if (typeof data !== "object" || data === null || !("material" in data)) {
          setError("Imported JSON must contain a 'material' key.");
          return;
        }
        const name = prompt(
          "Save as preset name:",
          file.name.replace(/\.json$/, ""),
        );
        if (!name?.trim()) return;
        await api.recipes.save(name.trim(), data);
        qc.invalidateQueries({ queryKey: ["recipes"] });
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    input.click();
  };

  return (
    <div className="h-full flex">
      {/* Left: recipe list */}
      <div className="w-[280px] border-r border-border overflow-y-auto shrink-0">
        <div className="px-3 py-2 flex items-center justify-between">
          <span className="text-text-muted text-[10px] uppercase tracking-wider">
            Recipes
          </span>
          <button
            type="button"
            onClick={onImport}
            className="text-accent text-xs flex items-center gap-1 hover:bg-elevated px-1.5 py-0.5 rounded"
          >
            <Upload size={11} /> Import
          </button>
        </div>
        <div className="text-text-muted text-[10px] uppercase tracking-wider px-3 py-1 mt-2">
          Built-in
        </div>
        {builtin.map((r) => (
          <button
            key={r.name}
            type="button"
            onClick={() => onSelect(r.name)}
            className={
              "w-full text-left px-3 py-1 text-xs hover:bg-elevated truncate " +
              (selected === r.name ? "text-accent" : "text-text-primary")
            }
          >
            {r.name}
          </button>
        ))}
        <div className="text-text-muted text-[10px] uppercase tracking-wider px-3 py-1 mt-2">
          User saved (★)
        </div>
        {user.length === 0 && (
          <div className="px-3 py-1 text-xs text-text-muted">(none yet)</div>
        )}
        {user.map((r) => (
          <button
            key={r.name}
            type="button"
            onClick={() => onSelect(r.name)}
            className={
              "w-full text-left px-3 py-1 text-xs hover:bg-elevated truncate " +
              (selected === r.name ? "text-accent" : "text-text-primary")
            }
          >
            ★ {r.name}
          </button>
        ))}
      </div>

      {/* Right: action bar + structured params editor (Properties) */}
      <div className="flex-1 flex flex-col min-w-0">
        {!selected ? (
          <div className="flex-1 flex items-center justify-center text-text-muted text-sm">
            Select a recipe to inspect or edit.
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 px-4 py-2 border-b border-border shrink-0">
              <span className="font-mono text-sm text-text-primary truncate">
                {isUser ? "★ " : ""}
                {selected}
              </span>
              <span className="text-text-muted text-xs shrink-0">
                {isUser ? "user preset" : "built-in (read-only)"}
              </span>
              <div className="ml-auto flex gap-2 shrink-0">
                <Button variant="secondary" onClick={onDuplicate}>
                  <Copy size={11} /> Duplicate
                </Button>
                {isUser && (
                  <>
                    <Button variant="secondary" onClick={onRename}>
                      Rename
                    </Button>
                    <Button onClick={onSaveEdits} disabled={saving}>
                      <Save size={11} /> {saving ? "Saving…" : "Save edits"}
                    </Button>
                    <Button variant="destructive" onClick={onDelete}>
                      <Trash2 size={11} /> Delete
                    </Button>
                  </>
                )}
              </div>
            </div>
            {error && (
              <div className="px-4 py-1 text-error text-xs bg-error/10 border-b border-error/30 shrink-0">
                {error}
              </div>
            )}
            {/* Same Properties tree as the Sim workspace's right rail.
                Each sub-panel reads + writes store.activeRecipe[Name|Data],
                so edits flow through the same path the sim workspace
                uses and Save reads activeRecipeData from the store. */}
            <div className="flex-1 overflow-y-auto">
              <Properties />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
