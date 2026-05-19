import { Link } from "@tanstack/react-router";

const NAV = [
  { to: "/runs", label: "Runs" },
  { to: "/models", label: "Models" },
  { to: "/recipes", label: "Recipes" },
  { to: "/sim/new", label: "New run" },
  { to: "/system", label: "System" },
] as const;

export function TopBar(): JSX.Element {
  return (
    <header className="fixed top-3 left-3 right-3 z-30 h-12 glass-topbar
                       flex items-center gap-3 px-4 text-sm">
      <Link to="/" className="flex items-center gap-2 shrink-0">
        <span className="w-2.5 h-2.5 rounded-full"
              style={{ background: "linear-gradient(135deg, #22d3ee, #a855f7)" }} />
        <span className="font-semibold tracking-tight">gsfluent</span>
        <span className="text-xs text-slate-500 ml-1">v2</span>
      </Link>

      <nav className="flex items-center gap-1 ml-4">
        {NAV.map((n) => (
          <Link
            key={n.to}
            to={n.to}
            className="px-2 py-1 rounded-md text-xs font-medium text-slate-400
                       hover:text-slate-100 hover:bg-elevated/60
                       data-[status=active]:text-slate-100
                       data-[status=active]:bg-elevated/80"
            activeOptions={{ exact: false }}
          >
            {n.label}
          </Link>
        ))}
      </nav>

      <div className="flex-1" />
    </header>
  );
}
