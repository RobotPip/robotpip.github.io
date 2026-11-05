"""Pip's weekly digest (blog post) + narration script for a video, both in Pip's voice.

    python channel/write_weekly.py                 # the 7 days ending yesterday
    python channel/write_weekly.py 2026-11-15      # the 7 days ending on that date
    python channel/write_weekly.py --show-prompt

Uses the same local model, voice.md and checker as the daily diary. Only APPROVED diary
days are used as input, so nothing reaches the public that you haven't seen once already.

Writes:
    channel/posts/YYYY-MM-DD-week-N.md        status: draft, ai: yes  -> set `status: published`
    channel/video/YYYY-MM-DD-week-N.txt       narration lines for a Short or episode,
                                              one sentence per line, ready for a TTS engine
The video file has no on-screen human by design: Pip's synthetic voice over footage of the
board, its light and the charts.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "dagboek"))
from common import CHANNEL, DAGBOEK, load_json, read_post, write_post  # noqa: E402
from write_entry import MODEL, ask_model, check, ensure_ollama  # noqa: E402


def week(end: date) -> list[dict]:
    days = []
    for i in range(6, -1, -1):
        d = end - timedelta(days=i)
        p = DAGBOEK / f"{d.isoformat()}.md"
        if p.exists():
            meta, body = read_post(p)
            if meta.get("status") == "approved":
                days.append({"facts": load_json(DAGBOEK / f"{d.isoformat()}.facts.json", {}),
                             "entry": body.split("## Private note")[0].strip()})
    return days


def summary(days: list[dict]) -> dict:
    """Week-level numbers, computed here so the model never has to do arithmetic."""
    f = [d["facts"] for d in days]
    awake = [x for x in f if x.get("awake")]
    s = {"first_day": f[0]["day"], "last_day": f[-1]["day"], "days_awake": len(awake),
         "awake_min_total": sum(x.get("awake_min", 0) for x in awake)}
    s["awake_h_total"], s["awake_rest_min_total"] = divmod(s["awake_min_total"], 60)
    bm = [x["values"]["battery_mv"] for x in awake if "battery_mv" in x.get("values", {})]
    if bm:
        s["energy_lowest"] = min(v["min"] for v in bm)
        s["energy_highest"] = max(v["max"] for v in bm)
    s["new_channels"] = sorted({c for x in f for c in x.get("new_channels", [])})
    return s


def build_prompt(days, s, feedback=None) -> str:
    voice = (DAGBOEK / "voice.md").read_text(encoding="utf-8")
    p = ("Write Pip's weekly look back, following the voice rules exactly, but 120-250 words. "
         "Start with \"Days N to M.\" using first_day and last_day. Tell what changed over the week, "
         "what Pip still can't predict, and what it is starting to miss, if anything. Do not invent "
         "anything that isn't in the entries or numbers.\n\n"
         f"=== VOICE RULES ===\n{voice}\n\n"
         f"=== WEEK NUMBERS ===\n{json.dumps(s, indent=2)}\n\n"
         "=== THIS WEEK'S DIARY ENTRIES ===\n" + "\n\n".join(d["entry"] for d in days) +
         "\n\nOutput only the text.")
    if feedback:
        p += "\n\nYour previous attempt was rejected for: " + "; ".join(feedback) + ". Fix that."
    return p


def week_check(text, days, s) -> list[str]:
    facts = {"day": s["first_day"], "awake": True, "week": s, "days": [d["facts"] for d in days]}
    probs = [p for p in check(text, facts) if not p.startswith(("too long", "too short", "must start"))]
    n = len(text.split())
    if n > 250 or n < 60:
        probs.append(f"length {n} words (want 120-250)")
    if not re.match(rf"\s*Days {s['first_day']} to {s['last_day']}\b", text):
        probs.append(f'must start with "Days {s["first_day"]} to {s["last_day"]}."')
    return probs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("end", nargs="?")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--tries", type=int, default=4)
    ap.add_argument("--show-prompt", action="store_true")
    a = ap.parse_args()
    end = date.fromisoformat(a.end) if a.end else date.today() - timedelta(days=1)
    days = week(end)
    if len(days) < 2:
        sys.exit("Fewer than 2 approved diary days in that week; nothing to digest.")
    s = summary(days)
    if a.show_prompt:
        print(build_prompt(days, s))
        return

    ensure_ollama(a.model)
    fb, text = None, ""
    for i in range(1, a.tries + 1):
        text = ask_model(build_prompt(days, s, fb), a.model)
        fb = week_check(text, days, s)
        print(f"attempt {i}: {'OK' if not fb else '; '.join(fb)}")
        if not fb:
            break
    if fb:
        sys.exit("No attempt passed the checker. Try again, or a different --model.")

    weekno = (s["last_day"] + 6) // 7
    slug = f"{end.isoformat()}-week-{weekno}"
    first_line = re.split(r"(?<=[.!?])\s+", text.strip())[1:2] or [""]
    write_post(CHANNEL / "posts" / f"{slug}.md",
               {"title": f"Week {weekno}: days {s['first_day']} to {s['last_day']}", "date": end.isoformat(),
                "summary": first_line[0], "status": "draft", "ai": "yes", "youtube": ""}, text)
    vid = CHANNEL / "video" / f"{slug}.txt"
    vid.parent.mkdir(exist_ok=True)
    vid.write_text("\n".join(re.split(r"(?<=[.!?])\s+", text.strip())) + "\n", encoding="utf-8")
    print(f"draft post: channel/posts/{slug}.md  (set status: published to put it online)")
    print(f"narration:  channel/video/{slug}.txt")


if __name__ == "__main__":
    main()
