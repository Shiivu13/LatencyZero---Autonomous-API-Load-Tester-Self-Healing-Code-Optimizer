import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from target_app.database import get_db, init_db
from dashboard.stream import generate_optimization_events

DASHBOARD_HTML = os.path.join(os.path.dirname(__file__), "..", "dashboard", "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="LatencyZero - Slow Target App & Autonomous Optimizer",
    description="Intentional performance bottlenecks with autonomous self-healing optimization dashboard",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def dashboard():
    """Serves the 3-panel LatencyZero live optimization dashboard."""
    return FileResponse(DASHBOARD_HTML)


@app.get("/api/stream/optimize")
async def stream_optimize(case: str = "case1"):
    """Streams live autonomous optimization progress via Server-Sent Events (SSE)."""
    return StreamingResponse(
        generate_optimization_events(case),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.get("/health")
def health():
    return {"status": "ok", "app": "slow_target_app"}


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
