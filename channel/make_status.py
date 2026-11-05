"""Ledger + requests -> site/static/status.json and the budget table in channel/README.md.

    python channel/make_status.py

Source of truth is channel/budget.csv (append-only, amounts AFTER fees):
    date,direction,amount_eur,request,source,note
    2026-11-20,in,5.00,001,ko-fi,"one-off tip"
    2026-11-21,in,3.00,,ko-fi-monthly,"monthly supporters, November"
    2026-11-24,out,11.40,001,shop,"parent answer 001, receipt receipts/001.pdf"

Rule from the channel plan: gifts fund the open request first; anything extra carries over.
So the per-request "raised" figure is the balance available when it opened plus gifts tagged
to it, capped at what it needs.

The live panel only shows the latest *approved* diary day, so it never reveals in real time
when Pip (or the keeper) is awake.
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CHANNEL, DAGBOEK, SITE, load_json, read_post, save_json  # noqa: E402


def ledger() -> list[dict]:
    with (CHANNEL / "budget.csv").open(newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("date")]
    for r in rows:
        r["amount"] = float(r["amount_eur"])
    return sorted(rows, key=lambda r: r["date"])


def totals(rows) -> dict:
    tin = sum(r["amount"] for r in rows if r["direction"] == "in")
    tout = sum(r["amount"] for r in rows if r["direction"] == "out")
    return {"in": round(tin, 2), "out": round(tout, 2), "balance": round(tin - tout, 2)}


def request_view(rows) -> list[dict]:
    reqs = load_json(CHANNEL / "requests.json", []) or []
    out = []
    for r in reqs:
        r = dict(r)
        spent = sum(x["amount"] for x in rows if x["direction"] == "out" and x["request"] == r["id"])
        r["spent_eur"] = round(spent, 2)
        if r.get("status") in ("open", "funded"):
            bal = totals(rows)["balance"]
            r["raised_eur"] = round(min(bal, r.get("needed_eur", 0) or bal), 2)
        out.append(r)
    return out


def latest_day() -> dict | None:
    for p in sorted(DAGBOEK.glob("????-??-??.md"), reverse=True):
        if read_post(p)[0].get("status") == "approved":
            return load_json(DAGBOEK / f"{p.stem}.facts.json", {}) or {}
    return None


def main():
    rows = ledger()
    reqs = request_view(rows)
    day = latest_day()
    energy = (day or {}).get("values", {}).get("battery_mv", {})
    open_req = next((r for r in reqs if r.get("status") in ("open", "funded")), None)
    status = {
        "updated": datetime.now().astimezone().isoformat(timespec="minutes"),
        "day": day and {"date": day.get("date"), "day": day.get("day"),
                        "awake_min": day.get("awake_min", 0),
                        "energy_mv": energy.get("end"), "energy_min_mv": energy.get("min")},
        "open_request": open_req and {k: open_req.get(k) for k in
                                      ("id", "title", "status", "needed_eur", "raised_eur")},
        "budget_eur": totals(rows),
        "gifts": sum(1 for r in rows if r["direction"] == "in"),
    }
    save_json(SITE / "static" / "status.json", status)

    # budget table inside channel/README.md, between the markers
    readme = CHANNEL / "README.md"
    t = totals(rows)
    table = ["| Date | In/out | € | Request | Source | Note |", "|---|---|---:|---|---|---|"]
    table += [f"| {r['date']} | {r['direction']} | {r['amount']:.2f} | {r['request'] or '–'} | "
              f"{r['source']} | {r['note']} |" for r in rows] or ["| – | – | – | – | – | no entries yet |"]
    table.append(f"\n**In €{t['in']:.2f} · out €{t['out']:.2f} · balance €{t['balance']:.2f}**")
    text = readme.read_text(encoding="utf-8")
    text = re.sub(r"(<!-- budget:start -->).*?(<!-- budget:end -->)",
                  lambda m: m.group(1) + "\n" + "\n".join(table) + "\n" + m.group(2), text, flags=re.S)
    readme.write_text(text, encoding="utf-8")
    print(f"status.json + README budget table updated (balance €{t['balance']:.2f})")


if __name__ == "__main__":
    main()
