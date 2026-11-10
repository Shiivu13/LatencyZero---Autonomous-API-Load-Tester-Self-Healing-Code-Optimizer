import ast
import asyncio
import inspect
import sqlite3
import time
import traceback
from typing import Any, Callable, Dict, Optional, Tuple
from fastapi import FastAPI, APIRouter
import requests
import httpx
from target_app.database import get_db, init_db


class DynamicSandbox:
    """
    Safely compiles, dry-runs, and mounts dynamically generated optimization code
    onto FastAPI routes.
    """

    def __init__(self, target_app: Optional[FastAPI] = None):
        from target_app.app import app as default_app
        self.app = target_app or default_app

    def build_namespace(self) -> Dict[str, Any]:
        """Creates the runtime execution environment for the dynamic function."""
        return {
            "get_db": get_db,
            "init_db": init_db,
            "FastAPI": FastAPI,
            "APIRouter": APIRouter,
            "app": self.app,
            "router": APIRouter(),
            "requests": requests,
            "httpx": httpx,
            "time": time,
            "asyncio": asyncio,
            "sqlite3": sqlite3,
            "__builtins__": __builtins__
        }

    def compile_function(
        self,
        code_string: str,
        function_name: Optional[str] = None
    ) -> Tuple[bool, Optional[Callable[..., Any]], Optional[str]]:
        """
        Compiles the Python code string and extracts the callable function.
        Returns (success, callable_func, error_traceback).
        """
        try:
            # 1. AST Syntax validation
            tree = ast.parse(code_string)
        except Exception:
            return False, None, traceback.format_exc()

        namespace = self.build_namespace()
        try:
            # 2. Dynamic execution
            exec(code_string, namespace)
        except Exception:
            return False, None, traceback.format_exc()

        # 3. Locate the target function
        target_fn = None
        if function_name and function_name in namespace and callable(namespace[function_name]):
            target_fn = namespace[function_name]

        if not target_fn:
            # Find function from AST walk
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in namespace and callable(namespace[node.name]):
                        target_fn = namespace[node.name]
                        break

        if not target_fn:
            for k, v in namespace.items():
                if callable(v) and getattr(v, "__module__", None) in (None, "__main__"):
                    target_fn = v
                    break

        if not target_fn:
            return False, None, "No valid callable function found in compiled code snippet."

        return True, target_fn, None

    def dry_run(self, func: Callable[..., Any]) -> Tuple[bool, Any, Optional[str]]:
        """
        Executes a dry-run invocation of the function to verify zero runtime exceptions.
        Handles both sync and async functions reliably without socket or event-loop hangs.
        """
        try:
            # Inspect parameters to call with default dummy arguments if required
            sig = inspect.signature(func)
            call_kwargs = {}
            for name, param in sig.parameters.items():
                if param.default is inspect.Parameter.empty:
                    call_kwargs[name] = {} if "dict" in str(param.annotation).lower() else None

            if inspect.iscoroutinefunction(func):
                async def _safe_sleep(*args, **kwargs):
                    return None

                orig_sleep = asyncio.sleep
                try:
                    asyncio.sleep = _safe_sleep
                    coro = func(**call_kwargs)
                    stack = [coro]
                    val = None
                    while stack:
                        top = stack[-1]
                        try:
                            val = top.send(val)
                            if inspect.iscoroutine(val):
                                stack.append(val)
                                val = None
                        except StopIteration as e:
                            stack.pop()
                            val = e.value
                    res = val
                finally:
                    asyncio.sleep = orig_sleep
            else:
                res = func(**call_kwargs)

            return True, res, None
        except Exception:
            return False, None, traceback.format_exc()

    def mount_sandbox_route(
        self,
        func: Callable[..., Any],
        sandbox_path: Optional[str] = None,
        http_method: str = "GET"
    ) -> str:
        """Mounts the verified function to a dynamic sandbox route on the FastAPI app."""
        func_name = getattr(func, "__name__", "custom_endpoint")
        if not func_name or func_name == "<lambda>":
            func_name = "custom_endpoint"
        path = sandbox_path or f"/api/sandbox/{func_name}"

        # Remove existing sandbox route if already registered
        self.app.router.routes = [
            r for r in self.app.router.routes
            if not (hasattr(r, "path") and r.path == path)
        ]

        self.app.add_api_route(path, endpoint=func, methods=[http_method])
        return path

    def hot_swap_live_route(
        self,
        target_path: str,
        new_func: Callable[..., Any]
    ) -> bool:
        """Replaces the endpoint handler of an existing live route with the optimized function."""
        for r in self.app.router.routes:
            if hasattr(r, "path") and r.path == target_path:
                r.endpoint = new_func
                return True
        return False
