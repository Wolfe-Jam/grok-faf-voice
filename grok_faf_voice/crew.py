from livekit.agents import JobContext
from . import RadioFAFSoulStateAgent

async def spawn_radiofaf_crew(ctx: JobContext, faf_path: str = "radiofaf.faf"):
    crew = {}
    voices = {"nelly": "nelly", "leo": "leo", "eve": "eve", "ara": "ara", "rex": "rex"}
    for role, voice in voices.items():
        agent = RadioFAFSoulStateAgent(faf_path=faf_path, voice=voice)
        agent.instructions += f"\nYou are {role.upper()} in the RadioFAF crew."
        session = await create_radiofaf_session(ctx)
        crew[role] = session
    return crew
