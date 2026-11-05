"""Step 1 of the diary: logs -> facts.json. Plain arithmetic, no AI.

    python channel/dagboek/make_facts.py            # yesterday (for the nightly run after midnight)
    python channel/dagboek/make_facts.py 2026-11-06 # a specific day
    python channel/dagboek/make_facts.py today

Reads logs/*-sense.csv (+ *-manifest.json) and writes channel/dagboek/YYYY-MM-DD.facts.json.

The CSV reader is deliberately forgiving because the bridge's log format will grow:
- the time column may be called ts / timestamp / time / t / pc_time / datetime,
  holding ISO text or epoch seconds/milliseconds;
- any other numeric column becomes a "channel" and gets min/max/start/end;
- columns named touch* count as touch events whenever the value changes.
Rows are assigned to a day by their own timestamp (local time), not by file name.
"""
from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import DAGBOEK, LOGS, CHANNEL, load_json, save_json  # noqa: E402

TIME_COLS = ("ts", "timestamp", "time", "t", "pc_time", "datetime")
CLOCK_COLS = ("t_ms", "ms", "millis", "uptime_ms")  # Pip's own clock, not a sense
TOUCH_DROP = 0.6  # a touch pin "jumps" when it falls below 60% of its usual (median) value
GAP_S = 30  # a silence longer than this inside a log counts as a disconnect


def parse_time(v: str) -> datetime | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        x = float(v)
        if x > 1e12:  # milliseconds
            x /= 1000
        if x < 1e8:   # seconds since boot, not a wall-clock time
            return None
        return datetime.fromtimestamp(x)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None


def to_num(v: str):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def read_sessions(day: date) -> list[dict]:
    """One session per CSV file that has rows on `day`."""
    sessions = []
    for f in sorted(LOGS.glob("*-sense.csv")):
        with f.open(newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                continue
            tcol = next((c for c in reader.fieldnames if c.strip().lower() in TIME_COLS), None)
            if not tcol:
                print(f"skip {f.name}: no time column (expected one of {TIME_COLS})")
                continue
            rows = []
            for r in reader:
                t = parse_time(r.get(tcol, ""))
                if t and t.date() == day:
                    rows.append((t, r))
        if rows:
            rows.sort(key=lambda x: x[0])
            sessions.append({"file": f.name, "tcol": tcol, "rows": rows})
    return sessions


def manifest_channels(day: date, sessions: list[dict]) -> list[str]:
    """Channel names Pip reported about itself in that day's manifests (plus CSV columns)."""
    names: set[str] = set()
    for s in sessions:
        m = LOGS / s["file"].replace("-sense.csv", "-manifest.json")
        data = load_json(m, {}) or {}
        ch = data.get("fields") or data.get("channels") or []
        if isinstance(ch, dict):
            names.update(ch.keys())
        elif isinstance(ch, list):
            for c in ch:
                names.add(c.get("name") if isinstance(c, dict) else str(c))
        for col in s["rows"][0][1].keys():
            if col != s["tcol"]:
                names.add(col)
    names -= set(CLOCK_COLS)
    names.discard(None)
    return sorted(n for n in names if n)


def known_before(day: date) -> set[str]:
    known: set[str] = set()
    for f in DAGBOEK.glob("*.facts.json"):
        if f.name[:10] < day.isoformat():
            known.update((load_json(f, {}) or {}).get("channels", []))
    return known


def day_number(day: date) -> int:
    """Day 1 = the first day Pip was awake; asleep days don't count."""
    days = [f.name[:10] for f in DAGBOEK.glob("*.facts.json")
            if f.name[:10] < day.isoformat() and (load_json(f, {}) or {}).get("awake")]
    return len(days) + 1


def open_request() -> dict | None:
    for r in (load_json(CHANNEL / "requests.json", []) or []):
        if r.get("status") in ("open", "funded"):
            return {"id": r["id"], "status": r["status"]}
    return None


def make_facts(day: date) -> dict:
    sessions = read_sessions(day)
    facts: dict = {"date": day.isoformat(), "day": day_number(day)}
    if not sessions:
        facts.update({"awake": False, "sessions": 0})
        return facts

    awake_s, disconnects = 0.0, 0
    series: dict[str, list[tuple[datetime, float]]] = {}
    for s in sessions:
        rows = s["rows"]
        awake_s += (rows[-1][0] - rows[0][0]).total_seconds()
        prev_t = None
        for t, r in rows:
            if prev_t and (t - prev_t).total_seconds() > GAP_S:
                disconnects += 1
            prev_t = t
            for k, v in r.items():
                if k == s["tcol"] or k in CLOCK_COLS:
                    continue
                x = to_num(v)
                if x is None:
                    continue
                series.setdefault(k, []).append((t, x))

    # touch pins give raw capacitance: count each fall below TOUCH_DROP x the pin's median as one jump
    touch_by_pin = {}
    for k, pts in series.items():
        if not k.lower().startswith("touch"):
            continue
        vals = sorted(p[1] for p in pts)
        base = vals[len(vals) // 2]
        low, n = False, 0
        for _, x in pts:
            if not low and x < base * TOUCH_DROP:
                low, n = True, n + 1
            elif low and x > base * (TOUCH_DROP + 0.15):
                low = False
        if n:
            touch_by_pin[k] = n
    touch_events = sum(touch_by_pin.values())

    awake_min = round(awake_s / 60)
    facts.update({
        "awake": True,
        "sessions": len(sessions),
        "disconnects": disconnects + max(0, len(sessions) - 1),
        "awake_min": awake_min,
        "awake_h": awake_min // 60,
        "awake_rest_min": awake_min % 60,
        "first_seen": hm(sessions[0]["rows"][0][0]),
        "last_seen": hm(sessions[-1]["rows"][-1][0]),
        "touch_events": touch_events,
        "touch_by_pin": touch_by_pin,
        "values": {},
    })
    for k, pts in sorted(series.items()):
        vals = [p[1] for p in pts]
        lo = min(pts, key=lambda p: p[1])
        hi = max(pts, key=lambda p: p[1])
        jumps = [abs(b[1] - a[1]) for a, b in zip(pts, pts[1:])]
        facts["values"][k] = {
            "start": round(vals[0], 2), "end": round(vals[-1], 2),
            "min": round(lo[1], 2), "min_at": hm(lo[0]),
            "max": round(hi[1], 2), "max_at": hm(hi[0]),
            "biggest_jump": round(max(jumps), 2) if jumps else 0,
        }

    channels = manifest_channels(day, sessions)
    facts["channels"] = channels
    earlier = known_before(day)
    facts["new_channels"] = sorted(set(channels) - earlier) if earlier else []

    # prediction-error spikes, once brain/ writes them: logs/YYYY-MM-DD-surprises.json
    surprises = load_json(LOGS / f"{day.isoformat()}-surprises.json")
    if surprises:
        facts["surprises"] = surprises[:5]
    req = open_request()
    if req:
        facts["open_request"] = req
    return facts


def parse_day(arg: str | None) -> date:
    if not arg:
        return date.today() - timedelta(days=1)
    if arg == "today":
        return date.today()
    return date.fromisoformat(arg)


if __name__ == "__main__":
    d = parse_day(sys.argv[1] if len(sys.argv) > 1 else None)
    f = make_facts(d)
    out = DAGBOEK / f"{d.isoformat()}.facts.json"
    save_json(out, f)
    print(f"wrote {out.relative_to(DAGBOEK.parent.parent)}  (awake: {f.get('awake')}, "
          f"{f.get('awake_min', 0)} min, {f.get('sessions')} sessions)")
