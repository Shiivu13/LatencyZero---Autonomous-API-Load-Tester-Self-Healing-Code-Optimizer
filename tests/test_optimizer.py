import os
import pytest
from fastapi import FastAPI
from analyzer.extractor import extract_from_file
from optimizer.client import BharatCodeOptimizerClient, OptimizationProposal
from optimizer.sandbox import DynamicSandbox
from optimizer.self_healing import SelfHealingLoop


APP_PATH = os.path.join(os.path.dirname(__file__), "..", "target_app", "app.py")


def test_sandbox_compile_and_dry_run_success():
    sandbox = DynamicSandbox()
    code = (
        'def test_fn():\n'
        '    return {"status": "ok", "value": 123}\n'
    )
    ok, func, err = sandbox.compile_function(code, "test_fn")
    assert ok is True
    assert err is None
    assert callable(func)

    run_ok, res, run_err = sandbox.dry_run(func)
    assert run_ok is True
    assert run_err is None
    assert res == {"status": "ok", "value": 123}


def test_sandbox_compile_syntax_error():
    sandbox = DynamicSandbox()
    bad_code = 'def broken_syntax(:\n    return 42'
    ok, func, err = sandbox.compile_function(bad_code)
    assert ok is False
    assert func is None
    assert "SyntaxError" in err


def test_sandbox_dry_run_runtime_error():
    sandbox = DynamicSandbox()
    runtime_fail_code = (
        'def failing_fn():\n'
        '    return 1 / 0\n'
    )
    ok, func, err = sandbox.compile_function(runtime_fail_code, "failing_fn")
    assert ok is True
    run_ok, res, run_err = sandbox.dry_run(func)
    assert run_ok is False
    assert res is None
    assert "ZeroDivisionError" in run_err


def test_sandbox_mount_route():
    app = FastAPI()
    sandbox = DynamicSandbox(target_app=app)
    def my_custom_ep():
        return {"custom": True}

    mounted_path = sandbox.mount_sandbox_route(my_custom_ep, "/api/sandbox/test-ep")
    assert mounted_path == "/api/sandbox/test-ep"
    registered_paths = [r.path for r in app.routes if hasattr(r, "path")]
    assert "/api/sandbox/test-ep" in registered_paths


def test_self_healing_case1_n_plus_one():
    snippet = extract_from_file(APP_PATH, route_path="/api/slow/n-plus-one")
    assert snippet is not None

    loop = SelfHealingLoop()
    result = loop.optimize_and_heal(
        endpoint_snippet=snippet,
        baseline_metrics={"p50_ms": 25.0, "p95_ms": 45.0, "rps": 35.0},
        mount_to_sandbox=True
    )

    assert result.status in ("success", "healed")
    assert result.sandbox_route == "/api/sandbox/slow_n_plus_one"
    assert result.mounted_function is not None
    assert "N+1" in result.bottleneck_diagnosis
    assert result.attempts_count >= 1


def test_self_healing_case2_sync_blocking():
    snippet = extract_from_file(APP_PATH, route_path="/api/slow/sync-blocking")
    assert snippet is not None

    loop = SelfHealingLoop()
    result = loop.optimize_and_heal(
        endpoint_snippet=snippet,
        baseline_metrics={"p50_ms": 250.0, "p95_ms": 252.0, "rps": 3.9},
        mount_to_sandbox=True
    )

    assert result.status in ("success", "healed")
    assert result.sandbox_route == "/api/sandbox/slow_sync_blocking"
    assert "time.sleep" in result.bottleneck_diagnosis or "blocking" in result.bottleneck_diagnosis.lower()


def test_self_healing_case3_heavy_json():
    snippet = extract_from_file(APP_PATH, route_path="/api/slow/heavy-json")
    assert snippet is not None

    loop = SelfHealingLoop()
    result = loop.optimize_and_heal(
        endpoint_snippet=snippet,
        baseline_metrics={"p50_ms": 22.0, "p95_ms": 30.0, "rps": 40.0},
        mount_to_sandbox=True
    )

    assert result.status in ("success", "healed")
    assert result.sandbox_route == "/api/sandbox/slow_heavy_json"
    assert "O(N^2)" in result.bottleneck_diagnosis or "quadratic" in result.bottleneck_diagnosis.lower()


def test_self_healing_auto_heal_recovery():
    """Simulates an initial buggy code suggestion from LLM that triggers error, then heals on attempt 2."""
    class MockFlakyClient(BharatCodeOptimizerClient):
        def __init__(self):
            super().__init__()
            self.call_count = 0

        def optimize_endpoint(self, code_snippet, baseline_metrics, previous_error=None, timeout=30.0):
            self.call_count += 1
            if self.call_count == 1:
                # Deliberate runtime bug in first attempt
                buggy_code = (
                    '@app.get("/api/test/flaky")\n'
                    'def flaky():\n'
                    '    return 10 / 0\n'
                )
                return OptimizationProposal(
                    bottleneck_diagnosis="Buggy attempt",
                    optimization_strategy="Introduce zero division error to trigger self-healing",
                    optimized_code=buggy_code,
                    function_name="flaky",
                    model_used="mock"
                )
            else:
                # Fixed/healed code in second attempt
                fixed_code = (
                    '@app.get("/api/test/flaky")\n'
                    'def flaky():\n'
                    '    return {"status": "healed", "answer": 42}\n'
                )
                return OptimizationProposal(
                    bottleneck_diagnosis="Healed zero division bug",
                    optimization_strategy="Corrected denominator logic",
                    optimized_code=fixed_code,
                    function_name="flaky",
                    model_used="mock"
                )

    snippet = extract_from_file(APP_PATH, route_path="/api/slow/heavy-json")
    flaky_client = MockFlakyClient()
    loop = SelfHealingLoop(client=flaky_client)

    result = loop.optimize_and_heal(snippet, {"p50_ms": 10.0})
    assert result.status == "healed"
    assert result.attempts_count == 2
    assert len(result.attempts_history) == 2
    assert result.attempts_history[0].success is False
    assert "ZeroDivisionError" in result.attempts_history[0].error_traceback
    assert result.attempts_history[1].success is True
