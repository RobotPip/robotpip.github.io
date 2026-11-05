"""Build Pip's website into site/dist/. Standard library only, no Node, no Hugo.

    python site/build.py            # build
    python site/build.py --serve    # build, then preview at http://localhost:8000

Inputs (all plain files in the repo):
    site/config.json                 title, links (YouTube, Ko-fi, monthly, ...), base path
    site/static/status.json          made by channel/make_status.py
    channel/dagboek/*.md             Pip's diary; only `status: approved` is published
    channel/dagboek/*.facts.json     numbers for the charts
    channel/posts/*.md               Pip's longer posts: weekly digests, episode write-ups (`status: published`)
    channel/requests.json            channel-side state of each request (title, € needed, status)
    parent/requests/<id>*.json       Pip's own request (missing / evidence / constraints / success)
    channel/episodes.json            YouTube episodes and Shorts
    channel/budget.csv               the ledger
"""
from __future__ import annotations

import csv
import re
import html
import json
import shutil
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "channel"))
from common import (CHANNEL, DAGBOEK, DISCLOSURE, REQUESTS_DIR, SITE,  # noqa: E402
                    load_json, markdown, read_post)

CFG = load_json(SITE / "config.json")
BASE = CFG.get("base_path", "").rstrip("/")
DIST = SITE / "dist"
e = html.escape

STATUS_LABEL = {"open": "Open, needs funding", "funded": "Funded", "installed": "Installed, Pip doesn't know yet",
                "discovered": "Pip found it", "met": "Need met", "not-met": "Need not met", "cancelled": "Cancelled"}


def fmt(x) -> str:
    return "–" if x is None else f"{x:.0f}"


def url(path: str) -> str:
    return f"{BASE}/{path.lstrip('/')}"


# ---------------------------------------------------------------- data
def diary() -> list[dict]:
    out = []
    for p in sorted(DAGBOEK.glob("????-??-??.md"), reverse=True):
        meta, body = read_post(p)
        if meta.get("status") != "approved":
            continue
        pip, _, note = body.partition("## Private note")
        out.append({"date": p.stem, "day": meta.get("day", "?"), "pip": pip.strip(), "note": note.strip(),
                    "facts": load_json(DAGBOEK / f"{p.stem}.facts.json", {}) or {}})
    return out


def posts() -> list[dict]:
    out = []
    for p in sorted((CHANNEL / "posts").glob("*.md"), reverse=True):
        meta, body = read_post(p)
        if meta.get("status") != "published":
            continue
        out.append({"slug": p.stem, "title": meta.get("title", p.stem), "date": meta.get("date", p.stem[:10]),
                    "summary": meta.get("summary", ""), "youtube": meta.get("youtube", ""),
                    "ai": meta.get("ai", "yes") != "no", "body": body})
    return out


def requests_data() -> list[dict]:
    status = load_json(SITE / "static" / "status.json", {}) or {}
    open_id = (status.get("open_request") or {}).get("id")
    out = []
    for r in load_json(CHANNEL / "requests.json", []) or []:
        r = dict(r)
        src = next(iter(sorted(REQUESTS_DIR.glob(f"{r['id']}*.json"))), None) if REQUESTS_DIR.exists() else None
        r["pip"] = load_json(src, {}) if src else {}
        if r["id"] == open_id:
            r["raised_eur"] = status["open_request"].get("raised_eur", 0)
        out.append(r)
    return sorted(out, key=lambda r: r["id"], reverse=True)


def ledger() -> list[dict]:
    with (CHANNEL / "budget.csv").open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("date")]


# ---------------------------------------------------------------- pieces
def link_buttons(compact=False) -> str:
    L = CFG["links"]
    items = [("youtube", "YouTube"), ("kofi", "Ko-fi"), ("monthly", "Monthly"), ("github", "GitHub"),
             ("instagram", "Instagram"), ("tiktok", "TikTok")]
    return "".join(f'<a class="chip" href="{e(L[k])}" rel="me noopener">{lab}</a>'
                   for k, lab in items if L.get(k)) or ("" if compact else '<span class="muted">Links coming soon.</span>')


