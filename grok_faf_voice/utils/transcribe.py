"""Transcribe a media file with xAI STT (dogfood, direct REST).

Side-note utility, not core SDK surface. Ships with the SDK so any
dev (or programmatic flow) can transcribe without the FAF-Voice UI.
When the UI lands, its transcript pane consumes the same primitive
exposed here as `transcribe()`.

Why direct REST instead of the LiveKit `xai.STT()` wrapper?
The wrapper hardcodes a 30-second total timeout — too short for
multi-minute conversations. xAI's actual REST endpoint
(``https://api.x.ai/v1/stt``) supports the same multipart upload
shape with no such limit. We use it directly so devs can transcribe
audio of any practical length.

Programmatic use::

    from pathlib import Path
    from grok_faf_voice.utils.transcribe import transcribe

    result = await transcribe(Path("CHAT-3.mov"))
    print(result["text"])

CLI use (today via ``python -m``)::

    python -m grok_faf_voice.utils.transcribe CHAT-3.mov [...]

Outputs (next to the source file):

- ``CHAT-3.txt`` — plain transcript
- ``CHAT-3.words.json`` — full xAI response with word-level timings

Future CLI entrypoint
---------------------
The module exposes ``cli()`` as a sync entry point so a
``[project.scripts]`` line can promote this to an installed command
without further refactoring::

    # pyproject.toml (future, not enabled today):
    [project.scripts]
    grok-faf-voice-transcribe = "grok_faf_voice.utils.transcribe:cli"

After ``pip install grok-faf-voice``, that would expose:
``grok-faf-voice-transcribe CHAT-3.mov``.

Decision deliberately deferred — we want this to stay a side-note,
not commit to a CLI naming/stability surface yet.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

XAI_STT_URL = "https://api.x.ai/v1/stt"
SAMPLE_RATE = 16000
DEFAULT_TIMEOUT_SECONDS = 600.0  # 10-minute total — covers long convos


def _preextract_to_wav(media_path: Path) -> Path:
    """ffmpeg → 16 kHz mono PCM s16le wav, video discarded.

    Bypasses PyAV's container-decoding issues by giving downstream
    code a uniform .wav regardless of the source container (.mov,
    .mp4, .m4a, .webm, etc.).
    """
    tmp = Path(tempfile.gettempdir()) / f"{media_path.stem}.transcribe.wav"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(media_path),
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-vn",
        "-acodec", "pcm_s16le",
        str(tmp),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}): "
            f"{result.stderr[-500:]}"
        )
    return tmp


async def transcribe(
    media_path: Path,
    *,
    language: str = "en",
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Transcribe a media file via xAI STT.

    Parameters
    ----------
    media_path : Path
        Audio or video file. Any container ffmpeg can decode.
    language : str, default "en"
        BCP-47 language code passed to xAI STT.
    api_key : str, optional
        xAI API key. Falls back to ``XAI_API_KEY`` env var (loaded
        from ``.env`` via ``python-dotenv``).
    timeout : float, default 600 (10 min)
        Total HTTP timeout. xAI processes ~30s of audio per second
        of wall time on average; the default covers ~30 minutes of
        audio with headroom.

    Returns
    -------
    dict
        Raw xAI STT JSON response::

            {
                "text": str,
                "language": str,
                "duration": float,
                "words": [{"text", "start", "end"}, ...]
            }

    Raises
    ------
    RuntimeError
        If ffmpeg fails or no API key is available.
    httpx.HTTPStatusError
        If xAI returns a non-2xx response.

    Notes
    -----
    This function does NOT call ``load_dotenv()`` — libraries should
    not mutate the global environment. If you want ``.env`` loaded,
    do so at the application entry point (``cli()`` does this).
    """
    api_key = api_key or os.environ.get("XAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "XAI_API_KEY not set. Add to .env or pass api_key=... explicitly."
        )

    wav = (
        _preextract_to_wav(media_path)
        if media_path.suffix.lower() != ".wav"
        else media_path
    )
    audio_bytes = wav.read_bytes()

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        resp = await client.post(
            XAI_STT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            files={"file": ("audio.wav", audio_bytes, "audio/wav")},
            data={"language": language, "format": "true"},
        )
        resp.raise_for_status()
        return resp.json()


# ----------------------------------------------------------------------
# CLI surface (deliberately side-note shape — see module docstring)
# ----------------------------------------------------------------------


async def _main(argv: list[str]) -> int:
    if not argv:
        print(
            "Usage: python -m grok_faf_voice.utils.transcribe "
            "<media_file> [<media_file> ...]"
        )
        return 2

    for arg in argv:
        media = Path(arg).expanduser().resolve()
        if not media.exists():
            print(f"[transcribe] SKIP — not found: {media}")
            continue

        print(f"[transcribe] Source: {media}")
        try:
            result = await transcribe(media)
        except Exception as e:
            print(f"[transcribe] FAIL: {type(e).__name__}: {e}")
            continue

        text = result.get("text", "")
        words = result.get("words", [])
        duration = result.get("duration", 0.0)

        text_path = media.with_suffix(".txt")
        text_path.write_text(text)

        words_path = media.with_name(f"{media.stem}.words.json")
        words_path.write_text(json.dumps(result, indent=2))

        print(f"[transcribe]   {duration:.1f}s audio · {len(words)} words")
        print(f"[transcribe]   WROTE {text_path}")
        print(f"[transcribe]   WROTE {words_path}")
        print()

    return 0


def cli() -> int:
    """Synchronous CLI entry point.

    Loads ``.env`` (so ``XAI_API_KEY`` etc. are available), then
    runs ``_main()`` via ``asyncio.run``. Library callers that want
    to bypass ``.env`` loading can call ``transcribe()`` directly.

    Wraps ``_main()`` so this function can be referenced verbatim
    from a future ``[project.scripts]`` entry::

        [project.scripts]
        grok-faf-voice-transcribe = "grok_faf_voice.utils.transcribe:cli"

    Today, invoke via ``python -m grok_faf_voice.utils.transcribe``.
    """
    load_dotenv()
    return asyncio.run(_main(sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(cli())
