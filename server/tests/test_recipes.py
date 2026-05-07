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


def test_save_then_read_back_via_api(client, tmp_path, monkeypatch):
    from gsfluent.core import recipes as rec
    monkeypatch.setattr(rec, "USER_RECIPES_DIR", tmp_path / "_user_recipes")
    payload = {"data": {"material": "metal", "E": 50000}, "based_on": "metal"}
    save_resp = client.put("/api/recipes/round_trip_demo", json=payload)
    assert save_resp.status_code == 200
    list_resp = client.get("/api/recipes")
    assert any(x["name"] == "round_trip_demo" and x["source"] == "user" for x in list_resp.json())
    get_resp = client.get("/api/recipes/round_trip_demo")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["name"] == "round_trip_demo"
    assert body["source"] == "user"
    assert body["data"]["material"] == "metal"
    assert body["data"]["_provenance"]["based_on"] == "metal"


def test_save_rejects_invalid_name(client, tmp_path, monkeypatch):
    from gsfluent.core import recipes as rec
    monkeypatch.setattr(rec, "USER_RECIPES_DIR", tmp_path / "_user_recipes")
    bad = client.put("/api/recipes/foo..bar", json={"data": {}})
    assert bad.status_code == 422
    bad_dotfile = client.put("/api/recipes/.hidden", json={"data": {}})
    assert bad_dotfile.status_code == 422
    # Make sure the directory was never created (since save was rejected pre-mkdir)
    # Actually the validator rejects BEFORE mkdir, but tmp_path doesn't have the dir anyway.
    assert not (tmp_path / "_user_recipes" / ".hidden.json").exists()


def test_get_corrupt_recipe_409(client, tmp_path, monkeypatch):
    from gsfluent.core import recipes as rec
    fake_user = tmp_path / "_user_recipes"
    fake_user.mkdir(parents=True)
    (fake_user / "broken.json").write_text("{not valid json")
    monkeypatch.setattr(rec, "USER_RECIPES_DIR", fake_user)
    r = client.get("/api/recipes/broken")
    assert r.status_code == 409
    assert "broken" in r.json()["detail"].lower() or "failed to read" in r.json()["detail"].lower()


def test_delete_user_preset(client, tmp_path, monkeypatch):
    from gsfluent.core import recipes as rec
    fake_user = tmp_path / "_user_recipes"
    fake_user.mkdir(parents=True)
    monkeypatch.setattr(rec, "USER_RECIPES_DIR", fake_user)
    # Create a user preset on disk
    target = fake_user / "deletable.json"
    target.write_text('{"material": "jelly"}')
    assert target.exists()
    r = client.delete("/api/recipes/deletable")
    assert r.status_code == 200
    assert r.json() == {"deleted": "deletable"}
    assert not target.exists()
    # Deleting again → 404
    r2 = client.delete("/api/recipes/deletable")
    assert r2.status_code == 404


def test_delete_builtin_forbidden(client, tmp_path, monkeypatch):
    from gsfluent.core import recipes as rec
    monkeypatch.setattr(rec, "USER_RECIPES_DIR", tmp_path / "_user_recipes")
    # 'jelly' is a known built-in; attempting to delete must 403.
    r = client.delete("/api/recipes/jelly")
    assert r.status_code == 403
    assert "built-in" in r.json()["detail"].lower()
