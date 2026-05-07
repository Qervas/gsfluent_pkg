"""Type schemas for boundary_conditions list entries.

Each entry is a (field_name, ui_type, default_value, hint) tuple. The
React BC editor uses this to render type-specific forms — pick a BC
type from a dropdown, the form fills with the right fields.
"""
BC_SCHEMAS: dict[str, list[tuple]] = {
    "bounding_box": [],
    "surface_collider": [
        ("point",        "vec3",   [0.0, 0.0, 0.0], "Plane origin"),
        ("normal",       "vec3",   [0.0, 0.0, 1.0], "Plane normal (unit)"),
        ("surface_type", "string", "sticky",        "sticky | slip | separate"),
        ("friction",     "float",  0.0,             "0..1"),
    ],
    "cuboid": [
        ("center",     "vec3",  [0.0, 0.0, 0.0], "Center"),
        ("size",       "vec3",  [1.0, 1.0, 1.0], "Half-extents"),
        ("velocity",   "vec3",  [0.0, 0.0, 0.0], "Linear velocity"),
        ("start_time", "float", 0.0,             "Activate at (s)"),
        ("end_time",   "float", 999.0,           "Deactivate at (s)"),
    ],
    "release_particles_sequentially": [
        ("axis",       "string", "z",  "x | y | z"),
        ("start_time", "float",  0.0,  "Begin (s)"),
        ("interval",   "float",  0.01, "Sweep step (s)"),
    ],
}
