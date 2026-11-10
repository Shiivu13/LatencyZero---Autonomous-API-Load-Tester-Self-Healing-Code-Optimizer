import json
import pytest
from target_app.app import dashboard
from dashboard.stream import generate_optimization_events, CASE_METRIC_CONFIGS


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


def test_stream_generator_case1():
    gen = generate_optimization_events("case1")
    # Step the async generator synchronously without Windows IOCP event loop
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
