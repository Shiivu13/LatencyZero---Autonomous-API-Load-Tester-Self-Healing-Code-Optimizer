# LatencyZero

LatencyZero load tests a FastAPI endpoint, finds the code behind it, asks an LLM
for a faster version, checks that the new version returns exactly the same
response, mounts it next to the original and load tests it again. You get the
before/after numbers and a patch you can apply.

It was built for the BharatCode Hackathon 2026 (Coding & Developer Tools track)
and uses the BharatCode model API. It also works without an API key; see
[Offline mode](#offline-mode).

Live demo: https://latencyzero-8ejj.onrender.com/ (free Render instance, so the
first request after a while can take ~30 s while it wakes up).

## How it works

1. **Baseline.** The app is served by a second uvicorn instance on a random
   localhost port, and a separate Python process sends N concurrent requests
   to the endpoint (`load_tester/tester.py`). We record p50/p95/p99, mean and
   requests per second.
2. **Extract.** The handler's source is pulled out of `target_app/app.py` with
   `ast`, including its decorator and line numbers (`analyzer/extractor.py`).
3. **Static checks.** A few AST checks flag common problems: a DB query inside
   a loop, blocking calls (`time.sleep`, `requests.*`) inside `async def`, and
   `x in some_list` inside a loop (`optimizer/offline.py`).
4. **Rewrite.** The code and the baseline numbers go to the LLM, which returns
   a diagnosis and a rewritten function (`optimizer/client.py`).
5. **Verify.** The candidate is compiled and called once in a worker thread.
   Its return value must equal the original function's return value. If
   compiling, running or the comparison fails, the error goes back to the LLM
   and it gets another try, up to 3 attempts (`optimizer/self_healing.py`).
6. **Measure again.** The verified function is mounted at
   `/api/sandbox/<function_name>` and load tested with the same settings.
7. **Patch.** A unified diff against `target_app/app.py` is generated, ready
   for `git apply`.

The dashboard streams each step over Server-Sent Events. The CLI runs the same
pipeline (`optimizer/pipeline.py`).

## The three demo endpoints

`target_app/app.py` contains three endpoints that are slow on purpose:

| Route | Problem |
| --- | --- |
| `/api/slow/n-plus-one` | Loads 30 users, then runs one query per user against an `orders` table (50k rows) with no index on `user_id`. |
| `/api/slow/sync-blocking` | Calls `time.sleep(0.25)` inside `async def`, which blocks the event loop for every other request. |
| `/api/slow/heavy-json` | De-duplicates 2,500 records with `if record not in unique_records`, an O(n²) list scan. |

## Results

From one run of `python cli.py` (20 requests, 20 concurrent) on a 4-vCPU
Linux container, offline mode. Your numbers will differ; the dashboard and CLI
always show what they just measured.

| Route | p50 before | p50 after | p95 before | p95 after | req/s before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| `/api/slow/n-plus-one` | 291 ms | 206 ms | 449 ms | 340 ms | 42.1 → 54.5 |
| `/api/slow/sync-blocking` | 2644 ms | 269 ms | 4789 ms | 271 ms | 4.0 → 67.1 |
| `/api/slow/heavy-json` | 265 ms | 36 ms | 433 ms | 44 ms | 42.4 → 297.3 |

The same run with `--concurrency 1` (one request at a time):

| Route | p50 before | p50 after |
| --- | ---: | ---: |
| `/api/slow/n-plus-one` | 58 ms | 7 ms |
| `/api/slow/sync-blocking` | 253 ms | 254 ms |
| `/api/slow/heavy-json` | 24 ms | 3 ms |

A few notes on reading these:

- The sync-blocking fix doesn't make a single request faster; it still waits
  250 ms. What it fixes is queueing: with `time.sleep` the requests run one
  after another, with `await asyncio.sleep` they overlap.
- The N+1 fix is about 8x faster per request but only ~1.4x under 20
  concurrent requests. SQLite runs in-process and doesn't scale well across
  threads in one Python process, so at that load the fixed version is limited
  by contention rather than by the query count. Against a networked database,
  where each query costs a round trip, the N+1 version would look worse.

