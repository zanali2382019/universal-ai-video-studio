import urllib.request
import json
import base64
import subprocess
from pathlib import Path
import hashlib
import time
import os

api_key = os.getenv("GEMINI_API_KEY", "")

benchmark_text = "فِي لَيْلَةٍ شَاتِيَةٍ سَاكِنَةٍ، تَطَلَّعَ الطِّفْلُ الصَّغِيرُ مِنْ نَافِذَةِ حُجْرَتِهِ نَحْوَ ظُلْمَةِ الأُفُقِ الْبَعِيدِ؛ فَإِذَا بِضِيَاءِ نَجْمٍ ذَهَبِيٍّ ثَاقِبٍ، يَتَلَأْلَأُ خَلْفَ سَحَابٍ رَقِيقٍ. هَمَسَ الْفَتَى بِفَرَحٍ، وَقَدْ غَمَرَتِ الطُّمَأْنِينَةُ فُؤَادَهُ: يَا لَهُ مِنْ مَشْهَدٍ بَدِيعٍ يَفِيضُ سِحْرًا وَدِفْئًا! هُنَاكَ فِي عَالَمِ \"دْرِيمْ مَاتِر\"، تُبْعَثُ أَجْمَلُ الرُّؤَى حَقِيقَةً لَا تَغِيبُ."

instructions = (
    "Speak the exact Arabic text in clear, authentic Modern Standard Arabic. "
    "Use precise articulation of Arabic consonants, correct vowel realization and grammatical endings, "
    "natural connected speech, a mature warm male storytelling voice, expressive but restrained intonation, "
    "and meaningful pauses. Avoid colloquial dialect, robotic rhythm, exaggerated theatrical performance, "
    "and foreign-influenced Arabic pronunciation. Preserve every word exactly. Do not translate, paraphrase, omit, or add words."
)

voices = [
    {"id": "Enzo", "name": "gemini_38_enzo", "profile": "Mature warm storytelling narrator (Storyteller preset)"},
    {"id": "Riko", "name": "gemini_38_riko", "profile": "Expressive, measured storytelling delivery (Storyteller preset)"},
    {"id": "Cruz", "name": "gemini_38_cruz", "profile": "Confident, clear, professional narrator (Professional preset)"}
]

out_dir = Path("channels/ch_dreammatter_001/exports/tests/gemini_38_arabic")
out_dir.mkdir(parents=True, exist_ok=True)

ffmpeg_path = r"C:\Users\96777\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"

results = {}

for v in voices:
    voice_id = v["id"]
    file_prefix = v["name"]
    wav_path = out_dir / f"{file_prefix}.wav"
    mp3_path = out_dir / f"{file_prefix}.mp3"
    
    print(f"\n--- Synthesizing voice: {voice_id} ({v['profile']}) ---")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": f"{instructions}\n\n## Transcript:\n{benchmark_text}"
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
    
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            elapsed = time.time() - t0
            print(f"Response received in {elapsed:.2f}s")
            
            candidates = data.get("candidates", [])
            if not candidates:
                print("No candidates in response:", data)
                continue
                
            parts = candidates[0].get("content", {}).get("parts", [])
            audio_part = None
            for p in parts:
                if "inlineData" in p:
                    audio_part = p["inlineData"]
                    break
                    
            if not audio_part:
                print("No inlineData found in parts:", parts)
                continue
                
            audio_bytes = base64.b64decode(audio_part["data"])
            wav_path.write_bytes(audio_bytes)
            print(f"Saved raw WAV: {wav_path} ({len(audio_bytes)} bytes)")
            
            # Convert to 48kHz stereo 192k MP3
            cmd = [
                ffmpeg_path, "-y",
                "-i", str(wav_path),
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                "-ar", "48000",
                "-ac", "2",
                str(mp3_path)
            ]
            subprocess.run(cmd, check=True)
            print(f"Converted to MP3: {mp3_path} ({mp3_path.stat().st_size} bytes)")
            
            # Measure ffprobe
            cmd_probe = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(mp3_path)]
            probe = json.loads(subprocess.check_output(cmd_probe))
            
            with open(mp3_path, "rb") as f:
                sha_mp3 = hashlib.sha256(f.read()).hexdigest().upper()
            with open(wav_path, "rb") as f:
                sha_wav = hashlib.sha256(f.read()).hexdigest().upper()
                
            cmd_lufs = ["ffmpeg", "-nostats", "-i", str(mp3_path), "-filter_complex", "ebur128=peak=true", "-f", "null", "-"]
            res_lufs = subprocess.run(cmd_lufs, stderr=subprocess.PIPE, text=True)
            out_lufs = res_lufs.stderr
            
            import re
            i_m = re.search(r"I:\s+([-\d.]+)\s+LUFS", out_lufs)
            tp_m = re.search(r"Peak:\s+([-\d.]+)\s+dBFS", out_lufs)
            
            results[voice_id] = {
                "voice_name": voice_id,
                "profile": v["profile"],
                "wav_file": str(wav_path),
                "wav_size": len(audio_bytes),
                "wav_sha256": sha_wav,
                "mp3_file": str(mp3_path),
                "mp3_size": mp3_path.stat().st_size,
                "mp3_sha256": sha_mp3,
                "duration": float(probe["format"]["duration"]),
                "sample_rate": int(probe["streams"][0]["sample_rate"]),
                "channels": int(probe["streams"][0]["channels"]),
                "bitrate": int(probe["format"].get("bit_rate", 0)),
                "i_lufs": i_m.group(1) if i_m else "N/A",
                "tp_dbtp": tp_m.group(1) if tp_m else "N/A"
            }
            
    except urllib.error.HTTPError as e:
        print(f"HTTPError for {voice_id}:", e.code, e.read().decode("utf-8"))
    except Exception as e:
        print(f"Exception for {voice_id}:", e)
        
    time.sleep(1)

with open(out_dir / "gemini_38_benchmark_audit.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)

print("\nAll Results:")
print(json.dumps(results, indent=2))

