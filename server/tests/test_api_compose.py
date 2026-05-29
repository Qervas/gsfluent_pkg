"""Tests for the structured composition API (POST /api/compose + library).

Pins that the endpoint:
  - composes a valid flat recipe from material x scenario x building
  - returns recipe_data byte-equal to the in-process composer
  - surfaces unknown names + over-ceiling picks as 422 validation errors
  - lists the libraries without leaking recipe internals
"""
from __future__ import annotations

import copy

from gsfluent.authoring import compose
from gsfluent.authoring.scenarios import SCENARIOS


def test_compose_happy_path(client):
    r = client.post(
        "/api/compose",
        json={"material": "watermelon", "scenario": "earthquake",
              "building": "cluster_6_15"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["material"] == "watermelon"
    assert body["scenario"] == "earthquake"
    assert body["building"] == "cluster_6_15"
    rd = body["recipe_data"]
    assert "_composed_from" in rd
    assert "boundary_conditions" in rd
    assert "substep_dt" in rd
    # Endpoint output must equal the in-process composer exactly.
    assert rd == compose("watermelon", "earthquake", "cluster_6_15")


def test_compose_unknown_material_is_422_with_valid_names(client):
    r = client.post(
        "/api/compose",
        json={"material": "nope", "scenario": "earthquake",
              "building": "cluster_6_15"},
    )
    assert r.status_code == 422
    msg = r.json()["detail"]["error"]["message"]
    assert "unknown material" in msg
    assert "watermelon" in msg  # the "have [...]" list is surfaced


def test_compose_unknown_scenario_is_422(client):
    r = client.post(
        "/api/compose",
        json={"material": "watermelon", "scenario": "nope",
              "building": "cluster_6_15"},
    )
    assert r.status_code == 422
    assert "unknown scenario" in r.json()["detail"]["error"]["message"]


def test_compose_unknown_building_is_422(client):
    r = client.post(
        "/api/compose",
        json={"material": "watermelon", "scenario": "earthquake",
              "building": "nope"},
    )
    assert r.status_code == 422
    assert "unknown building" in r.json()["detail"]["error"]["message"]


def test_compose_over_ceiling_is_422(client):
    # Craft a scenario whose impact speed exceeds the grid-escape ceiling;
    # the composer must hard-fail (we do NOT clamp).
    hot = copy.deepcopy(SCENARIOS["wrecking"])
    for ev in hot["events"]:
        if ev["kind"] == "impact":
            ev["speed"] = 9.0
    SCENARIOS["_api_hot_test"] = hot
    try:
        r = client.post(
            "/api/compose",
            json={"material": "watermelon", "scenario": "_api_hot_test",
                  "building": "cluster_6_15"},
        )
        assert r.status_code == 422
        assert "ceiling" in r.json()["detail"]["error"]["message"].lower()
    finally:
        del SCENARIOS["_api_hot_test"]


def test_compose_rejects_unknown_field(client):
    r = client.post(
        "/api/compose",
        json={"material": "watermelon", "scenario": "earthquake",
              "building": "cluster_6_15", "bogus": 1},
    )
    assert r.status_code == 422


def test_library_lists_scenarios_and_materials(client):
    r = client.get("/api/compose/library")
    assert r.status_code == 200
    lib = r.json()
    assert {"materials", "scenarios", "buildings"} <= set(lib)
    scen_names = {s["name"] for s in lib["scenarios"]}
    assert {"earthquake", "wrecking"} <= scen_names
    mat_names = {m["name"] for m in lib["materials"]}
    assert "watermelon" in mat_names
    assert any(b["name"] == "cluster_6_15" for b in lib["buildings"])


def test_library_does_not_leak_recipe_internals(client):
    lib = client.get("/api/compose/library").json()
    # The library summaries must not carry the heavy recipe-internal blocks.
    for m in lib["materials"]:
        assert "particle_filling" not in m
        assert "init_azimuthm" not in m  # no camera block leakage
    for s in lib["scenarios"]:
        assert "events" not in s  # only num_events summary
        assert "recommended_material" in s


def test_library_scenarios_recommend_watermelon(client):
    lib = client.get("/api/compose/library").json()
    for s in lib["scenarios"]:
        if s["name"] in ("earthquake", "wrecking"):
            assert s["recommended_material"] == "watermelon"
