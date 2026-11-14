from load_tester.tester import _calculate_percentile, LoadTestResult


def test_percentile_calculations():
    data = [float(x) for x in range(1, 101)]  # 1.0 to 100.0
    p50 = _calculate_percentile(data, 50.0)
    p95 = _calculate_percentile(data, 95.0)
    p99 = _calculate_percentile(data, 99.0)

    assert abs(p50 - 50.5) < 0.1
    assert abs(p95 - 95.05) < 0.1
    assert abs(p99 - 99.01) < 0.1


def test_percentile_edge_cases():
    assert _calculate_percentile([], 50.0) == 0.0
    assert _calculate_percentile([42.0], 50.0) == 42.0
    assert _calculate_percentile([42.0], 95.0) == 42.0


def test_load_test_result_summary():
    result = LoadTestResult(
        url="http://localhost:8000/api/slow/n-plus-one",
        concurrency=50,
        total_requests=50,
        successful_requests=50,
        failed_requests=0,
        total_time_seconds=1.25,
        requests_per_second=40.0,
        p50_ms=25.0,
        p95_ms=45.0,
        p99_ms=50.0,
        min_ms=10.0,
        max_ms=55.0,
        avg_ms=26.5,
        latencies_ms=[25.0] * 50
    )
    summary = result.summary()
    assert "LoadTest for http://localhost:8000/api/slow/n-plus-one" in summary
    assert "Concurrency: 50" in summary
    assert "Throughput: 40.0 req/sec" in summary
    assert "p50=25.0" in summary
    assert "p95=45.0" in summary



def test_local_server_benchmark():
    from fastapi import FastAPI
    from load_tester.tester import LocalServer, benchmark_url

    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    with LocalServer(app) as server:
        result = benchmark_url(server.base_url + "/ping", concurrency=2, total_requests=6)

    assert result.successful_requests == 6
    assert result.failed_requests == 0
    assert result.p50_ms > 0
