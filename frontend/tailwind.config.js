/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        canvas:    "#0d1117",
        pane:      "#0d1117",
        elevated:  "#161b22",
        border:    "#21262d",
        "text-primary":   "#c9d1d9",
        "text-secondary": "#8b949e",
        "text-muted":     "#6e7681",
        accent:    "#22d3ee",
        success:   "#34d399",
        warning:   "#fbbf24",
        error:     "#f87171",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "Menlo", "monospace"],
      },
      boxShadow: {
        "accent-glow": "0 0 12px rgba(34,211,238,0.3)",
      },
    },
  },
  plugins: [],
};
