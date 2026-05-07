type Tab = { id: string; label: string; active?: boolean; soon?: boolean };

const TABS: Tab[] = [
  { id: "sim",     label: "Sim",     active: true },
  { id: "compare", label: "Compare", soon:   true },
  { id: "render",  label: "Render",  soon:   true },
  { id: "recipes", label: "Recipes", soon:   true },
];

export function WorkspaceTabs() {
  return (
    <div className="h-8 border-b border-border px-3 flex items-center gap-4 text-xs shrink-0">
      {TABS.map((t) => (
        <span
          key={t.id}
          className={
            t.active
              ? "text-accent border-b-2 border-accent pb-0.5"
              : t.soon
              ? "text-text-muted cursor-not-allowed"
              : "text-text-secondary hover:text-text-primary cursor-pointer"
          }
        >
          {t.label}
          {t.soon ? " (soon)" : ""}
        </span>
      ))}
    </div>
  );
}
