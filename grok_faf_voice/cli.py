import asyncio
import click
from livekit.agents import AgentServer
from . import create_radiofaf_session
from .crew import spawn_radiofaf_crew

@click.command()
@click.option("--crew", is_flag=True)
@click.option("--faf", default="radiofaf.faf")
def main(crew: bool, faf: str):
    server = AgentServer()
    @server.rtc_session()
    async def entrypoint(ctx):
        if crew:
            await spawn_radiofaf_crew(ctx, faf)
        else:
            await create_radiofaf_session(ctx, faf)
    asyncio.run(server.run())

if __name__ == "__main__":
    main()
