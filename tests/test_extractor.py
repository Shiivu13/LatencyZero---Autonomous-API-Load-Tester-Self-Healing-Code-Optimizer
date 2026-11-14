import os
from target_app.app import slow_n_plus_one
from analyzer.extractor import extract_from_function, extract_from_file, extract_all_endpoints


APP_PATH = os.path.join(os.path.dirname(__file__), "..", "target_app", "app.py")


def test_extract_from_function():
    snippet = extract_from_function(slow_n_plus_one)
    assert snippet.func_name == "slow_n_plus_one"
    assert snippet.route_path == "/api/slow/n-plus-one"
    assert snippet.http_method == "GET"
    assert not snippet.is_async
    assert 'SELECT id, name, email FROM users' in snippet.code_string


def test_extract_case1_from_file():
    snippet = extract_from_file(APP_PATH, route_path="/api/slow/n-plus-one")
    assert snippet is not None
    assert snippet.func_name == "slow_n_plus_one"
    assert snippet.route_path == "/api/slow/n-plus-one"
    assert not snippet.is_async
    assert snippet.start_line < snippet.end_line
    assert "@app.get(\"/api/slow/n-plus-one\")" in snippet.code_string


def test_extract_case2_from_file():
    snippet = extract_from_file(APP_PATH, route_path="/api/slow/sync-blocking")
    assert snippet is not None
    assert snippet.func_name == "slow_sync_blocking"
    assert snippet.is_async
    assert "time.sleep(0.25)" in snippet.code_string


def test_extract_case3_from_file():
    snippet = extract_from_file(APP_PATH, route_path="/api/slow/heavy-json")
    assert snippet is not None
    assert snippet.func_name == "slow_heavy_json"
    assert not snippet.is_async
    assert "unique_records" in snippet.code_string


def test_extract_all_endpoints():
    all_endpoints = extract_all_endpoints(APP_PATH)
    routes = [ep.route_path for ep in all_endpoints]
    assert "/health" in routes
    assert "/api/slow/n-plus-one" in routes
    assert "/api/slow/sync-blocking" in routes
    assert "/api/slow/heavy-json" in routes

