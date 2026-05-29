def test_boundaries(client):
    r = client.get("/api/schemas/boundaries")
    assert r.status_code == 200
    body = r.json()
    # Required types
    for required in ("bounding_box", "surface_collider", "cuboid",
                     "release_particles_sequentially"):
        assert required in body, f"missing BC type: {required}"
    # bounding_box has zero fields
    assert body["bounding_box"] == []
    # cuboid fields MUST match what the sim actually reads (point, not center;
    # includes reset). The old schema said "center" — the sim ignored it.
    cuboid_field_names = {f["name"] for f in body["cuboid"]}
    assert cuboid_field_names == {"point", "size", "velocity", "start_time",
                                  "end_time", "reset"}
    point = next(f for f in body["cuboid"] if f["name"] == "point")
    assert point["type"] == "vec3"
    # surface_collider uses `surface` (not "surface_type")
    sc_names = {f["name"] for f in body["surface_collider"]}
    assert "surface" in sc_names and "surface_type" not in sc_names
    # release uses the real field set (not the old axis/interval form)
    rel_names = {f["name"] for f in body["release_particles_sequentially"]}
    assert rel_names == {"normal", "start_position", "end_position",
                         "num_layers", "start_time", "end_time"}
    # the real force + drag primitives are now exposed
    assert "particle_impulse" in body
    assert "enforce_particle_translation" in body


def test_materials(client):
    r = client.get("/api/schemas/materials")
    assert r.status_code == 200
    body = r.json()
    # All 7 canonical materials present
    for m in ("jelly", "metal", "sand", "foam", "snow", "plasticine", "watermelon"):
        assert m in body, f"missing material: {m}"
    # Spot-check a few values from R7_diversity
    assert body["metal"]["E"] == 50000.0
    assert body["jelly"]["nu"] == 0.38
    assert body["foam"]["density"] == 0.3


def test_materials_have_consistent_keys(client):
    """Every material should have the same parameter keys, so the React
    Material panel can iterate any material's keys and get a complete
    set of widgets."""
    r = client.get("/api/schemas/materials")
    body = r.json()
    expected_keys = {"E", "nu", "density", "yield_stress", "friction_angle",
                     "beta", "xi", "hardening", "alpha_0", "plastic_viscosity"}
    for name, defaults in body.items():
        assert set(defaults.keys()) == expected_keys, \
            f"material {name!r} has keys {set(defaults.keys())}, expected {expected_keys}"
