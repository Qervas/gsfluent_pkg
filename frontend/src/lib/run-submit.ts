import { sanitizeCellName } from "./cell";

export function isComposedRecipe(recipe: Record<string, unknown> | null): boolean {
  return !!recipe?._composed_from;
}

export function makeRunName(
  modelName: string,
  recipeName: string,
  now: Date = new Date(),
): string {
  const ts = now.toISOString().replace(/[:.]/g, "").slice(0, 15);
  const baseName = recipeName.replace(/^★ /, "");
  return sanitizeCellName(`${modelName}_${baseName}_${ts}`);
}

export function frameCountHint(recipe: Record<string, unknown>): number | undefined {
  const frameNum = Number(recipe.frame_num);
  return Number.isFinite(frameNum) ? frameNum : undefined;
}

/** Pull a human-readable cause out of an API error message. */
export function extractDetail(raw: string | null): string | null {
  if (!raw) return null;
  const bodyIdx = raw.indexOf(": ");
  if (bodyIdx >= 0) {
    const body = raw.slice(bodyIdx + 2).trim();
    if (body.startsWith("{")) {
      try {
        const parsed = JSON.parse(body);
        if (typeof parsed?.detail === "string") return parsed.detail;
        if (Array.isArray(parsed?.detail)) {
          return parsed.detail.map((d: { msg?: string; loc?: unknown[] }) =>
            d.msg ? `${(d.loc ?? []).join(".")}: ${d.msg}` : JSON.stringify(d),
          ).join("; ");
        }
      } catch {
        // Not JSON; fall back to the raw message.
      }
    }
  }
  return raw;
}

/** ETA from observed fps since the first frame landed. */
export function computeEta(
  nFrames: number,
  totalFrames: number,
  firstFrameAt: number | null,
  nowMs: number = Date.now(),
): string | null {
  if (firstFrameAt === null || nFrames === 0) return null;
  const elapsed = Math.max((nowMs - firstFrameAt) / 1000, 0.001);
  const fps = nFrames / elapsed;
  if (nFrames >= totalFrames || fps <= 0) return null;
  const remaining = (totalFrames - nFrames) / fps;
  const m = Math.floor(remaining / 60);
  const s = Math.floor(remaining % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}