def sparkline(days: list[dict]) -> str:
    pts = [(d["date"], d["facts"].get("values", {}).get("battery_mv", {})) for d in reversed(days[:21])]
    pts = [(dt, v) for dt, v in pts if v.get("min") is not None]
    if len(pts) < 2:
        return ""
    lo = min(v["min"] for _, v in pts)
    hi = max(v["max"] for _, v in pts)
    span = (hi - lo) or 1
    w, h, pad = 600, 120, 8

    def xy(i, val):
        return pad + i * (w - 2 * pad) / (len(pts) - 1), h - pad - (val - lo) / span * (h - 2 * pad)
    band = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(i, v["max"]) for i, (_, v) in enumerate(pts)))
    band += " " + " ".join(f"{x:.1f},{y:.1f}" for x, y in
                           reversed([xy(i, v["min"]) for i, (_, v) in enumerate(pts)]))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(i, v["end"]) for i, (_, v) in enumerate(pts)))
    return (f'<figure class="spark"><svg viewBox="0 0 {w} {h}" role="img" aria-label="My energy per day, '
            f'{pts[0][0]} to {pts[-1][0]}"><polygon points="{band}" class="band"/>'
            f'<polyline points="{line}" class="line"/></svg>'
            f'<figcaption>My energy, last {len(pts)} days · band = lowest to highest, line = end of day · '
            f'{lo:.0f}–{hi:.0f}</figcaption></figure>')


def progress(r: dict) -> str:
    need = float(r.get("needed_eur") or 0)
    got = float(r.get("raised_eur") or 0)
    pct = min(100, round(got / need * 100)) if need else 0
    kofi = CFG["links"].get("kofi")
    btn = f'<a class="btn" href="{e(kofi)}">Fund request #{e(r["id"])} on Ko-fi</a>' if kofi and r.get("status") == "open" else ""
    return (f'<div class="progress" role="progressbar" aria-valuenow="{pct}" aria-valuemin="0" aria-valuemax="100">'
            f'<span style="width:{pct}%"></span></div>'
            f'<p class="nums">€{got:.2f} of €{need:.2f}</p>{btn}')


def entry_card(d: dict, full=False) -> str:
    f = d["facts"]
    meta = []
    if f.get("awake"):
        meta.append(f'awake {f.get("awake_h", 0)} h {f.get("awake_rest_min", 0)} min')
        bm = f.get("values", {}).get("battery_mv")
        if bm:
            meta.append(f'energy {bm["start"]:.0f} → {bm["end"]:.0f}')
    else:
        meta.append("asleep")
    head = (f'<h2><a href="{url(f"diary/{d["date"]}/")}">Day {e(str(d["day"]))}</a></h2>'
            if not full else "")
    body = re.sub(r"^\s*Day\s+\d+\.\s*", "", d["pip"])  # heading already says it
    note = ""  # keeper's notes stay private: everything public is in Pip's voice
    return (f'<article class="entry">{head}<p class="meta"><time datetime="{d["date"]}">{d["date"]}</time>'
            f' · {" · ".join(meta)}</p><div class="pip">{markdown(body)}</div>{note}'
            f'{"<p class=disclose>" + e(DISCLOSURE) + "</p>" if full else ""}</article>')


def yt(id_: str, title: str) -> str:
    return (f'<div class="video"><iframe src="https://www.youtube-nocookie.com/embed/{e(id_)}" title="{e(title)}" '
            f'loading="lazy" allow="encrypted-media; picture-in-picture" allowfullscreen></iframe></div>')


# ---------------------------------------------------------------- layout
def page(title: str, body: str, path: str, desc: str = "") -> None:
    nav = [("", "Home"), ("diary/", "Diary"), ("blog/", "Blog"), ("requests/", "Requests"),
           ("videos/", "Videos"), ("support/", "Support Pip"), ("about/", "Rules")]
    navhtml = "".join(f'<a href="{url(p)}"{" aria-current=page" if p == path else ""}>{n}</a>' for p, n in nav)
    full_title = CFG["title"] if not title else f'{title} · {CFG["title"]}'
    doc = f"""<!doctype html>
<html lang="{CFG.get('language', 'en')}">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(full_title)}</title>
<meta name="description" content="{e(desc or CFG['tagline'])}">
<meta property="og:title" content="{e(full_title)}"><meta property="og:description" content="{e(desc or CFG['tagline'])}">
<link rel="alternate" type="application/rss+xml" title="{e(CFG['title'])}" href="{url('feed.xml')}">
<link rel="icon" href="{url('static/favicon.svg')}" type="image/svg+xml">
<link rel="stylesheet" href="{url('static/style.css')}">
</head>
<body>
<header class="top"><a class="brand" href="{url('')}"><span class="led" aria-hidden="true"></span>{e(CFG['title'])}</a>
<nav>{navhtml}</nav></header>
<main>
{body}
</main>
<footer><div class="links">{link_buttons(compact=True)}</div>
<p>Everything here is in Pip's voice: written by a language model from Pip's own sensor logs, and checked by a human
before it goes out. Pip's voice in videos is synthetic. Footage and numbers are real.
Pip never reads this site, and nothing here is fed back to it.</p>
<p lang="nl" class="muted">{e(CFG.get('nl_line', ''))}</p>
<p class="muted"><a href="{url('feed.xml')}">RSS</a></p></footer>
</body></html>
"""
    out = DIST / path / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")


