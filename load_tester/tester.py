import argparse
import asyncio
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional
import anyio
import httpx
import uvicorn


@dataclass
class LoadTestResult:
    url: str
    concurrency: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    total_time_seconds: float
    requests_per_second: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    avg_ms: float
    latencies_ms: List[float] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"LoadTest for {self.url}\n"
            f"  Requests: {self.total_requests} (Success: {self.successful_requests}, Failed: {self.failed_requests})\n"
            f"  Concurrency: {self.concurrency}\n"
            f"  Total Duration: {self.total_time_seconds:.2f}s\n"
            f"  Throughput: {self.requests_per_second:.1f} req/sec\n"
            f"  Latency (ms): min={self.min_ms:.1f} | p50={self.p50_ms:.1f} | p95={self.p95_ms:.1f} | p99={self.p99_ms:.1f} | max={self.max_ms:.1f}"
        )


def _calculate_percentile(sorted_list: List[float], percentile: float) -> float:
    """Calculates percentile with linear interpolation."""
    if not sorted_list:
        return 0.0
    if len(sorted_list) == 1:
        return sorted_list[0]

    k = (len(sorted_list) - 1) * (percentile / 100.0)
    floor_k = math.floor(k)
    ceil_k = math.ceil(k)

    if floor_k == ceil_k:
        return sorted_list[int(k)]

    d0 = sorted_list[int(floor_k)] * (ceil_k - k)
    d1 = sorted_list[int(ceil_k)] * (k - floor_k)
    return d0 + d1


async def _worker(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: Optional[Dict[str, str]],
    latencies: List[float],
    failures: List[Exception]
):
    async with sem:
        t0 = time.perf_counter()
        try:
            resp = await client.request(method, url, headers=headers)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code < 400:
                latencies.append(elapsed_ms)
            else:
                failures.append(Exception(f"HTTP {resp.status_code}"))
        except Exception as e:
            failures.append(e)


async def run_load_test(
    url: str,
    concurrency: int = 50,
    total_requests: int = 50,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 30.0
) -> LoadTestResult:
    """Send `total_requests` requests to `url`, at most `concurrency` at a time.

    Latencies are only recorded for successful (< 400) responses.
    """
    sem = asyncio.Semaphore(concurrency)
    latencies: List[float] = []
    failures: List[Exception] = []

    manage_client = client is None
    http_client = client or httpx.AsyncClient(timeout=timeout)

    start_time = time.perf_counter()
    try:
        tasks = [
            _worker(sem, http_client, method, url, headers, latencies, failures)
            for _ in range(total_requests)
        ]
        await asyncio.gather(*tasks)
    finally:
        if manage_client:
            await http_client.aclose()

    total_time = max(time.perf_counter() - start_time, 1e-6)

    sorted_lats = sorted(latencies)
    success_count = len(sorted_lats)
    fail_count = len(failures)

    rps = success_count / total_time
    p50 = _calculate_percentile(sorted_lats, 50.0)
    p95 = _calculate_percentile(sorted_lats, 95.0)
    p99 = _calculate_percentile(sorted_lats, 99.0)
    min_lat = sorted_lats[0] if sorted_lats else 0.0
    max_lat = sorted_lats[-1] if sorted_lats else 0.0
    avg_lat = sum(sorted_lats) / len(sorted_lats) if sorted_lats else 0.0

    return LoadTestResult(
        url=url,
        concurrency=concurrency,
        total_requests=total_requests,
        successful_requests=success_count,
        failed_requests=fail_count,
        total_time_seconds=total_time,
        requests_per_second=rps,
        p50_ms=p50,
        p95_ms=p95,
        p99_ms=p99,
        min_ms=min_lat,
        max_ms=max_lat,
        avg_ms=avg_lat,
        latencies_ms=sorted_lats
    )




class _LimitThreads:
    """ASGI wrapper that caps the threadpool used for sync (def) endpoints.

    Starlette's default allows 40 threads. With more threads than cores, sqlite
    reads in one process slow each other down a lot, which hides the effect of
    the code change we want to measure. Cap at the core count, as you would
    with gunicorn/uvicorn workers in production.
    """

    def __init__(self, app, threads: int):
        self.app = app
        self.threads = threads
        self._configured = False

    async def __call__(self, scope, receive, send):
        if not self._configured:
            anyio.to_thread.current_default_thread_limiter().total_tokens = self.threads
            self._configured = True
        await self.app(scope, receive, send)


class LocalServer:
    """Serve an ASGI app with uvicorn on 127.0.0.1 from a background thread.

    The benchmark client runs on its own event loop, so an endpoint that blocks
    the server's loop shows up as queueing latency, the same as in production.
    """

    def __init__(self, app, threads: Optional[int] = None, startup_timeout: float = 10.0):
        self.app = _LimitThreads(app, threads or os.cpu_count() or 4)
        self.startup_timeout = startup_timeout
        self.base_url = ""
        self._server = None
        self._thread = None

    def start(self) -> "LocalServer":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Accepted connections inherit this. Without it, Nagle + delayed ACK on
        # loopback adds ~40 ms to responses that are written in two chunks.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

        config = uvicorn.Config(self.app, log_level="warning", lifespan="off", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [sock]}, daemon=True)
        self._thread.start()

        deadline = time.monotonic() + self.startup_timeout
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                self.stop()
                raise RuntimeError("benchmark server did not start")
            time.sleep(0.01)

        self.base_url = f"http://127.0.0.1:{port}"
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> "LocalServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def benchmark_url(
    url: str,
    concurrency: int = 20,
    total_requests: int = 20,
    method: str = "GET",
) -> LoadTestResult:
    """Run a load test from a separate Python process and return the result.

    The client lives in its own process so it doesn't compete with the server
    for the GIL; in-process clients add tens of milliseconds of noise.
    """
    cmd = [
        sys.executable, "-m", "load_tester.tester", url,
        "--requests", str(total_requests),
        "--concurrency", str(concurrency),
        "--method", method,
    ]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=root, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"load test failed: {proc.stderr.strip()[-500:]}")
    return LoadTestResult(**json.loads(proc.stdout))


async def _run_cli(args) -> LoadTestResult:
    # trust_env=False: never send local benchmark traffic through an HTTP proxy.
    async with httpx.AsyncClient(timeout=args.timeout, trust_env=False) as client:
        return await run_load_test(args.url, args.concurrency, args.requests, args.method, client=client)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load test one URL and print the result as JSON.")
    parser.add_argument("url")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--method", default="GET")
    parser.add_argument("--timeout", type=float, default=30.0)
    result = asyncio.run(_run_cli(parser.parse_args()))
    print(json.dumps(asdict(result)))
