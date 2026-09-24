"""Fetch the standing voice string from MCPaaS.

The standing string is what a session is told at connect. It is not the
etch log. The receipt on the server is the hash of this string.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_INJECT_URL = "https://mcpaas.live/api/voice/inject"


class InjectError(Exception):
    """MCPaaS inject did not return a standing string."""


async def fetch_inject(
    api_key: str,
    *,
    model: str | None = None,
    url: str = DEFAULT_INJECT_URL,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST /api/voice/inject and return the JSON body.

    ``model`` is recorded on the receipt. It does not select the model
    on xAI — the caller still passes that to the realtime client.
    """
    payload: dict[str, str] = {}
    if model:
        payload["model"] = model

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await http.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code != 200:
        detail = response.text[:300]
        raise InjectError(
            f"inject failed ({response.status_code}) at {url}: {detail}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise InjectError(f"inject returned non-JSON from {url}") from exc

    if not isinstance(data, dict) or "instructions" not in data or "sha256" not in data:
        raise InjectError("inject response is missing instructions or sha256")
    if not isinstance(data["instructions"], str):
        raise InjectError("inject instructions must be a string")
    return data
