"""Side-note utilities for grok-faf-voice.

Dev-grade tools that ship with the SDK but live OUTSIDE the core API
surface (FAFContext, FAFMemory, Scratchpad). Every utility here has
a corresponding "fancy UI affordance" that FAF-Voice's frontend will
eventually expose — same primitive, two surfaces.

Today:
- transcribe — convert any audio/video file to text via xAI STT

Future candidates:
- summarize — soul-aware conversation summaries
- export_srt / export_vtt — caption files for the recording
- extract_paralinguistic — pull tone/emotion timeline from audio
"""
