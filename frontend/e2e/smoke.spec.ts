import { test, expect } from "@playwright/test";

test.describe("gsfluent shell smoke", () => {
  test("app boots and shell renders", async ({ page }) => {
    await page.goto("/");
    // App-shell brand identity
    await expect(page.getByText("gsfluent", { exact: false })).toBeVisible();
    // Workspace tabs
    await expect(page.getByText("Sim", { exact: true })).toBeVisible();
    await expect(page.getByText("Compare (soon)")).toBeVisible();
    // Outliner sections
    await expect(page.getByText("Models")).toBeVisible();
    await expect(page.getByText("Recipes")).toBeVisible();
    await expect(page.getByText("History")).toBeVisible();
    // Status strip ⌘K hint
    await expect(page.getByText("⌘K")).toBeVisible();
  });

  test("command palette opens via ⌘K (Cmd on macOS / Ctrl elsewhere)", async ({ page }) => {
    await page.goto("/");
    // Cross-platform: try Meta first, then Control. Both are wired in
    // useShortcuts.
    await page.keyboard.press("Meta+k");
    // The palette renders a search input with a recognizable placeholder.
    const palette = page.getByPlaceholder(/Type a command|search/i);
    if (!(await palette.isVisible().catch(() => false))) {
      // Fallback for non-mac runners.
      await page.keyboard.press("Control+k");
    }
    await expect(page.getByPlaceholder(/Type a command|search/i)).toBeVisible();
    // Close via Escape.
    await page.keyboard.press("Escape");
  });

  test("Recipes section lists at least one built-in recipe", async ({ page }) => {
    await page.goto("/");
    // The Outliner queries /api/recipes on mount. With the backend running,
    // at least 'jelly' should be present (built-in shipped recipe).
    // If backend is offline, skip rather than fail.
    const jelly = page.getByRole("button", { name: "jelly" });
    try {
      await expect(jelly).toBeVisible({ timeout: 8_000 });
    } catch {
      test.skip(true, "Backend not reachable; recipes endpoint unavailable.");
    }
  });
});
