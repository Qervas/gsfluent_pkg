import { Outlet, createRootRouteWithContext } from "@tanstack/react-router";
import type { QueryClient } from "@tanstack/react-query";
import { Shell } from "@/components/layout/Shell";

interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: RootRoute,
});

function RootRoute(): JSX.Element {
  return (
    <Shell>
      <Outlet />
    </Shell>
  );
}
