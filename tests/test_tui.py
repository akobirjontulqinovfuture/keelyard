"""Timeline browser driven headless with Textual's pilot: list, diff, rewind with confirm, cancel."""

import asyncio

from keelyard.snapshot import Shadow
from keelyard.tui import Timeline


def _steps(cfg, project):
    sh = Shadow(cfg)
    sh.snapshot("baseline")
    (project / "app.py").write_text("def add(a, b):\n    return a - b  # broken\n", encoding="utf-8")
    sh.snapshot("Edit")
    return sh


def test_timeline_shows_steps_and_diff(cfg, project):
    sh = _steps(cfg, project)

    async def go():
        app = Timeline(sh)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert len(app.steps) == 2 and app.steps[0].tool == "Edit"  # newest first
            body = str(app.query_one("#diffbody").render())
            assert "a - b" in body
            await pilot.press("down")
            await pilot.pause()
            assert "#1" in str(app.query_one("#head").render())
    asyncio.run(go())


def test_timeline_rewind_needs_confirmation(cfg, project):
    sh = _steps(cfg, project)

    async def go():
        app = Timeline(sh)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.press("down", "r", "n")  # cancel: nothing changes
            await pilot.pause()
            assert "a - b" in (project / "app.py").read_text(encoding="utf-8")
            await pilot.press("r", "y")
            await pilot.pause()
        return app.message
    msg = asyncio.run(go())
    assert "a + b" in (project / "app.py").read_text(encoding="utf-8")
    assert msg and "restored step #1" in msg and "undo" in msg


def test_timeline_g_and_G_jump_to_ends(cfg, project):
    sh = _steps(cfg, project)
    (project / "app.py").write_text("def add(a, b):\n    return a * b\n", encoding="utf-8")
    sh.snapshot("Edit")

    async def go():
        app = Timeline(sh)
        async with app.run_test(size=(120, 30)) as pilot:
            ol = app.query_one("#steps")
            await pilot.press("G")
            await pilot.pause()
            assert ol.highlighted == len(app.steps) - 1
            assert "#1" in str(app.query_one("#head").render())
            await pilot.press("g")
            await pilot.pause()
            assert ol.highlighted == 0
            assert f"#{app.steps[0].step}" in str(app.query_one("#head").render())
    asyncio.run(go())
