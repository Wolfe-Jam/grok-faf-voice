"""Unit tests for ``CustomVoiceClient`` (v0.2.0 Custom Voice support).

Mocks ``httpx.Client`` at the module level — no real network calls.
Covers: multipart upload, TTS bytes+file output, list/get/delete
endpoints, 4xx/5xx error handling, missing-key constructor guard.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from grok_faf_voice.custom_voices import CustomVoiceClient


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> CustomVoiceClient:
    """Client with a fake API key — no real calls."""
    monkeypatch.setenv("XAI_API_KEY", "xai-test-fake-key")
    return CustomVoiceClient()


def _mock_httpx_client(mock_client_class: MagicMock, response: MagicMock) -> MagicMock:
    """Wire up an httpx.Client mock so its context manager returns a mocked client.

    Returns the mocked client object so tests can assert on .request calls.
    """
    mock_client = MagicMock()
    mock_client.request.return_value = response
    mock_client_class.return_value.__enter__.return_value = mock_client
    mock_client_class.return_value.__exit__.return_value = None
    return mock_client


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestCreateVoice:
    @patch("grok_faf_voice.custom_voices.httpx.Client")
    def test_create_voice_multipart_payload(
        self,
        mock_client_class: MagicMock,
        client: CustomVoiceClient,
        tmp_path: Path,
    ) -> None:
        """create_voice() sends correct multipart/form-data + metadata."""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"RIFF....fake wav data....")

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "voice_id": "abc12345",
            "name": "Test",
        }
        mock_client = _mock_httpx_client(mock_client_class, mock_response)

        result = client.create_voice(
            str(audio),
            name="Test Voice",
            description="Demo",
            language="en",
            use_case="narration",
            tone="warm",
        )

        # Verify HTTP call
        args, kwargs = mock_client.request.call_args
        assert args[0] == "POST"
        assert args[1] == "https://api.x.ai/v1/custom-voices"
        assert "files" in kwargs
        assert kwargs["files"]["file"][0] == "test.wav"
        # Auth header preserved
        assert kwargs["headers"]["Authorization"] == "Bearer xai-test-fake-key"
        # Metadata fields sent as form data
        assert kwargs["data"] == {
            "name": "Test Voice",
            "description": "Demo",
            "language": "en",
            "use_case": "narration",
            "tone": "warm",
        }
        # Response parsed
        assert result["voice_id"] == "abc12345"


class TestTextToSpeech:
    @patch("grok_faf_voice.custom_voices.httpx.Client")
    def test_text_to_speech_returns_bytes_and_writes_file(
        self,
        mock_client_class: MagicMock,
        client: CustomVoiceClient,
        tmp_path: Path,
    ) -> None:
        """text_to_speech() posts JSON, returns bytes, optionally writes file."""
        audio_bytes = b"\x00\x01\x02fake mp3 bytes"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = audio_bytes
        mock_client = _mock_httpx_client(mock_client_class, mock_response)

        out_file = tmp_path / "out.mp3"
        result = client.text_to_speech(
            "Hello custom voice",
            voice_id="abc12345",
            language="en",
            output_path=str(out_file),
        )

        # Bytes returned
        assert result == audio_bytes
        # File written
        assert out_file.read_bytes() == audio_bytes

        # Verify HTTP call
        args, kwargs = mock_client.request.call_args
        assert args[0] == "POST"
        assert args[1] == "https://api.x.ai/v1/tts"
        payload = kwargs["json"]
        assert payload["text"] == "Hello custom voice"
        assert payload["voice_id"] == "abc12345"
        assert payload["language"] == "en"
        assert payload["optimize_streaming_latency"] == 0
        assert payload["text_normalization"] is False


class TestVoiceCRUD:
    @patch("grok_faf_voice.custom_voices.httpx.Client")
    def test_list_get_delete_endpoints(
        self,
        mock_client_class: MagicMock,
        client: CustomVoiceClient,
    ) -> None:
        """list_voices / get_voice / delete_voice hit correct /custom-voices paths."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"voices": [{"voice_id": "abc12345"}]}
        mock_client = _mock_httpx_client(mock_client_class, mock_response)

        # list
        client.list_voices(limit=50)
        args, kwargs = mock_client.request.call_args
        assert args[0] == "GET"
        assert args[1] == "https://api.x.ai/v1/custom-voices"
        assert kwargs["params"] == {"limit": 50}

        # get
        client.get_voice("abc12345")
        args, kwargs = mock_client.request.call_args
        assert args[0] == "GET"
        assert args[1] == "https://api.x.ai/v1/custom-voices/abc12345"

        # delete
        mock_response.json.return_value = {"deleted": True}
        deleted = client.delete_voice("abc12345")
        args, kwargs = mock_client.request.call_args
        assert args[0] == "DELETE"
        assert args[1] == "https://api.x.ai/v1/custom-voices/abc12345"
        assert deleted is True


