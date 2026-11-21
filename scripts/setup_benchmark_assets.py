import urllib.request
import subprocess
from pathlib import Path
import json

benchmark_dir = Path("channels/ch_dreammatter_001/exports/tests/arabic_tts_benchmark")
benchmark_dir.mkdir(parents=True, exist_ok=True)

# Master passage text
passage_text = "فِي لَيْلَةٍ شَاتِيَةٍ سَاكِنَةٍ، تَطَلَّعَ الطِّفْلُ الصَّغِيرُ مِنْ نَافِذَةِ حُجْرَتِهِ نَحْوَ ظُلْمَةِ الأُفُقِ الْبَعِيدِ؛ فَإِذَا بِضِيَاءِ نَجْمٍ ذَهَبِيٍّ ثَاقِبٍ، يَتَلَأْلَأُ خَلْفَ سَحَابٍ رَقِيقٍ. هَمَسَ الْفَتَى بِفَرَحٍ، وَقَدْ غَمَرَتِ الطُّمَأْنِينَةُ فُؤَادَهُ: يَا لَهُ مِنْ مَشْهَدٍ بَدِيعٍ يَفِيضُ سِحْرًا وَدِفْئًا! هُنَاكَ فِي عَالَمِ \"دْرِيمْ مَاتِر\"، تُبْعَثُ أَجْمَلُ الرُّؤَى حَقِيقَةً لَا تَغِيبُ."

with open(benchmark_dir / "benchmark_passage.txt", "w", encoding="utf-8") as f:
    f.write(passage_text)

print("Saved benchmark_passage.txt")

# Benchmark files mapping from HF space
samples = {
    "silma_f5_tts_msa_sample.mp3": "https://silma-ai-opensource-arabic-tts-benchmark.hf.space/gradio_api/file=/app/results/msa/silma_tts/silma_1_tts_msa.wav?h=1794019870.3313198",
    "habibi_msa_specialized_sample.mp3": "https://silma-ai-opensource-arabic-tts-benchmark.hf.space/gradio_api/file=/app/results/msa/habibi_specialized/habibi_1_specialized.wav?h=1794019870.3313198",
    "coqui_xtts_v2_msa_sample.mp3": "https://silma-ai-opensource-arabic-tts-benchmark.hf.space/gradio_api/file=/app/results/msa/xtts/xtts_1_msa.mp3?h=1794019870.3313198",
    "omnivoice_msa_sample.mp3": "https://silma-ai-opensource-arabic-tts-benchmark.hf.space/gradio_api/file=/app/results/msa/omnivoice/omnivoice_000_msa.wav?h=1794019870.3313198"
}

ffmpeg_path = r"C:\Users\96777\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"

for filename, url in samples.items():
    mp3_path = benchmark_dir / filename
    temp_wav = benchmark_dir / (filename + ".tmp")
    print(f"Downloading {filename}...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        with open(temp_wav, "wb") as f:
            f.write(r.read())
    
    # Convert cleanly to 48kHz stereo MP3 192k
    cmd = [
        ffmpeg_path, "-y",
        "-i", str(temp_wav),
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        str(mp3_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    temp_wav.unlink(missing_ok=True)
    print(f"  Converted -> {mp3_path} ({mp3_path.stat().st_size} bytes)")

print("All benchmark samples successfully downloaded and converted.")

