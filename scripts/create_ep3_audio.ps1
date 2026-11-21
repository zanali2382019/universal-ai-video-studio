# PowerShell script to generate Episode 03 voiceover and background music
$ErrorActionPreference = "Stop"

$audioDir = "channels/ch_dreammatter_001/media_vault/originals/audio"
if (-not (Test-Path $audioDir)) {
    New-Item -ItemType Directory -Path $audioDir -Force | Out-Null
}

$tempWav = "temp_ep3_voiceover.wav"
Add-Type -AssemblyName System.Speech
$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speak.Rate = 0
$speak.Volume = 100
$speak.SetOutputToWaveFile($tempWav)

$text = "One breezy afternoon, Tommy spotted something fluffy drifting down from the blue sky... a tiny, glowing cloud floating gently toward him! With a happy giggle, Tommy reached out and tickled the cloud. It giggled back with sweet chiming sparkles! Whoosh! Glowing pastel petals danced through the air, turning the garden into a blooming wonderland. Keep dreaming big with DreamMatter!"
$speak.Speak($text)
$speak.Dispose()

# Convert voiceover to 48kHz stereo AAC
$voOutput = "$audioDir/tommy_ep3_voiceover.aac"
& ffmpeg -y -i $tempWav -c:a aac -b:a 192k -ar 48000 -ac 2 $voOutput
Remove-Item $tempWav -ErrorAction SilentlyContinue

# Generate whimsical, gentle harmonic background music for Episode 3 (garden & floral theme: C major / F major chords: 261.63Hz C4, 329.63Hz E4, 392.00Hz G4, 523.25Hz C5, 659.25Hz E5 chime, gentle arpeggio-style harmonic blend, duration 24s)
$bgOutput = "$audioDir/tommy_ep3_bgmusic.aac"
$filterExpr = "aevalsrc=0.08*sin(2*PI*261.63*t)+0.07*sin(2*PI*329.63*t)+0.06*sin(2*PI*392.00*t)+0.04*sin(2*PI*523.25*t)+0.03*sin(2*PI*659.25*t):s=48000:d=24"
& ffmpeg -y -f lavfi -i $filterExpr -c:a aac -b:a 192k -ar 48000 -ac 2 $bgOutput

Write-Host "Voiceover created: $(Test-Path $voOutput)"
Write-Host "Background music created: $(Test-Path $bgOutput)"

