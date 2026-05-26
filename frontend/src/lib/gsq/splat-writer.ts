/** Pure mapping: decoder frame + static attrs -> one splat's args for Spark's
 *  PackedSplats.setSplat. Center+quat are per-frame; scale/color/opacity are
 *  static. The decoder emits quats as [w,x,y,z]; three.js / Spark want
 *  (x,y,z,w), so we reorder here. Z-up is handled by the camera, not here.
 *
 *  `out` is reused across calls to avoid per-splat allocation in the hot loop.
 */
import type { GsqStatic, GsqFrame } from "./decoder";

export interface SplatArgs {
  center: [number, number, number];
  scales: [number, number, number];
  quat: [number, number, number, number]; // x, y, z, w
  opacity: number;
  color: [number, number, number];
}

export function makeSplatArgs(): SplatArgs {
  return { center: [0, 0, 0], scales: [0, 0, 0], quat: [0, 0, 0, 1], opacity: 1, color: [0, 0, 0] };
}

export function splatArgs(
  frame: GsqFrame, st: GsqStatic, i: number, out: SplatArgs,
): SplatArgs {
  const p3 = i * 3;
  const q4 = i * 4;
  out.center[0] = frame.positions[p3];
  out.center[1] = frame.positions[p3 + 1];
  out.center[2] = frame.positions[p3 + 2];
  out.scales[0] = st.scales[p3];
  out.scales[1] = st.scales[p3 + 1];
  out.scales[2] = st.scales[p3 + 2];
  // [w,x,y,z] -> (x,y,z,w)
  out.quat[0] = frame.quats[q4 + 1];
  out.quat[1] = frame.quats[q4 + 2];
  out.quat[2] = frame.quats[q4 + 3];
  out.quat[3] = frame.quats[q4 + 0];
  out.opacity = st.opacity[i];
  out.color[0] = st.rgb[p3];
  out.color[1] = st.rgb[p3 + 1];
  out.color[2] = st.rgb[p3 + 2];
  return out;
}
