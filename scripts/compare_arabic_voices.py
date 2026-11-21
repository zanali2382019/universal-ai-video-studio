"""Controlled Arabic Voice Comparison Script for Task 3.7 Quality Remediation.

Synthesizes the exact same Arabic storytelling passage using:
1. Microsoft Naayf (Baseline SAPI/OneCore offline voice)
2. ar-SA-HamedNeural (Microsoft Azure Neural - Storyteller Male)
3. ar-SA-ZariyahNeural (Microsoft Azure Neural - Gentle Female)
4. ar-EG-ShakirNeural (Microsoft Azure Neural - Expressive Male)

Saves isolated voice samples (NO background music) to:
channels/ch_dreammatter_001/exports/tests/
"""
import sys
import os
import asyncio
import tempfile
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import edge_tts
from src.core.config import get_settings
from src.rendering.ffmpeg_wrapper import FFmpegWrapper
from src.rendering.probe_service import MediaProbeService

PASSAGE = "في ليلة هادئة، نظر تومي الصغير من نافذة غرفته، فشاهد نجمة ذهبية تبتسم له وتملأ قلبه بالدفء. عالم دريم ماتر، حيث تبدأ أجمل الأحلام."


async def synthesize_edge_tts(text: str, voice_id: str, out_aac: Path, wrapper: FFmpegWrapper):
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp_mp3:
        tmp_mp3_path = Path(tmp_mp3.name)

    try:
        communicate = edge_tts.Communicate(text, voice_id, rate="+0%", pitch="+0Hz")
        await communicate.save(str(tmp_mp3_path))

        # Convert to standard 48kHz stereo AAC
        cmd = [
            "-y",
            "-i", str(tmp_mp3_path),
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            str(out_aac),
        ]
        wrapper.run_ffmpeg(cmd, check=True)
    finally:
        if tmp_mp3_path.exists():
            tmp_mp3_path.unlink(missing_ok=True)


def synthesize_naayf(text: str, out_aac: Path, wrapper: FFmpegWrapper):
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_wav_path = Path(tmp_wav.name)

    try:
        ps_script = f"""
        $text = @'
{text}
'@
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        $asTaskGeneric = [System.WindowsRuntimeSystemExtensions].GetMethods() | ? {{ $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' }}
        function Await($WinRtTask, $ResultType) {{
            $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
            $netTask = $asTask.Invoke($null, @($WinRtTask))
            $netTask.Wait(-1) | Out-Null
            $netTask.Result
        }}

        [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime] | Out-Null
        $synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
        $voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object {{ $_.Language -like "ar*" }} | Select-Object -First 1
        $synth.Voice = $voice
        $streamOp = $synth.SynthesizeTextToStreamAsync($text)
        $stream = Await $streamOp ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
        
        $reader = New-Object Windows.Storage.Streams.DataReader($stream)
        $loadOp = $reader.LoadAsync($stream.Size)
        $bytesLoaded = Await $loadOp ([System.UInt32])
        $bytes = New-Object byte[] $stream.Size
        $reader.ReadBytes($bytes)
        [System.IO.File]::WriteAllBytes('{str(tmp_wav_path).replace("\\", "\\\\")}', $bytes)
        """
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            check=True,
            timeout=30,
        )
        cmd = [
            "-y",
            "-i", str(tmp_wav_path),
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-ac", "2",
            str(out_aac),
        ]
        wrapper.run_ffmpeg(cmd, check=True)
    finally:
        if tmp_wav_path.exists():
            tmp_wav_path.unlink(missing_ok=True)


async def main():
    settings = get_settings()
    out_dir = settings.channels_root / "ch_dreammatter_001" / "exports" / "tests"
    out_dir.mkdir(parents=True, exist_ok=True)

    wrapper = FFmpegWrapper()
    probe_service = MediaProbeService(ffmpeg_wrapper=wrapper)

    print("=" * 80)
    print("CONTROLLED ARABIC VOICE COMPARISON RUNNER")
    print(f"Passage: \"{PASSAGE}\"")
    print("=" * 80)

    candidates = [
        ("naayf_sapi", "Microsoft Naayf (Baseline SAPI / OneCore)", None),
        ("hamed_neural", "ar-SA-HamedNeural (Storyteller Male)", "ar-SA-HamedNeural"),
        ("zariyah_neural", "ar-SA-ZariyahNeural (Gentle Warm Female)", "ar-SA-ZariyahNeural"),
        ("shakir_neural", "ar-EG-ShakirNeural (Expressive Male)", "ar-EG-ShakirNeural"),
    ]

    results = []
    for cid, label, v_id in candidates:
        out_file = out_dir / f"comparison_{cid}.aac"
        print(f"\n[*] Synthesizing candidate: {label}...")
        if v_id is None:
            synthesize_naayf(PASSAGE, out_file, wrapper)
        else:
            await synthesize_edge_tts(PASSAGE, v_id, out_file, wrapper)

        probe = probe_service.probe_file(out_file)
        tp = probe_service.measure_true_peak(out_file)
        size = out_file.stat().st_size

        print(f"    -> Generated: {out_file.name}")
        print(f"       Duration: {probe.duration_sec:.2f}s | Size: {size:,} bytes")
        print(f"       True Peak: {tp.true_peak_dbtp:.2f} dBTP | Integrated Loudness: {tp.integrated_loudness_lufs:.2f} LUFS")

        results.append({
            "id": cid,
            "label": label,
            "file": str(out_file.resolve()),
            "duration": probe.duration_sec,
            "true_peak": tp.true_peak_dbtp,
            "lufs": tp.integrated_loudness_lufs,
        })

    print("\n" + "=" * 80)
    print("COMPARISON GENERATION COMPLETE — SUMMARY TABLE")
    print("=" * 80)
    for r in results:
        print(f"• {r['label']}")
        print(f"  Path: {r['file']}")
        print(f"  Duration: {r['duration']:.2f}s | True Peak: {r['true_peak']:.2f} dBTP | LUFS: {r['lufs']:.2f}")


if __name__ == "__main__":
    asyncio.run(main())

