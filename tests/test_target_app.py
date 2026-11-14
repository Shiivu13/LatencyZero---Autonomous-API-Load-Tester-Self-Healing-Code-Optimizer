import asyncio
import time

from target_app.app import health, slow_heavy_json, slow_n_plus_one, slow_sync_blocking
from target_app.database import ORDERS_PER_USER, init_db


def setup_module():
    init_db()


def test_health():
    assert health() == {"status": "ok", "app": "slow_target_app"}


def test_n_plus_one_response():
    res = slow_n_plus_one()
    assert res["count"] == 30
    assert all(len(u["orders"]) == ORDERS_PER_USER for u in res["users"])


def test_sync_blocking_takes_at_least_the_sleep():
    start = time.perf_counter()
    res = asyncio.run(slow_sync_blocking())
    assert time.perf_counter() - start >= 0.24
    assert res["case"] == "sync_blocking"


def test_heavy_json_deduplicates():
    res = slow_heavy_json()
    assert res["total_unique"] == 1000
    assert len(res["sample"]) == 5