# ---------------------------------------------------------------- pages
def build():
    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(SITE / "static", DIST / "static")
    status = load_json(SITE / "static" / "status.json", {}) or {}
    D, P, R = diary(), posts(), requests_data()
    E = load_json(CHANNEL / "episodes.json", []) or []
    rows = ledger()

    # home
    day = status.get("day") or {}
    b = status.get("budget_eur") or {"balance": 0, "in": 0, "out": 0}
    o = next((r for r in R if r.get("status") in ("open", "funded")), None)
    panel = f"""<section class="panel" aria-label="Pip right now">
<div class="stat"><span class="k">Last diary day</span><span class="v">{e(str(day.get('day', '–')))}</span><span class="s">{e(day.get('date') or 'not awake yet')}</span></div>
<div class="stat"><span class="k">My energy</span><span class="v">{fmt(day.get('energy_mv'))}</span><span class="s">end of day, lowest {fmt(day.get('energy_min_mv'))}</span></div>
<div class="stat"><span class="k">Awake</span><span class="v">{day.get('awake_min', '–')}</span><span class="s">minutes that day</span></div>
<div class="stat"><span class="k">Pip's budget</span><span class="v">€{b['balance']:.2f}</span><span class="s">in €{b['in']:.2f} · spent €{b['out']:.2f}</span></div>
</section>
<p class="muted small">Shown with a delay: the panel follows the last approved diary day. Updated {e(status.get('updated', '–'))}.</p>"""
    req_box = (f'<section class="request"><p class="kicker">Open request #{e(o["id"])} · {STATUS_LABEL.get(o["status"], o["status"])}</p>'
               f'<h2><a href="{url(f"requests/{o["id"]}/")}">{e(o.get("title", "Pip needs something"))}</a></h2>{progress(o)}</section>'
               if o else
               '<section class="request quiet"><p class="kicker">No open request</p><p>Pip hasn\'t asked for anything yet. '
               'It first has to notice something is missing, and prove it from its own data.</p></section>')
    latest_ep = E[0] if E else None
    page("", f"""<section class="hero"><h1>{e(CFG['tagline'])}</h1>
<p class="lede">Pip has no parts list. When it can show from its own data that something is missing,
it writes a request. Viewers fund the request, a parent agent picks the smallest thing that meets the need,
and Pip has to discover what was installed by itself.</p><div class="links">{link_buttons()}</div></section>
{panel}{req_box}{sparkline(D)}
<section><h2 class="section">Latest from the diary</h2>
{''.join(entry_card(d) for d in D[:5]) or '<p class="muted">No approved entries yet.</p>'}
<p><a href="{url('diary/')}">All diary entries →</a></p></section>
{f'<section><h2 class="section">Latest video</h2>{yt(latest_ep["youtube"], latest_ep["title"])}<p>{e(latest_ep["title"])}</p></section>' if latest_ep else ''}
""", "")

    # diary
    page("Diary", f"<h1>Pip's diary</h1><p class='lede'>One entry per day, in Pip's own small vocabulary. "
         f"{e(DISCLOSURE)}</p>{sparkline(D)}" + ("".join(entry_card(d) for d in D) or "<p class=muted>Nothing yet.</p>"),
         "diary/")
    for i, d in enumerate(D):
        newer = f'<a href="{url(f"diary/{D[i-1]["date"]}/")}">Day {D[i-1]["day"]} →</a>' if i > 0 else "<span></span>"
        older = f'<a href="{url(f"diary/{D[i+1]["date"]}/")}">← Day {D[i+1]["day"]}</a>' if i + 1 < len(D) else "<span></span>"
        page(f'Day {d["day"]}', f'<h1>Day {e(str(d["day"]))}</h1>{entry_card(d, full=True)}'
             f'<nav class="pager">{older}{newer}</nav>', f'diary/{d["date"]}/', d["pip"][:150])

    # blog
    page("Blog", "<h1>Blog</h1><p class='lede'>Build log, episodes and weekly digests.</p>" + ("".join(
        f'<article class="entry"><h2><a href="{url(f"blog/{p["slug"]}/")}">{e(p["title"])}</a></h2>'
        f'<p class="meta">{e(p["date"])}</p><p>{e(p["summary"])}</p></article>' for p in P)
        or "<p class=muted>First post coming soon.</p>"), "blog/")
    for p in P:
        page(p["title"], f'<article class="post"><h1>{e(p["title"])}</h1><p class="meta">{e(p["date"])}</p>'
             f'{yt(p["youtube"], p["title"]) if p["youtube"] else ""}{markdown(p["body"])}'
             f'{"<p class=disclose>" + e(DISCLOSURE) + "</p>" if p["ai"] else ""}</article>',
             f'blog/{p["slug"]}/', p["summary"])

    # requests
    def req_row(r):
        return (f'<article class="entry"><p class="kicker">#{e(r["id"])} · {STATUS_LABEL.get(r.get("status"), r.get("status"))}</p>'
                f'<h2><a href="{url(f"requests/{r["id"]}/")}">{e(r.get("title", "Request"))}</a></h2>'
                f'{progress(r) if r.get("status") in ("open", "funded") else ""}</article>')
    page("Requests", "<h1>Pip's requests</h1><p class='lede'>Pip never asks for a product. Each request says what is "
         "missing, the evidence from its own logs, the limits it measured, and how it will judge success.</p>"
         + ("".join(req_row(r) for r in R) or "<p class=muted>No requests yet.</p>"), "requests/")
    for r in R:
        pip = r["pip"]
        fields = [("Missing", "missing"), ("Evidence", "evidence"), ("Constraints", "constraints"),
                  ("Success criterion", "success_criterion")]

        def show(v):
            if isinstance(v, (list, dict)):
                return f"<pre><code>{e(json.dumps(v, indent=2, ensure_ascii=False))}</code></pre>"
            return f"<p>{e(str(v))}</p>"
        dl = "".join(f"<dt>{lab}</dt><dd>{show(pip[k])}</dd>" for lab, k in fields if k in pip)
        answer = (f'<section><h2>The parent\'s answer</h2>{markdown(r["answer"])}</section>'
                  if r.get("answer") and r.get("status") not in ("open", "funded") else "")
        outcome = f'<section><h2>Outcome</h2>{markdown(r["outcome"])}</section>' if r.get("outcome") else ""
        vid = yt(r["youtube"], r.get("title", "")) if r.get("youtube") else ""
        page(f'Request #{r["id"]}', f'<p class="kicker">Request #{e(r["id"])} · {STATUS_LABEL.get(r.get("status"), r.get("status"))}</p>'
             f'<h1>{e(r.get("title", "Request"))}</h1>'
             f'{progress(r) if r.get("status") in ("open", "funded") else ""}'
             f'<section><h2>In Pip\'s words</h2>{"<dl class=req>" + dl + "</dl>" if dl else "<p class=muted>Request file not published.</p>"}</section>'
             f'{answer}{vid}{outcome}'
             f'{f"<details><summary>Raw request JSON</summary><pre><code>{e(json.dumps(pip, indent=2, ensure_ascii=False))}</code></pre></details>" if pip else ""}',
             f'requests/{r["id"]}/', r.get("title", ""))

    # videos
    longs = [v for v in E if v.get("kind", "episode") == "episode"]
    shorts = [v for v in E if v.get("kind") == "short"]
    page("Videos", "<h1>Videos</h1><p class='lede'>One episode per request, from the first sign of a lack to "
         "Pip's own verdict. Plus Shorts.</p>"
         + "".join(f'<section class="ep">{yt(v["youtube"], v["title"])}<h2>{e(v["title"])}</h2>'
                   f'<p class="meta">{e(v.get("date", ""))}{" · request #" + e(v["request"]) if v.get("request") else ""}</p></section>'
                   for v in longs)
         + (f'<h2 class="section">Shorts</h2><div class="shorts">' + "".join(
             f'<a class="short" href="https://www.youtube.com/shorts/{e(v["youtube"])}">{e(v["title"])}</a>' for v in shorts)
            + "</div>" if shorts else "")
         + ("" if E else f"<p class=muted>The first episode, “Day 0: Pip wakes up”, is coming.</p>"
            f"<div class='links'>{link_buttons()}</div>"), "videos/")

    # support
    L = CFG["links"]
    ledger_rows = "".join(f"<tr><td>{e(r['date'])}</td><td>{e(r['direction'])}</td><td class=num>€{float(r['amount_eur']):.2f}</td>"
                          f"<td>{e(r['request'] or '–')}</td><td>{e(r['source'])}</td><td>{e(r['note'])}</td></tr>" for r in reversed(rows))
    page("Support Pip", f"""<h1>Support Pip</h1>
<p class="lede">{e(CFG['money_rule'])}</p>
<div class="ways">
<section class="way"><h2>Fund a request</h2><p>A one-off gift on Ko-fi. Each open request has its own goal.
Ko-fi takes no fee on one-off gifts; iDEAL and cards work without an account.</p>
{f'<a class="btn" href="{e(L["kofi"])}">Give on Ko-fi</a>' if L.get('kofi') else '<p class="muted">Ko-fi page coming soon.</p>'}</section>
<section class="way"><h2>Monthly</h2><p>A small monthly amount keeps Pip's budget topped up between requests,
so a new need doesn't have to wait.</p>
{f'<a class="btn" href="{e(L["monthly"])}">{e(CFG.get("monthly_label", "Support monthly"))}</a>' if L.get('monthly') else '<p class="muted">Monthly support coming soon.</p>'}</section>
<section class="way"><h2>Watch</h2><p>Watching, liking and subscribing on YouTube helps too. Once the channel
qualifies, ad income goes into the same budget.</p>
{f'<a class="btn ghost" href="{e(L["youtube"])}">YouTube channel</a>' if L.get('youtube') else ''}</section>
</div>
<section><h2 class="section">Viewers fund needs, they don't choose them</h2>
<p>Pip writes its own requests from its own data. Comments and gifts never reach Pip or the parent agent.
You can't buy Pip a camera; you can help it get whatever turns out to meet the need it proved.</p></section>
<section><h2 class="section">Every euro, in and out</h2>
<p class="muted">Amounts after fees. Receipts are kept with each request.</p>
<div class="tablewrap"><table><thead><tr><th>Date</th><th>In/out</th><th>Amount</th><th>Request</th><th>Source</th><th>Note</th></tr></thead>
<tbody>{ledger_rows or '<tr><td colspan=6 class=muted>No money in or out yet.</td></tr>'}</tbody></table></div>
<p class="nums">Balance €{b['balance']:.2f}</p></section>""", "support/")

    # about / rules
    about = (CHANNEL / "about.md")
    page("Rules", markdown(read_post(about)[1], shift=0) if about.exists() else "<h1>Rules</h1>", "about/")

    # 404 + RSS
    page("Not found", "<h1>Nothing here</h1><p>Pip doesn't know this page either.</p>", "404-tmp/")
    (DIST / "404-tmp" / "index.html").rename(DIST / "404.html")
    (DIST / "404-tmp").rmdir()
    rss(D, P)
    (DIST / ".nojekyll").write_text("")
    print(f"built {len(D)} diary entries, {len(P)} posts, {len(R)} requests, {len(E)} videos -> {DIST}")


