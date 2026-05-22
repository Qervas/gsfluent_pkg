import { test, expect, type Route } from "@playwright/test";

/**
 * Regression: clicking an unloaded sequence in the outliner should pop the
 * centered Download modal ("Not on this client" + "Download to play"). The
 * modal renders when ViserSplatScene sees /state.loading.phase === "error"
 * with error === "not_found" for the active sequence cell.
 *
 * Strategy: mock both the backend (/api/*) and the viser control sidecar
 * (http://localhost:8092/*) via page.route(). Page.route intercepts every
 * fetch the page makes, including cross-origin ones, so this works for the
 * viser control URL even though ViserSplatScene fetches it directly from
 * a different origin than the SPA.
 */
test("clicking an unloaded sequence shows the Download modal", async ({ page }) => {
  const SEQ_NAME = "test_seq_abc";

  // Viser state machine. The SPA polls /state every 500ms. Initially we
  // report a healthy-but-empty viser. After the SPA POSTs /set with our
  // sequence, the next /state poll returns loading.error=not_found, which
  // is exactly the signal that triggers the Download modal.
  let setReceived = false;

  // ── backend mocks ────────────────────────────────────────────────────
  await page.route("**/api/recipes", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/models", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/runs", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/runs/history", (route: Route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route("**/api/health", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", pkg_root: "/tmp" }),
    }),
  );
  await page.route("**/api/sequences", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          name: SEQ_NAME,
          source: "sim",
          source_path: null,
          model_ref: null, // orphan → shown in "Orphan sequences" group
          frame_count: 151,
          fps_hint: 24,
          n_splats: null,
          coord_convention: "z-up",
          first_frame_full: false,
          is_broken: false,
          created_at: null,
          cache: {
            viser_npz_mtime: null,
            viser_npz_bytes: null,
            frames_bin_mtime: null,
            frames_bin_bytes: null,
          },
        },
      ]),
    }),
  );

  // ── viser control sidecar mocks ──────────────────────────────────────
  // The default control URL is http://<host>:8092 (see ViserSplatScene).
  // Match by suffix so we don't have to care about the exact hostname.
  await page.route("**/:8092/state", (route: Route) => {
    // Once /set has been POSTed for our sequence, switch the response so
    // the next poll reports the "not_found" error that surfaces the modal.
    const body = setReceived
      ? {
          cell: null,
          frame: 0,
          n_frames: 0,
          cells: [],
          loading: {
            name: `sequence:${SEQ_NAME}`,
            phase: "error",
            error: "not_found",
          },
        }
      : {
          cell: null,
          frame: 0,
          n_frames: 0,
          cells: [],
          loading: null,
        };
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  });
  await page.route("**/:8092/set", (route: Route) => {
    setReceived = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: false, error: "not_found" }),
    });
  });
  // Defensive: catch /clear too, since wireName can flip to null on
  // transient store updates. Returning ok keeps the SPA from logging
  // network errors during the test.
  await page.route("**/:8092/clear", (route: Route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true }),
    }),
  );

  await page.goto("/");

  // Outliner renders the orphan sequence as a button with the seq name.
  const seqButton = page.getByRole("button", { name: SEQ_NAME }).first();
  await expect(seqButton).toBeVisible({ timeout: 10_000 });
  await seqButton.click();

  // Modal contents — "Not on this client" label + the sequence name in
  // mono font + the "Download to play" CTA.
  await expect(page.getByText("Not on this client")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Download to play" })).toBeVisible();
});
