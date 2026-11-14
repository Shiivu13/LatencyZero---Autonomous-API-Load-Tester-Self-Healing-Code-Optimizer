import pytest


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Keep tests offline even if a local .env has an API key."""
    monkeypatch.setenv("BHARATCODE_API_KEY", "")
