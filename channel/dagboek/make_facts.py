"""Step 1 of the diary: Pip's memory -> facts.json. Plain arithmetic, no AI.

    python channel/dagboek/make_facts.py            # yesterday (for the nightly run after midnight)
    python channel/dagboek/make_facts.py 2026-11-06 # a specific day
    python channel/dagboek/make_facts.py today

Reads logs/pip.db (the bridge's database, read-only, via pipdb.py) and writes
channel/dagboek/YYYY-MM-DD.facts.json. Falls back to the old logs/*-sense.csv files only
when the database has nothing for that day.

A day is a local calendar day (frames are stored as UTC epoch ms). The database holds
~20 frames/s, so everything is computed in one streaming pass: per channel start/end/min/max,
biggest jump and (for touch pins) the number of touches. Touch pins give raw capacitance;
a touch is a fall below TOUCH_DROP x the pin's median.
"""
from __future__ import annotations

import csv
import sys
from array import array
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import DAGBOEK, LOGS, CHANNEL, PIP, load_json, save_json  # noqa: E402

TIME_COLS = ("ts", "timestamp", "time", "t", "pc_time", "datetime")
CLOCK_COLS = ("t_ms", "ms", "millis", "uptime_ms")  # Pip's own clock, not a sense
TOUCH_DROP = 0.6  # a touch pin "jumps" when it falls below 60% of its usual (median) value
GAP_S = 30  # a silence longer than this inside a session counts as a disconnect


