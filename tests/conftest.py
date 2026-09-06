import pytest


@pytest.fixture(autouse=True)
def offline_ollama_library(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("Ollama library unavailable; retry or use a downloaded/custom model")
    monkeypatch.setattr("sh41_local.catalog.page", unavailable)
