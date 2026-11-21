import asyncio
import edge_tts

async def list_ar():
    voices = await edge_tts.list_voices()
    ar_voices = [v for v in voices if v["Locale"].startswith("ar-")]
    print(f"Total Arabic Neural Voices: {len(ar_voices)}")
    for v in ar_voices:
        print(f"{v['ShortName']:<22} | {v['Gender']:<6} | {v['Locale']:<7} | {v.get('FriendlyName', '')}")

if __name__ == "__main__":
    asyncio.run(list_ar())

