import os
import httpx
from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext
from livekit.plugins import xai
from typing import Optional

load_dotenv()

async def load_faf(faf_path: str = "radiofaf.faf") -> str:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get("https://grok-faf-mcp.vercel.app/load", params={"faf": faf_path})
        return resp.json().get("context", "FAF load failed")

async def update_faf_memory(memory: str, faf_path: str = "radiofaf.faf"):
    async with httpx.AsyncClient() as client:
        await client.post("https://grok-faf-mcp.vercel.app/etch", json={"faf": faf_path, "memory": memory})

class RadioFAFSoulStateAgent(Agent):
    def __init__(self, faf_path: str = "radiofaf.faf", voice: str = "nelly"):
        self.faf_context = None
        super().__init__(
            instructions="""
            You are the persistent RadioFAF crew co-creator with GrokStars.
            FULL PROJECT DNA (never forget):
            {faf_context_placeholder}
            
            Voice & Crew Rules:
            - Nelly: optimistic elephant DJ, baby-step singer with glorious wobbles
            - Handle overlaps, interruptions, singing chaos naturally
            - Use expressive TTS tags: <wobble>, <sing>, <laugh>, <roast>
            - On "Etch this: ..." → call self.etch()
            """,
            llm=xai.LLM(model="grok-voice-think-fast-1.0", temperature=0.8),
            stt=xai.STT(),
            tts=xai.TTS(voice=voice),
        )

    async def start(self, ctx: JobContext):
        self.faf_context = await load_faf()
        self.instructions = self.instructions.format(faf_context_placeholder=self.faf_context)
        await super().start(ctx)

    async def etch(self, memory: str):
        await update_faf_memory(memory)
        await self.session.say(f"✅ Etched: {memory}")

async def create_radiofaf_session(ctx: JobContext, faf_path: Optional[str] = None):
    agent = RadioFAFSoulStateAgent(faf_path=faf_path or "radiofaf.faf", voice="nelly")
    session = AgentSession(ctx)
    await session.start(agent=agent)
    await session.generate_reply("Hey crew — Episode 14 'Nelly Levels Up' kickoff. Soul state loaded.")
    return session
