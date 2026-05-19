import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Mirrors the GlassCard tone from the v1 SPA so users don't get
        // jarred during cutover.
        accent: {
          DEFAULT: "rgb(34 211 238)", // cyan-400
          glow: "rgb(168 85 247)",    // purple-500
        },
        elevated: "rgb(30 41 59 / 0.7)",
        border: "rgb(71 85 105 / 0.4)",
      },
      fontFamily: {
        sans: ["system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out",
      },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
      },
    },
  },
  plugins: [],
} satisfies Config;
