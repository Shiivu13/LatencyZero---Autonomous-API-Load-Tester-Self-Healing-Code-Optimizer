import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from fastapi import FastAPI
from analyzer.extractor import EndpointCodeSnippet
from optimizer.client import BharatCodeOptimizerClient, OptimizationProposal
from optimizer.sandbox import DynamicSandbox


@dataclass
class SelfHealingAttempt:
    attempt_number: int
    proposal: OptimizationProposal
    success: bool
    error_traceback: Optional[str] = None


@dataclass
class SelfHealingResult:
    route_path: str
    original_code: str
    optimized_code: str
    status: str  # "healed", "success", "failed"
    attempts_count: int
    bottleneck_diagnosis: str
    optimization_strategy: str
    sandbox_route: Optional[str] = None
    mounted_function: Optional[Callable[..., Any]] = None
    attempts_history: List[SelfHealingAttempt] = field(default_factory=list)


class SelfHealingLoop:
    """
    Self-Healing Orchestrator:
    Generates optimization patches via BharatCode API, tests them in DynamicSandbox,
    and automatically feeds back runtime errors to auto-heal until the patch runs cleanly.
    """

    def __init__(
        self,
        client: Optional[BharatCodeOptimizerClient] = None,
        sandbox: Optional[DynamicSandbox] = None,
        max_retries: int = 3
    ):
        self.client = client or BharatCodeOptimizerClient()
        self.sandbox = sandbox or DynamicSandbox()
        self.max_retries = max_retries

    def optimize_and_heal(
        self,
        endpoint_snippet: EndpointCodeSnippet,
        baseline_metrics: Dict[str, Any],
        mount_to_sandbox: bool = True,
        hot_swap_live: bool = False
    ) -> SelfHealingResult:
        """
        Executes the optimization and self-healing loop.
        """
        history: List[SelfHealingAttempt] = []
        current_error: Optional[str] = None
        last_proposal: Optional[OptimizationProposal] = None
        verified_func: Optional[Callable[..., Any]] = None
        sandbox_path: Optional[str] = None

        for attempt_idx in range(1, self.max_retries + 1):
            # 1. Ask BharatCode API for an optimization proposal
            proposal = self.client.optimize_endpoint(
                code_snippet=endpoint_snippet.code_string,
                baseline_metrics=baseline_metrics,
                previous_error=current_error
            )
            last_proposal = proposal

            # 2. Compile in sandbox
            compile_ok, func, compile_err = self.sandbox.compile_function(
                proposal.optimized_code,
                function_name=proposal.function_name or endpoint_snippet.func_name
            )

            if not compile_ok:
                current_error = f"Compilation/Syntax Error:\n{compile_err}"
                history.append(SelfHealingAttempt(
                    attempt_number=attempt_idx,
                    proposal=proposal,
                    success=False,
                    error_traceback=current_error
                ))
                continue

            # 3. Dry-run execution test in sandbox
            dry_run_ok, res, runtime_err = self.sandbox.dry_run(func)
            if not dry_run_ok:
                current_error = f"Runtime Execution Error:\n{runtime_err}"
                history.append(SelfHealingAttempt(
                    attempt_number=attempt_idx,
                    proposal=proposal,
                    success=False,
                    error_traceback=current_error
                ))
                continue

            # 4. Success! Verified clean execution without any errors
            verified_func = func
            history.append(SelfHealingAttempt(
                attempt_number=attempt_idx,
                proposal=proposal,
                success=True,
                error_traceback=None
            ))

            # 5. Mount to dynamic sandbox route
            if mount_to_sandbox and verified_func:
                sandbox_path = self.sandbox.mount_sandbox_route(
                    verified_func,
                    http_method=endpoint_snippet.http_method or "GET"
                )

            # 6. Optionally hot-swap live route
            if hot_swap_live and verified_func and endpoint_snippet.route_path:
                self.sandbox.hot_swap_live_route(
                    endpoint_snippet.route_path,
                    verified_func
                )

            status = "healed" if attempt_idx > 1 else "success"
            return SelfHealingResult(
                route_path=endpoint_snippet.route_path or "unknown",
                original_code=endpoint_snippet.code_string,
                optimized_code=proposal.optimized_code,
                status=status,
                attempts_count=attempt_idx,
                bottleneck_diagnosis=proposal.bottleneck_diagnosis,
                optimization_strategy=proposal.optimization_strategy,
                sandbox_route=sandbox_path,
                mounted_function=verified_func,
                attempts_history=history
            )

        # Max retries exhausted
        return SelfHealingResult(
            route_path=endpoint_snippet.route_path or "unknown",
            original_code=endpoint_snippet.code_string,
            optimized_code=last_proposal.optimized_code if last_proposal else "",
            status="failed",
            attempts_count=len(history),
            bottleneck_diagnosis=last_proposal.bottleneck_diagnosis if last_proposal else "Failed to optimize",
            optimization_strategy="Exhausted maximum self-healing retry attempts.",
            sandbox_route=None,
            mounted_function=None,
            attempts_history=history
        )

