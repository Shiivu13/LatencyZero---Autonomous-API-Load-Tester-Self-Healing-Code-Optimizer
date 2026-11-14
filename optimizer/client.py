import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "https://bharatcode.ai/api/model/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"

SYSTEM_PROMPT = """\
You review slow Python web endpoints and rewrite them to be faster.

You get the source of one FastAPI endpoint and the latency numbers measured
for it under concurrent load. Find what makes it slow and return a faster
version of the same function.

Rules:
- Keep the function name, parameters, decorator and the exact response shape.
  The rewritten function must return the same data as the original.
- Change only what is needed to fix the slowdown.
- Common fixes: batch queries instead of querying inside a loop, use non-blocking
  calls (await asyncio.sleep, httpx.AsyncClient, asyncio.to_thread) inside async
  functions, use sets/dicts instead of repeated list scans.

Reply with a single JSON object and nothing else:
{
  "bottleneck_diagnosis": "one or two sentences",
  "optimization_strategy": "one or two sentences",
  "function_name": "name of the function",
  "optimized_code": "the full function source, including its decorator"
}
"""


class OptimizerError(Exception):
    """The LLM request failed or returned something we could not use."""


@dataclass
class OptimizationProposal:
    bottleneck_diagnosis: str
    optimization_strategy: str
    optimized_code: str
    function_name: str
    source: str
    raw_response: Optional[str] = None


class BharatCodeOptimizerClient:
    """Small client for the BharatCode chat-completions API (OpenAI compatible)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("BHARATCODE_API_KEY", "")
        self.base_url = (base_url or os.getenv("BHARATCODE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.getenv("BHARATCODE_MODEL") or DEFAULT_MODEL
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self.api_key != "your_api_key_here"

    def build_user_prompt(
        self,
        code_snippet: str,
        baseline_metrics: Optional[Dict[str, Any]],
        previous_error: Optional[str] = None,
    ) -> str:
        parts = [f"Endpoint source:\n```python\n{code_snippet}\n```"]
        if baseline_metrics:
            parts.append(f"Measured latency (ms) and throughput (req/s):\n{json.dumps(baseline_metrics, indent=2)}")
        if previous_error:
            parts.append(
                "Your previous answer failed verification with this error. "
                f"Fix it and return the corrected function.\n{previous_error}"
            )
        return "\n\n".join(parts)

    def optimize_endpoint(
        self,
        code_snippet: str,
        baseline_metrics: Optional[Dict[str, Any]] = None,
        previous_error: Optional[str] = None,
    ) -> OptimizationProposal:
        if not self.available:
            raise OptimizerError("BHARATCODE_API_KEY is not set")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self.build_user_prompt(code_snippet, baseline_metrics, previous_error)},
            ],
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            res = httpx.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise OptimizerError(f"request failed: {exc}") from exc

        if res.status_code != 200:
            raise OptimizerError(f"API returned HTTP {res.status_code}: {res.text[:200]}")

        try:
            content = res.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise OptimizerError("unexpected response format") from exc

        return parse_llm_json(content, source=f"llm:{self.model}")


def parse_llm_json(raw: str, source: str) -> OptimizationProposal:
    """Parse the model reply. Tolerates a ```json fence around the object."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise OptimizerError("model reply did not contain a JSON object")

    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise OptimizerError(f"model reply was not valid JSON: {exc}") from exc

    code = (data.get("optimized_code") or "").strip()
    if not code:
        raise OptimizerError("model reply had no optimized_code")

    return OptimizationProposal(
        bottleneck_diagnosis=data.get("bottleneck_diagnosis", ""),
        optimization_strategy=data.get("optimization_strategy", ""),
        optimized_code=code,
        function_name=data.get("function_name", ""),
        source=source,
        raw_response=raw,
    )