def rss(D, P):
    items = []
    for d in D[:30]:
        items.append((d["date"], f'Day {d["day"]}', f'diary/{d["date"]}/', markdown(d["pip"])))
    for p in P[:30]:
        items.append((p["date"], p["title"], f'blog/{p["slug"]}/', e(p["summary"])))
    items.sort(reverse=True)
    base = CFG["base_url"].rstrip("/")

    def rfc(dt):
        return format_datetime(datetime.fromisoformat(dt[:10]).replace(hour=21, tzinfo=timezone.utc))
    xml = "".join(f"<item><title>{e(t)}</title><link>{base}/{p}</link><guid>{base}/{p}</guid>"
                  f"<pubDate>{rfc(dt)}</pubDate><description>{e(desc)}</description></item>" for dt, t, p, desc in items[:40])
    (DIST / "feed.xml").write_text(
        f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>{e(CFG["title"])}</title>'
        f'<link>{base}/</link><description>{e(CFG["tagline"])}</description>{xml}</channel></rss>', encoding="utf-8")


if __name__ == "__main__":
    build()
    if "--serve" in sys.argv:
        import functools
        import http.server
        # serve dist/ under the same base path GitHub Pages uses
        root = DIST.parent / "_preview"
        if root.exists():
            shutil.rmtree(root)
        target = root / BASE.strip("/") if BASE else root
        shutil.copytree(DIST, target)
        h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        print(f"preview: http://localhost:8000{BASE}/  (Ctrl+C to stop)")
        http.server.ThreadingHTTPServer(("localhost", 8000), h).serve_forever()
