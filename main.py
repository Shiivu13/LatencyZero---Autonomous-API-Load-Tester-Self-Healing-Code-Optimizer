import sys
import uvicorn

if __name__ == "__main__":
    print("=" * 70)
    print("  ⚡ LatencyZero — Autonomous API Load-Tester & Code Optimizer")
    print("  BharatCode Hackathon 2026 • Track: Coding & Developer Tools")
    print("=" * 70)
    print("  • Target App & API:   http://localhost:8000")
    print("  • Live UI Dashboard:  http://localhost:8000/")
    print("  • SSE Stream Route:   http://localhost:8000/api/stream/optimize")
    print("  • Slow Endpoints:")
    print("      Case 1: http://localhost:8000/api/slow/n-plus-one")
    print("      Case 2: http://localhost:8000/api/slow/sync-blocking")
    print("      Case 3: http://localhost:8000/api/slow/heavy-json")
    print("=" * 70)
    uvicorn.run("target_app.app:app", host="0.0.0.0", port=8000, reload=False)

