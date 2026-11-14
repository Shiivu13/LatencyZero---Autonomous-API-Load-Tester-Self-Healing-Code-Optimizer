"""End-to-end runs shared by the dashboard (SSE) and the CLI.

Both functions are async generators that yield plain dict events as each step
finishes. The last event has ``stage == "done"`` and carries the full result.

* ``optimize_case`` - for the bundled slow endpoints: measure, extract, rewrite,
  verify, mount the new version under /api/sandbox/ and measure again.
* ``review_code`` - for code pasted by a user: static checks and a proposed
  rewrite only. User code is never executed on the server.
"""

import ast
import asyncio
import difflib
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Optional

from analyzer.extractor import EndpointCodeSnippet, extract_from_file
from load_tester.tester import LoadTestResult, LocalServer, benchmark_url
from optimizer import offline
from optimizer.client import BharatCodeOptimizerClient, OptimizerError
from optimizer.self_healing import SelfHealingLoop

APP_FILE = os.path.join(os.path.dirname(__file__), "..", "target_app", "app.py")
MAX_CUSTOM_CODE_CHARS = 20_000

_benchmark_lock = asyncio.Lock()


@dataclass(frozen=True)
class Case:
    title: str
    route: str


CASES: Dict[str, Case] = {
    "case1": Case("N+1 queries on an unindexed table", "/api/slow/n-plus-one"),
    "case2": Case("Blocking sleep inside an async route", "/api/slow/sync-blocking"),
    "case3": Case("O(n^2) de-duplication", "/api/slow/heavy-json"),
}


def metrics_dict(result: LoadTestResult) -> Dict[str, Any]:
    return {
        "p50": round(result.p50_ms, 2),
        "p95": round(result.p95_ms, 2),
        "p99": round(result.p99_ms, 2),
        "avg": round(result.avg_ms, 2),
        "rps": round(result.requests_per_second, 1),
        "requests": result.total_requests,
        "failed": result.failed_requests,
        "concurrency": result.concurrency,
    }


def compare(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Optional[float]]:
    def pct_drop(key):
        return round((before[key] - after[key]) / before[key] * 100, 1) if before[key] else None

    return {
        "p50_change_pct": pct_drop("p50"),
        "p95_change_pct": pct_drop("p95"),
        "p50_speedup": round(before["p50"] / after["p50"], 1) if after["p50"] else None,
        "rps_ratio": round(after["rps"] / before["rps"], 1) if before["rps"] else None,
    }


def generate_patch(original_code: str, optimized_code: str, filename: str = "app.py") -> str:
    diff = difflib.unified_diff(
        original_code.splitlines(keepends=True),
        optimized_code.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    lines = []
    for line in diff:
        lines.append(line if line.endswith("\n") else line + "\n")
    return "".join(lines)


def replace_in_source(source: str, snippet: EndpointCodeSnippet, new_code: str) -> str:
    """Put ``new_code`` in place of the snippet's lines in the full file.

    Import lines at the top of ``new_code`` go to the file's import block.
    """
    new_lines = new_code.splitlines()
    imports = []
    while new_lines and new_lines[0].startswith(("import ", "from ")):
        imports.append(new_lines.pop(0))

    lines = source.splitlines(keepends=True)
    body = [line + "\n" for line in new_lines]
    result = lines[:snippet.start_line - 1] + body + lines[snippet.end_line:]

    existing = set(source.splitlines())
    missing = [imp + "\n" for imp in imports if imp not in existing]
    if missing:
        tree = ast.parse(source)
        first_import = next(
            (n.lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))),
            1,
        )
        result[first_import - 1:first_import - 1] = missing
    return "".join(result)


def _event(stage: str, message: str, **extra) -> Dict[str, Any]:
    return {"stage": stage, "message": message, **extra}


async def optimize_case(
    case_id: str,
    requests: int = 20,
    concurrency: int = 20,
    loop: Optional[SelfHealingLoop] = None,
) -> AsyncIterator[Dict[str, Any]]:
    import target_app.app as app_module
    from target_app.database import init_db

    case = CASES.get(case_id)
    if case is None:
        yield _event("error", f"Unknown case '{case_id}'.")
        return

    init_db()
    yield _event("start", f"{case.title} ({case.route})", case=case_id, route=case.route)

    # One benchmark at a time: parallel runs would skew each other's numbers.
    if _benchmark_lock.locked():
        yield _event("note", "Waiting for another run to finish...")
    async with _benchmark_lock:
        server = await asyncio.to_thread(LocalServer(app_module.app).start)
        try:
            async for event in _optimize_with_server(case_id, case, server, app_module, requests, concurrency, loop):
                yield event
        finally:
            await asyncio.to_thread(server.stop)


