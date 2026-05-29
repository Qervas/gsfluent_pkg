"""Recipe authoring layer — compose a flat sim recipe from three orthogonal
inputs: MATERIAL × SCENARIO × BUILDING.

The flat recipe JSON the sim consumes is a *build artifact*, not a source of
truth. Authors pick a material (building-agnostic physics), a scenario (a
building-relative timeline of force events), and a building (the scanned model
+ its bbox + camera), and `compose()` assembles the flat dict the existing sim
already eats. The sim, the .gsq codec, and the frontend player are unchanged —
this is a layer *above* the flat format.

See docs/slides/2026-05-29-gsfluent-recipe-authoring (local) for the design.
"""
from gsfluent.authoring.compose import compose, ComposeError

__all__ = ["compose", "ComposeError"]
