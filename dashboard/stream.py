import json
from typing import AsyncIterator, Optional

from optimizer.pipeline import optimize_case, review_code


async def generate_optimization_events(
    case_id: str = "case1",
    custom_code: Optional[str] = None,
) -> AsyncIterator[str]:
    """Format pipeline events as Server-Sent Events."""
    if case_id == "custom":
        events = review_code(custom_code or "")
    else:
        events = optimize_case(case_id)

    async for event in events:
        yield f"data: {json.dumps(event)}\n\n"
