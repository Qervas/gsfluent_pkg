import { useEffect } from "react";
import { useStore, SPEED_X_VALUES, type SpeedX } from "@/lib/store";

/**
 * Window-level keyboard shortcuts for transport controls. Mirrors the
 * affordances exposed by the PlaybackBar so a power-user keeps their
 * hands on the keyboard.
 *
 *   Space        toggle playing
 *   ←  /  →      step ±1 frame
 *   J  /  K      step ±10 frames (vim-style big step)
 *   ,  /  .      cycle speed down / up through SPEED_X_VALUES
 *   L            toggle loop
 *
 * Shortcuts are suppressed while the focused element is an editable
 * input — typing into the path field in SequenceTree shouldn't trigger
 * scrubbing. Match the rule used by `lib/use-shortcuts.ts` for parity.
 */
export function PlaybackKeybinds() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't fire while the user is typing.
      const tag = (
        document.activeElement as HTMLElement | null
      )?.tagName?.toUpperCase();
      const isEditable =
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        (document.activeElement as HTMLElement | null)?.isContentEditable === true;
      if (isEditable) return;

      // Skip when modifier keys are held — those are owned by other
      // shortcut systems (Cmd+K palette, Ctrl+B sidebar, etc.).
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      const st = useStore.getState();

      switch (e.key) {
        case " ": // Space — play/pause
        case "Spacebar":
          e.preventDefault();
          st.setPlaying(!st.playing);
          return;
        case "ArrowLeft":
          e.preventDefault();
          st.stepFrame(-1);
          return;
        case "ArrowRight":
          e.preventDefault();
          st.stepFrame(1);
          return;
      }

      // Letter keys are case-insensitive but skip if shift was used as
      // a modifier (e.g. user is hitting Shift+, for `<`).
      const k = e.key.toLowerCase();
      if (e.shiftKey && k !== ",") return;

      switch (k) {
        case "j":
          e.preventDefault();
          st.stepFrame(-10);
          return;
        case "k":
          e.preventDefault();
          st.stepFrame(10);
          return;
        case ",":
          e.preventDefault();
          st.setSpeedX(speedDown(st.speedX));
          return;
        case ".":
          e.preventDefault();
          st.setSpeedX(speedUp(st.speedX));
          return;
        case "l":
          e.preventDefault();
          st.setLoop(!st.loop);
          return;
      }
    };

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return null;
}

function speedDown(s: SpeedX): SpeedX {
  const i = SPEED_X_VALUES.indexOf(s);
  return SPEED_X_VALUES[Math.max(0, i - 1)];
}

function speedUp(s: SpeedX): SpeedX {
  const i = SPEED_X_VALUES.indexOf(s);
  return SPEED_X_VALUES[Math.min(SPEED_X_VALUES.length - 1, i + 1)];
}
