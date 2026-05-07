import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

export function ModelTree() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["models"],
    queryFn: api.models.list,
  });
  const activeModel = useStore((s) => s.activeModel);
  const setActiveModel = useStore((s) => s.setActiveModel);
  const qc = useQueryClient();
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onAddPath = async () => {
    const p = path.trim();
    if (!p) return;
    setBusy(true);
    setError(null);
    try {
      const m = await api.models.register(p);
      setActiveModel(m);
      qc.invalidateQueries({ queryKey: ["models"] });
      setPath("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="text-text-muted text-[10px] uppercase tracking-wider px-2 py-1">
        Models
      </div>
      <div className="px-2 pb-1 flex gap-1">
        <input
          type="text"
          placeholder="/path/to/model_dir"
          value={path}
          disabled={busy}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onAddPath();
          }}
          className="font-mono flex-1 bg-canvas border border-border rounded px-1.5 py-0.5 text-[11px] text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
          title="Paste a 3DGS model directory containing point_cloud/iteration_<N>/point_cloud.ply"
        />
        <button
          onClick={onAddPath}
          disabled={busy || !path.trim()}
          className="bg-elevated hover:bg-border border border-border text-accent text-[11px] px-2 rounded disabled:opacity-30"
          title="Register the local path (no copy)"
        >
          {busy ? "…" : "+"}
        </button>
      </div>
      {error && (
        <div className="px-3 pb-1 text-error text-[10px]">{error}</div>
      )}
      {isLoading && (
        <div className="text-text-muted text-xs px-3 py-1">Loading…</div>
      )}
      {!isLoading && data.length === 0 && (
        <div className="text-text-muted text-xs px-3 py-1">
          (drag a .ply onto the viewport, or paste a path above)
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
