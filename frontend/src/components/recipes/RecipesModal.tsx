import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { X, Trash2, Copy, Upload, Download, Lock } from "lucide-react";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { useOverrides } from "@/lib/use-overrides";
import { JsonEditor } from "@/components/properties/widgets/JsonEditor";
import type { RecipeListItem } from "@/lib/types";

/** RecipesModal — center-screen library manager. Replaces the
 *  separate Recipes workspace. Doesn't remount the viewport: it just
 *  layers over the AppShell with a translucent backdrop.
 *
 *  Triggered by:
 *   - clicking the Recipes pill in the TopBar
 *   - Cmd/Ctrl-R (registered in App.tsx)
 *
 *  Esc / click-outside / ✕ dismisses. */
export function RecipesModal() {
  const open       = useStore((s) => s.recipesModalOpen);
  const setOpen    = useStore((s) => s.setRecipesModalOpen);
  const loadActive = useStore((s) => s.loadActiveRecipe);
  const { overrideCount } = useOverrides();
  const qc = useQueryClient();

  const { data: recipes = [] } = useQuery({
    queryKey: ["recipes"],
    queryFn: api.recipes.list,
  });

  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  if (!open) return null;

  const builtin = (recipes as RecipeListItem[]).filter((r) => r.source === "builtin");
  const user    = (recipes as RecipeListItem[]).filter((r) => r.source === "user");
  const selectedItem = recipes.find((r) => r.name === selected);
  const isUser = selectedItem?.source === "user";

  const onUseInSim = async () => {
    if (!selected) return;
    if (overrideCount > 0 && !confirm(`Discard ${overrideCount} overrides?`)) return;
    try {
      const r = await api.recipes.get(selected);
      loadActive(r.name, r.data);
      useStore.getState().showToast(`Loaded ${r.name} into Sim`, "success");
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const onDuplicate = async () => {
    if (!selected) return;
    const newName = prompt("Duplicate as:", `${selected}_copy`);
    if (!newName?.trim()) return;
    try {
      const r = await api.recipes.get(selected);
      await api.recipes.save(newName.trim(), r.data, selected);
      qc.invalidateQueries({ queryKey: ["recipes"] });
      setSelected(newName.trim());
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
      setSelected(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
        const name = prompt("Save as preset name:", file.name.replace(/\.json$/, ""));
        if (!name?.trim()) return;
        await api.recipes.save(name.trim(), data);
        qc.invalidateQueries({ queryKey: ["recipes"] });
        setSelected(name.trim());
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    };
    input.click();
  };

  const onExport = async () => {
    if (!selected) return;
    try {
      const r = await api.recipes.get(selected);
      const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${selected}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={() => setOpen(false)}
      role="dialog"
      aria-label="Recipes library"
      aria-modal="true"
    >
      <div
        className="glass-card w-[720px] h-[520px] flex overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="w-[200px] border-r border-border flex flex-col">
          <div className="px-3 py-2 flex items-center justify-between border-b border-border">
            <span className="text-text-muted text-[10px] uppercase tracking-wider">Library</span>
            <button
              onClick={onImport}
              className="text-accent text-[10px] flex items-center gap-1 hover:bg-elevated px-1 rounded"
              title="Import .json"
            >
              <Upload size={11} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto py-1">
            <div className="px-3 py-1 text-text-muted text-[9px] uppercase tracking-wider">Built-in</div>
            {builtin.map((r) => (
              <button
                key={r.name}
                onClick={() => setSelected(r.name)}
                className={
                  "w-full text-left px-3 py-1 text-xs font-mono truncate hover:bg-elevated " +
                  (selected === r.name ? "text-accent bg-accent/10" : "text-text-primary")
                }
              >
                {r.name}
              </button>
            ))}
            <div className="px-3 py-1 mt-2 text-text-muted text-[9px] uppercase tracking-wider">User ★</div>
            {user.length === 0 && (
              <div className="px-3 py-1 text-[10px] text-text-muted">(none yet)</div>
            )}
            {user.map((r) => (
              <button
                key={r.name}
                onClick={() => setSelected(r.name)}
                className={
                  "w-full text-left px-3 py-1 text-xs font-mono truncate hover:bg-elevated " +
                  (selected === r.name ? "text-accent bg-accent/10" : "text-text-primary")
                }
              >
                ★ {r.name}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 flex flex-col min-w-0">
          <div className="px-4 py-2 border-b border-border flex items-center gap-2">
            <span className="font-mono text-sm truncate flex-1">
              {selected ?? "Pick a recipe"}
            </span>
            {selected && (
              <>
                <span className="text-[10px] text-text-muted">
                  {isUser ? "user" : "built-in (read-only)"}
                </span>
                <button onClick={onDuplicate} className="text-[10px] text-text-secondary hover:text-text-primary flex items-center gap-1">
                  <Copy size={11} /> Duplicate
                </button>
                {isUser && (
                  <button onClick={onDelete} className="text-[10px] text-error hover:text-text-primary flex items-center gap-1">
                    <Trash2 size={11} />
                  </button>
                )}
                <button onClick={onExport} className="text-[10px] text-text-secondary hover:text-text-primary" title="Export .json">
                  <Download size={11} />
                </button>
                <button onClick={onUseInSim} className="text-[10px] bg-accent text-canvas px-2 py-0.5 rounded font-medium">
                  Use in Sim
                </button>
              </>
            )}
            <button onClick={() => setOpen(false)} className="text-text-muted hover:text-text-primary" aria-label="Close">
              <X size={14} />
            </button>
          </div>
          {error && (
            <div className="px-4 py-1 text-error text-[10px] bg-error/10 border-b border-error/30">
              {error}
            </div>
          )}
          <div className="flex-1 min-h-0 overflow-y-auto">
            {selected ? (
              <RecipeDetail name={selected} />
            ) : (
              <div className="p-6 text-text-muted text-xs">
                Select a recipe on the left to inspect or edit.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Detail view: edits buffer in `draft` and only persist on explicit
 *  Save. Avoids per-keystroke writes (which the previous version did,
 *  causing surprise commits + race conditions with optimistic refetch). */
function RecipeDetail({ name }: { name: string }) {
  const qc = useQueryClient();
  const { data: r, isLoading } = useQuery({
    queryKey: ["recipes", name],
    queryFn: () => api.recipes.get(name),
  });
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  // Reset draft whenever the loaded recipe changes (different name or
  // server-side update).
  useEffect(() => {
    setDraft(null);
    setSaveErr(null);
  }, [name, r?.data]);

  if (isLoading || !r) return <div className="p-4 text-text-muted text-xs">Loading…</div>;
  const isUser = r.source === "user";
  const current = draft ?? r.data;
  const dirty = draft !== null && JSON.stringify(draft) !== JSON.stringify(r.data);

  const onSave = async () => {
    if (!isUser || !draft) return;
    setSaving(true);
    setSaveErr(null);
    try {
      await api.recipes.save(name, draft);
      await qc.invalidateQueries({ queryKey: ["recipes", name] });
      await qc.invalidateQueries({ queryKey: ["recipes"] });
      setDraft(null);
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const onDiscard = () => {
    setDraft(null);
    setSaveErr(null);
  };

  return (
    <div className="px-3 py-3 flex flex-col gap-2 h-full">
      {!isUser && (
        <div className="px-2 py-1 bg-warning/10 text-warning text-[10px] rounded flex items-center gap-2">
          <Lock size={10} />
          <span>Built-in recipe — read-only. Click <strong>Duplicate</strong> to edit.</span>
        </div>
      )}
      {isUser && (
        <div className="flex items-center gap-2 text-[10px]">
          {dirty ? (
            <>
              <span className="text-warning">● modified</span>
              <div className="ml-auto flex gap-2">
                <button
                  onClick={onDiscard}
                  disabled={saving}
                  className="text-text-muted hover:text-text-primary disabled:opacity-50"
                >
                  Discard
                </button>
                <button
                  onClick={onSave}
                  disabled={saving}
                  className="bg-accent text-canvas px-2 py-0.5 rounded font-medium disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Save"}
                </button>
              </div>
            </>
          ) : (
            <span className="text-text-muted">saved</span>
          )}
        </div>
      )}
      {saveErr && (
        <div className="px-2 py-1 bg-error/10 text-error text-[10px] rounded">
          {saveErr}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-auto">
        <JsonEditor
          value={current}
          baseline={null}
          readOnly={!isUser}
          onChange={(next) => setDraft(next)}
        />
      </div>
    </div>
  );
}
