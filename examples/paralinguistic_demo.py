"""Paralinguistic demo — voice memory that remembers HOW you spoke.

The agent records tone / emotional state / speaking style across
sessions and opens future calls with awareness.

Run::

    cp .env.example .env
    # Set XAI_API_KEY, LIVEKIT_*, and (when available) MCPAAS_TOKEN
    python examples/paralinguistic_demo.py console

In session 1, speak with energy — frustrated, excited, hurried, calm.
The agent will call `note_paralinguistic` when it notices.

A later turn can call recall_memory. The open prompt does not carry
the soul. note_paralinguistic stages a marker; it is saved only after
the agent speaks it and the namepoint holder confirms.

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
    api_key=os.environ.get("MCPAAS_API_KEY"),
)

server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        llm=xai.realtime.RealtimeModel(voice="Ara"),
    )
    await session.start(
        room=ctx.room,
        agent=Agent(
            instructions=faf.system_prompt(),
            tools=mem.tools(session),
        ),
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
