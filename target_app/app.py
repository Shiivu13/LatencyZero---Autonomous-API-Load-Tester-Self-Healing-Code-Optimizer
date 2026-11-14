import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from dashboard.stream import generate_optimization_events
from target_app.database import get_db, init_db

DASHBOARD_HTML = os.path.join(os.path.dirname(__file__), "..", "dashboard", "index.html")
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class OptimizeRequest(BaseModel):
    case: str = "case1"
    custom_code: Optional[str] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="LatencyZero",
    description="Demo app with deliberately slow endpoints, plus the dashboard that benchmarks and rewrites them.",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(DASHBOARD_HTML)


@app.get("/api/stream/optimize")
async def stream_optimize(case: str = "case1"):
    """Run one of the bundled cases and stream progress as Server-Sent Events."""
    return StreamingResponse(
        generate_optimization_events(case),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.post("/api/stream/optimize")
async def post_stream_optimize(req: OptimizeRequest):
    """Same as the GET route; use case="custom" with custom_code to review pasted code."""
    return StreamingResponse(
        generate_optimization_events(req.case, req.custom_code),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@app.get("/health")
def health():
    return {"status": "ok", "app": "slow_target_app"}


# The three endpoints below are slow on purpose. They are the inputs for the
# optimizer; don't fix them here.

# ---------------------------------------------------------------------------
# CASE 1: Unindexed N+1 SQLite Loop
# Bottleneck: 1 query to fetch users + N separate queries inside a Python loop
# without any index on `orders.user_id`. High DB lock contention under concurrency.
# ---------------------------------------------------------------------------
@app.get("/api/slow/n-plus-one")
def slow_n_plus_one():
    conn = get_db()
    cur = conn.cursor()

    # 1. First query: fetch users
    cur.execute("SELECT id, name, email FROM users LIMIT 30")
    users = cur.fetchall()

    results = []
    # 2. N queries inside a Python loop (N+1 Anti-Pattern) on an unindexed column
    for user in users:
        cur.execute(
            "SELECT id, amount, status FROM orders WHERE user_id = ?",
            (user["id"],)
        )
        orders = cur.fetchall()
        results.append({
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "orders": [dict(o) for o in orders]
        })

    conn.close()
    return {"count": len(results), "users": results}


# ---------------------------------------------------------------------------
# CASE 2: Blocking CPU / Sync I/O in Async Route
# Bottleneck: Synchronous `time.sleep()` inside an `async def` route halts the
# entire single-threaded asyncio event loop, causing sequential queue buildup.
# ---------------------------------------------------------------------------
@app.get("/api/slow/sync-blocking")
async def slow_sync_blocking():
    # Anti-Pattern: synchronous blocking call in async event loop
    time.sleep(0.25)
    return {
        "status": "success",
        "message": "Processed payload with blocking sync call",
        "case": "sync_blocking"
    }


# ---------------------------------------------------------------------------
# CASE 3: Redundant Heavy JSON Computation / O(N^2) Filtering
# Bottleneck: O(N^2) linear scan duplicate elimination on 2,500 objects,
# burning CPU cycles on every single HTTP request without memoization or set lookup.
# ---------------------------------------------------------------------------
@app.get("/api/slow/heavy-json")
def slow_heavy_json():
    # Synthetic metric data with duplicates
    raw_metrics = [
        {"id": i % 100, "metric": f"metric_{i % 50}", "score": (i * 19) % 1000}
        for i in range(2500)
    ]

    # Anti-Pattern: O(N^2) list membership check inside loop
    unique_records = []
    for record in raw_metrics:
        if record not in unique_records:  # O(N) scan * N items = O(N^2) CPU burn
            unique_records.append(record)

    return {
        "status": "success",
        "total_unique": len(unique_records),
        "sample": unique_records[:5]
    }
