import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export function ModelTree() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["models"],
    queryFn: api.models.list,
  });
  const activeModel = useStore((s) => s.activeModel);
  const setActiveModel = useStore((s) => s.setActiveModel);

  return (
    <div>
      <div className="text-text-muted text-[10px] uppercase tracking-wider px-2 py-1">
        Models
      </div>
      {isLoading && (
        <div className="text-text-muted text-xs px-3 py-1">Loading…</div>
      )}
      {!isLoading && data.length === 0 && (
        <div className="text-text-muted text-xs px-3 py-1">
          (drag a .ply onto the viewport)
        </div>
      )}
      {data.map((m) => (
        <button
          key={m.name}
          onClick={() => setActiveModel(m)}
          className={
            "w-full text-left px-3 py-1 text-xs hover:bg-elevated truncate " +
            (activeModel?.name === m.name ? "text-accent" : "text-text-primary")
          }
          title={m.path}
        >
          {m.name}
        </button>
      ))}
    </div>
  );
}
