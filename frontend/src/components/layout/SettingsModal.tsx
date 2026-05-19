import { useEffect, useState } from "react";
import { Settings, X, Check, AlertCircle, Loader2 } from "lucide-react";
import { useStore } from "@/lib/store";

/** Settings modal. For now: backend URL + Test connection.
 *
 * `apiBase` lives in localStorage; setting it routes every /api/* fetch
 * to that origin (bypassing vite proxy). Empty string => use vite proxy. */
export function SettingsModal() {
  const open = useStore((s) => s.settingsOpen);
  const close = () => useStore.getState().setSettingsOpen(false);
  const apiBase = useStore((s) => s.apiBase);
  const setApiBase = useStore((s) => s.setApiBase);

  const [draft, setDraft] = useState<string>(apiBase ?? "");
  const [testState, setTestState] = useState<
    { kind: "idle" } |
    { kind: "testing" } |
    { kind: "ok"; pkgRoot?: string } |
    { kind: "err"; message: string }
  >({ kind: "idle" });

  // Reset draft + clear test result whenever the modal re-opens or apiBase
  // changes from outside.
  useEffect(() => {
    if (open) {
      setDraft(apiBase ?? "");
      setTestState({ kind: "idle" });
    }
  }, [open, apiBase]);

  if (!open) return null;

  const normalized = draft.trim().replace(/\/$/, "");
  const isLooksLikeUrl =
    normalized === "" ||
    /^https?:\/\/[^/\s]+/i.test(normalized);

  const doTest = async () => {
    if (!isLooksLikeUrl) return;
    setTestState({ kind: "testing" });
    try {
      // Test the URL the user typed (NOT the saved one) so they get
      // feedback before committing.
      const target = normalized
        ? `${normalized}/api/health`
        : "/api/health"; // vite proxy fallback
      const r = await fetch(target, { method: "GET" });
      if (!r.ok) {
        setTestState({ kind: "err", message: `HTTP ${r.status}` });
        return;
      }
      const data = await r.json().catch(() => ({}));
      setTestState({
        kind: "ok",
        pkgRoot: typeof data.pkg_root === "string" ? data.pkg_root : undefined,
      });
    } catch (e) {
      const msg =
        e instanceof Error
          ? e.message
          : "network error (CORS / unreachable / wrong port)";
      setTestState({ kind: "err", message: msg });
    }
  };

  const doSave = () => {
    setApiBase(normalized || null);
    close();
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm"
        onClick={close}
        aria-hidden
      />
      {/* Panel */}
      <div
        className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2
                   z-50 w-[520px] max-w-[92vw] glass-card p-5 flex flex-col gap-4"
        role="dialog"
        aria-modal
        aria-label="Settings"
      >
        <div className="flex items-center gap-2">
          <Settings size={16} className="text-text-secondary" />
          <h2 className="text-base font-semibold">Settings</h2>
          <button
            onClick={close}
            className="ml-auto text-text-muted hover:text-text-primary"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex flex-col gap-2">
          <label className="text-xs font-medium text-text-secondary">
            Backend URL
          </label>
          <input
            type="text"
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setTestState({ kind: "idle" });
            }}
            placeholder="http://your-backend:port  (leave blank to use vite proxy)"
            className="px-3 h-9 bg-elevated border border-border rounded text-sm
                       text-text-primary placeholder:text-text-muted focus:outline-none
                       focus:border-accent font-mono"
            spellCheck={false}
            autoComplete="off"
          />
          <div className="text-[11px] text-text-muted">
            Include scheme + host + port. When set, all <code>/api/*</code>{" "}
            requests go straight to this URL (CORS must allow this origin
            on the backend). Leave blank to keep using the vite proxy.
          </div>
          {!isLooksLikeUrl && (
            <div className="text-[11px] text-warning">
              That doesn't look like a URL. Expected{" "}
              <code>http(s)://host:port</code>.
            </div>
          )}
        </div>

        {/* Test connection */}
        <div className="flex items-center gap-2">
          <button
            onClick={doTest}
            disabled={!isLooksLikeUrl || testState.kind === "testing"}
            className="h-8 px-3 text-xs rounded bg-elevated border border-border
                       hover:border-accent disabled:opacity-50 disabled:cursor-not-allowed
                       flex items-center gap-1.5"
          >
            {testState.kind === "testing" ? (
              <>
                <Loader2 size={12} className="animate-spin" />
                Testing…
              </>
            ) : (
              "Test connection"
            )}
          </button>
          {testState.kind === "ok" && (
            <div className="text-xs text-success flex items-center gap-1 truncate">
              <Check size={12} />
              <span>
                ok
                {testState.pkgRoot && (
                  <>
                    {" "}
                    · <code className="text-text-muted">{testState.pkgRoot}</code>
                  </>
                )}
              </span>
            </div>
          )}
          {testState.kind === "err" && (
            <div className="text-xs text-warning flex items-center gap-1 truncate">
              <AlertCircle size={12} />
              <span>{testState.message}</span>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 pt-1 border-t border-border/40">
          <button
            onClick={close}
            className="h-8 px-3 text-xs rounded border border-border
                       text-text-secondary hover:text-text-primary hover:border-text-secondary"
          >
            Cancel
          </button>
          <button
            onClick={doSave}
            disabled={!isLooksLikeUrl}
            className="h-8 px-3 text-xs rounded bg-accent text-canvas font-medium
                       hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Save
          </button>
        </div>
      </div>
    </>
  );
}
