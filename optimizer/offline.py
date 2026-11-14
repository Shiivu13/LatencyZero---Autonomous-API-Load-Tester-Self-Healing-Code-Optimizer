"""Optimizer that works without an LLM.

Used when no API key is configured or the API call fails. It has two parts:

* ``find_issues`` runs a few AST checks for common latency problems.
* ``propose`` returns a rewrite: a hand-written fix for the two bundled demo
  endpoints that can't be fixed mechanically, otherwise the rewrites we can
  apply safely (blocking calls inside ``async def``). If nothing applies the
  code is returned unchanged and the caller reports that.
"""

import ast
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from optimizer.client import OptimizationProposal

BLOCKING_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "request"}


@dataclass
class Issue:
    kind: str
    line: int
    message: str


def _dotted_name(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _walk_same_scope(node: ast.AST):
    """ast.walk that doesn't descend into nested functions, lambdas or classes."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        stack.extend(ast.iter_child_nodes(child))


def _is_blocking_call(call: ast.Call) -> Optional[str]:
    name = _dotted_name(call.func)
    if name == "time.sleep":
        return name
    if name.startswith("requests.") and name.split(".", 1)[1] in BLOCKING_HTTP_METHODS:
        return name
    if name in ("urllib.request.urlopen", "subprocess.run", "subprocess.check_output"):
        return name
    return None


def _list_names(func: ast.AST) -> set:
    """Names assigned an empty list or list literal inside the function."""
    names = set()
    for node in _walk_same_scope(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def find_issues(code: str) -> List[Issue]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [Issue("syntax_error", exc.lineno or 0, f"Syntax error: {exc.msg}")]

    issues: List[Issue] = []
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    for func in funcs:
        is_async = isinstance(func, ast.AsyncFunctionDef)
        list_names = _list_names(func)

        for node in _walk_same_scope(func):
            if isinstance(node, ast.Call):
                blocking = _is_blocking_call(node)
                if blocking and is_async:
                    issues.append(Issue(
                        "blocking_call_in_async",
                        node.lineno,
                        f"`{blocking}()` blocks the event loop inside `async def {func.name}`; "
                        "every other request waits until it returns.",
                    ))
                elif blocking:
                    issues.append(Issue(
                        "blocking_call",
                        node.lineno,
                        f"`{blocking}()` holds a worker thread for the whole call in `{func.name}`.",
                    ))

            if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                for inner in _walk_same_scope(node):
                    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                        if inner.func.attr == "execute":
                            issues.append(Issue(
                                "query_in_loop",
                                inner.lineno,
                                "Database query inside a loop (N+1 pattern): one round trip per item.",
                            ))
                    if isinstance(inner, ast.Compare):
                        for op, comp in zip(inner.ops, inner.comparators):
                            if isinstance(op, (ast.In, ast.NotIn)) and isinstance(comp, ast.Name) and comp.id in list_names:
                                issues.append(Issue(
                                    "list_membership_in_loop",
                                    inner.lineno,
                                    f"`in {comp.id}` scans a list on every iteration, which is O(n^2) overall.",
                                ))

    # Nested loops can report the same line twice.
    seen = set()
    unique = []
    for issue in sorted(issues, key=lambda i: i.line):
        key = (issue.kind, issue.line)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


# Hand-written fixes for the bundled demo endpoints, keyed by (function, route).
# A query-in-loop or O(n^2) scan can't be rewritten mechanically in general,
# so offline mode ships the fix a reviewer would write for these two.
REFERENCE_FIXES: Dict[Tuple[str, str], Dict[str, str]] = {
    ("slow_n_plus_one", "/api/slow/n-plus-one"): {
        "diagnosis": "One query per user to load orders (N+1), on a column with no index.",
        "strategy": "Load all orders for the selected users with a single IN (...) query and group them in a dict.",
        "code": '''\
@app.get("/api/slow/n-plus-one")
def slow_n_plus_one():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id, name, email FROM users LIMIT 30")
    users = [dict(u) for u in cur.fetchall()]

    orders_by_user = {u["id"]: [] for u in users}
    if users:
        placeholders = ",".join("?" * len(users))
        cur.execute(
            f"SELECT id, user_id, amount, status FROM orders WHERE user_id IN ({placeholders})",
            list(orders_by_user),
        )
        for row in cur.fetchall():
            orders_by_user[row["user_id"]].append(
                {"id": row["id"], "amount": row["amount"], "status": row["status"]}
            )

    for u in users:
        u["orders"] = orders_by_user[u["id"]]

    conn.close()
    return {"count": len(users), "users": users}''',
    },
    ("slow_heavy_json", "/api/slow/heavy-json"): {
        "diagnosis": "De-duplication checks `record not in unique_records`, a list scan per record (O(n^2)).",
        "strategy": "Track seen records in a set of tuples so each check is O(1); output order is unchanged.",
        "code": '''\
@app.get("/api/slow/heavy-json")
def slow_heavy_json():
    raw_metrics = [
        {"id": i % 100, "metric": f"metric_{i % 50}", "score": (i * 19) % 1000}
        for i in range(2500)
    ]

    seen = set()
    unique_records = []
    for record in raw_metrics:
        key = (record["id"], record["metric"], record["score"])
        if key not in seen:
            seen.add(key)
            unique_records.append(record)

    return {
        "status": "success",
        "total_unique": len(unique_records),
        "sample": unique_records[:5]
    }''',
    },
}


def _first_function(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


def _route_of(func) -> Optional[str]:
    for dec in func.decorator_list:
        if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant):
            return str(dec.args[0].value)
    return None


def _rewrite_blocking_calls(code: str, tree: ast.AST) -> Tuple[str, List[str]]:
    """Rewrite blocking calls inside async functions. Returns (new_code, notes)."""
    src = code.encode("utf-8")
    line_starts = [0]
    for line in code.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line.encode("utf-8")))

    def offset(lineno: int, col: int) -> int:
        return line_starts[lineno - 1] + col

    edits = []
    notes = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.AsyncFunctionDef):
            continue
        for node in _walk_same_scope(func):
            if not isinstance(node, ast.Call):
                continue
            name = _is_blocking_call(node)
            if not name:
                continue

            args = [ast.get_source_segment(code, a) for a in node.args]
            args += [ast.get_source_segment(code, k) if k.arg is None else f"{k.arg}={ast.get_source_segment(code, k.value)}"
                     for k in node.keywords]

            if name == "time.sleep":
                replacement = f"await asyncio.sleep({', '.join(args)})"
                notes.append("time.sleep() -> await asyncio.sleep()")
            else:
                replacement = f"await asyncio.to_thread({', '.join([name] + args)})"
                notes.append(f"{name}() -> await asyncio.to_thread({name}, ...)")

            start = offset(node.lineno, node.col_offset)
            end = offset(node.end_lineno, node.end_col_offset)
            edits.append((start, end, replacement.encode("utf-8")))

    for start, end, replacement in sorted(edits, reverse=True):
        src = src[:start] + replacement + src[end:]

    new_code = src.decode("utf-8")
    if edits and "import asyncio" not in new_code:
        new_code = "import asyncio\n" + new_code
    return new_code, notes


def propose(code: str) -> OptimizationProposal:
    tree = ast.parse(code)
    func = _first_function(tree)
    func_name = func.name if func else ""

    if func is not None:
        fix = REFERENCE_FIXES.get((func.name, _route_of(func) or ""))
        if fix:
            return OptimizationProposal(
                bottleneck_diagnosis=fix["diagnosis"],
                optimization_strategy=fix["strategy"],
                optimized_code=fix["code"],
                function_name=func.name,
                source="offline:reference-fix",
            )

    issues = [i for i in find_issues(code) if i.kind != "syntax_error"]
    new_code, notes = _rewrite_blocking_calls(code, tree)

    if notes:
        return OptimizationProposal(
            bottleneck_diagnosis=" ".join(i.message for i in issues if i.kind == "blocking_call_in_async"),
            optimization_strategy="Moved blocking work off the event loop: " + "; ".join(dict.fromkeys(notes)) + ".",
            optimized_code=new_code,
            function_name=func_name,
            source="offline:rewrite",
        )

    diagnosis = " ".join(i.message for i in issues) or "No known latency pattern found."
    return OptimizationProposal(
        bottleneck_diagnosis=diagnosis,
        optimization_strategy="No automatic rewrite available for this code without an LLM.",
        optimized_code=code,
        function_name=func_name,
        source="offline:none",
    )
