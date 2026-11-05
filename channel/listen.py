"""Hear Pip read a diary entry (or a weekly narration) before you approve or publish it.

    python channel/listen.py                     # the newest diary entry
    python channel/listen.py 2026-11-05          # a specific day
    python channel/listen.py video/2026-11-15-week-2.txt   # a narration script
    python channel/listen.py 2026-11-05 --plain  # without the robot effect
    python channel/listen.py 2026-11-05 --voice Zira

Windows only, nothing to install: uses the built-in Windows voices (System.Speech) and plays
through your speakers. It also saves the audio next to the entry as channel/audio/<name>.wav,
so you can drop it straight into a video edit. Only Pip's words are read; "## Private note"
is skipped. The .wav files are local only (publish_site.py doesn't copy channel/audio).
"""
from __future__ import annotations

import argparse
import array
import base64
import math
import re
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CHANNEL, DAGBOEK, read_post  # noqa: E402

AUDIO = CHANNEL / "audio"


def pick_text(arg: str | None) -> tuple[str, str]:
    if arg and arg.endswith(".txt"):
        p = Path(arg) if Path(arg).exists() else CHANNEL / arg
        return p.stem, p.read_text(encoding="utf-8")
    if arg:
        p = DAGBOEK / f"{arg}.md"
    else:
        entries = sorted(DAGBOEK.glob("????-??-??.md"))
        if not entries:
            sys.exit("No diary entries yet. Run write_entry.py first.")
        p = entries[-1]
    if not p.exists():
        sys.exit(f"No entry {p.name}.")
    _, body = read_post(p)
    return p.stem, body.split("## Private note")[0].strip()


def speakable(text: str) -> str:
    """Make Pip's short-hand sound right when spoken."""
    t = re.sub(r"[*_`#>]", "", text)
    t = re.sub(r"(\d+)\s*h\b", r"\1 hours", t)
    t = re.sub(r"(\d+)\s*min\b", r"\1 minutes", t)
    t = re.sub(r"\b1 hours\b", "1 hour", t)
    t = re.sub(r"\b1 minutes\b", "1 minute", t)
    t = re.sub(r"\b0 minutes\b\.?", "", t)
    t = re.sub(r"(\d{1,2}):(\d{2})", lambda m: f"{int(m[1])} {m[2]}" if m[2] != "00" else f"{int(m[1])} o'clock", t)
    t = re.sub(r"\s+([.,])", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


def synthesize(text: str, out: Path, voice: str | None) -> None:
    from xml.sax.saxutils import escape
    sentences = re.split(r"(?<=[.!?])\s+", text)
    body = '<break time="350ms"/>'.join(escape(s) for s in sentences if s)
    ssml = ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            f'<prosody rate="-10%" pitch="+15%">{body}</prosody></speak>')
    pick = f"$s.SelectVoice(($s.GetInstalledVoices() | % {{ $_.VoiceInfo.Name }} | ? {{ $_ -like '*{voice}*' }} | Select -First 1));" \
        if voice else "$s.SelectVoiceByHints('Male');"
    ps = ("Add-Type -AssemblyName System.Speech;"
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          f"{pick}"
          f"$s.SetOutputToWaveFile('{out}');"
          f"$s.SpeakSsml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{base64.b64encode(ssml.encode()).decode()}')));"
          "$s.Dispose()")
    enc = base64.b64encode(ps.encode("utf-16-le")).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand", enc], capture_output=True, text=True)
    if r.returncode or not out.exists():
        sys.exit("Windows speech failed:\n" + (r.stderr or r.stdout))


def robotize(path: Path) -> None:
    """Small robot flavour, standard library only: a fast tremble (ring modulation) + a short metallic echo."""
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        if params.sampwidth != 2:
            return
        frames = array.array("h", w.readframes(params.nframes))
    rate, ch = params.framerate, params.nchannels
    out = array.array("h", frames)
    delay = int(rate * 0.012) * ch
    for i in range(len(frames)):
        t = (i // ch) / rate
        x = frames[i] * (0.62 + 0.38 * math.sin(2 * math.pi * 42 * t))
        if i >= delay:
            x += 0.35 * frames[i - delay]
        out[i] = max(-32768, min(32767, int(x * 0.9)))
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(out.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="?")
    ap.add_argument("--plain", action="store_true")
    ap.add_argument("--voice", help="part of a Windows voice name, e.g. David, Zira, Mark")
    ap.add_argument("--no-play", action="store_true")
    a = ap.parse_args()
    if sys.platform != "win32":
        sys.exit("listen.py uses the Windows voices; run it on the PC.")

    name, text = pick_text(a.what)
    spoken = speakable(text)
    print(f"Pip says ({name}):\n  {spoken}\n")
    AUDIO.mkdir(exist_ok=True)
    out = AUDIO / f"{name}.wav"
    synthesize(spoken, out, a.voice)
    if not a.plain:
        robotize(out)
    print(f"saved {out.relative_to(CHANNEL.parent)}")
    if not a.no_play:
        import winsound
        winsound.PlaySound(str(out), winsound.SND_FILENAME)


if __name__ == "__main__":
    main()
