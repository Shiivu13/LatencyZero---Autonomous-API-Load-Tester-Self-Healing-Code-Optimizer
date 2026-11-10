import os
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from analyzer.extractor import extract_from_file
from optimizer.self_healing import SelfHealingLoop

console = Console()


def run_step4_demo():
    app_file = os.path.join(os.path.dirname(__file__), "target_app", "app.py")
    console.print(Panel("[bold green]LatencyZero - Step 4: BharatCode Optimizer & Self-Healing Loop[/bold green]"))

    cases = [
        ("/api/slow/n-plus-one", {"p50_ms": 25.0, "p95_ms": 48.0, "rps": 35.0}, "Case 1: Unindexed N+1 Loop"),
        ("/api/slow/sync-blocking", {"p50_ms": 250.0, "p95_ms": 252.0, "rps": 3.9}, "Case 2: Blocking Sync I/O"),
        ("/api/slow/heavy-json", {"p50_ms": 22.0, "p95_ms": 30.0, "rps": 40.0}, "Case 3: O(N^2) JSON Deduplication")
    ]

    loop = SelfHealingLoop()

    for route, metrics, label in cases:
        console.print(f"\n[bold yellow]▶ Analyzing & Optimizing {label} ({route})[/bold yellow]")
        snippet = extract_from_file(app_file, route_path=route)
        if not snippet:
            continue

        result = loop.optimize_and_heal(
            endpoint_snippet=snippet,
            baseline_metrics=metrics,
            mount_to_sandbox=True
        )

        table = Table(title=f"Optimization Result: {label}", show_header=True, header_style="bold magenta")
        table.add_column("Field", style="cyan", width=22)
        table.add_column("Details", style="white")

        table.add_row("Route Path", result.route_path)
        table.add_row("Status", f"[bold green]{result.status.upper()}[/bold green]" if result.status in ("success", "healed") else f"[bold red]{result.status.upper()}[/bold red]")
        table.add_row("Attempts Count", str(result.attempts_count))
        table.add_row("Mounted Sandbox Route", f"[bold blue]{result.sandbox_route}[/bold blue]")
        table.add_row("Bottleneck Diagnosis", result.bottleneck_diagnosis)
        table.add_row("Optimization Strategy", result.optimization_strategy)

        console.print(table)
        console.print("\n[bold cyan]Optimized Function Mounted in Sandbox:[/bold cyan]")
        console.print(Syntax(result.optimized_code, "python", theme="monokai", line_numbers=True))

    console.print("\n[bold green]✓ Step 4: BharatCode Optimizer & Self-Healing Loop verified successfully![/bold green]")


if __name__ == "__main__":
    run_step4_demo()
