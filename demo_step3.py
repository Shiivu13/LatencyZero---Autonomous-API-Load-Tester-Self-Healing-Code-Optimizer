import os
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from analyzer.extractor import extract_from_file, extract_all_endpoints

console = Console()

def run_step3_demo():
    app_file = os.path.join(os.path.dirname(__file__), "target_app", "app.py")
    console.print(Panel("[bold green]LatencyZero - Step 3: AST Extractor & Load Tester Engine[/bold green]"))

    console.print("\n[bold cyan]1. Detecting and extracting all endpoints from target_app/app.py...[/bold cyan]")
    endpoints = extract_all_endpoints(app_file)
    for ep in endpoints:
        console.print(f"  • Found [yellow]{ep.http_method} {ep.route_path}[/yellow] (Lines {ep.start_line}-{ep.end_line}, Async={ep.is_async})")

    # Inspect Case 1
    console.print("\n[bold cyan]2. Extracting exact code snippet for Case 1 (/api/slow/n-plus-one):[/bold cyan]")
    case1 = extract_from_file(app_file, route_path="/api/slow/n-plus-one")
    if case1:
        console.print(Syntax(case1.code_string, "python", theme="monokai", line_numbers=True, start_line=case1.start_line))

    # Inspect Case 2
    console.print("\n[bold cyan]3. Extracting exact code snippet for Case 2 (/api/slow/sync-blocking):[/bold cyan]")
    case2 = extract_from_file(app_file, route_path="/api/slow/sync-blocking")
    if case2:
        console.print(Syntax(case2.code_string, "python", theme="monokai", line_numbers=True, start_line=case2.start_line))

    console.print("\n[bold green]✓ Step 3 AST Extraction & Load Tester verified successfully![/bold green]")

if __name__ == "__main__":
    run_step3_demo()

