Add-Type -AssemblyName System.Speech
$speak = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speak.Rate = 0
$speak.Volume = 100
$speak.SetOutputToWaveFile("temp_ep2_voiceover.wav")
$text = "Night fell over the quiet town, but Tommy was not sleepy yet. Outside his nursery window, a tiny golden star was dancing in the sky! With a soft giggle, Tommy opened the latch. The warm little star fluttered right inside, lighting up his favorite teddy bear! Resting softly beside his pillow, the little star shone a soothing nightlight glow. Goodnight, Tommy! Sweet dreams from DreamMatter!"
$speak.Speak($text)
$speak.Dispose()

# Convert voiceover to 48kHz stereo AAC
ffmpeg -y -i temp_ep2_voiceover.wav -c:a aac -b:a 192k -ar 48000 -ac 2 channels/ch_dreammatter_001/media_vault/originals/audio/tommy_ep2_voiceover.aac
Remove-Item temp_ep2_voiceover.wav -ErrorAction SilentlyContinue

# Generate soothing lullaby music track (G major / C major chords: 392Hz G, 493.88Hz B, 587.33Hz D, 783.99Hz G, with 523.25Hz C chime)
ffmpeg -y -f lavfi -i "aevalsrc=0.10*sin(2*PI*392.00*t)+0.08*sin(2*PI*493.88*t)+0.07*sin(2*PI*587.33*t)+0.05*sin(2*PI*783.99*t)+0.04*sin(2*PI*1046.50*t):s=48000:d=21" -c:a aac -b:a 192k -ar 48000 -ac 2 channels/ch_dreammatter_001/media_vault/originals/audio/tommy_ep2_bgmusic.aac

# Test both files
Test-Path channels/ch_dreammatter_001/media_vault/originals/audio/tommy_ep2_voiceover.aac
Test-Path channels/ch_dreammatter_001/media_vault/originals/audio/tommy_ep2_bgmusic.aac

