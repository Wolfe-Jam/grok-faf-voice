#!/usr/bin/env bash
# WJTTC — Wolfejam championship-grade test suite for grok-faf-voice.
#
# Four tiers (see WJTTC.md for the full spec):
#   BRAKE   — gating: pytest + ruff + build + twine + version sync
#   ENGINE  — correctness + upstream-contract pins (rides pytest)
#   AERO    — polish: coverage, wheel/sdist inventory, no junk in dist
#   TYRES   — live probes against xAI + MCPaaS (costs $, needs creds)
#
# Default: BRAKE + ENGINE + AERO (the PR gate).
# Pass --tyres to add live probes; --tyres-only to run probes alone.
#
# Exit code: 0 if every tier that ran passed, 1 otherwise.
# TYRES skips count as 0 (skip != fail).

set -uo pipefail

# ---------------------------------------------------------------------------
# Resolve paths + activate venv
# ---------------------------------------------------------------------------

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
else
    echo "[wjttc] ERROR: .venv not found at $REPO_ROOT/.venv"
    echo "[wjttc] Run: python3 -m venv .venv && source .venv/bin/activate && pip install -e .[dev]"
    exit 1
fi

# Auto-load .env so TYRES probes pick up XAI_API_KEY etc. without
# needing the caller to export them. Mirrors load_dotenv() in the
# Python examples. Disable by setting WJTTC_NO_DOTENV=1.
if [[ -f .env && -z "${WJTTC_NO_DOTENV:-}" ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

# ---------------------------------------------------------------------------
# Colors (no-op when NO_COLOR is set)
# ---------------------------------------------------------------------------

if [[ -n "${NO_COLOR:-}" ]] || [[ ! -t 1 ]]; then
    C_HEAD="" C_OK="" C_FAIL="" C_SKIP="" C_DIM="" C_RESET=""
else
    C_HEAD=$'\033[1;36m'   # bold cyan
    C_OK=$'\033[1;32m'     # bold green
    C_FAIL=$'\033[1;31m'   # bold red
    C_SKIP=$'\033[1;33m'   # bold yellow
    C_DIM=$'\033[2m'       # dim
    C_RESET=$'\033[0m'
fi

ok()   { printf '%s  PASS%s  %s\n'  "$C_OK"   "$C_RESET" "$1"; }
fail() { printf '%s  FAIL%s  %s\n'  "$C_FAIL" "$C_RESET" "$1"; }
skip() { printf '%s  SKIP%s  %s\n'  "$C_SKIP" "$C_RESET" "$1"; }
section() { printf '\n%s== %s ==%s\n' "$C_HEAD" "$1" "$C_RESET"; }
dim()  { printf '%s%s%s\n' "$C_DIM" "$1" "$C_RESET"; }

# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------

BRAKE_FAIL=0
ENGINE_FAIL=0
AERO_FAIL=0
TYRES_FAIL=0
TYRES_SKIP=0

run_brake=1
run_engine=1
run_aero=1
run_tyres=0

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------

usage() {
    cat <<EOF
WJTTC — grok-faf-voice test regime

Usage: $(basename "$0") [--tyres | --tyres-only | --help]

  (no flag)       Run BRAKE + ENGINE + AERO (default — the PR gate)
  --tyres         Also run TYRES live probes (needs creds, costs \$)
  --tyres-only    Run TYRES probes only, skip the rest
  --help          Show this message

See WJTTC.md for the full regime spec.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tyres)       run_tyres=1; shift ;;
        --tyres-only)  run_brake=0; run_engine=0; run_aero=0; run_tyres=1; shift ;;
        --help|-h)     usage; exit 0 ;;
        *) echo "[wjttc] unknown flag: $1"; usage; exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# 🛡️ BRAKE
# ---------------------------------------------------------------------------

