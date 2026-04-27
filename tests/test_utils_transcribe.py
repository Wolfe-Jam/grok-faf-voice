"""Smoke tests for the transcribe side-note utility.

Live STT calls would hit the network + xAI quota. We test only the
shape: imports, auth check, the request envelope, output handling.
A real round-trip via xAI would be a separate `network`-marker test
or a manual run.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from grok_faf_voice.utils.transcribe import (
    XAI_STT_URL,
    cli,
    transcribe,
)


def test_imports():
    """Public surface is importable."""
    assert transcribe is not None
    assert cli is not None
    assert XAI_STT_URL == "https://api.x.ai/v1/stt"


async def test_transcribe_raises_without_api_key(tmp_path: Path):
    """transcribe() raises if no API key is reachable."""
    sample = tmp_path / "sample.wav"
    sample.write_bytes(b"fake wav bytes")

    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(RuntimeError, match="XAI_API_KEY"):
            await transcribe(sample, api_key=None)


async def test_transcribe_explicit_key_overrides_missing_env(tmp_path: Path):
    """An explicit api_key arg works even with no env var set.

    We mock httpx so no network call happens; we just verify the
    auth path lets us through to the request stage.
    """
    sample = tmp_path / "sample.wav"
    # Minimal valid 16kHz mono WAV header + a few bytes of silence
    sample.write_bytes(b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 100)

    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "text": "mock transcript",
                "language": "English",
                "duration": 1.0,
                "words": [],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, files=None, data=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            return FakeResponse()

    with patch("grok_faf_voice.utils.transcribe.httpx.AsyncClient", FakeClient):
        result = await transcribe(sample, api_key="explicit-test-key")

    assert captured["url"] == XAI_STT_URL
    assert captured["headers"]["Authorization"] == "Bearer explicit-test-key"
    assert captured["data"]["language"] == "en"
    assert captured["data"]["format"] == "true"
    assert result["text"] == "mock transcript"
