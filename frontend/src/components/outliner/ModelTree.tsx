import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";

// Same fallback as Viewport / ViserSplatScene — viser_headless's control
// API is on :8092 by default, overridable via VITE_VISER_CONTROL_URL.
function viserControlUrl(): string {
  const env = (import.meta.env.VITE_VISER_CONTROL_URL as string | undefined)?.replace(/\/$/, "");
  if (env) return env;
  const host = typeof window !== "undefined" ? window.location.hostname : "localhost";
  return `http://${host}:8092`;
}

export function ModelTree() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["models"],
    queryFn: api.models.list,
  });
  const activeModel = useStore((s) => s.activeModel);
  const setActiveModel = useStore((s) => s.setActiveModel);
  const qc = useQueryClient();
  const [serverPath, setServerPath] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [convertYUp, setConvertYUp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Server-side path: registers an existing model directory on the
   *  GPU box (no upload, no copy). Path must point at a 3DGS dir
   *  containing `point_cloud/iteration_<N>/point_cloud.ply`. */
  const onAddServerPath = async () => {
    const p = serverPath.trim();
    if (!p) return;
    setBusy(true);
    setError(null);
    try {
      const m = await api.models.register(p, convertYUp);
      setActiveModel(m);
      qc.invalidateQueries({ queryKey: ["models"] });
      setServerPath("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  /** Laptop-local path: fetches the .ply via viser_headless's
   *  /read-local endpoint, then uploads through /api/models/upload
   *  exactly like a drag-drop would. Browsers can't read arbitrary
   *  filesystem paths directly, so the laptop-side viser process
   *  acts as the FS reader. */
  const onAddLocalPath = async () => {
    const p = localPath.trim();
    if (!p) return;
    setBusy(true);
    setError(null);
    try {
      const url = `${viserControlUrl()}/read-local?path=${encodeURIComponent(p)}`;
      const r = await fetch(url);
      if (!r.ok) {
        const detail = await r.text().catch(() => "");
        throw new Error(`viser /read-local: HTTP ${r.status} ${detail}`);
      }
      const blob = await r.blob();
      const name = p.split("/").pop() || "model.ply";
      const file = new File([blob], name, { type: "application/octet-stream" });
      const m = await api.models.upload(file, undefined, convertYUp);
      setActiveModel(m);
      qc.invalidateQueries({ queryKey: ["models"] });
      setLocalPath("");
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

      {/* Server-side path (existing flow). Goes through /api/models/register. */}
      <div className="px-2 pt-1 pb-0.5 flex gap-1">
        <input
          type="text"
          placeholder="server: /path/to/model_dir"
          value={serverPath}
          disabled={busy}
          onChange={(e) => setServerPath(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") onAddServerPath(); }}
          className="font-mono flex-1 bg-canvas border border-border rounded px-1.5 py-0.5 text-[11px] text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
          title="Path to a 3DGS model directory on the SERVER (containing point_cloud/iteration_<N>/point_cloud.ply). No copy — registered in place."
        />
        <button
          onClick={onAddServerPath}
          disabled={busy || !serverPath.trim()}
          className="bg-elevated hover:bg-border border border-border text-accent text-[11px] px-2 rounded disabled:opacity-30"
          title="Register the server-side model directory"
        >
          {busy ? "…" : "+"}
        </button>
      </div>

      {/* Laptop-local .ply path. Reads via viser_headless, uploads via /api/models/upload. */}
      <div className="px-2 pt-0.5 pb-1 flex gap-1">
        <input
          type="text"
          placeholder="local: ~/path/to/model.ply"
          value={localPath}
          disabled={busy}
          onChange={(e) => setLocalPath(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") onAddLocalPath(); }}
          className="font-mono flex-1 bg-canvas border border-border rounded px-1.5 py-0.5 text-[11px] text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-1 focus:ring-accent"
          title="Path to a .ply file on YOUR LAPTOP. The workbench reads it via viser_headless and uploads to the server."
        />
        <button
          onClick={onAddLocalPath}
          disabled={busy || !localPath.trim()}
          className="bg-elevated hover:bg-border border border-border text-accent text-[11px] px-2 rounded disabled:opacity-30"
          title="Read the .ply from your laptop's disk and upload to the server"
        >
          {busy ? "…" : "↑"}
        </button>
      </div>

      <div className="px-2 pb-1">
        <label
          className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider text-text-muted hover:text-text-primary cursor-pointer select-none"
          title="Source is Y-up (PhysGaussian/Inria); convert to Z-up at import. Applies to both server-register and local-upload."
        >
          <input
            type="checkbox"
            checked={convertYUp}
            disabled={busy}
            onChange={(e) => setConvertYUp(e.target.checked)}
            className="accent-accent"
          />
          Y-up
        </label>
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
