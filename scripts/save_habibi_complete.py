import urllib.request
import subprocess
from pathlib import Path

audio_url = "https://chenxie95-habibi-tts.hf.space/gradio_api/file=/tmp/gradio/c0239a41b4f30ba8c7acb78a3d7032b4cf2822bd33c3a512c07a802d99678edf/audio.wav"
out_dir = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark")
raw_wav = out_dir / "habibi_msa_specialized_complete.wav"
final_mp3 = out_dir / "habibi_msa_specialized_complete.mp3"

ffmpeg_path = r"C:\Users\96777\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

print(f"Downloading Habibi complete sample from {audio_url}...")
req = urllib.request.Request(audio_url, headers=headers)
with urllib.request.urlopen(req, timeout=30) as resp:
    data = resp.read()
    raw_wav.write_bytes(data)

print(f"Downloaded WAV: {raw_wav} ({raw_wav.stat().st_size} bytes)")

# Convert cleanly to 48kHz stereo MP3 192k
cmd = [
    ffmpeg_path, "-y",
    "-i", str(raw_wav),
    "-c:a", "libmp3lame",
    "-b:a", "192k",
    "-ar", "48000",
    "-ac", "2",
    str(final_mp3)
]
subprocess.run(cmd, check=True)
print(f"Successfully converted to complete benchmark MP3: {final_mp3} ({final_mp3.stat().st_size} bytes)")

