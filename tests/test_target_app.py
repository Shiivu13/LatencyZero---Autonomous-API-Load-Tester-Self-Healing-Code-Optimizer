import time
from target_app.database import init_db
from target_app.app import health, slow_n_plus_one, slow_sync_blocking, slow_heavy_json


def run_coro(coro):
    """Executes a coroutine that contains sync operations without blocking loop."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    raise RuntimeError("Coroutine did not complete synchronously")


def test_init_db():
    init_db()


def test_health():
    res = health()
    assert res == {"status": "ok", "app": "slow_target_app"}


def test_case1_n_plus_one():
    start = time.perf_counter()
    res = slow_n_plus_one()
    elapsed = time.perf_counter() - start
    assert res["count"] == 30
    assert len(res["users"][0]["orders"]) == 10
    print(f"\n[Case 1: N+1 DB Loop] Completed in {elapsed*1000:.2f}ms")


def test_case2_sync_blocking():
    start = time.perf_counter()
    res = run_coro(slow_sync_blocking())
    elapsed = time.perf_counter() - start
    assert res["status"] == "success"
    assert res["case"] == "sync_blocking"
    assert elapsed >= 0.24
    print(f"\n[Case 2: Sync Blocking I/O] Completed in {elapsed*1000:.2f}ms")


def test_case3_heavy_json():
    start = time.perf_counter()
    res = slow_heavy_json()
    elapsed = time.perf_counter() - start
    assert res["status"] == "success"
    assert res["total_unique"] == 1000
    print(f"\n[Case 3: O(N^2) JSON Processing] Completed in {elapsed*1000:.2f}ms")
