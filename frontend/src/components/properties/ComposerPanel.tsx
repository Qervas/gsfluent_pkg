import { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { SelectInput } from "./widgets/SelectInput";
import { NumberInput } from "./widgets/NumberInput";
import { api } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { ComposeLibrary } from "@/lib/types";

/** Primary recipe authoring surface: pick MATERIAL x SCENARIO x BUILDING and
 *  the server composes a verified flat recipe. The flat-field panels below
 *  (Solver/Forces/Boundary/...) become advanced overrides on top of whatever
 *  the composer produced.
 *
 *  "Total frames" is a free choice. Changing it sets a frame_num override
 *  (exactly like other solver parameters). The composed baseline keeps the
 *  scenario's default; the user's choice wins at run time via the normal
 *  override merge. No backend change required — it's just a field in the JSON
 *  recipe. */
export function ComposerPanel() {
  const loadActiveRecipe = useStore((s) => s.loadActiveRecipe);
  const activeRecipeName = useStore((s) => s.activeRecipeName);

  const { data: lib } = useQuery<ComposeLibrary>({
    queryKey: ["compose-library"],
    queryFn: api.compose.library,
  });

  const [building, setBuilding] = useState<string | null>(null);
  const [scenario, setScenario] = useState<string | null>(null);
  const [material, setMaterial] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const seeded = useRef(false);

  // Self-seed once: when the library has loaded and no recipe is active yet,
  // compose the default (first scenario + its recommended material + first
  // building) so the user lands on a ready verified recipe. Guarded so it
  // never clobbers a recipe the user picked from the library.
  useEffect(() => {
    if (seeded.current || !lib || activeRecipeName) return;
    const sc = lib.scenarios[0];
    const bld = lib.buildings[0];
    if (!sc || !bld) return;
    seeded.current = true;
    const mat = sc.recommended_material ?? lib.materials[0]?.name;
    if (!mat) return;
    setScenario(sc.name);
    setMaterial(mat);
    setBuilding(bld.name);
    // Frame count is now a free override (handled via the global override system).
    // We clear any previous frame_num override when seeding a new composed recipe
    // so the scenario default from the baseline is used unless the user changes it.
    useStore.getState().clearOverride?.("frame_num");
    void (async () => {
      try {
        const result = await api.compose.run(mat, sc.name, bld.name);
        loadActiveRecipe(`${sc.name}·${mat}`, result.recipe_data);
      } catch { /* surfaced on next manual change */ }
    })();
  }, [lib, activeRecipeName, loadActiveRecipe]);

  if (!lib) {
    return <div className="text-text-muted text-xs p-1">Loading composer…</div>;
  }

  // Defaults: first building, first scenario, that scenario's recommended
  // material (so the one-click default is always a verified-good combo).
  const b = building ?? lib.buildings[0]?.name ?? "";
  const s = scenario ?? lib.scenarios[0]?.name ?? "";
  const scen = lib.scenarios.find((x) => x.name === s);
  const m = material ?? scen?.recommended_material ?? lib.materials[0]?.name ?? "";

  const recompose = async (mat: string, sc: string, bld: string) => {
    setBusy(true);
    setErr(null);
    try {
      const result = await api.compose.run(mat, sc, bld);
      const name = `${sc}·${mat}`;
      loadActiveRecipe(name, result.recipe_data);
    } catch (e) {
      setErr(extractComposeError(e instanceof Error ? e.message : String(e)));
    } finally {
      setBusy(false);
    }
  };

  const onScenario = (sc: string) => {
    setScenario(sc);
    // Snap material to the new scenario's recommendation (the guided default).
    const scenObj = lib.scenarios.find((x) => x.name === sc);
    const rec = scenObj?.recommended_material;
    const mat = rec ?? m;
    setMaterial(mat);

    // When scenario changes, clear any previous frame_num override so the new
    // scenario's default (from the fresh baseline) takes effect. User can then
    // freely adjust "Total frames" below.
    useStore.getState().clearOverride?.("frame_num");
    void recompose(mat, sc, b);
  };
  const onMaterial = (mat: string) => { setMaterial(mat); void recompose(mat, s, b); };
  const onBuilding = (bld: string) => { setBuilding(bld); void recompose(m, s, bld); };

  const recMismatch = scen?.recommended_material && scen.recommended_material !== m;

  return (
    <div className="space-y-1">
      <SelectInput
        label="Scenario"
        value={s}
        options={lib.scenarios.map((x) => x.name)}
        onChange={onScenario}
        hint={scen?.desc}
      />
      <SelectInput
        label="Material"
        value={m}
        options={lib.materials.map((x) => x.name)}
        onChange={onMaterial}
        hint={lib.materials.find((x) => x.name === m)?.desc}
      />
      <SelectInput
        label="Building"
        value={b}
        options={lib.buildings.map((x) => x.name)}
        onChange={onBuilding}
        hint={lib.buildings.find((x) => x.name === b)?.desc}
      />

      {/* Free choice for total simulation frames.
          This sets a frame_num override on top of the composed baseline
          (same mechanism as the Solver panel). The scenario default is used
          unless the user changes this value. */}
      <NumberInput
        label="Total frames"
        value={Number(
          (useStore((s) => s.simOverrides) as Record<string, unknown>)?.frame_num ??
            (useStore((s) => s.simRecipeBaseline) as Record<string, unknown>)?.frame_num ??
            150,
        )}
        onChange={(n) => {
          const val = Math.max(1, Math.round(n));
          useStore.getState().setOverride("frame_num", val);
        }}
        step={1}
        hint="Any positive integer. Controls how many frames the simulation will produce. This is a free override — the scenario provides a default."
      />

      {busy && <div className="text-text-muted text-[11px] px-1">composing…</div>}
      {recMismatch && !err && (
        <div className="text-warning text-[11px] px-1">
          Recommended material for {s}: <span className="font-mono">{scen!.recommended_material}</span>
        </div>
      )}
      {err && (
        <div className="text-error text-[11px] px-1 py-1 border border-error/40 rounded bg-error/5">
          {err}
        </div>
      )}
      <div className="text-text-muted text-[10px] px-1 pt-1 italic">
        Composed recipe — fine-tune below as overrides.
      </div>
    </div>
  );
}

/** Pull the human reason out of our 422 envelope ("HTTP 422: {detail:{error:
 *  {message}}}"). Falls back to the raw string. */
function extractComposeError(raw: string): string {
  const idx = raw.indexOf(": ");
  if (idx >= 0) {
    const body = raw.slice(idx + 2).trim();
    if (body.startsWith("{")) {
      try {
        const p = JSON.parse(body);
        const msg = p?.detail?.error?.message;
        if (typeof msg === "string") return msg;
      } catch { /* fall through */ }
    }
  }
  return raw;
}
