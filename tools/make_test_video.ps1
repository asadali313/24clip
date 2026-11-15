# Generates a test video with real synthesized speech for pipeline testing.
# Output: D:\AI_Clip_Generator\tests\sample_talk.mp4  (~60s, 1280x720, spoken script)
param()
$ErrorActionPreference = 'Stop'

$work = 'D:\AI_Clip_Generator\tests'
New-Item -ItemType Directory -Force -Path $work | Out-Null

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SetOutputToWaveFile("$work\sample_speech.wav")

# Script with distinct statements separated by pauses - useful for segmentation tests.
$script = @"
Hello everyone, and welcome to the show. Today I want to talk about something surprising that most people get wrong every single day.
Here is the first thing you should know. Sleep is not just rest. Your brain actually cleans itself while you sleep.
That discovery changed how scientists think about memory. In twenty nineteen, researchers found channels that flush waste out of the brain.
Now let me give you a practical tip. If you want to remember what you learn, review it right before you go to bed.
The second mistake people make is drinking coffee late in the afternoon. Caffeine stays in your body for more than six hours.
So the lesson is simple. Protect your sleep, and your brain will thank you. That is all for today. Thanks for watching.
"@
# Split into sentences, speak with pauses
$lines = $script -split "`n" | Where-Object { $_.Trim().Length -gt 0 }
foreach ($line in $lines) {
    $synth.Speak($line.Trim())
    Start-Sleep -Milliseconds 600
}
$synth.Dispose()
Write-Host "WAV created: $work\sample_speech.wav"