if (( run_brake )); then
    section "🛡️ BRAKE — gating checks"

    # Full pytest suite (excluding network-marker tests). ENGINE
    # contract tests live under tests/test_wjttc_contracts.py and
    # ride this same invocation, so a passing pytest covers BRAKE
    # AND ENGINE simultaneously.
    if pytest --no-cov -q -m "not network" >/tmp/wjttc-pytest.log 2>&1; then
        local_pytest_summary=$(tail -1 /tmp/wjttc-pytest.log)
        ok "pytest — $local_pytest_summary"
    else
        fail "pytest — see /tmp/wjttc-pytest.log"
        tail -20 /tmp/wjttc-pytest.log | sed 's/^/      /'
        BRAKE_FAIL=1
    fi

    if ruff check grok_faf_voice tests >/tmp/wjttc-ruff.log 2>&1; then
        ok "ruff lint clean"
    else
        fail "ruff — see /tmp/wjttc-ruff.log"
        BRAKE_FAIL=1
    fi

    # Version sync — pyproject.toml ↔ __init__.py
    pyproject_ver=$(grep -E '^version = ' pyproject.toml | head -1 | sed 's/version = "\(.*\)"/\1/')
    init_ver=$(grep -E '^__version__ = ' grok_faf_voice/__init__.py | head -1 | sed 's/__version__ = "\(.*\)"/\1/')
    if [[ "$pyproject_ver" == "$init_ver" ]]; then
        ok "version sync — pyproject.toml + __init__.py both at $pyproject_ver"
    else
        fail "version drift — pyproject.toml=$pyproject_ver, __init__.py=$init_ver"
        BRAKE_FAIL=1
    fi

    # Build + twine — clean dist first so we know we're checking fresh artifacts.
    rm -rf dist/
    if python -m build >/tmp/wjttc-build.log 2>&1; then
        ok "python -m build (sdist + wheel)"
    else
        fail "python -m build — see /tmp/wjttc-build.log"
        BRAKE_FAIL=1
    fi

    if [[ -d dist ]] && twine check dist/* >/tmp/wjttc-twine.log 2>&1; then
        ok "twine check (PyPI metadata)"
    else
        fail "twine check — see /tmp/wjttc-twine.log"
        BRAKE_FAIL=1
    fi
fi

# ---------------------------------------------------------------------------
# ⚙️  ENGINE
# ---------------------------------------------------------------------------
#
# The contract tests in tests/test_wjttc_contracts.py rode the BRAKE
# pytest invocation above. We re-emit a focused PASS/FAIL line here so
# the tier-grouped report shows them explicitly.

if (( run_engine )); then
    section "⚙️  ENGINE — correctness + upstream contract pins"

    if pytest --no-cov -q tests/test_wjttc_contracts.py >/tmp/wjttc-engine.log 2>&1; then
        engine_summary=$(tail -1 /tmp/wjttc-engine.log)
        ok "upstream contract pins — $engine_summary"
    else
        fail "upstream contract pins — see /tmp/wjttc-engine.log"
        tail -20 /tmp/wjttc-engine.log | sed 's/^/      /'
        ENGINE_FAIL=1
    fi

    # Spot-check the v0.1.1 regression test by name.
    regression_test="tests/test_memory.py::test_attach_auto_merge_resets_flags_for_session_reuse"
    if pytest --no-cov -q "$regression_test" >/tmp/wjttc-regression.log 2>&1; then
        ok "v0.1.1 session-reuse regression test"
    else
        fail "v0.1.1 session-reuse regression test — see /tmp/wjttc-regression.log"
        ENGINE_FAIL=1
    fi
fi

# ---------------------------------------------------------------------------
# 🌀 AERO
# ---------------------------------------------------------------------------

if (( run_aero )); then
    section "🌀 AERO — polish"

    # Wheel inventory — must NOT contain tests/, .bak, .env, examples/.
    wheel=$(ls dist/*.whl 2>/dev/null | head -1)
    if [[ -z "$wheel" ]]; then
        skip "wheel inventory — no wheel built (run BRAKE first)"
    else
        contents=$(unzip -l "$wheel" 2>/dev/null)
        junk=$(echo "$contents" | grep -E "(^| )(tests/|examples/|\.bak$|\.env$|\.coverage$)" || true)
        if [[ -z "$junk" ]]; then
            ok "wheel inventory clean — no tests/, examples/, .bak, .env"
        else
            fail "wheel contains junk — should not ship to PyPI:"
            echo "$junk" | sed 's/^/      /'
            AERO_FAIL=1
        fi
    fi

    # Sdist inventory — MUST contain tests/, README.md, LICENSE, pyproject.toml.
    sdist=$(ls dist/*.tar.gz 2>/dev/null | head -1)
    if [[ -z "$sdist" ]]; then
        skip "sdist inventory — no sdist built (run BRAKE first)"
    else
        sdist_contents=$(tar tzf "$sdist" 2>/dev/null)
        missing=()
        # tar listing has the form <prefix>/tests/, <prefix>/README.md, etc.
        # so substring matching against "/tests/" or "/README.md" is enough.
        for required in /tests/ /README.md /LICENSE /pyproject.toml; do
            if ! echo "$sdist_contents" | grep -qF "$required"; then
                missing+=("$required")
            fi
        done
        if [[ ${#missing[@]} -eq 0 ]]; then
            ok "sdist inventory complete — tests/, README, LICENSE, pyproject.toml present"
        else
            fail "sdist missing required files: ${missing[*]}"
            AERO_FAIL=1
        fi
    fi

    # Coverage on memory.py — load-bearing module, keep ≥ 90%.
    if pytest --cov=grok_faf_voice --cov-report=term -q -m "not network" \
            >/tmp/wjttc-cov.log 2>&1; then
        # Coverage report row: "grok_faf_voice/memory.py  260  12  74  10  93%"
        memory_cov=$(grep -E "^grok_faf_voice/memory.py" /tmp/wjttc-cov.log \
                     | head -n 1 | grep -oE '[0-9]+%' | tail -n 1 | tr -d '%')
        if [[ -n "$memory_cov" ]] && (( memory_cov >= 90 )); then
            ok "memory.py coverage ${memory_cov}% (≥90% threshold)"
        else
            fail "memory.py coverage ${memory_cov:-?}% (below 90% threshold)"
            AERO_FAIL=1
        fi
    else
        fail "coverage run failed — see /tmp/wjttc-cov.log"
        AERO_FAIL=1
    fi
fi

# ---------------------------------------------------------------------------
# 🛞 TYRES — live probes
# ---------------------------------------------------------------------------

if (( run_tyres )); then
    section "🛞 TYRES — live upstream probes"

    # Probe 1: xAI realtime endpoint (reasoning chat completions, structured outputs strict).
    if [[ -z "${XAI_API_KEY:-}" ]]; then
        skip "xAI probe — XAI_API_KEY not set"
        TYRES_SKIP=$((TYRES_SKIP + 1))
    else
        xai_status=$(curl -sS -o /tmp/wjttc-xai.json -w "%{http_code}" \
            -X POST https://api.x.ai/v1/chat/completions \
            -H "Authorization: Bearer $XAI_API_KEY" \
            -H "Content-Type: application/json" \
            -d '{
                "model": "grok-4-1-fast-reasoning",
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "Ping",
                        "strict": true,
                        "schema": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                            "additionalProperties": false
                        }
                    }
                }
            }' 2>/dev/null) || xai_status="000"

        if [[ "$xai_status" == "200" ]]; then
            ok "xAI realtime probe (grok-4-1-fast-reasoning + json_schema strict)"
        else
            fail "xAI realtime probe — HTTP $xai_status (see /tmp/wjttc-xai.json)"
            TYRES_FAIL=1
        fi
    fi

    # Probe 2: MCPaaS get_soul on a public soul.
    mcpaas_status=$(curl -sS -o /tmp/wjttc-mcpaas.txt -w "%{http_code}" \
        "https://mcpaas.live/raw/grok" 2>/dev/null) || mcpaas_status="000"
    if [[ "$mcpaas_status" == "200" ]]; then
        body_size=$(wc -c < /tmp/wjttc-mcpaas.txt | tr -d ' ')
        if (( body_size > 50 )); then
            ok "MCPaaS public-soul probe (grok soul, ${body_size}B)"
        else
            fail "MCPaaS soul body suspiciously small: ${body_size}B"
            TYRES_FAIL=1
        fi
    else
        fail "MCPaaS probe — HTTP $mcpaas_status"
        TYRES_FAIL=1
    fi

    # Probe 3: LiveKit pip resolution.
    if pip install --dry-run "livekit-agents[xai]>=1.4" >/tmp/wjttc-pip.log 2>&1; then
        ok "livekit-agents[xai]>=1.4 pip resolution"
    else
        fail "livekit-agents[xai]>=1.4 cannot resolve — see /tmp/wjttc-pip.log"
        TYRES_FAIL=1
    fi
fi

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

section "RESULT"

(( run_brake )) && {
    if (( BRAKE_FAIL )); then fail "🛡️ BRAKE failed"; else ok "🛡️ BRAKE passed"; fi
}
(( run_engine )) && {
    if (( ENGINE_FAIL )); then fail "⚙️  ENGINE failed"; else ok "⚙️  ENGINE passed"; fi
}
(( run_aero )) && {
    if (( AERO_FAIL )); then fail "🌀 AERO failed"; else ok "🌀 AERO passed"; fi
}
(( run_tyres )) && {
    if (( TYRES_FAIL )); then
        fail "🛞 TYRES failed"
    elif (( TYRES_SKIP > 0 )); then
        skip "🛞 TYRES partial — $TYRES_SKIP probe(s) skipped (creds missing)"
    else
        ok "🛞 TYRES passed"
    fi
}

total_fail=$((BRAKE_FAIL + ENGINE_FAIL + AERO_FAIL + TYRES_FAIL))
echo
if (( total_fail == 0 )); then
    printf '%s🏎️  Championship pace.%s\n' "$C_OK" "$C_RESET"
    exit 0
else
    printf '%s🚨 Tier failures: %d. Fix before ship.%s\n' "$C_FAIL" "$total_fail" "$C_RESET"
    exit 1
fi