## Offline mode

Without `BHARATCODE_API_KEY`, or if the API call fails, the rewrite step uses
`optimizer/offline.py` instead of the LLM:

- `/api/slow/sync-blocking` is rewritten automatically: blocking calls inside
  `async def` become `await asyncio.sleep(...)` or
  `await asyncio.to_thread(requests.get, ...)`.
- `/api/slow/n-plus-one` and `/api/slow/heavy-json` can't be fixed by a
  mechanical rewrite, so offline mode uses a hand-written reference fix for
  these two routes. The UI and CLI label these results
  `offline:reference-fix`, so it is always clear where a patch came from.

Everything else (load tests, verification, comparison with the original
response, patch) runs the same way in both modes.

## Reviewing your own code

The dashboard's "Review your code" tab takes a pasted endpoint, runs the
static checks and proposes a rewrite (from the LLM, or offline rewrites).
Pasted code is **never executed** on the server, so there is no benchmark for
it, only the proposed diff.

## Running it

Python 3.11+.

```bash
git clone https://github.com/Shiivu13/LatencyZero---Autonomous-API-Load-Tester-Self-Healing-Code-Optimizer.git
cd LatencyZero---Autonomous-API-Load-Tester-Self-Healing-Code-Optimizer
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # optional: add your BHARATCODE_API_KEY
```

Dashboard:

```bash
python main.py
```

Then open http://localhost:8000/. The demo database (`target_app/demo.db`) is
created on first start.

CLI:

```bash
python cli.py                                  # all three cases
python cli.py --case case2 --show-code         # one case, print the diff
python cli.py --requests 50 --concurrency 10   # change the load
python cli.py --json                           # machine-readable output
```

The CLI exits with status 1 if any case fails to produce a verified rewrite.

Load test any URL on its own:

```bash
python -m load_tester.tester http://localhost:8000/api/slow/heavy-json --requests 100 --concurrency 20
```

### Configuration

| Variable | Default |
| --- | --- |
| `BHARATCODE_API_KEY` | unset (offline mode) |
| `BHARATCODE_BASE_URL` | `https://bharatcode.ai/api/model/v1` |
| `BHARATCODE_MODEL` | `deepseek-v4.1-flash` |
| `PORT` | `8000` for `main.py`, `10000` in Docker |

## Docker

```bash
docker compose up --build        # http://localhost:8000/
```

or

```bash
docker build -t latencyzero .
docker run -p 8000:10000 --env-file .env latencyzero
```

### Render

`render.yaml` is a Render blueprint for the free plan:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Shiivu13/LatencyZero---Autonomous-API-Load-Tester-Self-Healing-Code-Optimizer)

Set `BHARATCODE_API_KEY` when Render asks for it, or leave it empty to run in
offline mode.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests always run in offline mode, even if `.env` has a key.

## Layout

```text
target_app/      demo FastAPI app: slow endpoints, dashboard and SSE routes, SQLite seed
load_tester/     concurrent load tester, local benchmark server
analyzer/        AST extraction of endpoint source
optimizer/
  client.py        BharatCode API client
  offline.py       static checks and offline rewrites
  sandbox.py       compile, dry-run and mount candidate functions
  self_healing.py  propose / verify / retry loop
  pipeline.py      end-to-end run used by the dashboard and the CLI
dashboard/       single-page dashboard and SSE formatting
cli.py           command-line interface
main.py          starts the dashboard
```

## Limitations

- `optimizer/sandbox.py` is not a security sandbox. Candidate code runs in the
  server process with full builtins. That is fine for rewrites of the bundled
  demo endpoints, and it is why pasted code is never executed.
- Only FastAPI endpoints written in Python are supported, and only a handful
  of patterns are detected statically. The LLM handles more, but anything it
  returns still has to pass verification.
- The response comparison calls the handler without arguments, so it works
  for endpoints without required parameters (like the demo ones).
- Benchmarks run on the same machine as the app. They are good for comparing
  before and after, not for capacity planning.
