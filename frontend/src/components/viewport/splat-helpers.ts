import type { StaticAttrs } from "@/lib/types";

/**
 * Pack the backend's StaticAttrs (R per-particle 3x3 + scales + rgb + opacity)
 * plus the active xyz frame into the buffers @mkkellogg/gaussian-splats-3d
 * expects: positions (n*3 float), scales (n*3 float), rotations (n*4 quat
 * float, w-first), colors (n*4 uint8 RGBA).
 *
 * The R→quaternion conversion uses the standard branch on tr(R) for
 * numerical stability. R rows are stored flat (R[0..8] = first matrix,
 * R[9..17] = second, etc.).
 */
export function packForSplats(attrs: StaticAttrs, xyz: Float32Array) {
  const n = attrs.n;
  const positions = xyz; // already (n, 3) float32, just pass through
  const scales = new Float32Array(attrs.scales);
  const rotations = new Float32Array(n * 4);
  for (let i = 0; i < n; i++) {
    const o = i * 9;
    const m00 = attrs.R[o + 0], m01 = attrs.R[o + 1], m02 = attrs.R[o + 2];
    const m10 = attrs.R[o + 3], m11 = attrs.R[o + 4], m12 = attrs.R[o + 5];
    const m20 = attrs.R[o + 6], m21 = attrs.R[o + 7], m22 = attrs.R[o + 8];
    const tr = m00 + m11 + m22;
    let qw: number, qx: number, qy: number, qz: number;
    if (tr > 0) {
      const s = 0.5 / Math.sqrt(tr + 1.0);
      qw = 0.25 / s;
      qx = (m21 - m12) * s;
      qy = (m02 - m20) * s;
      qz = (m10 - m01) * s;
    } else if (m00 > m11 && m00 > m22) {
      const s = 2.0 * Math.sqrt(1.0 + m00 - m11 - m22);
      qw = (m21 - m12) / s;
      qx = 0.25 * s;
      qy = (m01 + m10) / s;
      qz = (m02 + m20) / s;
    } else if (m11 > m22) {
      const s = 2.0 * Math.sqrt(1.0 + m11 - m00 - m22);
      qw = (m02 - m20) / s;
      qx = (m01 + m10) / s;
      qy = 0.25 * s;
      qz = (m12 + m21) / s;
    } else {
      const s = 2.0 * Math.sqrt(1.0 + m22 - m00 - m11);
      qw = (m10 - m01) / s;
      qx = (m02 + m20) / s;
      qy = (m12 + m21) / s;
      qz = 0.25 * s;
    }
    rotations[i * 4 + 0] = qw;
    rotations[i * 4 + 1] = qx;
    rotations[i * 4 + 2] = qy;
    rotations[i * 4 + 3] = qz;
  }
  const colors = new Uint8Array(n * 4);
  for (let i = 0; i < n; i++) {
    colors[i * 4 + 0] = (attrs.rgb[i * 3 + 0] * 255) | 0;
    colors[i * 4 + 1] = (attrs.rgb[i * 3 + 1] * 255) | 0;
    colors[i * 4 + 2] = (attrs.rgb[i * 3 + 2] * 255) | 0;
    colors[i * 4 + 3] = (attrs.opacity[i] * 255) | 0;
  }
  return { positions, scales, rotations, colors };
}
