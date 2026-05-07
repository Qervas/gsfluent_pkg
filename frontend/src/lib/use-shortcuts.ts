import { useEffect } from "react";

type ShortcutHandlers = {
  onOpenPalette?: () => void;
  onRun?: () => void;
  onToggleInspector?: () => void;
  onToggleSidebar?: () => void;
};

export function useShortcuts(handlers: ShortcutHandlers): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't intercept when typing in inputs / textareas / contenteditables.
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toUpperCase();
      const isEditable =
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        target?.isContentEditable === true;

      const meta = e.metaKey || e.ctrlKey;

      // Cmd/Ctrl+K — open palette (works even from inputs)
      if (meta && e.key.toLowerCase() === "k") {
        e.preventDefault();
        handlers.onOpenPalette?.();
        return;
      }

      // The rest are ignored when typing in a form field.
      if (isEditable) return;

      // Cmd/Ctrl+Enter — run sim
      if (meta && e.key === "Enter") {
        e.preventDefault();
        handlers.onRun?.();
        return;
      }

      // Cmd/Ctrl+B — toggle sidebar
      if (meta && e.key.toLowerCase() === "b") {
        e.preventDefault();
        handlers.onToggleSidebar?.();
        return;
      }

      // i — toggle inspector
      if (e.key.toLowerCase() === "i" && !meta && !e.altKey && !e.shiftKey) {
        e.preventDefault();
        handlers.onToggleInspector?.();
        return;
      }
    };

    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [handlers]);
}
