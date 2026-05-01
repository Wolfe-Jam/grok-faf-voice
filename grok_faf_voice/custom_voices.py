"""Custom Voices client for xAI's Custom Voices API.

Voice cloning + TTS synthesis. Built on httpx (matches existing
v0.1.3 HTTP infra used by FAFContext).

Auth: ``XAI_API_KEY`` (Bearer). Separate from MCPaaS Voice keys —
this calls xAI's API directly for voice creation and TTS.

API spec: https://docs.x.ai/developers/model-capabilities/audio/voice

Voice IDs from the API are exactly 8 characters, lowercase alphanumeric
(per docs: ``"voice_id": 8-character lowercase alphanumeric identifier``).

The 5 built-in voices (Ara, Eve, Leo, Rex, Sal) and custom voice IDs
flow through the same ``voice_id`` parameter — xAI accepts either form
case-insensitively.

Usage::

    from grok_faf_voice import CustomVoiceClient

    cv = CustomVoiceClient()  # uses $XAI_API_KEY

    # Clone a voice from a 90-120s WAV sample
    voice = cv.create_voice("sample.wav", name="My Clone", language="en")
    print(voice["voice_id"])  # → "nlbqfwie" (8-char lowercase alphanum)

    # Synthesize TTS
    cv.text_to_speech(
        "Hello from the new voice",
        voice_id=voice["voice_id"],
        output_path="hello.mp3",
    )

    # Or use any built-in voice (case-insensitive)
    cv.text_to_speech("Hi", voice_id="ara", output_path="hi.mp3")

The 30 free voices via web console at https://console.x.ai work too —
just pass the ``voice_id`` from there.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any, BinaryIO, Optional, Union

import httpx


class CustomVoiceClient:
    """Sync client for xAI Custom Voices + TTS endpoints.

    Constructor args
    ----------------
    api_key
        xAI API key. Falls back to ``$XAI_API_KEY``. Raises ``ValueError``
        if neither is set.
    timeout
        Request timeout in seconds (default 60). Voice creation can take
        several seconds; TTS synthesis is typically <2s.

    Notes
    -----
    Built on ``httpx.Client`` (sync) for parity with the rest of
    ``grok-faf-voice``'s HTTP infra. No new dependencies introduced.
    """

    BASE_URL = "https://api.x.ai/v1"
    DEFAULT_TIMEOUT = 60.0

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key or os.getenv("XAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "XAI_API_KEY is required. Get one at https://x.ai/api "
                "or pass api_key= explicitly."
            )
        self.timeout = timeout
        self._headers = {"Authorization": f"Bearer {self.api_key}"}

    def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue an HTTP request. Raises ``httpx.HTTPStatusError`` on 4xx/5xx
        with the parsed JSON error body included in the message.
        """
        url = f"{self.BASE_URL}{endpoint}"
        headers = {**self._headers, **kwargs.pop("headers", {})}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.request(method, url, headers=headers, **kwargs)
            if resp.status_code >= 400:
                try:
                    err = resp.json()
                except Exception:
                    err = {"error": resp.text}
                raise httpx.HTTPStatusError(
                    f"xAI API {resp.status_code}: {err}",
                    request=resp.request,
                    response=resp,
                )
            return resp

    # ------------------------------------------------------------------
    # Voice CRUD
    # ------------------------------------------------------------------

    def create_voice(
        self,
        file: Union[str, Path, BinaryIO],
        name: Optional[str] = None,
        description: Optional[str] = None,
        gender: Optional[str] = None,
        accent: Optional[str] = None,
        age: Optional[str] = None,
        language: Optional[str] = None,
        use_case: Optional[str] = None,
        tone: Optional[str] = None,
    ) -> dict:
        """Clone a voice from an audio sample (multipart upload).

        Per xAI docs: 90-120s of natural speech (max 120s), WAV
        recommended. Sample must include the verification passphrase.

        Returns the full server response dict::

            {
              "voice_id": "nlbqfwie",        # 8-char lowercase alphanum
              "name": "My Clone",
              "description": "...",
              "language": "en",
              ...
            }

        Pass any combination of metadata kwargs (name/description/gender/
        accent/age/language/use_case/tone). All are optional.
        """
        files: dict = {}
        data: dict = {}
        opened_file = None

        try:
            if isinstance(file, (str, Path)):
                p = Path(file)
                if not p.exists():
                    raise FileNotFoundError(f"Audio file not found: {p}")
                mt = mimetypes.guess_type(str(p))[0] or "audio/wav"
                opened_file = open(p, "rb")
                files["file"] = (p.name, opened_file, mt)
            else:
                files["file"] = ("ref.wav", file, "audio/wav")

            for key, value in [
                ("name", name),
                ("description", description),
                ("gender", gender),
                ("accent", accent),
                ("age", age),
                ("language", language),
                ("use_case", use_case),
                ("tone", tone),
            ]:
                if value is not None:
                    data[key] = value

            return self._request(
                "POST", "/custom-voices",
                files=files, data=data,
            ).json()
        finally:
            if opened_file is not None:
                opened_file.close()

    def list_voices(self, limit: int = 100) -> list:
        """List all custom voices on the account."""
        return self._request(
            "GET", "/custom-voices",
            params={"limit": limit},
        ).json().get("voices", [])

    def get_voice(self, voice_id: str) -> dict:
        """Get full metadata for a custom voice by ID."""
        return self._request("GET", f"/custom-voices/{voice_id}").json()

    def update_voice(self, voice_id: str, **meta: Any) -> dict:
        """Update metadata fields on a custom voice (name, description, etc.)."""
        return self._request(
            "PATCH", f"/custom-voices/{voice_id}",
            json=meta,
        ).json()

    def delete_voice(self, voice_id: str) -> bool:
        """Delete a custom voice. Returns ``True`` if the server confirms."""
        return self._request(
            "DELETE", f"/custom-voices/{voice_id}",
        ).json().get("deleted", False)

    def download_reference_audio(
        self,
        voice_id: str,
        out: Union[str, Path],
    ) -> Path:
        """Download the reference audio sample used to clone this voice.

        Useful for verification or migration. Returns the output ``Path``.
        """
        resp = self._request("GET", f"/custom-voices/{voice_id}/audio")
        p = Path(out)
        p.write_bytes(resp.content)
        return p

    # ------------------------------------------------------------------
    # TTS synthesis
    # ------------------------------------------------------------------

    def text_to_speech(
        self,
        text: str,
        voice_id: str,
        language: str = "en",
        output_format: Optional[dict] = None,
        optimize_streaming_latency: int = 0,
        text_normalization: bool = False,
        output_path: Optional[Union[str, Path]] = None,
    ) -> bytes:
        """Synthesize speech from text using any voice — built-in or custom.

        Parameters
        ----------
        voice_id
            Built-in name (case-insensitive: ``ara``, ``eve``, ``leo``,
            ``rex``, ``sal``) OR custom 8-char lowercase alphanumeric ID
            from :meth:`create_voice`.
        language
            ISO language code (default ``"en"``).
        output_format
            Optional output format dict per xAI spec (codec, sample rate, etc.).
        optimize_streaming_latency
            Latency optimization level (0 = best quality, higher = lower
            latency). Default 0.
        text_normalization
            If True, server normalizes numbers/abbreviations before synthesis.
        output_path
            If provided, writes the audio bytes to this path AND returns them.

        Returns
        -------
        bytes
            Raw audio bytes (typically MP3, depending on output_format).
        """
        payload: dict = {
            "text": text,
            "voice_id": voice_id,
            "language": language,
            "optimize_streaming_latency": optimize_streaming_latency,
            "text_normalization": text_normalization,
        }
        if output_format is not None:
            payload["output_format"] = output_format

        resp = self._request(
            "POST", "/tts",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        audio = resp.content
        if output_path is not None:
            Path(output_path).write_bytes(audio)
        return audio

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def list_built_in_voices(self) -> list[dict]:
        """Return the 5 built-in xAI realtime voices.

        Format matches :meth:`list_voices` for consistency::

            [{"voice_id": "ara", "name": "Ara"}, ...]
        """
        return [
            {"voice_id": v, "name": v.title()}
            for v in ("ara", "eve", "leo", "rex", "sal")
        ]

    def get_voice_id_for_name(self, name: str) -> Optional[str]:
        """Resolve a built-in voice name OR a custom voice_id to a canonical ID.

        - Built-in names match case-insensitively (``"Ara"``, ``"ara"``,
          ``"ARA"`` all return ``"ara"``).
        - Custom voice IDs (8-char lowercase alphanumeric) pass through.
        - Returns ``None`` if input is neither.
        """
        n = name.lower()
        for v in self.list_built_in_voices():
            if v["name"].lower() == n or v["voice_id"] == n:
                return v["voice_id"]
        # Custom voice ID pattern (per xAI docs: 8-char lowercase alphanumeric).
        # Note: ``"12345678".islower()`` is False because there are no cased
        # chars — check for absence of uppercase instead.
        if (
            len(name) == 8
            and name.isalnum()
            and not any(c.isupper() for c in name)
        ):
            return name
        return None