def hm(ts_s: float) -> str:
    return datetime.fromtimestamp(ts_s).strftime("%H:%M")


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- streaming statistics
class Day:
    """Collects one day's facts from frames fed in time order, session by session."""

    def __init__(self):
        self.sessions: list[dict] = []
        self.channels: set[str] = set()
        self.stats: dict[str, dict] = {}
        self.touch: dict[str, array] = {}
        self.gaps = 0
        self._last_ts = None

    def begin(self, name: str, fields: list[str]):
        self.sessions.append({"name": name, "first": None, "last": None})
        self.channels.update(f for f in fields if f and f not in CLOCK_COLS)
        self._last_ts = None

    def add(self, ts: float, fields: list[str], values) -> None:
        """ts in epoch seconds; values in the same order as fields."""
        s = self.sessions[-1]
        if s["first"] is None:
            s["first"] = ts
        s["last"] = ts
        if self._last_ts is not None and ts - self._last_ts > GAP_S:
            self.gaps += 1
        self._last_ts = ts
        stats = self.stats
        for k, v in zip(fields, values):
            x = num(v)
            if x is None:
                continue
            st = stats.get(k)
            if st is None:
                stats[k] = {"start": x, "end": x, "min": x, "min_t": ts, "max": x, "max_t": ts,
                            "prev": x, "jump": 0.0}
                if k.lower().startswith("touch"):
                    self.touch[k] = array("f")
            else:
                st["end"] = x
                if x < st["min"]:
                    st["min"], st["min_t"] = x, ts
                if x > st["max"]:
                    st["max"], st["max_t"] = x, ts
                j = abs(x - st["prev"])
                if j > st["jump"]:
                    st["jump"] = j
                st["prev"] = x
            if k in self.touch:
                self.touch[k].append(x)

    def touches(self) -> dict[str, int]:
        out = {}
        for k, vals in self.touch.items():
            if not vals:
                continue
            sample = sorted(vals[:: max(1, len(vals) // 20000)])
            base = sample[len(sample) // 2]
            low, n = False, 0
            for x in vals:
                if not low and x < base * TOUCH_DROP:
                    low, n = True, n + 1
                elif low and x > base * (TOUCH_DROP + 0.15):
                    low = False
            if n:
                out[k] = n
        return out


# ---------------------------------------------------------------- sources
def day_range_ms(day: date) -> tuple[int, int]:
    t0 = datetime.combine(day, dtime.min).timestamp()
    t1 = datetime.combine(day + timedelta(days=1), dtime.min).timestamp()
    return int(t0 * 1000), int(t1 * 1000)


def read_db(day: date) -> Day | None:
    db = LOGS / "pip.db"
    if not db.exists():
        return None
    sys.path.insert(0, str(PIP))
    try:
        import pipdb
    except ImportError:
        print("pipdb.py not found next to logs/; can't read pip.db")
        return None
    con = pipdb.connect(db, readonly=True)
    try:
        t0, t1 = day_range_ms(day)
        sessions = pipdb.sessions_between(con, t0, t1)
        if not sessions:
            return None
        cmap = pipdb.channel_map(con)
        d = Day()
        for s in sessions:
            fields = [f for f in s["manifest"].get("fields", []) if f != "t_ms" and f in cmap]
            d.begin(s["name"], fields)
            col_sql = "".join(f', "{cmap[f]}"' for f in fields)
            cur = con.execute(f"SELECT ts_ms{col_sql} FROM frames WHERE session_id=? AND ts_ms>=? AND ts_ms<? "
                              f"ORDER BY ts_ms, rowid", (s["id"], t0, t1))
            while True:
                rows = cur.fetchmany(5000)
                if not rows:
                    break
                for row in rows:
                    d.add(row[0] / 1000, fields, row[1:])
        return d
    finally:
        con.close()


def parse_time(v: str) -> float | None:
    v = (v or "").strip()
    if not v:
        return None
    try:
        x = float(v)
        if x > 1e12:
            x /= 1000
        return x if x >= 1e8 else None   # below that it's seconds since boot
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None


def read_csv(day: date) -> Day | None:
    """Old logs from before the database (logs/*-sense.csv)."""
    t0, t1 = (x / 1000 for x in day_range_ms(day))
    d = Day()
    for f in sorted(LOGS.glob("*-sense.csv")):
        with f.open(newline="", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                continue
            tcol = next((c for c in reader.fieldnames if c.strip().lower() in TIME_COLS), None)
            if not tcol:
                continue
            fields = [c for c in reader.fieldnames if c != tcol and c not in CLOCK_COLS]
            rows = []
            for r in reader:
                t = parse_time(r.get(tcol, ""))
                if t is not None and t0 <= t < t1:
                    rows.append((t, [r.get(c) for c in fields]))
        if rows:
            rows.sort(key=lambda x: x[0])
            m = load_json(LOGS / f.name.replace("-sense.csv", "-manifest.json"), {}) or {}
            extra = [c for c in (m.get("fields") or []) if isinstance(c, str)]
            d.begin(f.name, fields + extra)
            for t, vals in rows:
                d.add(t, fields, vals)
    return d if d.sessions else None


# ---------------------------------------------------------------- facts
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
    d = read_db(day) or read_csv(day)
    facts: dict = {"date": day.isoformat(), "day": day_number(day)}
    if not d:
        facts.update({"awake": False, "sessions": 0})
        return facts

    awake_s = sum((s["last"] - s["first"]) for s in d.sessions if s["first"] is not None)
    awake_min = round(awake_s / 60)
    touch_by_pin = d.touches()
    facts.update({
        "awake": True,
        "sessions": len(d.sessions),
        "disconnects": d.gaps + max(0, len(d.sessions) - 1),
        "awake_min": awake_min,
        "awake_h": awake_min // 60,
        "awake_rest_min": awake_min % 60,
        "first_seen": hm(min(s["first"] for s in d.sessions if s["first"] is not None)),
        "last_seen": hm(max(s["last"] for s in d.sessions if s["last"] is not None)),
        "touch_events": sum(touch_by_pin.values()),
        "touch_by_pin": touch_by_pin,
        "values": {},
    })
    for k, st in sorted(d.stats.items()):
        facts["values"][k] = {
            "start": round(st["start"], 2), "end": round(st["end"], 2),
            "min": round(st["min"], 2), "min_at": hm(st["min_t"]),
            "max": round(st["max"], 2), "max_at": hm(st["max_t"]),
            "biggest_jump": round(st["jump"], 2),
        }

    channels = sorted(d.channels)
    facts["channels"] = channels
    earlier = known_before(day)
    facts["new_channels"] = sorted(set(channels) - earlier) if earlier else []

    # prediction-error spikes from the brain: logs/YYYY-MM-DD-surprises.json, biggest first
    surprises = load_json(LOGS / f"{day.isoformat()}-surprises.json")
    if surprises:
        facts["surprises"] = sorted(surprises, key=lambda s: -float(s.get("size", 0)))[:5]
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
