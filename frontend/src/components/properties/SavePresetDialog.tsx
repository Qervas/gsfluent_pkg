import * as Dialog from "@radix-ui/react-dialog";
import { Save } from "lucide-react";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import { Button } from "@/components/ui/button";

export function SavePresetDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const activeRecipeData = useStore((s) => s.activeRecipeData);
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  const qc = useQueryClient();

  const onSave = async () => {
    if (!activeRecipeData) return;
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const saved = await api.recipes.save(
        name.trim(),
        activeRecipeData,
        activeRecipeName ?? undefined,
      );
      // Refresh the recipe list in the Outliner; switch the active recipe to
      // the freshly saved one so the user sees its provenance footer update.
      qc.invalidateQueries({ queryKey: ["recipes"] });
      setActiveRecipe(saved.name, saved.data);
      setName("");
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button variant="outline" className="w-full">
          <Save size={12} />
          Save as preset
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-full max-w-sm -translate-x-1/2 -translate-y-1/2 rounded border border-border bg-elevated p-4 shadow-xl space-y-3">
          <Dialog.Title className="text-sm font-semibold text-text-primary">
            Save current recipe as a preset
          </Dialog.Title>
          <Dialog.Description className="text-xs text-text-secondary">
            Saves to <code className="text-accent">work/_user_recipes/</code> and adds a ★ entry
            in the Outliner.
          </Dialog.Description>
          <input
            type="text"
            placeholder="my_preset"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={saving}
            autoFocus
            className="font-mono w-full bg-canvas border border-border rounded px-2 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
            onKeyDown={(e) => {
              if (e.key === "Enter") onSave();
            }}
          />
          {error && (
            <div className="text-error text-xs bg-error/15 border border-error/40 px-2 py-1 rounded">
              {error}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <Button
              variant="ghost"
              onClick={() => setOpen(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button onClick={onSave} disabled={saving || !name.trim()}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
