"""Compile, dry-run and mount candidate endpoint code.

This is *not* a security sandbox: the code runs with full builtins in the
server process. Only feed it code generated for the bundled demo endpoints,
never code submitted by users.
"""

import ast
import asyncio
import concurrent.futures
import inspect
import sqlite3
import time
import traceback
from typing import Any, Callable, Dict, Optional, Tuple

import httpx
import requests
from fastapi import FastAPI

from target_app.database import get_db


class DynamicSandbox:
    def __init__(self, target_app: Optional[FastAPI] = None, dry_run_timeout: float = 10.0):
        if target_app is None:
            from target_app.app import app as target_app
        self.app = target_app
        self.dry_run_timeout = dry_run_timeout

    def build_namespace(self) -> Dict[str, Any]:
        return {
            "__name__": "latencyzero_sandbox",
            "get_db": get_db,
            "asyncio": asyncio,
            "httpx": httpx,
            "requests": requests,
            "sqlite3": sqlite3,
            "time": time,
        }

    @staticmethod
    def strip_decorators(code: str) -> str:
        """Drop decorators from top-level functions.

        Executing ``@app.get(...)`` would register a second route on the live app;
        the sandbox mounts the function itself under /api/sandbox/ instead.
        """
        tree = ast.parse(code)
        lines = code.splitlines()
        drop = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
                first = min(d.lineno for d in node.decorator_list)
                drop.update(range(first, node.lineno))
        return "\n".join(line for i, line in enumerate(lines, start=1) if i not in drop)

    def compile_function(
        self,
        code_string: str,
        function_name: Optional[str] = None,
    ) -> Tuple[bool, Optional[Callable[..., Any]], Optional[str]]:
        """Returns (ok, function, error_traceback)."""
        try:
            code = self.strip_decorators(code_string)
            compiled = compile(code, "<candidate>", "exec")
        except SyntaxError:
            return False, None, traceback.format_exc(limit=0)

        namespace = self.build_namespace()
        try:
            exec(compiled, namespace)
        except Exception:
            return False, None, traceback.format_exc()

        func = namespace.get(function_name) if function_name else None
        if not callable(func):
            defined = [
                v for v in namespace.values()
                if inspect.isfunction(v) and v.__module__ == "latencyzero_sandbox"
            ]
            func = defined[0] if defined else None

        if func is None:
            return False, None, "No function definition found in the candidate code."
        return True, func, None

    def dry_run(self, func: Callable[..., Any]) -> Tuple[bool, Any, Optional[str]]:
        """Call the function once, in a worker thread, and return (ok, result, error)."""
        kwargs = {
            name: None
            for name, param in inspect.signature(func).parameters.items()
            if param.default is inspect.Parameter.empty
        }

        def call():
            if inspect.iscoroutinefunction(func):
                return asyncio.run(func(**kwargs))
            return func(**kwargs)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(call)
            try:
                return True, future.result(timeout=self.dry_run_timeout), None
            except concurrent.futures.TimeoutError:
                return False, None, f"Dry run did not finish within {self.dry_run_timeout}s."
            except Exception:
                return False, None, traceback.format_exc()

    def mount_sandbox_route(
        self,
        func: Callable[..., Any],
        sandbox_path: Optional[str] = None,
        http_method: str = "GET",
    ) -> str:
        """Register ``func`` on the app, replacing any route already at that path."""
        name = getattr(func, "__name__", "") or "candidate"
        path = sandbox_path or f"/api/sandbox/{name}"

        self.app.router.routes = [
            r for r in self.app.router.routes
            if getattr(r, "path", None) != path
        ]
        self.app.add_api_route(path, endpoint=func, methods=[http_method])
        return path