async def _optimize_with_server(case_id, case, server, app_module, requests, concurrency, loop):
    yield _event("load_test", f"Sending {requests} requests to {case.route} ({concurrency} concurrent)...")
    before_raw = await asyncio.to_thread(benchmark_url, server.base_url + case.route, concurrency, requests)
    before = metrics_dict(before_raw)
    yield _event(
        "baseline",
        f"Before: p50 {before['p50']} ms, p95 {before['p95']} ms, {before['rps']} req/s",
        metrics=before,
    )

    snippet = extract_from_file(APP_FILE, route_path=case.route)
    if snippet is None:
        yield _event("error", f"Could not find the handler for {case.route} in target_app/app.py.")
        return
    yield _event(
        "extract",
        f"Found {snippet.func_name}() at target_app/app.py:{snippet.start_line}-{snippet.end_line}",
    )

    for issue in offline.find_issues(snippet.code_string):
        yield _event("issue", f"line {snippet.start_line + issue.line - 1}: {issue.message}")

    loop = loop or SelfHealingLoop()
    yield _event(
        "optimize",
        f"Requesting a rewrite from {loop.client.model}..." if loop.client.available
        else "No API key configured, using the offline optimizer...",
    )
    reference = getattr(app_module, snippet.func_name, None)
    result = await asyncio.to_thread(loop.optimize_and_heal, snippet, before, reference)

    for note in result.notes:
        yield _event("note", note)
    for attempt in result.attempts_history:
        if attempt.success:
            yield _event("attempt", f"Attempt {attempt.attempt_number}: compiled, ran and returned the same response as the original.")
        elif attempt.error_traceback:
            first_line = attempt.error_traceback.strip().splitlines()[0]
            yield _event("attempt", f"Attempt {attempt.attempt_number} failed: {first_line}", ok=False)

    after = None
    sandbox_route = None
    patch = ""
    if result.ok:
        sandbox_route = loop.sandbox.mount_sandbox_route(
            result.verified_function,
            http_method=snippet.http_method or "GET",
        )
        yield _event("load_test", f"Sending {requests} requests to {sandbox_route} ({concurrency} concurrent)...")
        after_raw = await asyncio.to_thread(benchmark_url, server.base_url + sandbox_route, concurrency, requests)
        after = metrics_dict(after_raw)
        yield _event(
            "after",
            f"After: p50 {after['p50']} ms, p95 {after['p95']} ms, {after['rps']} req/s",
            metrics=after,
        )
        with open(APP_FILE, encoding="utf-8") as f:
            source = f.read()
        patch = generate_patch(source, replace_in_source(source, snippet, result.optimized_code), "target_app/app.py")

    comparison = compare(before, after) if after else None
    if comparison and comparison["p50_speedup"]:
        summary = f"p50 {before['p50']} ms -> {after['p50']} ms ({comparison['p50_speedup']}x)"
    elif result.status == "unchanged":
        summary = "No rewrite was produced."
    else:
        summary = f"No verified rewrite after {result.attempts_count} attempt(s)."

    yield _event(
        "done",
        summary,
        mode="benchmark",
        case=case_id,
        status=result.status,
        source=result.source,
        attempts=result.attempts_count,
        before=before,
        after=after,
        comparison=comparison,
        diagnosis=result.bottleneck_diagnosis,
        strategy=result.optimization_strategy,
        original_code=result.original_code,
        optimized_code=result.optimized_code,
        patch=patch,
        sandbox_route=sandbox_route,
    )


async def review_code(
    code: str,
    client: Optional[BharatCodeOptimizerClient] = None,
) -> AsyncIterator[Dict[str, Any]]:
    code = (code or "").strip()
    if not code:
        yield _event("error", "No code provided.")
        return
    if len(code) > MAX_CUSTOM_CODE_CHARS:
        yield _event("error", f"Code is too long (limit {MAX_CUSTOM_CODE_CHARS} characters).")
        return

    yield _event("start", "Reviewing pasted code (static analysis only, the code is not executed).")

    issues = offline.find_issues(code)
    if any(i.kind == "syntax_error" for i in issues):
        yield _event("error", issues[0].message)
        return
    for issue in issues:
        yield _event("issue", f"line {issue.line}: {issue.message}")
    if not issues:
        yield _event("issue", "No known latency pattern found by the static checks.")

    client = client or BharatCodeOptimizerClient()
    proposal = None
    if client.available:
        yield _event("optimize", f"Requesting a rewrite from {client.model}...")
        try:
            proposal = await asyncio.to_thread(client.optimize_endpoint, code, None, None)
        except OptimizerError as exc:
            yield _event("note", f"LLM unavailable ({exc}); using the offline optimizer.")
    else:
        yield _event("optimize", "No API key configured, using the offline optimizer...")

    if proposal is None:
        proposal = offline.propose(code)

    status = "unchanged" if proposal.optimized_code.strip() == code else "proposed"
    if status == "proposed":
        try:
            compile(proposal.optimized_code, "<proposal>", "exec")
        except SyntaxError as exc:
            yield _event("attempt", f"Proposed code does not compile: {exc.msg} (line {exc.lineno})", ok=False)
            status = "failed"
        else:
            yield _event("attempt", "Proposed code compiles. It was not run or benchmarked.")

    yield _event(
        "done",
        {
            "proposed": "Rewrite proposed (not benchmarked).",
            "unchanged": "No rewrite was produced.",
            "failed": "The proposed rewrite does not compile.",
        }[status],
        mode="review",
        case="custom",
        status=status,
        source=proposal.source,
        attempts=1,
        before=None,
        after=None,
        comparison=None,
        diagnosis=proposal.bottleneck_diagnosis,
        strategy=proposal.optimization_strategy,
        original_code=code,
        optimized_code=proposal.optimized_code,
        patch=generate_patch(code, proposal.optimized_code) if status == "proposed" else "",
        sandbox_route=None,
    )
