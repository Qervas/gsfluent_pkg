import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export function HistoryTree({ onPick }: { onPick: (run_name: string) => void }) {
  const { data = [], isLoading } = useQuery({
    queryKey: ["history"],
    queryFn: api.runs.history,
    refetchInterval: 5_000,
  });

  return (
    <div>
      <div className="text-text-muted text-[10px] uppercase tracking-wider px-2 py-1 mt-2">
        History
      </div>
      {isLoading && (
        <div className="text-text-muted text-xs px-3 py-1">Loading…</div>
      )}
      {!isLoading && data.length === 0 && (
        <div className="text-text-muted text-xs px-3 py-1">(no runs yet)</div>
      )}
      {data.map((h) => (
        <button
          key={h.run_name}
          onClick={() => onPick(h.run_name)}
          className="w-full text-left px-3 py-1 text-xs hover:bg-elevated text-text-primary truncate flex items-center justify-between gap-2"
          title={`${h.run_name} · status=${h.status} · particles=${h.particles ?? "?"}`}
        >
          <span className="truncate">{h.run_name}</span>
          <span
            className={
              "shrink-0 text-[10px] " +
              (h.status === "done"
                ? "text-success"
                : h.status === "error"
                ? "text-error"
                : h.status === "cancelled"
                ? "text-text-muted"
                : "text-accent")
            }
          >
            {h.status}
          </span>
        </button>
      ))}
    </div>
  );
}
