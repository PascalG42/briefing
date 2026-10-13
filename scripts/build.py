"""Vertont episodes/*.md und baut site/ mit MP3s und feed.xml.

Ablauf je Folge:
  1. Bereits veroeffentlichte MP3 wiederverwenden, wenn der Text unveraendert ist (manifest.json).
  2. Sonst jeden Abschnitt mit Gemini TTS vertonen.
  3. Scheitert Gemini (Fehler, Limit, Audio zu kurz), die ganze Folge mit Piper vertonen.
Folgen aelter als KEEP_DAYS Tage kommen nicht mehr auf die Seite.
"""

import base64
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

import yaml

ROOT = Path(__file__).resolve().parent.parent
EPISODES = ROOT / "episodes"
SITE = ROOT / "site"
AUDIO = SITE / "audio"
WORK = ROOT / "work"

BASE_URL = os.environ["BASE_URL"].rstrip("/")
MODEL = os.environ.get("TTS_MODEL", "gemini-3.8-flash-tts")
VOICE = os.environ.get("TTS_VOICE", "Charon")
STYLE = os.environ.get("TTS_STYLE", "")
API_KEY = os.environ.get("GEMINI_API_KEY", "")
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "30"))
# Gratis-Kontingent: wenige Anfragen pro Tag. Deshalb Abschnitte zu grossen Anfragen buendeln.
GEMINI_MAX_WORDS = int(os.environ.get("GEMINI_MAX_WORDS", "750"))
GEMINI_PAUSE = "\n\n<short pause>\n\n"

FEED_TITLE = "Briefing"
FEED_DESCRIPTION = "Automatisch erzeugtes deutsches News-Briefing."

PIPER_HIGH = "https://huggingface.co/rhasspy/piper-voices/resolve/main/de/de_DE/thorsten/high/de_DE-thorsten-high.onnx"
PIPER_LOW_TAR = "https://github.com/rhasspy/piper/releases/download/v0.0.2/voice-de-thorsten-low.tar.gz"

# Hoechstens so viele Woerter pro Sekunde; schneller heisst: Audio abgeschnitten.
MAX_WORDS_PER_SEC = 5.0


class TTSError(Exception):
    pass


def log(msg):
    print(msg, flush=True)


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


# ---------- Folgen einlesen ----------

