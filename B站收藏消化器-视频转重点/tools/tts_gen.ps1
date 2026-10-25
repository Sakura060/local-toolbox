# Generate 3 topic WAVs via Windows SAPI (16kHz 16bit mono) for ASR pipeline test.
# ASCII-only on purpose (cmd/PowerShell encoding quirks).
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$outDir = Join-Path $PSScriptRoot "tts_out"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

$topics = @(
    @{
        name = "topic1"
        text = "Welcome to the show. Today we will talk about three things: cooking, football, and travel. Let us start with cooking. A good soup needs patience. Simmer the broth slowly for at least two hours. Fresh herbs make a big difference. Salt early and taste often. Never rush a good stew."
    },
    @{
        name = "topic2"
        text = "Now let us switch to football. The striker scored two goals in the second half. The defense looked tired after the break. The coach changed the formation at half time. The goalkeeper made a great save. The final score was three to one."
    },
    @{
        name = "topic3"
        text = "Finally, some travel tips for Japan. Buy a rail pass before you go. Tokyo is crowded but exciting. Visit Kyoto in autumn for the best views. Book hotels early, because prices rise fast in spring. Try the local food stalls at night."
    }
)

$voice = $null
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $voice = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture -like "en-*" } | Select-Object -First 1 } catch {}
if ($voice) { $synth.SelectVoice($voice.VoiceInfo.Name) }
Write-Output ("Voice: " + $synth.Voice.Name)

foreach ($t in $topics) {
    $path = Join-Path $outDir ($t.name + ".wav")
    $synth.SetOutputToWaveFile($path, $fmt)
    $synth.Rate = 0
    $synth.Speak($t.text)
    $synth.SetOutputToNull()
    Write-Output ("Wrote " + $path)
}
$synth.Dispose()
