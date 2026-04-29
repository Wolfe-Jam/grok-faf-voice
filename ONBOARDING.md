# Onboarding — grok-faf-voice

Get a contributor (or yourself, on a fresh machine) productive in
under five minutes.

---

## System requirements

**Four keys** — paste into `.env`:

| Key | Source |
|---|---|
| `XAI_API_KEY` | [console.x.ai](https://console.x.ai) |
| `LIVEKIT_URL` | [cloud.livekit.io](https://cloud.livekit.io) → your project |
| `LIVEKIT_API_KEY` | same LiveKit project dashboard |
| `LIVEKIT_API_SECRET` | same LiveKit project dashboard |

**One namepoint** — your soul address (where your agent's memories live):

- **Free tier**: `name + 2 digits` — e.g. `james77`, `amy123`,
  `atlanta96`. Pick yours at [mcpaas.live](https://mcpaas.live).
- Premium tiers (paid): 3-letter ($9), 4+ letters ($2). See
  mcpaas.live for full tier details.
- Set `FAF_SOUL=<your-namepoint>` in `.env`.

**The namepoint is your etch destination, not a token.** Reads
against public souls (`grok`, `faf`, `nelly`, `spacex`, etc.) work
today without any auth.

**Voice key (write auth)** — free flow launching soon:

- A free Voice key flow is launching on `mcpaas.live`. Until it
  ships, `FAFMemory` is **read-only** against public souls (`grok`,
  `faf`, `nelly`, `spacex`, etc.) — useful for exercising the recall
  path while you wait for the writeable side.
- The agent loop, scratchpad, paralinguistic markers, and the merge
  engine all run today against in-memory state and public-soul
  reads. The persistence-to-soul step (etch / write_soul) needs the
  Voice key.
- Once the page is live, set `MCPAAS_TOKEN=<your-voice-key>` in
  `.env` and your namepoint becomes write-capable.

> Note: the existing [mcpaas.live/slash/dashboard](https://mcpaas.live/slash/dashboard)
> issues paid tokens for the Slash API gateway product (token-budget
> estimation, separate from Voice). Don't use Slash tokens for Voice
> writes — wait for the free Voice key page.

---

## Four steps

1. **Clone + venv**
   ```bash
   git clone https://github.com/Wolfe-Jam/grok-faf-voice
   cd grok-faf-voice
   python3 -m venv .venv && source .venv/bin/activate
   ```

2. **Install + configure**
   ```bash
   pip install -e ".[dev]"
   cp .env.example .env       # paste your 4 keys + namepoint
   ```

3. **Verify with WJTTC**
   ```bash
   ./scripts/wjttc.sh             # BRAKE + ENGINE + AERO (default)
   ./scripts/wjttc.sh --tyres     # + live probes against xAI + MCPaaS
   ```
   Green across every tier = your setup is healthy. Red on a tier
   tells you which layer broke.

4. **Talk to your agent**
   ```bash
   python examples/hello_grok_with_etch.py console
   ```
   In the voice loop, try:
   > "Etch this — first contact verified."

   Close the session. Restart. Ask:
   > "What do you remember?"

   The agent recalls — that's the cross-session loop, persisted to
   your namepoint via MCPaaS.

---

## PR conventions

- Bug fixes ship with a regression test. (See v0.1.1's
  `test_attach_auto_merge_resets_flags_for_session_reuse` for the
  pattern — the test came with the fix, not after.)
- WJTTC sweep must pass: BRAKE + ENGINE + AERO green.
- Ruff must be clean.
- New upstream-surface dependencies (LiveKit, xAI, MCPaaS, fastmcp,
  Pydantic) get a contract pin in `tests/test_wjttc_contracts.py`
  so drift surfaces in CI before users hit it.

See [WJTTC.md](WJTTC.md) for the full test regime spec.
