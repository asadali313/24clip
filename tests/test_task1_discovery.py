"""Task 1 verification: content-type detection, boring rejection, and that
picks land on genuinely interesting moments of the real GTA transcript."""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\AI_Clip_Generator")

from app import config
config.ensure_layout()

from app.core import discovery, scoring, segmentation
from app.core.transcriber import Segment

checks = []
def check(name, cond, extra=""):
    checks.append((name, bool(cond)))
    print(f"  {'PASS' if cond else 'FAIL'}: {name} {extra}")


def load_sentences():
    tj = Path(r"D:\AI_Clip_Generator\projects\project_001\transcripts"
              r"\transcript.json")
    data = json.loads(tj.read_text(encoding="utf-8"))
    segs = [Segment.from_dict(s) for s in data["segments"]]
    sents = segmentation.build_sentences(segs)
    return sents


def run_discovery(sents, min_d, max_d, target):
    eng = discovery.DiscoveryEngine(sents)
    cands = eng.generate(min_d, max_d, target)
    scorer = scoring.Scorer(config.Settings(), sents, [], None)
    for c in cands:
        scorer.score(c)
        reason = scorer.hard_reject(c)
        if reason:
            c.verdict = "rejected"
            c.reject_reason = reason
    return eng, cands


print("=== 1. CONTENT-TYPE DETECTION ===")
sents = load_sentences()
eng, cands = run_discovery(sents, 18, 45, 30)
check("GTA video detected as gaming", eng.video_type == "gaming",
      f"({eng.video_type})")
from app.core.discovery import detect_video_type
doc = detect_video_type("Historians found evidence in the archive that the "
                        "empire collapsed in the 14th century according to "
                        "experts and records show a civilization shift.")
check("documentary text detected", doc == "documentary", f"({doc})")
pod = detect_video_type("That's a great question. My hot take: I strongly "
                        "disagree, let's talk about why people believe this "
                        "debate.")
check("podcast text detected", pod == "podcast", f"({pod})")
check("neutral text stays general",
      detect_video_type("the weather today is mild and calm in the city "
                        "with light wind and some clouds over the hills")
      == "general")

print("=== 2. BORING-SEGMENT REJECTION ===")
flat_text = ("so we walked down the road and then we walked a bit more and "
             "then we saw a building and then we kept walking down the road "
             "and then we turned left and kept going and then sat down")
mon = discovery._monotony(flat_text)
check("monotony detector flags flat text", mon > 0.6, f"({mon:.2f})")
lively = ("The biggest mistake? 90 percent of players ignore the economy - "
          "and that's why they lose every single fight!")
mon2 = discovery._monotony(lively)
check("lively text scores low monotony", mon2 < 0.4, f"({mon2:.2f})")

rejected = [c for c in cands if c.verdict == "rejected"]
reasons = {c.reject_reason for c in rejected}
check("bad openers rejected", any("mid-conversation" in r for r in reasons),
      f"({[r for r in reasons if 'conversation' in r][:1]})")

print("=== 3. PICK QUALITY (real GTA transcript) ===")
survivors = sorted([c for c in cands if c.verdict != "rejected"],
                   key=lambda c: c.score, reverse=True)
check("candidates exist", len(survivors) >= 5, f"({len(survivors)})")
top6 = survivors[:6]
# known-strong moments in this video (verified content):
strong_markers = ("emoji under jason", "ankle monitor", "rob nelson",
                  "hammer lady", "changes everything", "irredeemable")
hits = sum(1 for c in top6
           if any(m in c.text.lower() for m in strong_markers))
check("top picks hit known-strong moments", hits >= 2, f"({hits}/6)")
for c in top6:
    print(f"    {c.score:5.1f}  {c.start:7.0f}s  {c.text[:80]}")
print("    bottom of pool:")
for c in survivors[-2:]:
    print(f"    {c.score:5.1f}  {c.start:7.0f}s  {c.text[:80]}")

# monotone candidates should rank below signal-rich ones
mono_scores = [c.score for c in survivors if c.monotony > 0.7]
sig_scores = [c.score for c in survivors
              if c.monotony < 0.4 and len(c.hook_signals) >= 2]
if mono_scores and sig_scores:
    check("monotone ranks below signal-rich",
          sum(sig_scores) / len(sig_scores)
          > sum(mono_scores) / len(mono_scores),
          f"(avg {sum(sig_scores)/len(sig_scores):.0f} vs "
          f"{sum(mono_scores)/len(mono_scores):.0f})")
else:
    print(f"    (mono={len(mono_scores)} sig={len(sig_scores)} - "
          "comparison skipped)")

# type-specific highlights got boosted
hl = [c for c in cands if any("highlight" in t or "moment" in t
                              for t in c.idea_types)]
check("type-specific signals assigned", len(hl) >= 1, f"({len(hl)})")

failed = [n for n, p in checks if not p]
print(f"\n=== RESULT: {len(checks) - len(failed)}/{len(checks)} passed ===")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("TASK 1 DISCOVERY VERIFICATION PASSED")
