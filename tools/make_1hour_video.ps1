# Generates a ~1 hour test video with structured spoken content for
# long-video acceptance testing. Speech is synthesized locally with SAPI.
param()
$ErrorActionPreference = 'Stop'

$work = 'D:\AI_Clip_Generator\tests'
New-Item -ItemType Directory -Force -Path $work | Out-Null

Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = 2
$synth.SetOutputToWaveFile("$work\hour_speech.wav")

$paragraphs = @(
"Welcome to this deep dive episode. Today we will cover ten practical lessons about focus, habits, and learning. Stay with me, because lesson number seven surprises almost everyone.",
"Let me start with the biggest mistake people make. Most people try to focus harder, when they should focus shorter. The brain is not built for hours of endless concentration.",
"Research from Stanford showed that people who multitask believe they are good at it, but they are actually worse than everyone else. That study changed how companies design work.",
"Here is the first practical tip. Work in blocks of twenty five minutes, then rest for five. This simple rhythm keeps your attention fresh through the whole day.",
"The second tip is about your phone. Every notification costs you more than the seconds it takes to read. It can take several minutes to return to deep focus after an interruption.",
"Now here is something surprising. Sleep is when your brain replays what you learned and moves it into long term memory. Students who sleep after studying remember far more than those who stay up.",
"Lesson number four. Water matters more than coffee. Even mild dehydration measurably reduces concentration, so keep a bottle on your desk.",
"Let me tell you a quick story. A few years ago I worked with a programmer who wrote brilliant code but burned out every three months. When he switched to strict evening cutoffs, his output doubled within half a year.",
"That story teaches an important principle. Rest is not the opposite of productivity. Rest is part of productivity.",
"Here is a common myth. Listening to music while working helps everyone. The truth is more nuanced. Familiar instrumental music helps repetitive tasks, but lyrics compete with language processing.",
"Now for the surprising lesson number seven. Daydreaming is a feature, not a bug. When you let your mind wander, the brain activates a network that connects distant ideas, which is where insights come from.",
"So how do you use this? Schedule deliberate boredom. Ten minutes of walking without a podcast, without music, without your phone. Let the mind drift on purpose.",
"Another powerful technique is teaching. When you explain an idea to someone else, you quickly discover the gaps in your own understanding. This is called the protégé effect.",
"Spacing beats cramming, every single time. Reviewing material in short sessions across a week builds stronger memory than one long night before the exam.",
"To summarize the ten lessons. Focus shorter, protect your attention, respect sleep, drink water, set boundaries, question myths, embrace boredom, teach others, space your reviews, and rest on purpose.",
"If you take just one lesson from today, make it this one. Small systems beat big goals. Change your environment, and your habits will follow. Thanks for watching, and see you in the next episode."
)

# 44 passes x ~175 s of speech (rate 2) ~= 7200 s of audio -> 2 hours.
# We stop the synthesis at the 1 hour mark by timing passes.
$passes = 44
for ($pass = 1; $pass -le $passes; $pass++) {
    foreach ($p in $paragraphs) {
        $synth.Speak($p)
        Start-Sleep -Milliseconds 250
    }
}
$synth.Dispose()
Write-Host "HOUR WAV DONE"
