"""Tests must never touch a real Zoho account, even when a developer's .env has real ZOHO_* values.
Tests that exercise the Zoho code set their own fake config and stub zoho.api."""
import os

os.environ["ZOHO_SYNC"] = "false"   # set before msp_agent.cli calls load_dotenv (which does not override)

import pytest  # noqa: E402

from msp_agent import zoho  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_zoho(monkeypatch):
    monkeypatch.setenv("ZOHO_SYNC", "false")

    def refuse(*a, **k):
        raise AssertionError("a test tried to call the real Zoho API")

    monkeypatch.setattr(zoho, "_http", refuse)
