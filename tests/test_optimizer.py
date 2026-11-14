import os

import pytest
from fastapi import FastAPI

from analyzer.extractor import EndpointCodeSnippet, extract_from_file
from optimizer.client import (
    BharatCodeOptimizerClient,
    OptimizationProposal,
    OptimizerError,
    parse_llm_json,
)
from optimizer.sandbox import DynamicSandbox
from optimizer.self_healing import SelfHealingLoop
import target_app.app as target

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "target_app", "app.py")


def snippet_for(route):
    snippet = extract_from_file(APP_PATH, route_path=route)
    assert snippet is not None
    return snippet


class ScriptedClient(BharatCodeOptimizerClient):
    """Returns pre-written proposals in order, recording the feedback it gets."""

    def __init__(self, codes):
        super().__init__(api_key="test-key")
        self.codes = list(codes)
        self.errors = []

    def optimize_endpoint(self, code_snippet, baseline_metrics=None, previous_error=None):
        self.errors.append(previous_error)
        return OptimizationProposal("diag", "strategy", self.codes.pop(0), "", source="llm:test")


def test_sandbox_compile_and_dry_run():
    sandbox = DynamicSandbox(target_app=FastAPI())
    ok, func, err = sandbox.compile_function("def f():\n    return {'value': 123}\n", "f")
    assert ok and err is None

    ok, result, err = sandbox.dry_run(func)
    assert ok and result == {"value": 123}


def test_sandbox_runs_async_functions():
    sandbox = DynamicSandbox(target_app=FastAPI())
    ok, func, _ = sandbox.compile_function("async def f():\n    await asyncio.sleep(0)\n    return 1\n")
    assert ok
    assert sandbox.dry_run(func) == (True, 1, None)


def test_sandbox_syntax_error():
    sandbox = DynamicSandbox(target_app=FastAPI())
    ok, func, err = sandbox.compile_function("def broken(:\n    return 42")
    assert not ok and func is None
    assert "SyntaxError" in err


def test_sandbox_runtime_error():
    sandbox = DynamicSandbox(target_app=FastAPI())
    _, func, _ = sandbox.compile_function("def f():\n    return 1 / 0\n")
    ok, _, err = sandbox.dry_run(func)
    assert not ok
    assert "ZeroDivisionError" in err


def test_sandbox_does_not_register_decorated_route():
    app = FastAPI()
    sandbox = DynamicSandbox(target_app=app)
    routes_before = len(app.routes)
    ok, _, _ = sandbox.compile_function('@app.get("/x")\ndef f():\n    return 1\n')
    assert ok
    assert len(app.routes) == routes_before


def test_sandbox_mount_replaces_existing_route():
    app = FastAPI()
    sandbox = DynamicSandbox(target_app=app)

    def handler():
        return {}

    sandbox.mount_sandbox_route(handler, "/api/sandbox/x")
    sandbox.mount_sandbox_route(handler, "/api/sandbox/x")
    assert [r.path for r in app.routes].count("/api/sandbox/x") == 1


@pytest.mark.parametrize("route,func_name", [
    ("/api/slow/n-plus-one", "slow_n_plus_one"),
    ("/api/slow/sync-blocking", "slow_sync_blocking"),
    ("/api/slow/heavy-json", "slow_heavy_json"),
])
def test_offline_fix_matches_original_response(route, func_name):
    loop = SelfHealingLoop()
    result = loop.optimize_and_heal(snippet_for(route), reference_function=getattr(target, func_name))

    assert result.status == "success"
    assert result.source.startswith("offline:")
    assert result.verified_function is not None
    assert result.optimized_code != result.original_code


def test_heals_after_runtime_error():
    client = ScriptedClient([
        "def f():\n    return 10 / 0\n",
        "def f():\n    return {'answer': 42}\n",
    ])
    loop = SelfHealingLoop(client=client)
    result = loop.optimize_and_heal(snippet_for("/api/slow/heavy-json"))

    assert result.status == "healed"
    assert result.attempts_count == 2
    assert result.attempts_history[0].success is False
    assert "ZeroDivisionError" in client.errors[1]


def test_rejects_rewrite_with_different_response():
    client = ScriptedClient(["def f():\n    return {'status': 'different'}\n"] * 3)
    loop = SelfHealingLoop(client=client, max_retries=3)
    result = loop.optimize_and_heal(
        snippet_for("/api/slow/heavy-json"),
        reference_function=target.slow_heavy_json,
    )

    assert result.status == "failed"
    assert result.verified_function is None
    assert result.attempts_count == 3
    assert "different response" in client.errors[1]


def test_falls_back_to_offline_when_llm_errors():
    class BrokenClient(BharatCodeOptimizerClient):
        def optimize_endpoint(self, *args, **kwargs):
            raise OptimizerError("HTTP 402")

    loop = SelfHealingLoop(client=BrokenClient(api_key="k"))
    result = loop.optimize_and_heal(snippet_for("/api/slow/sync-blocking"))

    assert result.status == "success"
    assert result.source == "offline:rewrite"
    assert any("HTTP 402" in note for note in result.notes)


def test_unchanged_when_nothing_to_fix():
    snippet = EndpointCodeSnippet(
        func_name="fast", is_async=False, route_path="/fast", http_method="GET",
        start_line=1, end_line=2, code_string="def fast():\n    return {'ok': True}",
    )
    result = SelfHealingLoop().optimize_and_heal(snippet)
    assert result.status == "unchanged"
    assert not result.ok


def test_parse_llm_json_with_fence():
    raw = '```json\n{"optimized_code": "def f():\\n    return 1", "function_name": "f"}\n```'
    proposal = parse_llm_json(raw, source="llm:test")
    assert proposal.optimized_code == "def f():\n    return 1"
    assert proposal.function_name == "f"


def test_parse_llm_json_rejects_garbage():
    with pytest.raises(OptimizerError):
        parse_llm_json("sorry, I can't help", source="llm:test")


def test_client_without_key_is_unavailable():
    client = BharatCodeOptimizerClient(api_key="")
    assert not client.available
    with pytest.raises(OptimizerError):
        client.optimize_endpoint("def f(): pass")
