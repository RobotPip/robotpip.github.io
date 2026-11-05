"""Step 4 of the diary: the keeper approves an entry so the site will publish it (rule 6).

    python channel/dagboek/approve.py 2026-11-06
    python channel/dagboek/approve.py 2026-11-06 --unpublish   # back to draft

Before approving, edit YYYY-MM-DD.md freely: fix Pip's text, and jot anything for yourself
under "## Private note". That part is never published; only Pip's words go on the site. The checker runs again on
Pip's part and warns if your edit broke a rule, but you have the final say.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import DAGBOEK, load_json, read_post, write_post  # noqa: E402
from write_entry import check  # noqa: E402

if len(sys.argv) < 2:
    sys.exit(__doc__)
day = sys.argv[1]
path = DAGBOEK / f"{day}.md"
if not path.exists():
    sys.exit(f"No entry {path.name}. Run write_entry.py {day} first.")

meta, body = read_post(path)
if "--unpublish" in sys.argv:
    meta["status"] = "draft"
    write_post(path, meta, body)
    sys.exit(f"{path.name} is a draft again; it disappears from the site on the next build.")

pip_part = body.split("## Private note")[0].strip()
facts = load_json(DAGBOEK / f"{day}.facts.json", {})
problems = check(pip_part, facts) if facts else ["no facts.json to check against"]
if problems:
    print("Warning, Pip's text breaks the voice rules:\n  - " + "\n  - ".join(problems))
    if input("Approve anyway? [y/N] ").strip().lower() != "y":
        sys.exit("Not approved.")
meta["status"] = "approved"
meta.pop("problems", None)
write_post(path, meta, body)
print(f"Approved {path.name}. Commit and push to publish.")
