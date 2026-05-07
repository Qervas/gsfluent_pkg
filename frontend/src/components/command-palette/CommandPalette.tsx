import { Command } from "cmdk";
import { useEffect, useState } from "react";
import { Play, FolderOpen } from "lucide-react";
import { useStore } from "@/lib/store";
import { api } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";

export type CommandPaletteRef = {
  open: () => void;
  close: () => void;
  toggle: () => void;
};

export function CommandPalette({
  onRun,
}: {
  onRun: () => void;
}) {
  const [open, setOpen] = useState(false);
  const activeModel = useStore((s) => s.activeModel);
  const activeRecipeName = useStore((s) => s.activeRecipeName);
  const setActiveRecipe = useStore((s) => s.setActiveRecipe);
  const qc = useQueryClient();

  // Listen for the ⌘K toggle event from useShortcuts.
  useEffect(() => {
    const handler = () => setOpen((o) => !o);
    document.addEventListener("gsfluent:open-palette", handler);
    return () => document.removeEventListener("gsfluent:open-palette", handler);
  }, []);

  const recipes = qc.getQueryData<{ name: string; source: string }[]>(["recipes"]) ?? [];

  const onPickRecipe = async (name: string) => {
    try {
      const r = await api.recipes.get(name);
      setActiveRecipe(r.name, r.data);
    } catch (e) {
      console.error("failed to load recipe", e);
    }
    setOpen(false);
  };

  const triggerRun = () => {
    setOpen(false);
    onRun();
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-start justify-center pt-32"
      onClick={() => setOpen(false)}
    >
      <Command
        label="Command palette"
        className="w-[520px] bg-elevated border border-border rounded-lg overflow-hidden shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <Command.Input
          placeholder="Type a command or search…"
          className="w-full bg-canvas px-3 py-2.5 text-sm border-b border-border outline-none text-text-primary placeholder:text-text-muted"
          autoFocus
        />
        <Command.List className="max-h-[400px] overflow-auto p-1">
          <Command.Empty className="p-3 text-xs text-text-muted">
            No matching commands.
          </Command.Empty>

          <Command.Group heading="Actions" className="px-2 pt-2 pb-1 text-[10px] uppercase tracking-wider text-text-muted">
            <CmdItem
              onSelect={triggerRun}
              icon={<Play size={14} />}
              label="Run simulation"
              keys="⌘ ↵"
              disabled={!activeModel || !activeRecipeName}
              hint={(!activeModel || !activeRecipeName) ? "Pick a model + recipe first" : undefined}
            />
          </Command.Group>

          {recipes.length > 0 && (
            <Command.Group heading="Pick recipe" className="px-2 pt-2 pb-1 text-[10px] uppercase tracking-wider text-text-muted">
              {recipes.map((r) => (
                <CmdItem
                  key={r.name}
                  onSelect={() => onPickRecipe(r.name)}
                  icon={<FolderOpen size={14} />}
                  label={(r.source === "user" ? "★ " : "") + r.name}
                />
              ))}
            </Command.Group>
          )}
        </Command.List>
      </Command>
    </div>
  );
}

function CmdItem({
  onSelect,
  icon,
  label,
  keys,
  disabled,
  hint,
}: {
  onSelect: () => void;
  icon?: React.ReactNode;
  label: string;
  keys?: string;
  disabled?: boolean;
  hint?: string;
}) {
  return (
    <Command.Item
      onSelect={() => { if (!disabled) onSelect(); }}
      disabled={disabled}
      className={
        "flex items-center gap-2 px-2 py-1.5 text-xs rounded cursor-pointer " +
        "data-[selected=true]:bg-canvas data-[selected=true]:text-accent " +
        (disabled ? "opacity-40 cursor-not-allowed" : "")
      }
      title={hint}
    >
      {icon}
      <span className="flex-1">{label}</span>
      {keys && <span className="text-text-muted font-mono text-[10px]">{keys}</span>}
    </Command.Item>
  );
}
