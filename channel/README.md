# channel/: Pip's diary, blog, vlog and budget

Everything public comes from Pip. The human behind it stays off the internet: no name, face or voice
anywhere. You only check, approve and push. Standard-library Python only, nothing to install
except Ollama for the local model.

## Daily (2–5 min)

```
python channel/dagboek/make_facts.py           # logs -> facts.json (numbers, no AI)
python channel/dagboek/write_entry.py          # local LLM writes Pip's entry, checker rejects made-up numbers / world words
#   read channel/dagboek/YYYY-MM-DD.md, fix if needed; "## Private note" is never published
python channel/dagboek/approve.py YYYY-MM-DD   # status: approved
python ../publish_site.py                      # copies only public files to the site repo, privacy scan, push
```
`nightly.bat` does the first two steps on a schedule (Task Scheduler, ~03:00). Defaults to yesterday.

First time: `ollama pull qwen3:14b` (any model works: `--model` or env `PIP_DIARY_MODEL`).
Try `write_entry.py --show-prompt` to see exactly what the model gets.

## Weekly

```
python channel/write_weekly.py     # from the 7 approved days ending yesterday
```
Makes `posts/<date>-week-N.md` (draft; set `status: published`) and `video/<date>-week-N.txt`,
narration for a Short, one sentence per line, ready for a TTS voice.

## Videos without you in them

- **Voice:** a clearly robotic local TTS reading the narration file (e.g. Piper). Never a cloned human voice.
- **Picture:** the board, its LED, the bridge console, the charts from the site. Hands at most, no face,
  no room features you'd recognise, no window views.
- **Episode per request:** Pip's request text → evidence charts → "funded by N people" → the parent's answer
  (agent text) → install (hands or a cut) → Pip's discovery in its logs → Pip's verdict.
- Add each upload to `episodes.json`:
  `{"title": "Day 0: Pip wakes up", "youtube": "VIDEO_ID", "date": "2026-11-08", "kind": "episode", "request": ""}`
  (`"kind": "short"` for Shorts).
- Description line every time: *Pip's narration is AI-generated (language model + synthetic voice) from Pip's own
  sensor logs. Footage is real.* Tick YouTube's altered/synthetic box when anything could look or sound real.

## Staying anonymous (do these before the first upload)

- Make **separate accounts** under the channel's name: a Google account for YouTube, a GitHub account or org
  for the repo (the Pages URL shows the account name), and a Ko-fi page with a display name, not your own.
  Stripe/Ko-fi need your real identity for payouts, but don't show it publicly; check the public page after setup.
- Strip **EXIF/GPS** from photos and check video for reflections, mail, screens and street noise.
- Keep the repo **private** if logs reveal your schedule; the site only shows approved, delayed days anyway.
- Never put an email address, real name or location in `site/config.json`, posts or video descriptions.
- Put your name, street, town and email in `channel/dagboek/banned_private.txt` (one per line, never published).
  The diary checker rejects drafts that contain them, and `publish_site.py` refuses to push if any public
  file does.

## Requests

When the parent turns a request into a package, add it to `requests.json`:
```json
[{"id": "001", "title": "Something that changes when I am moved", "status": "open", "needed_eur": 12,
  "answer": "", "outcome": "", "youtube": ""}]
```
Status: `open` → `funded` → `installed` → `discovered` → `met` / `not-met` (or `cancelled`).
Pip's own words come from `parent/requests/001*.json` (missing / evidence / constraints / success_criterion).
The parent's `answer` only shows once the request is past funding. Set one Ko-fi goal per open request.

## Budget

Append to `budget.csv`, amounts **after** fees, one line per gift or purchase. Monthly support goes in as
one line per month. Ad income too, once YouTube pays out. Keep receipts. Not tax advice: see the channel plan.

<!-- budget:start -->
| Date | In/out | € | Request | Source | Note |
|---|---|---:|---|---|---|
| – | – | – | – | – | no entries yet |

**In €0.00 · out €0.00 · balance €0.00**
<!-- budget:end -->
