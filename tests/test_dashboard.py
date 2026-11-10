import json
import pytest
from target_app.app import dashboard
from dashboard.stream import generate_optimization_events, generate_patch, CASE_METRIC_CONFIGS


def test_dashboard_file_exists():
    res = dashboard()
    assert res.status_code == 200
    assert "index.html" in res.path


def test_case_metric_configs():
    assert "case1" in CASE_METRIC_CONFIGS
    assert "case2" in CASE_METRIC_CONFIGS
    assert "case3" in CASE_METRIC_CONFIGS
    assert CASE_METRIC_CONFIGS["case1"]["before"]["p50"] > CASE_METRIC_CONFIGS["case1"]["after"]["p50"]
    assert CASE_METRIC_CONFIGS["case2"]["before"]["p50"] > CASE_METRIC_CONFIGS["case2"]["after"]["p50"]
    assert CASE_METRIC_CONFIGS["case3"]["before"]["p50"] > CASE_METRIC_CONFIGS["case3"]["after"]["p50"]


def test_generate_patch():
    orig = "def foo():\n    time.sleep(1)\n    return 1"
    opt = "async def foo():\n    await asyncio.sleep(0.01)\n    return 1"
    patch = generate_patch(orig, opt, "app.py")
    assert "--- a/app.py" in patch
    assert "+++ b/app.py" in patch
    assert "-    time.sleep(1)" in patch
    assert "+    await asyncio.sleep(0.01)" in patch


def test_stream_generator_case1():
    gen = generate_optimization_events("case1")
    coro = gen.asend(None)
    try:
        first_event = coro.send(None)
    except StopIteration as e:
        first_event = e.value

    assert first_event is not None
    assert first_event.startswith("data: ")
    data = json.loads(first_event.replace("data: ", "").strip())
    assert data["stage"] == "init"
    assert data["progress"] == 10


def test_stream_generator_custom_code():
    custom = "def my_func():\n    time.sleep(0.2)\n    return {'status': 'done'}"
    gen = generate_optimization_events("custom", custom_code=custom)
    coro = gen.asend(None)
    try:
        first_event = coro.send(None)
    except StopIteration as e:
        first_event = e.value

    assert first_event is not None
    data = json.loads(first_event.replace("data: ", "").strip())
    assert data["stage"] == "init"
    assert "Custom Code Snippet" in data["message"]
