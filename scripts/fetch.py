"""Laedt Feeds und verlinkte Artikel herunter und legt sie als Text in quellen/ ab.

Laeuft lokal vor dem Claude-Aufruf. Claude liest danach nur diese Dateien,
ohne eigenen Internetzugang. quellen/ wird nicht ins Repo uebernommen.
"""

import datetime as dt
import email.utils
import html
import re
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FEEDS = {
    "tagesschau": "https://www.tagesschau.de/index~rss2.xml",
    "deutschlandfunk": "https://www.deutschlandfunk.de/nachrichten-100.rss",
    "heise": "https://www.heise.de/rss/heise-top-atom.xml",
    "spiegel": "https://www.spiegel.de/schlagzeilen/index.rss",
}
ITEMS_PER_FEED = 12
MAX_AGE_HOURS = 30
MAX_WORDS = 700

OUT = Path(__file__).resolve().parent.parent / "quellen"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Briefing/1.0"
ATOM = "{http://www.w3.org/2005/Atom}"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def clean(text):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return " ".join(text.split())


def parse_date(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def parse_feed(xml):
    root = ET.fromstring(xml)
    items = []
    for it in root.iter("item"):
        items.append({
            "title": clean(it.findtext("title")),
            "link": (it.findtext("link") or "").strip(),
            "teaser": clean(it.findtext("description")),
            "date": parse_date(it.findtext("pubDate")),
        })
    for e in root.iter(f"{ATOM}entry"):
        link = e.find(f"{ATOM}link")
        items.append({
            "title": clean(e.findtext(f"{ATOM}title")),
            "link": link.get("href", "") if link is not None else "",
            "teaser": clean(e.findtext(f"{ATOM}summary")),
            "date": parse_date(e.findtext(f"{ATOM}updated") or e.findtext(f"{ATOM}published")),
        })
    return items


# Absaetze mit diesen Klassen sind Navigation, Anrisse anderer Artikel, Bildtexte usw.
SKIP_CLASS = re.compile(r"teaser|navigation|snackbar|banner|podcast|info|caption|topics|newsletter|footer", re.I)
# Videos, Livestreams und Audios haben keinen Artikeltext.
SKIP_URL = re.compile(r"/(video|multimedia|livestream|audio|podcast)", re.I)
# <p>-Absaetze und die Textbloecke des Deutschlandfunks
BLOCK = re.compile(r'<(p|div)\b([^>]*)>(.*?)</\1>', re.S | re.I)


def article_text(url):
    page = get(url)
    page = re.sub(r"<(script|style|noscript|nav|header|footer|aside)\b.*?</\1>", " ", page, flags=re.S | re.I)
    paras = []
    for tag, attrs, inner in BLOCK.findall(page):
        cls = re.search(r'class="([^"]*)"', attrs)
        cls = cls.group(1) if cls else ""
        if tag.lower() == "div" and "article-details-text" not in cls:
            continue
        if SKIP_CLASS.search(cls):
            continue
        text = clean(inner)
        if len(text) > 40 and text not in paras:
            paras.append(text)
    words = " ".join(paras).split()
    return " ".join(words[:MAX_WORDS])


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=MAX_AGE_HOURS)
    total = 0

    for name, url in FEEDS.items():
        try:
            items = parse_feed(get(url))
        except Exception as e:
            print(f"  {name}: Feed nicht abrufbar ({e})")
            continue
        items = [
            i for i in items
            if i["link"] and not SKIP_URL.search(i["link"]) and (i["date"] is None or i["date"] >= cutoff)
        ][:ITEMS_PER_FEED]

        def load(item):
            try:
                item["text"] = article_text(item["link"])
            except Exception as e:
                item["text"] = ""
                item["error"] = str(e)
            return item

        with ThreadPoolExecutor(max_workers=6) as pool:
            items = list(pool.map(load, items))

        lines = [f"# {name}", ""]
        for i in items:
            lines += [
                f"## {i['title']}",
                f"URL: {i['link']}",
                f"Datum: {i['date']:%Y-%m-%d %H:%M} UTC" if i["date"] else "Datum: unbekannt",
                f"Anriss: {i['teaser']}",
                "",
                f"Artikeltext: {i['text'] or '(nicht verfuegbar)'}",
                "",
            ]
        (OUT / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")
        full = sum(1 for i in items if len(i["text"].split()) >= 50)
        total += full
        print(f"  {name}: {len(items)} Meldungen, davon {full} mit Artikeltext")

    if total == 0:
        print("FEHLER: keine Artikeltexte geladen")
        sys.exit(1)


if __name__ == "__main__":
    main()
