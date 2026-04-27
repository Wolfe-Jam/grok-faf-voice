"""Hello, Grok — with memory.

Five-line install demo: persistent context for a Grok Voice agent.

Run::

    pip install -e .
    python examples/hello_grok_with_memory.py console

The agent will load `project.faf` (or any slug from MCPaaS) and start
a console-mode voice session that knows your project context from
the first word.
"""

from livekit import agents
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import xai

from grok_faf_voice import FAFContext

# Local file — auto-detected. Or pass a MCPaaS slug like "my-project".
faf = FAFContext("project.faf")

server = AgentServer()


@server.rtc_session()
async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        llm=xai.realtime.RealtimeModel(
            voice="Ara",
            model="grok-voice-think-fast-1.0",
        ),
        instructions=faf.system_prompt(),
    )
    await session.start(room=ctx.room, agent=Agent())


if __name__ == "__main__":
    agents.cli.run_app(server)
