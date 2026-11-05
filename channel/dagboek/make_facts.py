"""Step 1 of the diary: logs -> facts.json. Plain arithmetic, no AI.

    python channel/dagboek/make_facts.py            # yesterday (for the nightly run after midnight)
    python channel/dagboek/make_facts.py 2026-11-06 # a specific day
    python channel/dagboek/make_facts.py today

Reads Pip's raw memory, logs/pip.db (SQLite, read-only; see pipdb.py in the Pip
folder), and writes channel/dagboek/YYYY-MM-DD.facts.json.

The output format is exactly what the CSV version produced (same keys, same
rounding, HH:MM local times), so the diary checker and the website don't change:
- one session per bridge session that has frames on the day;
- every numeric channel gets start/end/min/max/biggest_jump;
- channels named touch* count as touch events whenever the value changes.
Frames are assigned to a day by their own timestamp (local time).
Only one day of frames is read, however large the database grows.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import DAGBOEK, LOGS, CHANNEL, PIP, load_json, save_json  # noqa: E402

sys.path.insert(0, str(PIP))
import pipdb  # noqa: E402

GAP_S = 30  # a silence longer than this inside a session counts as a disconnect
TCOL = "pc_time"  # the time column of the old CSV logs; never a channel


def to_num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def day_window_ms(day: date) -> tuple[int, int]:
    """Local day as epoch ms, with one second of margin each side (exact filter below)."""
    start = datetime.combine(day, dtime.min).timestamp()
    end = datetime.combine(day + timedelta(days=1), dtime.min).timestamp()
    return int(start * 1000) - 1000, int(end * 1000) + 1000


def read_sessions(day: date, db_path: Path | None = None) -> list[dict]:
    """One session per bridge session that has frames on `day`, oldest first."""
    try:
        con = pipdb.connect(db_path or LOGS / "pip.db", readonly=True)
    except FileNotFoundError as e:
        print(e)
        return []
    t0, t1 = day_window_ms(day)
    sessions = []
    try:
        for s in sorted(pipdb.sessions_between(con, t0, t1), key=lambda s: s["name"]):
            rows = []
            for fr in pipdb.frames(con, s, t0, t1):
                t = datetime.fromtimestamp(fr.pop("ts_ms") / 1000)
                if t.date() == day:
                    rows.append((t, fr))
            if rows:
                rows.sort(key=lambda x: x[0])
                sessions.append({"name": s["name"], "manifest": s["manifest"], "rows": rows})
    finally:
        con.close()
    return sessions


def manifest_channels(day: date, sessions: list[dict]) -> list[str]:
    """Channel names Pip reported about itself in that day's manifests (plus logged columns).

    Kept identical to the CSV version, which also counted the manifest's top-level
    keys (name, fw, fields, ...) and t_ms. See the note in the report.
    """
    names: set[str] = set()
    for s in sessions:
        data = s["manifest"] or {}
        ch = data.get("channels", data)
        if isinstance(ch, dict):
            names.update(ch.keys())
        elif isinstance(ch, list):
            for c in ch:
                names.add(c.get("name") if isinstance(c, dict) else str(c))
        for col in s["rows"][0][1].keys():
            if col != TCOL:
                names.add(col)
    names.discard(None)
    return sorted(n for n in names if n)


def known_before(day: date) -> set[str]:
    known: set[str] = set()
    for f in DAGBOEK.glob("*.facts.json"):
        if f.name[:10] < day.isoformat():
            known.update((load_json(f, {}) or {}).get("channels", []))
    return known


def day_number(day: date) -> int:
    """Day 1 = first day that has a facts file (or this one)."""
    days = sorted(f.name[:10] for f in DAGBOEK.glob("*.facts.json"))
    days = [d for d in days if d < day.isoformat()]
    return len(days) + 1


def open_request() -> dict | None:
    for r in (load_json(CHANNEL / "requests.json", []) or []):
        if r.get("status") in ("open", "funded"):
            return {"id": r["id"], "status": r["status"]}
    return None


def make_facts(day: date, db_path: Path | None = None) -> dict:
    sessions = read_sessions(day, db_path)
    facts: dict = {"date": day.isoformat(), "day": day_number(day)}
    if not sessions:
        facts.update({"awake": False, "sessions": 0})
        return facts

    awake_s, disconnects, touch_events = 0.0, 0, 0
    series: dict[str, list[tuple[datetime, float]]] = {}
    for s in sessions:
        rows = s["rows"]
        awake_s += (rows[-1][0] - rows[0][0]).total_seconds()
        prev_t, prev_touch = None, {}
        for t, r in rows:
            if prev_t and (t - prev_t).total_seconds() > GAP_S:
                disconnects += 1
            prev_t = t
            for k, v in r.items():
                x = to_num(v)
                if x is None:
                    continue
                series.setdefault(k, []).append((t, x))
                if k.lower().startswith("touch"):
                    if k in prev_touch and x != prev_touch[k]:
                        touch_events += 1
                    prev_touch[k] = x

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
