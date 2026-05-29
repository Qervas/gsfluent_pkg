"""Type schemas for boundary_conditions list entries.

Each entry is a (field_name, ui_type, default_value, hint) tuple. The React BC
editor (frontend BoundaryEditor.tsx) is schema-driven: it renders exactly the
fields listed here, so this MUST match what the sim actually reads.

Ground truth = the upstream BC dispatch `set_boundary_conditions`
(utils/decode_param.py:248) + the solver methods in mpm_solver_warp.py. Verified
2026-05-29. The previous version of this file was STALE — it listed
`surface_type` (sim reads `surface`), `center` (sim reads `point`), and an
`axis`/`interval` release form the sim never reads — so the UI showed users
fields the simulation silently ignored. Fixed to the real field names below.
"""
BC_SCHEMAS: dict[str, list[tuple]] = {
    # Global grid-domain container. No fields.
    "bounding_box": [],

    # Ground / wall plane. Sim reads: point, normal, surface, friction,
    # start_time, end_time (add_surface_collider, mpm_solver_warp.py:918).
    # `surface` is "sticky" | "slip" | "separate" (NOT "surface_type").
    "surface_collider": [
        ("point",      "vec3",   [0.0, 0.0, 0.5], "Plane origin"),
        ("normal",     "vec3",   [0.0, 0.0, 1.0], "Plane normal (unit)"),
        ("surface",    "string", "slip",          "sticky | slip | separate"),
        ("friction",   "float",  0.0,             "0..1 (must be 0 if sticky)"),
        ("start_time", "float",  0.0,             "Activate at (s)"),
        ("end_time",   "float",  1000.0,          "Deactivate at (s)"),
    ],

    # Moving box that IMPOSES its velocity on the particles inside it (a
    # Dirichlet velocity puppet — set_velocity_on_cuboid, L1022). Sim reads:
    # point (center, NOT "center"), size (half-extents), velocity, start_time,
    # end_time, reset. Box (point ± size) must stay inside the grid [0, grid_lim]
    # and |velocity| should stay <= ~2 or debris escapes the grid.
    "cuboid": [
        ("point",      "vec3",  [1.0, 1.0, 1.0], "Box center"),
        ("size",       "vec3",  [0.3, 0.3, 0.3], "Half-extents"),
        ("velocity",   "vec3",  [0.0, 0.0, 0.0], "Imposed velocity"),
        ("start_time", "float", 0.0,             "Activate at (s)"),
        ("end_time",   "float", 1.0,             "Deactivate at (s)"),
        ("reset",      "int",   0,               "0 = re-impose each step"),
    ],

    # Rigidly translate (or pin, with velocity 0) the particles in a box —
    # enforce_particle_velocity_translation (L1247). The base-pin uses this with
    # velocity [0,0,0]; a non-zero velocity hauls the region (topple/yank).
    "enforce_particle_translation": [
        ("point",      "vec3",  [1.0, 1.0, 0.6], "Box center"),
        ("size",       "vec3",  [0.7, 0.7, 0.02], "Half-extents"),
        ("velocity",   "vec3",  [0.0, 0.0, 0.0], "0 = pin (anchor)"),
        ("start_time", "float", 0.0,             "Activate at (s)"),
        ("end_time",   "float", 1000.0,          "Deactivate at (s)"),
    ],

    # A real FORCE on the particles in a box — add_impulse_on_particles (L1198).
    # NOTE: the solver applies dv = force / particle_mass, and mass ~ 1e-4, so
    # `force` is SINGLE DIGITS (e.g. 0.5..2), NOT thousands, or particles
    # instantly escape the grid (verified 2026-05-29).
    "particle_impulse": [
        ("point",      "vec3",  [1.0, 1.0, 1.0], "Box center"),
        ("size",       "vec3",  [0.25, 0.25, 0.25], "Half-extents"),
        ("force",      "vec3",  [1.0, 0.0, -0.5], "Force (single digits!)"),
        ("num_dt",     "int",   6,               "Substeps to apply over"),
        ("start_time", "float", 0.2,             "Fire at (s)"),
    ],

    # Freeze the body, then unfreeze layer-by-layer along `normal` between
    # start_time..end_time — staged top-down gravity collapse
    # (release_particles_sequentially, L1401). Sim reads: normal,
    # start_position, end_position, num_layers, start_time, end_time
    # (NOT the old "axis"/"interval" form).
    "release_particles_sequentially": [
        ("normal",         "vec3",  [0.0, 0.0, 1.0], "Release axis (unit)"),
        ("start_position", "float", 1.5,             "First layer (cube coord)"),
        ("end_position",   "float", 0.6,             "Last layer (cube coord)"),
        ("num_layers",     "int",   80,              "Number of layers"),
        ("start_time",     "float", 0.2,             "Begin (s)"),
        ("end_time",       "float", 1.0,             "End (s)"),
    ],
}
