import argparse
import asyncio
import json
import sys

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from optimizer.pipeline import CASES, optimize_case

console = Console()

STATUS_STYLE = {
    "success": "green",
    "healed": "green",
    "unchanged": "yellow",
    "failed": "red",
}


async def run_cases(case_ids, requests, concurrency, verbose):
    results = []
    for case_id in case_ids:
        if verbose:
            console.print(f"\n[bold]{case_id}[/bold]")
        async for event in optimize_case(case_id, requests=requests, concurrency=concurrency):
            if event["stage"] == "done":
                results.append(event)
            elif event["stage"] == "error":
                results.append({"case": case_id, "status": "failed", "error": event["message"]})
                if verbose:
                    console.print(f"  [red]{event['message']}[/red]")
            elif verbose:
                console.print(f"  {event['message']}", highlight=False)
    return results


def _fmt(metrics, key, unit=""):
    return f"{metrics[key]}{unit}" if metrics else "-"


def print_table(results):
    table = Table(show_header=True, header_style="bold")
    table.add_column("Endpoint")
    table.add_column("p50 before", justify="right")
    table.add_column("p50 after", justify="right")
    table.add_column("p95 before", justify="right")
    table.add_column("p95 after", justify="right")
    table.add_column("req/s", justify="right")
    table.add_column("Speedup", justify="right")
    table.add_column("Status")

    for r in results:
        before, after, cmp_ = r.get("before"), r.get("after"), r.get("comparison")
        rps = f"{before['rps']} -> {after['rps']}" if before and after else _fmt(before, "rps")
        speedup = f"{cmp_['p50_speedup']}x" if cmp_ and cmp_["p50_speedup"] else "-"
        style = STATUS_STYLE.get(r["status"], "white")
        table.add_row(
            CASES[r["case"]].route if r.get("case") in CASES else r.get("case", "?"),
            _fmt(before, "p50", " ms"),
            _fmt(after, "p50", " ms"),
            _fmt(before, "p95", " ms"),
            _fmt(after, "p95", " ms"),
            rps,
            speedup,
            f"[{style}]{r['status']}[/{style}]",
        )
    console.print()
    console.print(table)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark and optimize the bundled slow endpoints.")
    parser.add_argument("--case", choices=[*CASES, "all"], default="all")
    parser.add_argument("--requests", type=int, default=20, help="requests per benchmark (default: 20)")
    parser.add_argument("--concurrency", type=int, default=20, help="concurrent requests (default: 20)")
    parser.add_argument("--show-code", action="store_true", help="print the original and rewritten code")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    args = parser.parse_args(argv)

    case_ids = list(CASES) if args.case == "all" else [args.case]
    results = asyncio.run(run_cases(case_ids, args.requests, args.concurrency, verbose=not args.json))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_table(results)
        if args.show_code:
            for r in results:
                if r.get("optimized_code") and r["optimized_code"] != r.get("original_code"):
                    console.print(f"\n[bold]{r['case']}[/bold] ({r['source']}): {r['strategy']}")
                    console.print(Syntax(r["patch"] or r["optimized_code"], "diff", theme="ansi_dark"))

    return 0 if all(r["status"] in ("success", "healed") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
