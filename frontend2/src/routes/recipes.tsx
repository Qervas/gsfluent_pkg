import { Outlet, createFileRoute } from "@tanstack/react-router";

// Layout route for /recipes/*. The list page lives in recipes.index.tsx;
// detail in recipes.$id.tsx; new in recipes.new.tsx. This file only
// renders Outlet so child routes paint.
export const Route = createFileRoute("/recipes")({
  component: RecipesLayout,
});

function RecipesLayout(): JSX.Element {
  return <Outlet />;
}
