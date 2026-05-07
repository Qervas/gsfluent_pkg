def test_list_recipes_includes_builtins(client):
    r = client.get("/api/recipes")
    assert r.status_code == 200
    names = {x["name"] for x in r.json()}
    for n in ("jelly", "metal", "demolition"):
        assert n in names, f"expected built-in recipe {n!r} in list"

def test_get_recipe(client):
    r = client.get("/api/recipes/jelly")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "jelly"
    assert body["data"]["material"] == "jelly"

def test_get_unknown_404(client):
    assert client.get("/api/recipes/nope").status_code == 404

def test_save_user_preset(client, tmp_path, monkeypatch):
    from gsfluent.core import recipes as rec
    monkeypatch.setattr(rec, "USER_RECIPES_DIR", tmp_path / "_user_recipes")
    payload = {"data": {"material": "jelly", "E": 9999.0}}
    r = client.put("/api/recipes/test_preset", json=payload)
    assert r.status_code == 200
    saved = (tmp_path / "_user_recipes" / "test_preset.json").read_text()
    assert "9999" in saved
    import json as J
    assert "_provenance" in J.loads(saved)
