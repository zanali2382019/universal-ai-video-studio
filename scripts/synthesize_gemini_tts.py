import urllib.request
import json
import base64
import subprocess
from pathlib import Path
import time
import sys
import os

api_key = os.getenv("GEMINI_API_KEY", "")

arabic_text = "فِي ظَهِيرَةِ يَوْمٍ دَافِئٍ، لَمَحَ تَوْمِي سَحَابَةً قُطْنِيَّةً صَغِيرَةً تَهْبِطُ نَحْوَهُ بِلَمَعَانٍ سَاحِر! مَدَّ تَوْمِي يَدَهُ وَدَغْدَغَهَا بِلُطْفٍ، فَضَحِكَتِ السَّحَابَةُ مُطْلِقَةً نَغَمَاتٍ وَبَرِيقًا بَدِيعًا! وَفَجْأَةً، تَنَاثَرَتْ زُهُورٌ مُلَوَّنَةٌ حَوْلَهُ، لِتَمْلأَ الْحَدِيقَةَ بَهْجَةً وَسِحْرًا. أَحْلَامٌ سَعِيدَةٌ مِنْ دْرِيمْ مَاتِر!"

instructions = (
    "Speak the exact Arabic text in clear, authentic Modern Standard Arabic. "
    "Use precise articulation of Arabic consonants, correct vowel realization and grammatical endings, "
    "natural connected speech, a mature warm male storytelling voice, expressive and friendly bedtime narration, "
    "and natural pauses. Preserve every word exactly. Do not translate, paraphrase, omit, or add words."
)

voice_id = sys.argv[1] if len(sys.argv) > 1 else "Enzo"
output_path = Path("channels/ch_dreammatter_001/media_vault/originals/audio/tommy_ep3_voiceover_ar.aac")
output_path.parent.mkdir(parents=True, exist_ok=True)

raw_wav = Path("temp_gemini_tts.wav")

url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent?key={api_key}"
payload = {
    "contents": [
        {
            "parts": [
                {
                    "text": f"{instructions}\n\n## Transcript:\n{arabic_text}"
                }
            ]
        }
    ],
    "generationConfig": {
        "temperature": 1,
        "responseModalities": ["audio"],
        "speechConfig": {
            "voiceConfig": {
                "prebuiltVoiceConfig": {
                    "voiceName": voice_id
                }
            }
        }
    }
}

req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

print(f"Synthesizing voice '{voice_id}' via Gemini 3.8 Flash TTS...")
t0 = time.time()
with urllib.request.urlopen(req, timeout=90) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    elapsed = time.time() - t0
    print(f"Response received in {elapsed:.2f}s")

candidates = data.get("candidates", [])
if not candidates:
    raise RuntimeError(f"No candidates in response: {data}")

parts = candidates[0].get("content", {}).get("parts", [])
audio_part = None
for p in parts:
    if "inlineData" in p:
        audio_part = p["inlineData"]
        break

if not audio_part:
    raise RuntimeError("No inlineData found in response parts.")

audio_bytes = base64.b64decode(audio_part["data"])
raw_wav.write_bytes(audio_bytes)
print(f"Raw WAV saved: {len(audio_bytes)} bytes")

# Convert to 48kHz stereo AAC
ffmpeg_cmd = [
    "ffmpeg", "-y",
    "-i", str(raw_wav),
    "-c:a", "aac",
    "-b:a", "192k",
    "-ar", "48000",
    "-ac", "2",
    str(output_path)
]
subprocess.run(ffmpeg_cmd, check=True)
raw_wav.unlink(missing_ok=True)
print(f"Successfully converted to 48kHz stereo AAC: {output_path} ({output_path.stat().st_size} bytes)")

