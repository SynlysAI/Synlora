from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

loop_mod = importlib.import_module("yuxi.services.plane_projection_loop")


@pytest.mark.asyncio
async def test_loop_tolerates_iteration_failures(monkeypatch):
    calls = {"n": 0}

    async def fake_project():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        if calls["n"] >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(loop_mod, "_project_once", fake_project)
    monkeypatch.setattr(loop_mod, "POLL_SECONDS", 0.01)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(loop_mod.plane_projection_loop(), timeout=5)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_start_stop_is_idempotent():
    app = SimpleNamespace(state=SimpleNamespace(plane_projection_task=None))
    monkeypatch_target = loop_mod.POLL_SECONDS
    loop_mod.POLL_SECONDS = 0.01
    try:
        await loop_mod.start_plane_projection_loop(app)
        first = app.state.plane_projection_task
        await loop_mod.start_plane_projection_loop(app)
        assert app.state.plane_projection_task is first
        await loop_mod.stop_plane_projection_loop(app)
        assert app.state.plane_projection_task is None
        await loop_mod.stop_plane_projection_loop(app)
    finally:
        loop_mod.POLL_SECONDS = monkeypatch_target
