import urllib.request
import subprocess
from pathlib import Path
import hashlib
import json

voices = [
    "ar-XA-Chirp3-HD-Achird",
    "ar-XA-Chirp3-HD-Algenib",
    "ar-XA-Chirp3-HD-Algieba",
    "ar-XA-Chirp3-HD-Charon"
]

out_dir = Path("channels/ch_dreammatter_001/exports/tests/google_chirp3_msa")
out_dir.mkdir(parents=True, exist_ok=True)

ffmpeg_path = r"C:\Users\96777\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

results = {}

for voice in voices:
    url = f"https://docs.cloud.google.com/static/text-to-speech/docs/audio/{voice}.wav"
    wav_path = out_dir / f"{voice}_official_sample.wav"
    mp3_path = out_dir / f"{voice}_official_sample.mp3"
    
    print(f"Downloading {voice} from {url}...")
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
        wav_path.write_bytes(data)
        
    # Convert cleanly to 48kHz stereo MP3
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
    
    with open(mp3_path, "rb") as f:
        sha_mp3 = hashlib.sha256(f.read()).hexdigest().upper()
    with open(wav_path, "rb") as f:
        sha_wav = hashlib.sha256(f.read()).hexdigest().upper()
        
    cmd_probe = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(mp3_path)]
    probe = json.loads(subprocess.check_output(cmd_probe))
    
    results[voice] = {
        "wav_path": str(wav_path),
        "wav_size": wav_path.stat().st_size,
        "wav_sha256": sha_wav,
        "mp3_path": str(mp3_path),
        "mp3_size": mp3_path.stat().st_size,
        "mp3_sha256": sha_mp3,
        "duration": float(probe["format"]["duration"]),
        "sample_rate": int(probe["streams"][0]["sample_rate"]),
        "channels": int(probe["streams"][0]["channels"]),
        "bit_rate": int(probe["format"].get("bit_rate", 0))
    }

print("Results:", json.dumps(results, indent=2))
with open(out_dir / "official_samples_audit.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2)

