import { useEffect, useState } from "react";

export type ViewerStats = {
  mode: "server" | "local";
  fps?: number;
  bitrateKbps?: number;
  rttMs?: number;
  frameBytes?: number;
  currentFrame?: number;
};

export function StatsHud({ stats }: { stats: ViewerStats }): JSX.Element | null {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "s" && !e.metaKey && !e.ctrlKey) {
        const t = e.target as HTMLElement | null;
        if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
          return;
        }
        setVisible((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (!visible) return null;

  return (
    <div className="absolute top-2 right-2 bg-slate-900/90 text-xs font-mono p-2 rounded border border-border space-y-0.5 pointer-events-none">
      <Row k="mode" v={stats.mode} />
      {stats.fps !== undefined && <Row k="fps" v={stats.fps.toFixed(1)} />}
      {stats.bitrateKbps !== undefined && <Row k="kbps" v={stats.bitrateKbps.toFixed(0)} />}
      {stats.rttMs !== undefined && <Row k="rtt" v={`${stats.rttMs.toFixed(0)}ms`} />}
      {stats.frameBytes !== undefined && <Row k="bytes" v={`${(stats.frameBytes / 1e6).toFixed(2)} MB`} />}
      {stats.currentFrame !== undefined && <Row k="frame" v={String(stats.currentFrame)} />}
      <div className="text-slate-500 text-[10px] mt-1">press S to toggle</div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }): JSX.Element {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-slate-500">{k}</span>
      <span className="text-slate-200">{v}</span>
    </div>
  );
}
