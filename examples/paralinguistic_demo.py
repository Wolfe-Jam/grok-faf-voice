"""Paralinguistic demo — voice memory that remembers HOW you spoke.

The agent records tone / emotional state / speaking style across
sessions and opens future calls with awareness.

Run::

    cp .env.example .env
    # Set XAI_API_KEY, LIVEKIT_*, and (when available) MCPAAS_TOKEN
    python examples/paralinguistic_demo.py console

In session 1, speak with energy — frustrated, excited, hurried, calm.
The agent will call `note_paralinguistic` when it notices.

In session 2 (different day), the agent opens with awareness:

    "Last time you sounded frustrated about the checkout flow — should
     we revisit, or fresh topic today?"

This is the live, audio-aware, session-persistent layer that the
static MCP can't provide.
"""

import os

from dotenv import load_dotenv
from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import xai

from grok_faf_voice import FAFContext, FAFMemory

load_dotenv()

faf = FAFContext("project.faf")
mem = FAFMemory(
    soul=os.environ.get("FAF_SOUL", "grok"),
    token=os.environ.get("MCPAAS_TOKEN"),
)

server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    # Compose system prompt: static .faf + paralinguistic memory of
    # prior sessions. The summary is empty if there's nothing yet, so
    # this composition works clean from the very first session.
    base_prompt = faf.system_prompt()
    para_summary = await mem.paralinguistic_summary()

    instructions = base_prompt
    if para_summary:
        instructions += f"\n\n{para_summary}"

    session = AgentSession(
        llm=xai.realtime.RealtimeModel(voice="Ara"),
    )
    await session.start(
        room=ctx.room,
        agent=Agent(
            instructions=instructions,
            tools=mem.tools(),  # etch + recall + note_paralinguistic
        ),
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