def parse_episode(path):
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"{path.name}: Frontmatter fehlt")
    meta = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)

    sections = []
    for block in re.split(r"^#{1,6} .*$", body, flags=re.M):
        text = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if text:
            sections.append(text)
    if not sections:
        raise ValueError(f"{path.name}: kein Skripttext")

    date = dt.date.fromisoformat(str(meta.get("datum") or path.stem))
    return {
        "slug": path.stem,
        "date": date,
        "title": meta.get("titel") or f"Briefing vom {date:%d.%m.%Y}",
        "sources": meta.get("quellen") or [],
        "sections": sections,
        "hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


# ---------- Gemini ----------

def find_audio(obj):
    """Sucht alle Base64-Audiobloecke in der Antwort, egal in welcher Verschachtelung."""
    found = []
    if isinstance(obj, dict):
        if obj.get("type") == "audio" and isinstance(obj.get("data"), str):
            found.append(obj["data"])
        inline = obj.get("inlineData") or obj.get("inline_data")
        if isinstance(inline, dict) and isinstance(inline.get("data"), str):
            found.append(inline["data"])
        for v in obj.values():
            found.extend(find_audio(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(find_audio(v))
    return found


def gemini_section(text, out_wav):
    content = {"type": "text", "text": text}
    if STYLE:
        content["annotations"] = [{"type": "speech_metadata", "style": STYLE}]
    body = {
        "model": MODEL,
        "input": [{"type": "user_input", "content": [content]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": VOICE}]},
    }
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        data=json.dumps(body).encode("utf-8"),
        headers={"x-goog-api-key": API_KEY, "Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            if e.code == 429 and "PerDay" in detail:
                raise TTSError(f"Gemini-Tageslimit erreicht: {detail}")
            if e.code in (429, 500, 503) and attempt < 2:
                log(f"    Gemini HTTP {e.code}, neuer Versuch in 40 s")
                time.sleep(40)
                continue
            raise TTSError(f"Gemini HTTP {e.code}: {detail}")
        except (urllib.error.URLError, TimeoutError) as e:
            raise TTSError(f"Gemini nicht erreichbar: {e}")

    blocks = find_audio(data)
    if not blocks:
        raise TTSError(f"Gemini-Antwort ohne Audio: {json.dumps(data)[:500]}")
    audio = base64.b64decode(blocks[-1])

    if audio[:4] == b"RIFF":
        out_wav.write_bytes(audio)
    else:
        # Rohes PCM: 16 bit, mono, 24 kHz
        pcm = out_wav.with_suffix(".pcm")
        pcm.write_bytes(audio)
        run(["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(pcm), str(out_wav)])

    words = len(text.split())
    secs = duration(out_wav)
    if secs < words / MAX_WORDS_PER_SEC:
        raise TTSError(f"Audio zu kurz ({secs:.0f} s fuer {words} Woerter), vermutlich abgeschnitten")


# ---------- Piper ----------

_piper_model = None


def piper_model():
    global _piper_model
    if _piper_model:
        return _piper_model
    voices = WORK / "piper"
    voices.mkdir(parents=True, exist_ok=True)
    onnx = voices / "de_DE-thorsten-high.onnx"
    try:
        urllib.request.urlretrieve(PIPER_HIGH, onnx)
        urllib.request.urlretrieve(PIPER_HIGH + ".json", str(onnx) + ".json")
        _piper_model = onnx
    except Exception as e:
        log(f"  Piper-Stimme high nicht ladbar ({e}), nehme low")
        tgz = voices / "low.tar.gz"
        urllib.request.urlretrieve(PIPER_LOW_TAR, tgz)
        with tarfile.open(tgz) as t:
            t.extractall(voices, filter="data")
        _piper_model = next(voices.rglob("*low*.onnx"))
    return _piper_model


def piper_section(text, out_wav):
    subprocess.run(
        ["piper", "-m", str(piper_model()), "-f", str(out_wav)],
        input=text, text=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )


# ---------- Folge bauen ----------

def gemini_chunks(sections):
    """Fasst Abschnitte zu Bloecken von hoechstens GEMINI_MAX_WORDS Woertern zusammen."""
    chunks, current, words = [], [], 0
    for text in sections:
        n = len(text.split())
        if current and words + n > GEMINI_MAX_WORDS:
            chunks.append(GEMINI_PAUSE.join(current))
            current, words = [], 0
        current.append(text)
        words += n
    if current:
        chunks.append(GEMINI_PAUSE.join(current))
    return chunks


def synthesize(ep, engine, workdir):
    texts = gemini_chunks(ep["sections"]) if engine is gemini_section else ep["sections"]
    parts = []
    for i, text in enumerate(texts, 1):
        log(f"    Teil {i}/{len(texts)} ({len(text.split())} Woerter)")
        wav = workdir / f"{engine.__name__}_{i:02d}.wav"
        engine(text, wav)
        norm = workdir / f"norm_{i:02d}.wav"
        run(["ffmpeg", "-y", "-i", str(wav), "-ar", "24000", "-ac", "1", "-sample_fmt", "s16", str(norm)])
        parts.append(norm)
    return parts


def build_mp3(ep, out_mp3):
    workdir = WORK / ep["slug"]
    workdir.mkdir(parents=True, exist_ok=True)

    engine_name = "gemini"
    parts = None
    if API_KEY:
        try:
            log(f"  Gemini ({MODEL}, Stimme {VOICE})")
            parts = synthesize(ep, gemini_section, workdir)
        except TTSError as e:
            log(f"  Gemini gescheitert: {e}")
    else:
        log("  Kein GEMINI_API_KEY gesetzt")
    if parts is None:
        engine_name = "piper"
        log("  Ersatz: Piper")
        parts = synthesize(ep, piper_section, workdir)

    silence = workdir / "pause.wav"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "0.8",
         "-sample_fmt", "s16", str(silence)])
    listfile = workdir / "list.txt"
    lines = []
    for i, p in enumerate(parts):
        if i:
            lines.append(f"file '{silence.as_posix()}'")
        lines.append(f"file '{p.as_posix()}'")
    listfile.write_text("\n".join(lines) + "\n", encoding="utf-8")

    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ac", "1", "-ar", "24000",
         "-codec:a", "libmp3lame", "-b:a", "64k",
         "-metadata", f"title={ep['title']}", "-metadata", f"artist={FEED_TITLE}",
         str(out_mp3)])
    return engine_name


def fetch_previous():
    try:
        with urllib.request.urlopen(f"{BASE_URL}/manifest.json", timeout=30) as r:
            return json.loads(r.read())
    except Exception as e:
        log(f"Kein bisheriges manifest.json ({e})")
        return {}


def reuse(name, out_mp3):
    try:
        urllib.request.urlretrieve(f"{BASE_URL}/audio/{name}", out_mp3)
        return True
    except Exception as e:
        log(f"  Alte MP3 nicht ladbar ({e})")
        return False


# ---------- Feed ----------

def shownotes(ep):
    items = []
    for s in ep["sources"]:
        if isinstance(s, dict):
            url = html.escape(str(s.get("url", "")), quote=True)
            title = html.escape(str(s.get("titel") or s.get("url", "")))
            items.append(f'<li><a href="{url}">{title}</a></li>')
        else:
            items.append(f"<li>{html.escape(str(s))}</li>")
    return "<p>Quellen:</p><ul>" + "".join(items) + "</ul>" if items else "<p>Keine Quellen angegeben.</p>"


def write_feed(episodes, manifest):
    now = email.utils.format_datetime(dt.datetime.now(dt.timezone.utc))
    items = []
    for ep in episodes:
        info = manifest[ep["slug"]]
        url = f"{BASE_URL}/audio/{info['file']}"
        pub = dt.datetime.combine(ep["date"], dt.time(5, 0), dt.timezone.utc)
        secs = int(info["duration"])
        notes = shownotes(ep).replace("]]>", "]]&gt;")
        items.append(f"""    <item>
      <title>{escape(ep['title'])}</title>
      <guid isPermaLink="false">{escape(url)}</guid>
      <pubDate>{email.utils.format_datetime(pub)}</pubDate>
      <enclosure url="{escape(url)}" length="{info['bytes']}" type="audio/mpeg"/>
      <itunes:duration>{secs // 60}:{secs % 60:02d}</itunes:duration>
      <description><![CDATA[{notes}]]></description>
    </item>""")

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{FEED_TITLE}</title>
    <link>{BASE_URL}/</link>
    <atom:link href="{BASE_URL}/feed.xml" rel="self" type="application/rss+xml"/>
    <description>{FEED_DESCRIPTION}</description>
    <language>de-de</language>
    <lastBuildDate>{now}</lastBuildDate>
    <itunes:author>{FEED_TITLE}</itunes:author>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="News"/>
{chr(10).join(items)}
  </channel>
</rss>
"""
    (SITE / "feed.xml").write_text(feed, encoding="utf-8")
    (SITE / "index.html").write_text(
        f'<!doctype html><meta charset="utf-8"><title>{FEED_TITLE}</title>'
        f'<p>Podcast-Feed: <a href="feed.xml">{BASE_URL}/feed.xml</a></p>\n',
        encoding="utf-8",
    )


def main():
    AUDIO.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(exist_ok=True)

    cutoff = dt.date.today() - dt.timedelta(days=KEEP_DAYS)
    episodes = []
    for path in sorted(EPISODES.glob("*.md")):
        ep = parse_episode(path)
        if ep["date"] < cutoff:
            log(f"{ep['slug']}: aelter als {KEEP_DAYS} Tage, entfaellt")
            continue
        episodes.append(ep)
    episodes.sort(key=lambda e: e["date"], reverse=True)

    previous = fetch_previous()
    manifest = {}
    failed = []
    for ep in episodes:
        log(f"{ep['slug']}: {ep['title']}")
        # Fingerabdruck im Dateinamen: geaenderte Folgen gelten fuer Podcast-Apps als neue Folge.
        name = f"{ep['slug']}-{ep['hash'][:8]}.mp3"
        out_mp3 = AUDIO / name
        old = previous.get(ep["slug"])
        if old and old.get("hash") == ep["hash"] and reuse(old.get("file", f"{ep['slug']}.mp3"), out_mp3):
            log("  unveraendert, bestehende MP3 uebernommen")
            engine = old.get("engine", "?")
        else:
            try:
                engine = build_mp3(ep, out_mp3)
            except Exception as e:
                log(f"  FEHLER: {e}")
                failed.append(ep["slug"])
                continue
        manifest[ep["slug"]] = {
            "hash": ep["hash"],
            "file": name,
            "engine": engine,
            "bytes": out_mp3.stat().st_size,
            "duration": duration(out_mp3),
        }
        log(f"  fertig: {engine}, {manifest[ep['slug']]['duration'] / 60:.1f} min, "
            f"{manifest[ep['slug']]['bytes'] / 1e6:.1f} MB")

    episodes = [e for e in episodes if e["slug"] in manifest]
    (SITE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_feed(episodes, manifest)
    log(f"Feed: {BASE_URL}/feed.xml ({len(episodes)} Folgen)")

    if failed:
        log(f"Nicht vertont: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
