"""Tests for grok_faf_voice.agent — VoiceAgent zero-config composer.

Identity resolution is the heart of zero-config; cover all four
precedence rungs. Live anonymous-provisioning tests are gated on
the GROK_FAF_VOICE_LIVE_TESTS env var so CI doesn't hammer
mcpaas.live unprompted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from grok_faf_voice.agent import (
    DEFAULT_ANONYMOUS_ISSUE_URL,
    IDENTITY_SCHEMA_VERSION,
    SUPPORTED_VOICES,
    VoiceAgent,
    VoiceAgentConfigError,
    _load_identity,
    _maybe_load_faf_context,
    _persist_identity,
    _resolve_identity,
)

# ── Construction validation ────────────────────────────────────────────────


class TestVoiceAgentConstruction:
    def test_default_construction_does_not_touch_network(self) -> None:
        """Constructing must not provision identity — only run() does."""
        agent = VoiceAgent()
        assert agent._voice == "Ara"
        assert agent._project_faf == "auto"
        assert agent._api_key_arg is None
        assert agent._namepoint_arg is None

    def test_unsupported_voice_rejected_with_actionable_message(self) -> None:
        with pytest.raises(VoiceAgentConfigError) as exc:
            VoiceAgent(voice="Bob")
        msg = str(exc.value)
        assert "Bob" in msg
        for v in SUPPORTED_VOICES:
            assert v in msg, f"error message must list {v}"

    def test_built_in_voice_case_insensitive(self) -> None:
        """Built-in voice names must be accepted regardless of case."""
        for variant in ("Ara", "ARA", "ara", "aRa"):
            VoiceAgent(voice=variant)  # must not raise
        for variant in ("EVE", "eve", "Eve"):
            VoiceAgent(voice=variant)

    def test_valid_8_char_preset_id_accepted(self) -> None:
        """Console preset voice IDs (8 chars lowercase alphanumeric)."""
        for vid in ("abcdefgh", "12345678", "nlbqfwie", "a1b2c3d4", "355dca53"):
            VoiceAgent(voice=vid)  # must not raise

    def test_valid_12_char_clone_id_accepted(self) -> None:
        """Custom console clone voice IDs (12 chars lowercase alphanumeric).

        Spec confirmed by xAI on 2026-05-03 — custom clones use 12 chars,
        presets use 8. Both lengths share the same /v1/tts endpoint.
        """
        for vid in ("vluy2u1jtsif", "abcdefghijkl", "a1b2c3d4e5f6", "123456789abc"):
            VoiceAgent(voice=vid)  # must not raise

    def test_custom_voice_id_uppercase_rejected(self) -> None:
        """Uppercase voice IDs are not valid (xAI spec is lowercase)."""
        with pytest.raises(VoiceAgentConfigError):
            VoiceAgent(voice="ABCDEFGH")  # 8 uppercase
        with pytest.raises(VoiceAgentConfigError):
            VoiceAgent(voice="VLUY2U1JTSIF")  # 12 uppercase

    def test_custom_voice_id_invalid_lengths_rejected(self) -> None:
        """Only exactly 8 OR exactly 12 chars are valid — discrete, not range.

        xAI's spec defines two specific lengths (8 = preset, 12 = clone).
        Anything else (7, 9, 10, 11, 13, ...) must be rejected.
        """
        for vid in ("abcdefg", "abcdefghi", "abcdefghij", "abcdefghijk",
                    "abcdefghijklm", "abcdefghijklmn"):
            with pytest.raises(VoiceAgentConfigError):
                VoiceAgent(voice=vid)

    def test_custom_voice_id_with_special_chars_rejected(self) -> None:
        """Non-alphanumeric chars are not valid in custom voice IDs."""
        with pytest.raises(VoiceAgentConfigError):
            VoiceAgent(voice="abc-defg")  # 8 chars but has dash
        with pytest.raises(VoiceAgentConfigError):
            VoiceAgent(voice="vluy2u1_tsif")  # 12 chars but has underscore

    def test_unsupported_voice_message_mentions_both_lengths(self) -> None:
        """The error must guide users to both preset and clone formats."""
        with pytest.raises(VoiceAgentConfigError) as exc:
            VoiceAgent(voice="NotARealVoice")
        msg = str(exc.value)
        # Should mention the custom-ID option, both lengths, and xAI
        assert "custom" in msg.lower()
        assert "8" in msg   # preset length
        assert "12" in msg  # clone length
        assert "x.ai" in msg.lower() or "xai" in msg.lower()

    def test_lopsided_explicit_kwargs_rejected(self) -> None:
        with pytest.raises(VoiceAgentConfigError):
            VoiceAgent(api_key="x")
        with pytest.raises(VoiceAgentConfigError):
            VoiceAgent(namepoint="x")

    def test_both_explicit_kwargs_accepted(self) -> None:
        agent = VoiceAgent(api_key="k", namepoint="n")
        assert agent._api_key_arg == "k"
        assert agent._namepoint_arg == "n"

    def test_all_supported_voices_accepted(self) -> None:
        for v in SUPPORTED_VOICES:
            VoiceAgent(voice=v)

    def test_explicit_identity_path_overrides_default(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom_identity.json"
        agent = VoiceAgent(identity_path=custom)
        assert agent._identity_path == custom


# ── Identity persistence ───────────────────────────────────────────────────


class TestIdentityPersistence:
    def test_persist_then_load_roundtrip(self, tmp_path: Path) -> None:
        p = tmp_path / "id.json"
        _persist_identity(p, "namepoint99", "mcp_voice_xxx")
        assert _load_identity(p) == ("namepoint99", "mcp_voice_xxx")

    def test_file_written_with_restrictive_permissions(self, tmp_path: Path) -> None:
        # Skip on Windows where chmod is a no-op
        if os.name != "posix":
            pytest.skip("POSIX only")
        p = tmp_path / "id.json"
        _persist_identity(p, "n10", "k")
        assert oct(p.stat().st_mode)[-3:] == "600"

    def test_load_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert _load_identity(tmp_path / "nope.json") is None

    def test_load_raises_for_malformed_json(self, tmp_path: Path) -> None:
        p = tmp_path / "id.json"
        p.write_text("{ not json")
        with pytest.raises(VoiceAgentConfigError) as exc:
            _load_identity(p)
        # Message must guide the user to a recovery action
        assert "Delete" in str(exc.value)

    def test_load_rejects_unknown_schema_version(self, tmp_path: Path) -> None:
        p = tmp_path / "id.json"
        p.write_text(json.dumps({"version": 999, "namepoint": "x", "api_key": "y"}))
        with pytest.raises(VoiceAgentConfigError) as exc:
            _load_identity(p)
        assert "999" in str(exc.value)
        assert str(IDENTITY_SCHEMA_VERSION) in str(exc.value)

    def test_load_rejects_missing_required_fields(self, tmp_path: Path) -> None:
        p = tmp_path / "id.json"
        p.write_text(json.dumps({"version": IDENTITY_SCHEMA_VERSION, "namepoint": "x"}))
        with pytest.raises(VoiceAgentConfigError):
            _load_identity(p)

    def test_persist_creates_parent_directories(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c" / "id.json"
        _persist_identity(deep, "n10", "k")
        assert deep.exists()


# ── Identity resolution precedence ─────────────────────────────────────────


class TestResolveIdentityPrecedence:
    def test_explicit_kwargs_win_over_everything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCPAAS_API_KEY", "env_key")
        monkeypatch.setenv("FAF_SOUL", "env_np")
        p = tmp_path / "id.json"
        _persist_identity(p, "file_np", "file_key")

        np, key = _resolve_identity(
            api_key="explicit_key",
            namepoint="explicit_np",
            identity_path=p,
            anonymous_issue_url="http://should-not-be-called",
        )
        assert (np, key) == ("explicit_np", "explicit_key")

    def test_env_vars_used_when_no_explicit_kwargs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCPAAS_API_KEY", "env_key")
        monkeypatch.setenv("FAF_SOUL", "env_np")
        p = tmp_path / "id.json"
        _persist_identity(p, "file_np", "file_key")

        np, key = _resolve_identity(
            api_key=None,
            namepoint=None,
            identity_path=p,
            anonymous_issue_url="http://should-not-be-called",
        )
        assert (np, key) == ("env_np", "env_key")

    def test_only_one_env_var_set_falls_through_to_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Only one env var set — must NOT use it (incomplete pair)
        monkeypatch.setenv("MCPAAS_API_KEY", "lonely_key")
        monkeypatch.delenv("FAF_SOUL", raising=False)
        p = tmp_path / "id.json"
        _persist_identity(p, "file_np", "file_key")

        np, key = _resolve_identity(
            api_key=None,
            namepoint=None,
            identity_path=p,
            anonymous_issue_url="http://should-not-be-called",
        )
        assert (np, key) == ("file_np", "file_key")

    def test_file_used_when_no_kwargs_no_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MCPAAS_API_KEY", raising=False)
        monkeypatch.delenv("FAF_SOUL", raising=False)
        p = tmp_path / "id.json"
        _persist_identity(p, "file_np", "file_key")

        np, key = _resolve_identity(
            api_key=None,
            namepoint=None,
            identity_path=p,
            anonymous_issue_url="http://should-not-be-called",
        )
        assert (np, key) == ("file_np", "file_key")

    def test_anonymous_provisioning_when_nothing_else_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mocked anonymous-issue HTTP — no live network call."""
        monkeypatch.delenv("MCPAAS_API_KEY", raising=False)
        monkeypatch.delenv("FAF_SOUL", raising=False)
        p = tmp_path / "id.json"

        with patch("grok_faf_voice.agent._provision_anonymous") as mock_prov:
            mock_prov.return_value = ("anonxyz12", "mcp_voice_mock")
            np, key = _resolve_identity(
                api_key=None,
                namepoint=None,
                identity_path=p,
                anonymous_issue_url="http://test",
            )
            assert (np, key) == ("anonxyz12", "mcp_voice_mock")
            mock_prov.assert_called_once_with("http://test")
            # Identity must be persisted after provisioning
            assert p.exists()
            assert _load_identity(p) == ("anonxyz12", "mcp_voice_mock")


