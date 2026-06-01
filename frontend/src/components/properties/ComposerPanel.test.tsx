// @vitest-environment happy-dom
import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ReactNode } from "react";

vi.mock("@/lib/api", async (orig) => {
  const actual = await orig<typeof import("@/lib/api")>();
  // Minimal compose library so the panel gets past its `if (!lib)` early return.
  const LIB = {
    materials: [{ name: "watermelon", desc: "soft" }],
    scenarios: [
      { name: "earthquake", desc: "shake", recommended_material: "watermelon" },
    ],
    buildings: [{ name: "tower", desc: "tall" }],
  };
  return {
    ...actual,
    api: {
      ...actual.api,
      compose: {
        library: vi.fn().mockResolvedValue(LIB),
        run: vi.fn().mockResolvedValue({ recipe_data: {} }),
      },
    },
  };
});

import { ComposerPanel } from "./ComposerPanel";

function renderWithQuery(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("ComposerPanel", () => {
  // Regression for React #310 ("Rendered more hooks than during the previous
  // render"). The compose-library query is pending on first render, so the
  // panel takes its `if (!lib) return …` early return. When the query resolves
  // and the panel re-renders with `lib`, every hook it calls (including the
  // "Total frames" store reads) MUST already have run on the pending render —
  // otherwise the hook count changes between renders and React throws. This is
  // exactly the pending→resolved transition a user triggers by opening the
  // panel, so it must not crash.
  it("survives the library query resolving without a hooks-count violation", async () => {
    renderWithQuery(<ComposerPanel />);
    // Pending render shows the loading placeholder…
    expect(screen.getByText(/Loading composer/i)).toBeTruthy();
    // …and after the query resolves the full panel renders. If a hook sits
    // after the early return, this resolve render crashes and "Total frames"
    // never commits.
    await waitFor(() => {
      expect(screen.getByText("Total frames")).toBeTruthy();
    });
  });
});
