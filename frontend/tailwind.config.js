/** @type {import('tailwindcss').Config} */
// Design tokens (Stage redesign — see docs/superpowers/specs/2026-05-16-...).
// Color names are semantic; raw hex values live here only. Everywhere
// else, components reference `text-primary` / `bg-canvas` / etc.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Stage palette — deeper canvas so the floating cards lift cleanly.
        canvas:           "#0a0f1a",
        elevated:         "#141a26",
        border:           "#1f2937",
        "border-active":  "#2a3441",

        // Text contrast bumped against the canvas — `text-muted` is now
        // WCAG AA against `bg-elevated/85`.
        "text-primary":   "#e5edf5",
        "text-secondary": "#94a3b8",
        "text-muted":     "#94a3b8",

        // Accent stays cyan; pairs with the splat color palette.
        accent:           "#22d3ee",
        "accent-glow":    "rgba(34, 211, 238, 0.4)",
        success:          "#34d399",
        warning:          "#fbbf24",
        error:            "#f87171",

        // Aliased back to `elevated` for components that pre-date this
        // refactor — drop the alias once every call-site is migrated.
        pane:             "#141a26",
      },
      fontFamily: {
        sans: ["'Inter Variable'", "Inter", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono Variable'", "JetBrains Mono", "ui-monospace", "Menlo", "monospace"],
      },
      fontSize: {
        // Locked scale — replace ad-hoc text-[10px] / text-[11px] sprawl
        // with semantic sizes. Line-heights tuned for dense UI rows.
        "xxs":  ["10px", "14px"],
        "xs":   ["12px", "16px"],
        "sm":   ["13px", "18px"],
        "base": ["15px", "22px"],
        "lg":   ["18px", "26px"],
        "xl":   ["24px", "32px"],
      },
      transitionDuration: {
        fast:  "150ms",   // hover / focus
        panel: "200ms",   // panel show / hide / collapse
        swap:  "300ms",   // workspace switch
      },
      transitionTimingFunction: {
        // Material's "standard easing"
        motion: "cubic-bezier(0.2, 0, 0, 1)",
      },
      boxShadow: {
        // Glass-card elevation. One shadow token, applied via the
        // .glass-card utility class in index.css so we never re-derive it.
        glass: "0 8px 32px -8px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04)",
        "accent-glow": "0 0 16px rgba(34,211,238,0.4)",
        // Backwards-compat alias for components that haven't migrated yet.
        "accent-glow-soft": "0 0 12px rgba(34,211,238,0.3)",
      },
    },
  },
  plugins: [],
};
