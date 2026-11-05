"""Steps 2 + 3 of the diary: facts.json -> Pip's entry (local LLM) -> checker.

    python channel/dagboek/write_entry.py                  # yesterday
    python channel/dagboek/write_entry.py 2026-11-06
    python channel/dagboek/write_entry.py 2026-11-06 --show-prompt   # print the prompt, no model call
    python channel/dagboek/write_entry.py 2026-11-06 --model qwen3:14b --tries 5

Writes channel/dagboek/YYYY-MM-DD.md with `status: draft`. Nothing is published until you run
approve.py (rule 6). If no draft passes the checker, the last attempt is saved as
YYYY-MM-DD.rejected.md with the reasons, so you can see what went wrong.

Needs Ollama running locally (https://ollama.com) with the model pulled:
    ollama pull qwen3:14b
Override with env vars PIP_DIARY_MODEL / OLLAMA_URL if you like.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import DAGBOEK, load_json, write_post  # noqa: E402
from make_facts import parse_day  # noqa: E402

MODEL = os.environ.get("PIP_DIARY_MODEL", "qwen3:14b")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
ALWAYS_OK_NUMBERS = {0, 1}


# ---------------------------------------------------------------- voice.md
def voice_sections() -> dict[str, list[str]]:
    text = (DAGBOEK / "voice.md").read_text(encoding="utf-8")
    sections, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip().lower()
            sections[cur] = []
        elif cur and line.strip().startswith("- "):
            sections[cur].append(line.strip()[2:].strip())
    return sections


def limits() -> dict[str, int]:
    out = {"min_words": 15, "max_words": 150}
    for item in voice_sections().get("limits", []):
        k, _, v = item.partition(":")
        if v.strip().isdigit():
            out[k.strip()] = int(v)
    return out


# ---------------------------------------------------------------- checker (no AI)
def fact_numbers(facts) -> list[float]:
    nums: list[float] = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            nums.append(float(x))
        elif isinstance(x, str):
            nums.extend(float(n) for n in re.findall(r"\d+(?:\.\d+)?", x))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(k)  # channel names like touch4 -> 4
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(facts)
    return nums + [abs(n) for n in nums if n < 0]  # "the hum fell to 70" for rssi -70


def check(text: str, facts: dict) -> list[str]:
    problems = []
    lower = text.lower()
    private = DAGBOEK / "banned_private.txt"  # never published
    extra = [w.strip() for w in private.read_text(encoding="utf-8").splitlines() if w.strip()] if private.exists() else []
    for w in voice_sections().get("banned words", []) + extra:
        if re.search(rf"\b{re.escape(w.lower())}(s|es|ed|ing)?\b", lower):
            problems.append(f"banned word: {w}")
    allowed = fact_numbers(facts)
    for raw in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", text):
        x = float(raw)
        if x in ALWAYS_OK_NUMBERS:
            continue
        if not any(abs(x - a) <= max(0.5, abs(a) * 0.005) for a in allowed):
            problems.append(f"number not in facts: {raw}")
    n = len(text.split())
    lim = limits()
    if n > lim["max_words"]:
        problems.append(f"too long: {n} words (max {lim['max_words']})")
    if facts.get("awake") and n < lim["min_words"]:
        problems.append(f"too short: {n} words (min {lim['min_words']})")
    if not re.match(rf"\s*Day {facts['day']}\b", text):
        problems.append(f'must start with "Day {facts["day"]}."')
    return problems


# ---------------------------------------------------------------- model
def build_prompt(facts: dict, feedback: list[str] | None = None) -> str:
    voice = (DAGBOEK / "voice.md").read_text(encoding="utf-8")
    p = (
        "Write today's diary entry for Pip, following the voice rules exactly.\n\n"
        f"=== VOICE RULES ===\n{voice}\n\n"
        f"=== TODAY'S FACTS (the only things you know) ===\n{json.dumps(facts, indent=2)}\n\n"
        "Use only numbers that appear in the facts. Never use a banned word, not even inside "
        "another word. Output only the entry text, nothing else."
    )
    if feedback:
        p += "\n\nYour previous attempt was rejected for: " + "; ".join(feedback) + ". Fix that."
    return p


def ensure_ollama(model: str) -> None:
    """Start the Ollama server if it isn't running, and check the model is pulled."""
    import shutil
    import subprocess
    import time

    def tags():
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=3) as r:
            return [m["name"] for m in json.loads(r.read()).get("models", [])]
    try:
        names = tags()
    except (urllib.error.URLError, OSError):
        exe = shutil.which("ollama") or os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        if not os.path.exists(exe):
            sys.exit("Ollama isn't installed (or not on PATH). Get it from https://ollama.com/download")
        print("Starting Ollama...")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        for _ in range(30):
            time.sleep(1)
            try:
                names = tags()
                break
            except (urllib.error.URLError, OSError):
                pass
        else:
            sys.exit("Started Ollama, but it didn't answer within 30 seconds. Try again, or open the Ollama app.")
    want = model if ":" in model else model + ":latest"
    if want not in names:
        sys.exit(f"The model {model} isn't downloaded yet. Run:  ollama pull {model}\n"
                 f"(downloaded now: {', '.join(names) or 'none'})")


def ask_model(prompt: str, model: str) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.7}}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            text = json.loads(r.read())["response"]
    except urllib.error.URLError as e:
        sys.exit(f"Can't reach Ollama at {OLLAMA} ({e.reason}). Is `ollama serve` running?")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # reasoning models
    return text.strip().strip('"').strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("day", nargs="?")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--tries", type=int, default=4)
    ap.add_argument("--show-prompt", action="store_true")
    a = ap.parse_args()

    d = parse_day(a.day)
    facts = load_json(DAGBOEK / f"{d.isoformat()}.facts.json")
    if facts is None:
        sys.exit(f"No facts for {d}. Run make_facts.py {d} first.")
    out = DAGBOEK / f"{d.isoformat()}.md"
    if out.exists():
        from common import read_post
        if read_post(out)[0].get("status") == "approved":
            sys.exit(f"{out.name} is already approved; not overwriting.")

    if a.show_prompt:
        print(build_prompt(facts))
        return

    ensure_ollama(a.model)
    feedback, text = None, ""
    for attempt in range(1, a.tries + 1):
        text = ask_model(build_prompt(facts, feedback), a.model)
        feedback = check(text, facts)
        print(f"attempt {attempt}: {'OK' if not feedback else '; '.join(feedback)}")
        if not feedback:
            break

    meta = {"date": d.isoformat(), "day": facts["day"], "status": "draft",
            "model": a.model, "awake": str(facts.get("awake", False)).lower()}
    body = text + "\n\n## Private note\n\n"
    if feedback:
        meta["status"] = "rejected"
        meta["problems"] = "; ".join(feedback)
        write_post(DAGBOEK / f"{d.isoformat()}.rejected.md", meta, body)
        sys.exit("No attempt passed the checker; saved as .rejected.md. Edit it by hand or rerun.")
    write_post(out, meta, body)
    print(f"draft saved: {out.name}  -> read it, edit if needed, then: "
          f"python channel/dagboek/approve.py {d}")


if __name__ == "__main__":
    main()
