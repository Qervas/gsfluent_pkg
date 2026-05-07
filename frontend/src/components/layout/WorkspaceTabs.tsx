import { useStore } from "@/lib/store";
import type { Workspace } from "@/lib/types";

type Tab = { id: Workspace; label: string };

const TABS: Tab[] = [
  { id: "sim",     label: "Sim" },
  { id: "compare", label: "Compare" },
  { id: "render",  label: "Render" },
  { id: "recipes", label: "Recipes" },
];

export function WorkspaceTabs() {
  const activeWorkspace = useStore((s) => s.activeWorkspace);
  const setActiveWorkspace = useStore((s) => s.setActiveWorkspace);

  return (
    <div className="h-8 border-b border-border px-3 flex items-center gap-4 text-xs shrink-0">
      {TABS.map((t) => {
        const isActive = activeWorkspace === t.id;
        return (
          <button
            key={t.id}
            type="button"
            onClick={() => setActiveWorkspace(t.id)}
            className={
              isActive
                ? "text-accent border-b-2 border-accent pb-0.5"
                : "text-text-secondary hover:text-text-primary cursor-pointer"
            }
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
