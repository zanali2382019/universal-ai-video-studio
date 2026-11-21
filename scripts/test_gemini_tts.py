import urllib.request
import json
import os

api_key = os.getenv("GEMINI_API_KEY", "")

benchmark_text = "فِي لَيْلَةٍ شَاتِيَةٍ سَاكِنَةٍ، تَطَلَّعَ الطِّفْلُ الصَّغِيرُ مِنْ نَافِذَةِ حُجْرَتِهِ نَحْوَ ظُلْمَةِ الأُفُقِ الْبَعِيدِ؛ فَإِذَا بِضِيَاءِ نَجْمٍ ذَهَبِيٍّ ثَاقِبٍ، يَتَلَأْلَأُ خَلْفَ سَحَابٍ رَقِيقٍ. هَمَسَ الْفَتَى بِفَرَحٍ، وَقَدْ غَمَرَتِ الطُّمَأْنِينَةُ فُؤَادَهُ: يَا لَهُ مِنْ مَشْهَدٍ بَدِيعٍ يَفِيضُ سِحْرًا وَدِفْئًا! هُنَاكَ فِي عَالَمِ \"دْرِيمْ مَاتِر\"، تُبْعَثُ أَجْمَلُ الرُّؤَى حَقِيقَةً لَا تَغِيبُ."

instructions = "Speak the exact Arabic text in clear, authentic Modern Standard Arabic. Use precise articulation of Arabic consonants, correct vowel realization and grammatical endings, natural connected speech, a mature warm male storytelling voice, expressive but restrained intonation, and meaningful pauses. Avoid colloquial dialect, robotic rhythm, exaggerated theatrical performance, and foreign-influenced Arabic pronunciation. Preserve every word exactly. Do not translate, paraphrase, omit, or add words."

# Test 1: generateContent endpoint (as shown in Google AI Studio "Get Code")
url1 = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash-tts:generateContent?key={api_key}"
payload1 = {
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
                    "voiceName": "Enzo"
                }
            }
        }
    }
}

req1 = urllib.request.Request(
    url1,
    data=json.dumps(payload1).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

try:
    print("Testing generateContent endpoint...")
    with urllib.request.urlopen(req1, timeout=60) as resp:
        res1 = json.loads(resp.read().decode("utf-8"))
        print("Success! Keys in response:", list(res1.keys()))
        candidates = res1.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            print("Parts count:", len(parts))
            for i, p in enumerate(parts):
                if "inlineData" in p:
                    mime = p["inlineData"].get("mimeType")
                    data_len = len(p["inlineData"].get("data", ""))
                    print(f"Part {i}: inlineData mime={mime}, base64 len={data_len}")
except urllib.error.HTTPError as e:
    print("Error generateContent:", e.code, e.read().decode("utf-8"))
except Exception as e:
    print("Exception generateContent:", e)

# Test 2: Interactions API endpoint (from speech-generation docs)
url2 = "https://generativelanguage.googleapis.com/v1beta/interactions"
payload2 = {
    "model": "gemini-3.8-flash-tts",
    "input": [{
        "type": "user_input",
        "content": [{
            "type": "text",
            "text": f"{instructions}\n\n{benchmark_text}",
            "annotations": [{
                "type": "speech_metadata",
                "style": "mature warm male storytelling voice with clear Modern Standard Arabic pronunciation"
            }]
        }]
    }],
    "response_format": {
        "type": "audio"
    },
    "generation_config": {
        "speech_config": [
            {"voice": "Enzo"}
        ]
    }
}

req2 = urllib.request.Request(
    url2,
    data=json.dumps(payload2).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "x-goog-api-key": api_key
    }
)

try:
    print("\nTesting interactions endpoint...")
    with urllib.request.urlopen(req2, timeout=60) as resp:
        res2 = json.loads(resp.read().decode("utf-8"))
        print("Success! Keys in response:", list(res2.keys()))
except urllib.error.HTTPError as e:
    print("Error interactions:", e.code, e.read().decode("utf-8"))
except Exception as e:
    print("Exception interactions:", e)

