import asyncio
import json

import httpx

from dashboard.stream import generate_optimization_events
from optimizer.pipeline import CASES, compare, generate_patch
from target_app.app import app


def collect(gen):
    async def run():
        return [json.loads(chunk[len("data: "):]) async for chunk in gen]
    return asyncio.run(run())


def test_dashboard_served():
    async def get():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/")

    res = asyncio.run(get())
    assert res.status_code == 200
    assert "LatencyZero" in res.text


def test_cases_point_at_existing_routes():
    paths = {getattr(r, "path", None) for r in app.routes}
    for case in CASES.values():
        assert case.route in paths


def test_generate_patch():
    orig = "def foo():\n    time.sleep(1)\n    return 1\n"
    opt = "async def foo():\n    await asyncio.sleep(1)\n    return 1\n"
    patch = generate_patch(orig, opt, "app.py")
    assert "--- a/app.py" in patch
    assert "+++ b/app.py" in patch
    assert "-    time.sleep(1)" in patch
    assert "+    await asyncio.sleep(1)" in patch


def test_compare():
    before = {"p50": 100.0, "p95": 200.0, "rps": 10.0}
    after = {"p50": 25.0, "p95": 50.0, "rps": 40.0}
    result = compare(before, after)
    assert result["p50_speedup"] == 4.0
    assert result["p50_change_pct"] == 75.0
    assert result["rps_ratio"] == 4.0


def test_stream_custom_code_is_static_only():
    code = (
        "import time\n\n"
        "async def handler():\n"
        "    time.sleep(0.2)\n"
        "    return {'ok': True}\n"
    )
    events = collect(generate_optimization_events("custom", custom_code=code))
    done = events[-1]

    assert events[0]["stage"] == "start"
    assert done["stage"] == "done"
    assert done["mode"] == "review"
    assert done["status"] == "proposed"
    assert done["before"] is None and done["after"] is None
    assert "await asyncio.sleep(0.2)" in done["optimized_code"]


def test_stream_custom_code_syntax_error():
    events = collect(generate_optimization_events("custom", custom_code="def broken(:\n"))
    assert events[-1]["stage"] == "error"


def test_stream_unknown_case():
    events = collect(generate_optimization_events("nope"))
    assert events == [{"stage": "error", "message": "Unknown case 'nope'."}]
