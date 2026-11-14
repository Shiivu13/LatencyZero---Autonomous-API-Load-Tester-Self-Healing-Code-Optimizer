"""Propose -> compile -> dry-run -> compare loop.

Each candidate is compiled and called once. If a reference function (the
original endpoint) is given, the candidate's response must equal the
original's. Any failure is sent back to the LLM as feedback for the next
attempt, up to ``max_retries`` attempts.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from analyzer.extractor import EndpointCodeSnippet
from optimizer import offline
from optimizer.client import BharatCodeOptimizerClient, OptimizationProposal, OptimizerError
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
    # "success": first attempt passed, "healed": passed after a retry,
    # "unchanged": nothing to rewrite, "failed": no attempt passed.
    status: str
    attempts_count: int
    bottleneck_diagnosis: str
    optimization_strategy: str
    source: str = ""
    verified_function: Optional[Callable[..., Any]] = None
    attempts_history: List[SelfHealingAttempt] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("success", "healed")


class SelfHealingLoop:
    def __init__(
        self,
        client: Optional[BharatCodeOptimizerClient] = None,
        sandbox: Optional[DynamicSandbox] = None,
        max_retries: int = 3,
    ):
        self.client = client or BharatCodeOptimizerClient()
        self.sandbox = sandbox or DynamicSandbox()
        self.max_retries = max_retries

    def _propose(
        self,
        snippet: EndpointCodeSnippet,
        metrics: Optional[Dict[str, Any]],
        previous_error: Optional[str],
        notes: List[str],
    ) -> OptimizationProposal:
        if self.client.available:
            try:
                return self.client.optimize_endpoint(snippet.code_string, metrics, previous_error)
            except OptimizerError as exc:
                notes.append(f"LLM unavailable ({exc}); used the offline optimizer.")
        elif not notes:
            notes.append("No BHARATCODE_API_KEY set; used the offline optimizer.")
        return offline.propose(snippet.code_string)

    def _verify(self, proposal: OptimizationProposal, snippet: EndpointCodeSnippet, reference) -> tuple:
        ok, func, err = self.sandbox.compile_function(
            proposal.optimized_code,
            function_name=proposal.function_name or snippet.func_name,
        )
        if not ok:
            return None, f"Compile error:\n{err}"

        ok, result, err = self.sandbox.dry_run(func)
        if not ok:
            return None, f"Runtime error:\n{err}"

        if reference is not None:
            ok, expected, err = self.sandbox.dry_run(reference)
            if ok and result != expected:
                return None, (
                    "The rewritten function returned a different response than the original. "
                    "It must return exactly the same data."
                )
        return func, None

    def optimize_and_heal(
        self,
        endpoint_snippet: EndpointCodeSnippet,
        baseline_metrics: Optional[Dict[str, Any]] = None,
        reference_function: Optional[Callable[..., Any]] = None,
    ) -> SelfHealingResult:
        history: List[SelfHealingAttempt] = []
        notes: List[str] = []
        error: Optional[str] = None
        proposal: Optional[OptimizationProposal] = None

        for attempt in range(1, self.max_retries + 1):
            proposal = self._propose(endpoint_snippet, baseline_metrics, error, notes)

            if proposal.source == "offline:none":
                history.append(SelfHealingAttempt(attempt, proposal, success=False))
                return self._result(endpoint_snippet, proposal, "unchanged", history, notes)

            func, error = self._verify(proposal, endpoint_snippet, reference_function)
            history.append(SelfHealingAttempt(attempt, proposal, success=func is not None, error_traceback=error))

            if func is not None:
                status = "success" if attempt == 1 else "healed"
                return self._result(endpoint_snippet, proposal, status, history, notes, func)

            # Offline proposals are deterministic, so retrying would repeat the same failure.
            if proposal.source.startswith("offline"):
                break

        return self._result(endpoint_snippet, proposal, "failed", history, notes)

    @staticmethod
    def _result(snippet, proposal, status, history, notes, func=None) -> SelfHealingResult:
        return SelfHealingResult(
            route_path=snippet.route_path or "",
            original_code=snippet.code_string,
            optimized_code=proposal.optimized_code if proposal else snippet.code_string,
            status=status,
            attempts_count=len(history),
            bottleneck_diagnosis=proposal.bottleneck_diagnosis if proposal else "",
            optimization_strategy=proposal.optimization_strategy if proposal else "",
            source=proposal.source if proposal else "",
            verified_function=func,
            attempts_history=history,
            notes=notes,
        )
