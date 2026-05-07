import asyncio
import json
from pathlib import Path

import pytest


def _make_fake_sim(path: Path) -> None:
    path.write_text(
        "#!/bin/bash\n"
        "echo '[fake] running'\n"
        "exit 0\n"
    )
    path.chmod(0o755)


def test_runner_writes_manifest_and_recipe(tmp_path, monkeypatch):
    """End-to-end: spawn a fake sim, wait for it to finish, verify manifest + recipe on disk."""
    from gsfluent.core import runner as r

    fake_sim = tmp_path / "fake_sim.sh"
    _make_fake_sim(fake_sim)
    monkeypatch.setattr(r, "SIM_ONE_SH", fake_sim)
    monkeypatch.setattr(r, "FUSED_DIR", tmp_path / "fused")

    rec = {"material": "jelly", "frame_num": 1, "_provenance": {"based_on": "jelly"}}
    rid = asyncio.run(_start_and_wait(r, rec))

    out = tmp_path / "fused" / "t_001"
    assert (out / "manifest.json").exists()
    assert (out / "recipe_effective.json").exists()

    rec_dump = json.loads((out / "recipe_effective.json").read_text())
    assert rec_dump["material"] == "jelly"

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["run_name"] == "t_001"
    assert manifest["status"] == "done"
    assert manifest["exit_code"] == 0
    assert manifest["recipe_source"] == "jelly"
    assert manifest["particles"] == 10000


def test_runner_handles_failing_sim(tmp_path, monkeypatch):
    """A non-zero exit code lands in the manifest as status=error."""
    from gsfluent.core import runner as r

    fake_sim = tmp_path / "fake_fail.sh"
    fake_sim.write_text(
        "#!/bin/bash\necho '[fake] failing'\nexit 7\n"
    )
    fake_sim.chmod(0o755)
    monkeypatch.setattr(r, "SIM_ONE_SH", fake_sim)
    monkeypatch.setattr(r, "FUSED_DIR", tmp_path / "fused")

    rid = asyncio.run(_start_and_wait(r, {"material": "jelly"}, run_name="t_fail"))
    out = tmp_path / "fused" / "t_fail"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["status"] == "error"
    assert manifest["exit_code"] == 7


async def _start_and_wait(r, recipe, run_name="t_001"):
    # Reset the in-process registry so tests don't leak across each other.
    r._RUNS.clear()
    rid = await r.start_run(
        run_name=run_name,
        model_dir=Path("/tmp/fake_model_dir"),
        recipe_data=recipe,
        recipe_source_name=recipe.get("_provenance", {}).get("based_on", "jelly"),
        particles=10000,
    )
    await r.wait_for_run(rid)
    return rid
