import { Outlet, createFileRoute } from "@tanstack/react-router";

// Layout route for /runs/*. The list page lives in runs.index.tsx;
// the detail page lives in runs.$id.tsx. This file only renders the
// Outlet so child routes paint.
export const Route = createFileRoute("/runs")({
  component: RunsLayout,
});

function RunsLayout(): JSX.Element {
  return <Outlet />;
}