# ── FAFContext auto-detection ──────────────────────────────────────────────


class TestProjectFafResolution:
    def test_false_skips_loading(self) -> None:
        assert _maybe_load_faf_context(False) is None

    def test_auto_with_no_project_faf_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert _maybe_load_faf_context("auto") is None

    def test_auto_with_project_faf_loads_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "project.faf").write_text("name: test\n")
        ctx = _maybe_load_faf_context("auto")
        assert ctx is not None
        assert "name: test" in ctx.system_prompt()

    def test_unreadable_faf_silently_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A broken project.faf must not block agent startup."""
        monkeypatch.chdir(tmp_path)
        # Path that exists but FAFContext can't load (an httpx fetch
        # would 404; here we use a path that doesn't exist locally and
        # isn't a valid MCPaaS slug — _maybe_load_faf_context must not
        # raise).
        ctx = _maybe_load_faf_context("nonexistent-slug-xyz-12345")
        # Either returns None, or returns a context that we can't load
        # — either way must not propagate the exception.
        if ctx is not None:
            try:
                ctx.system_prompt()
            except Exception:
                pass


# ── Live integration (optional) ────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("GROK_FAF_VOICE_LIVE_TESTS") != "1",
    reason="Set GROK_FAF_VOICE_LIVE_TESTS=1 to enable live mcpaas.live tests",
)
class TestLiveAnonymousProvisioning:
    def test_live_anonymous_issue_returns_anon_namepoint(
        self, tmp_path: Path
    ) -> None:
        p = tmp_path / "id.json"
        np, key = _resolve_identity(
            api_key=None,
            namepoint=None,
            identity_path=p,
            anonymous_issue_url=DEFAULT_ANONYMOUS_ISSUE_URL,
        )
        assert np.startswith("anon")
        assert len(np) >= 12  # anon + 6 letters + 2 digits minimum
        assert key.startswith("mcp_voice_")
        assert p.exists()
