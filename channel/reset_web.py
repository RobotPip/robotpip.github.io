"""Start Pip's channel over: wipe every diary day, facts file, recording, post, narration,
request, episode and ledger line. Scripts, voice.md, banned_private.txt, about.md, the design
and the Day 0 pictures stay.

    python channel/reset_web.py          # asks you to type RESET
    python channel/reset_web.py --yes    # no question

Only touches WebContent/. Pip's logs/ and pip.db are not touched (clear those yourself).
After this, run "4 Publish site" so the public site is emptied too.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CHANNEL, DAGBOEK, SITE  # noqa: E402


def gone(p: Path, removed: list[str]) -> None:
    if p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()
    else:
        return
    removed.append(str(p.relative_to(CHANNEL.parent)))


def main():
    if "--yes" not in sys.argv:
        print("This deletes all diary days, facts, audio, posts, video scripts, requests,\n"
              "episodes and budget lines in WebContent. Scripts and design stay.")
        if input("Type RESET to go on: ").strip() != "RESET":
            sys.exit("Nothing changed.")

    removed: list[str] = []
    for pattern in ("????-??-??.md", "????-??-??.rejected.md", "????-??-??.facts.json"):
        for p in DAGBOEK.glob(pattern):
            gone(p, removed)
    for sub in ("audio", "video", "posts"):
        d = CHANNEL / sub
        if d.exists():
            for p in d.iterdir():
                if p.name != ".gitkeep":
                    gone(p, removed)
    for p in (SITE / "dist", SITE / "_preview", SITE / "static" / "status.json"):
        gone(p, removed)

    (CHANNEL / "requests.json").write_text("[]\n", encoding="utf-8")
    (CHANNEL / "episodes.json").write_text("[]\n", encoding="utf-8")
    (CHANNEL / "budget.csv").write_text("date,direction,amount_eur,request,source,note\n", encoding="utf-8")
    subprocess.run([sys.executable, str(CHANNEL / "make_status.py")], check=False)

    print(f"Removed {len(removed)} item(s):")
    for r in removed:
        print("  -", r)
    print("requests.json, episodes.json and budget.csv are empty again.")
    print('Next: run "4 Publish site" to empty the public site as well.')


if __name__ == "__main__":
    main()