class TestErrorHandling:
    @patch("grok_faf_voice.custom_voices.httpx.Client")
    def test_4xx_5xx_responses_raise_with_status(
        self,
        mock_client_class: MagicMock,
        client: CustomVoiceClient,
    ) -> None:
        """_request raises httpx.HTTPStatusError on 4xx/5xx with status in message."""
        for status, body in [
            (401, {"error": "Unauthorized"}),
            (404, {"error": "Voice not found"}),
            (500, {"error": "Internal server error"}),
        ]:
            mock_response = MagicMock()
            mock_response.status_code = status
            mock_response.json.return_value = body
            mock_response.request = MagicMock()
            mock_client_class.return_value.__enter__.return_value.request.return_value = mock_response

            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                if status == 404:
                    client.get_voice("badid")
                else:
                    client.list_voices()

            # Status code surfaces in error message
            assert str(status) in str(exc_info.value)


class TestConstructor:
    def test_missing_api_key_raises_clear_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Constructor raises ValueError if XAI_API_KEY is unavailable."""
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="XAI_API_KEY is required"):
            CustomVoiceClient(api_key=None)

    def test_explicit_api_key_overrides_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Explicit api_key kwarg takes precedence over $XAI_API_KEY."""
        monkeypatch.setenv("XAI_API_KEY", "from-env")
        cv = CustomVoiceClient(api_key="from-kwarg")
        assert cv.api_key == "from-kwarg"

    def test_api_key_from_env_when_no_kwarg(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Falls back to $XAI_API_KEY if no kwarg given."""
        monkeypatch.setenv("XAI_API_KEY", "from-env")
        cv = CustomVoiceClient()
        assert cv.api_key == "from-env"


class TestVoiceIDHelpers:
    def test_list_built_in_voices_returns_five(
        self,
        client: CustomVoiceClient,
    ) -> None:
        """list_built_in_voices returns the 5 canonical voices."""
        voices = client.list_built_in_voices()
        assert len(voices) == 5
        ids = {v["voice_id"] for v in voices}
        assert ids == {"ara", "eve", "leo", "rex", "sal"}

    def test_get_voice_id_for_name_resolves_built_ins_case_insensitive(
        self,
        client: CustomVoiceClient,
    ) -> None:
        """Built-in names match case-insensitively, return canonical lowercase."""
        assert client.get_voice_id_for_name("Ara") == "ara"
        assert client.get_voice_id_for_name("ARA") == "ara"
        assert client.get_voice_id_for_name("ara") == "ara"
        assert client.get_voice_id_for_name("Eve") == "eve"

    def test_get_voice_id_for_name_passes_through_custom_ids(
        self,
        client: CustomVoiceClient,
    ) -> None:
        """8-char lowercase alphanumeric IDs pass through unchanged."""
        assert client.get_voice_id_for_name("abcdefgh") == "abcdefgh"
        assert client.get_voice_id_for_name("12345678") == "12345678"
        assert client.get_voice_id_for_name("nlbqfwie") == "nlbqfwie"

    def test_get_voice_id_for_name_rejects_invalid(
        self,
        client: CustomVoiceClient,
    ) -> None:
        """Returns None for inputs that match neither built-in nor custom format."""
        assert client.get_voice_id_for_name("Bob") is None  # not a built-in
        assert client.get_voice_id_for_name("ABCDEFGH") is None  # uppercase custom
        assert client.get_voice_id_for_name("abcdefg") is None  # 7 chars
        assert client.get_voice_id_for_name("abcdefghi") is None  # 9 chars
        assert client.get_voice_id_for_name("abc-defg") is None  # special char
