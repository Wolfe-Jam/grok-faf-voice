async def inject_expressive_tags(text: str, persona: str = "nelly") -> str:
    if persona == "nelly":
        text = text.replace("jingle", "<sing intensity='medium'>jingle</sing>")
        text = text.replace("wobble", "<wobble intensity='high'>wobble</wobble>")
        text = text.replace("happy", "<laugh>happy</laugh>")
    return text
