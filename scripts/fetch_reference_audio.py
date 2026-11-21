import subprocess
from pathlib import Path

ffmpeg_path = r"C:\Users\96777\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
out_dir = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark")
out_dir.mkdir(parents=True, exist_ok=True)

target_mp3 = out_dir / "reference_ahmad_fakhouri_20s.mp3"
target_wav = out_dir / "reference_ahmad_fakhouri_20s.wav"

url = "https://www.youtube.com/watch?v=FTYrvehWqg4"

cmd = [
    "python", "-m", "yt_dlp",
    "-f", "ba/b",
    "--ffmpeg-location", ffmpeg_path,
    "--download-sections", "*00:00-00:20",
    "--force-keyframes-at-cuts",
    "-x",
    "--audio-format", "mp3",
    "--audio-quality", "0",
    "-o", str(target_mp3),
    url
]

print("Executing yt-dlp to download reference audio interval 00:00-00:20...")
res = subprocess.run(cmd, capture_output=True, text=True)
print("Return code:", res.returncode)
print("STDOUT:", res.stdout[-500:] if res.stdout else "None")
print("STDERR:", res.stderr[-500:] if res.stderr else "None")

if target_mp3.exists():
    print(f"SUCCESS: Reference audio downloaded: {target_mp3} ({target_mp3.stat().st_size} bytes)")
    # Convert to WAV for inspection / acoustic analysis
    wav_cmd = [
        ffmpeg_path, "-y",
        "-i", str(target_mp3),
        "-ar", "48000",
        "-ac", "1", # mono for acoustic feature extraction
        str(target_wav)
    ]
    subprocess.run(wav_cmd, check=True)
    print(f"Created WAV mono copy: {target_wav} ({target_wav.stat().st_size} bytes)")
else:
    # Check if yt-dlp created a slightly different name
    candidates = list(out_dir.glob("reference_ahmad_fakhouri*"))
    print("Candidates:", candidates)

