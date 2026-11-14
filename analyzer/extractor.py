import ast
import inspect
from dataclasses import dataclass
from typing import Any, Callable, List, Optional


@dataclass
class EndpointCodeSnippet:
    func_name: str
    is_async: bool
    route_path: Optional[str]
    http_method: Optional[str]
    start_line: int
    end_line: int
    code_string: str
    file_path: Optional[str] = None


def _get_route_info(decorator_node: ast.expr) -> tuple[Optional[str], Optional[str]]:
    """Extracts HTTP method and route path from decorator AST node like @app.get('/path')."""
    if not isinstance(decorator_node, ast.Call):
        return None, None

    method = None
    if isinstance(decorator_node.func, ast.Attribute):
        method = decorator_node.func.attr.upper()

    path = None
    if decorator_node.args and isinstance(decorator_node.args[0], ast.Constant):
        path = str(decorator_node.args[0].value)
    else:
        for kw in decorator_node.keywords:
            if kw.arg in ("path", "prefix") and isinstance(kw.value, ast.Constant):
                path = str(kw.value.value)
                break

    return method, path


def extract_from_function(func: Callable[..., Any]) -> EndpointCodeSnippet:
    """Extracts the exact code string and metadata from a Python function using `inspect`."""
    source = inspect.getsource(func)
    lines, start_line = inspect.getsourcelines(func)
    end_line = start_line + len(lines) - 1
    func_name = getattr(func, "__name__", "unknown")
    is_async = inspect.iscoroutinefunction(func)

    # Parse with AST to identify decorators
    tree = ast.parse(source)
    route_path = None
    http_method = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                m, p = _get_route_info(dec)
                if p:
                    http_method, route_path = m, p
                    break
            break

    file_path = None
    try:
        file_path = inspect.getsourcefile(func)
    except Exception:
        pass

    return EndpointCodeSnippet(
        func_name=func_name,
        is_async=is_async,
        route_path=route_path,
        http_method=http_method,
        start_line=start_line,
        end_line=end_line,
        code_string=source.strip(),
        file_path=file_path
    )


def extract_from_file(
    file_path: str,
    route_path: Optional[str] = None,
    func_name: Optional[str] = None
) -> Optional[EndpointCodeSnippet]:
    """
    Parses a python file with AST and extracts the exact code string matching
    either `route_path` (e.g. '/api/slow/n-plus-one') or `func_name`.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        full_source = f.read()

    tree = ast.parse(full_source, filename=file_path)
    lines = full_source.splitlines()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current_name = node.name
            current_method, current_path = None, None

            for dec in node.decorator_list:
                m, p = _get_route_info(dec)
                if p:
                    current_method, current_path = m, p
                    break

            matched = False
            if route_path and current_path == route_path:
                matched = True
            elif func_name and current_name == func_name:
                matched = True

            if matched:
                start_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
                end_line = node.end_lineno
                code_snippet = "\n".join(lines[start_line - 1 : end_line])

                return EndpointCodeSnippet(
                    func_name=current_name,
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    route_path=current_path,
                    http_method=current_method,
                    start_line=start_line,
                    end_line=end_line,
                    code_string=code_snippet,
                    file_path=file_path
                )

    return None


def extract_all_endpoints(file_path: str) -> List[EndpointCodeSnippet]:
    """Finds and extracts all endpoint functions in a given Python file."""
    with open(file_path, "r", encoding="utf-8") as f:
        full_source = f.read()

    tree = ast.parse(full_source, filename=file_path)
    lines = full_source.splitlines()
    endpoints: List[EndpointCodeSnippet] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current_method, current_path = None, None
            for dec in node.decorator_list:
                m, p = _get_route_info(dec)
                if p:
                    current_method, current_path = m, p
                    break

            # Only include actual HTTP route endpoints
            if current_path:
                start_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno
                end_line = node.end_lineno
                code_snippet = "\n".join(lines[start_line - 1 : end_line])

                endpoints.append(EndpointCodeSnippet(
                    func_name=node.name,
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    route_path=current_path,
                    http_method=current_method,
                    start_line=start_line,
                    end_line=end_line,
                    code_string=code_snippet,
                    file_path=file_path
                ))

    return endpoints
