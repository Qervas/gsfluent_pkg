// Module shim for @mkkellogg/gaussian-splats-3d. The lib ships no type
// declarations and we only touch a handful of symbols (DropInViewer,
// UncompressedSplatArray, SplatBufferGenerator) — declared as `any` so
// the compiler accepts namespace property accesses regardless of which
// symbols are actually re-exported by the lib's module entrypoint.
declare module "@mkkellogg/gaussian-splats-3d";
