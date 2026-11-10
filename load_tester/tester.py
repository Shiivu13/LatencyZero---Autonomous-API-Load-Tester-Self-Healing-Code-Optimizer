import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import httpx


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
    """
    Executes a high-throughput parallel load test against `url` using `asyncio.gather`.
    Measures requests/second and p50, p95, p99 latencies in milliseconds.
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
        # Run parallel requests using asyncio.gather as mandated by spec
        await asyncio.gather(*tasks)
    finally:
        if manage_client:
            await http_client.aclose()

    total_time = time.perf_counter() - start_time
    total_time = max(total_time, 0.0001)

    sorted_lats = sorted(latencies)
    success_count = len(sorted_lats)
    fail_count = len(failures)

    rps = total_requests / total_time if total_time > 0 else 0.0
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

