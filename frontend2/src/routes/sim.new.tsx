import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/sim/new")({
  component: NewRunPage,
});

function NewRunPage(): JSX.Element {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">New run</h1>
      <div className="glass p-8 text-slate-400">
        Submit form lands in Phase 7 (model + recipe pickers, overrides).
      </div>
    </div>
  );
}
