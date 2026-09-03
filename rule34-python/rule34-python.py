#!/usr/bin/env python3
"""
rule34.xxx scraper for Stash — HTML mode — v2.2
- 1 seule requete HTTP par scene
- Resout les stored_id des studios/artistes via l'API Stash locale (nom + alias)
- Encode les stored_id dans la ligne "Artists: nom[id] | nom2[id2]" du champ details
- Les performers/tags sont matches nativement par Stash (pas besoin de les resoudre ici,
  sauf performers qu'on resout aussi car c'est peu couteux)
"""

import sys
import json
import time
import re
import os
import random
from html.parser import HTMLParser
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

VERSION = "2.3-persistent-ratelimit"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Config file ───────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(SCRIPT_DIR, "rule34-config.json")
_config = {}

CONFIG_DEFAULTS = {
    "min_interval": 1.5,
    "retries":      5,
    "api_key":      "",
    "user_id":      "",
    "stash_api_key": "",
}


def log(msg):
    print(msg, file=sys.stderr)


def config_load():
    global _config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            _config = {**CONFIG_DEFAULTS, **loaded}
        except Exception as e:
            log(f"Config load error (using defaults): {e}")
            _config = dict(CONFIG_DEFAULTS)
    else:
        _config = dict(CONFIG_DEFAULTS)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(CONFIG_DEFAULTS, f, indent=2)
        except Exception:
            pass


def cfg(key):
    return _config.get(key, CONFIG_DEFAULTS.get(key))


# ── Stash GraphQL local ───────────────────────────────────────────────────────

def stash_gql(query, variables=None):
    """Interroge l'API GraphQL Stash locale. Retourne data ou {} en cas d'erreur."""
    server = os.environ.get("STASH_SERVER_CONNECTION", "http://localhost:8888")
    server = server.rstrip("/")
    url = f"{server}/graphql"

    api_key = (
        os.environ.get("STASH_API_KEY", "") or
        cfg("stash_api_key") or
        ""
    )

    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }
    if api_key:
        headers["ApiKey"] = api_key

    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")

    try:
        req = Request(url, data=payload, headers=headers, method="POST")
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("data", {})
    except Exception as e:
        log(f"Stash GQL error: {e}")
        return {}


def resolve_studio_id(name):
    """Cherche un studio par nom exact, puis par alias. Retourne l'ID ou None."""
    if not name:
        return None
    q = """query($n: String!) {
        findStudios(studio_filter: {name: {value: $n, modifier: EQUALS}}, filter: {per_page: 1}) {
            studios { id }
        }
    }"""
    data = stash_gql(q, {"n": name})
    studios = (data.get("findStudios") or {}).get("studios", [])
    if studios:
        log(f"Studio '{name}' found by name: {studios[0]['id']}")
        return studios[0]["id"]

    q2 = """query($n: String!) {
        findStudios(studio_filter: {aliases: {value: $n, modifier: INCLUDES}}, filter: {per_page: 5}) {
            studios { id name aliases }
        }
    }"""
    data2 = stash_gql(q2, {"n": name})
    for s in (data2.get("findStudios") or {}).get("studios", []):
        for alias in (s.get("aliases") or []):
            if alias.lower() == name.lower():
                log(f"Studio '{name}' found by alias -> '{s['name']}': {s['id']}")
                return s["id"]

    log(f"Studio '{name}' not found in Stash")
    return None


def resolve_performer_id(name):
    """Cherche un performer par nom exact. Retourne l'ID ou None."""
    if not name:
        return None
    q = """query($n: String!) {
        findPerformers(performer_filter: {name: {value: $n, modifier: EQUALS}}, filter: {per_page: 1}) {
            performers { id }
        }
    }"""
    data = stash_gql(q, {"n": name})
    perfs = (data.get("findPerformers") or {}).get("performers", [])
    if perfs:
        return perfs[0]["id"]
    return None


def enrich_scene(scene):
    """
    - Resout les stored_id de TOUS les artistes de la ligne Artists: (nom + alias)
    - Encode les IDs dans details : "Artists: nom[id] | nom2 | nom3[id3]"
    - Injecte studio.stored_id pour le studio principal
    - Resout les performers (peu couteux)
    Les tags sont matches nativement par Stash, pas besoin ici.
    """
    if not scene:
        return scene

    details = scene.get("details", "") or ""

    # Collecter tous les artistes depuis la ligne Artists:
    all_artist_names = []
    for line in details.split("\n"):
        m = re.match(r"^Artists:\s*(.+)$", line)
        if m:
            all_artist_names = [a.strip() for a in m.group(1).split("|") if a.strip()]
            break

    if not all_artist_names and scene.get("studio") and scene["studio"].get("name"):
        all_artist_names = [scene["studio"]["name"]]

    # Resoudre chaque artiste
    artist_ids = {}
    for name in all_artist_names:
        sid = resolve_studio_id(name)
        if sid:
            artist_ids[name] = sid

    # Injecter dans le studio principal
    if scene.get("studio") and scene["studio"].get("name"):
        sname = scene["studio"]["name"]
        if sname in artist_ids:
            scene["studio"]["stored_id"] = artist_ids[sname]

    # Reconstruire la ligne Artists: avec les IDs encodes : nom[id]
    if all_artist_names:
        artist_parts = []
        for name in all_artist_names:
            sid = artist_ids.get(name)
            artist_parts.append(f"{name}[{sid}]" if sid else name)
        artist_line = "Artists: " + " | ".join(artist_parts)

        lines = details.split("\n") if details else []
        new_lines = []
        replaced = False
        for line in lines:
            if re.match(r"^Artists:", line):
                new_lines.append(artist_line)
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.insert(0, artist_line)
        scene["details"] = "\n".join(new_lines)

    # Performers (nom exact)
    for p in scene.get("performers") or []:
        if p.get("name") and not p.get("stored_id"):
            pid = resolve_performer_id(p["name"])
            if pid:
                p["stored_id"] = pid

    # PREUVE dans le log : la ligne details qui part vers Stash
    log(f"details out: {scene.get('details', '')[:160]}")

    return scene


# ── Rate limiting ─────────────────────────────────────────────────────────────
# Fichier timestamp partage entre tous les sous-processus (fix inter-process)
TIMESTAMP_FILE = os.path.join(SCRIPT_DIR, ".last_request_time")


def _read_last_time():
    try:
        if os.path.exists(TIMESTAMP_FILE):
            with open(TIMESTAMP_FILE, "r") as f:
                return float(f.read().strip())
    except Exception:
        pass
    return 0.0


def _write_last_time(t):
    try:
        with open(TIMESTAMP_FILE, "w") as f:
            f.write(str(t))
    except Exception:
        pass


def rate_limited_get(url, retries=None):
    if retries is None:
        retries = cfg("retries")
    min_interval = cfg("min_interval")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    for attempt in range(retries):
        elapsed = time.time() - _read_last_time()
        # Jitter aleatoire : min_interval + 0 a 1.5s supplementaire
        jitter = random.uniform(0, 3.0)
        interval = min_interval + jitter
        if elapsed < interval:
            wait = interval - elapsed
            log(f"Rate limit: attente {wait:.2f}s (inter-process, jitter={jitter:.2f}s)")
            time.sleep(wait)

        try:
            req = Request(url, headers=headers)
            _write_last_time(time.time())
            with urlopen(req, timeout=20) as resp:
                return resp.read()

        except HTTPError as e:
            if e.code == 429:
                # Pause aleatoire entre 60 et 90s pour eviter pattern regulier
                wait = random.uniform(60, 90)
                log(f"Rate limited (429). Pause {wait:.0f}s — retry {attempt+1}/{retries}...")
                time.sleep(wait)
            elif e.code in (502, 503):
                wait = (2 ** attempt) * 3
                log(f"Server error {e.code}. Waiting {wait}s — retry {attempt+1}/{retries}...")
                time.sleep(wait)
            else:
                log(f"HTTP error {e.code} for {url}")
                return None
        except URLError as e:
            log(f"URL error: {e.reason}")
            return None

    log(f"All {retries} retries exhausted for {url}")
    return None


# ── HTML parser ───────────────────────────────────────────────────────────────
class Rule34HTMLParser(HTMLParser):
    TAG_TYPES = (
        "tag-type-general",
        "tag-type-character",
        "tag-type-artist",
        "tag-type-copyright",
        "tag-type-metadata",
    )

    def __init__(self):
        super().__init__()
        self.tags = []
        self.date_raw = ""
        self.source_urls = []
        self.notes = []

        self._in_sidebar = False
        self._sidebar_depth = 0
        self._current_li_class = ""
        self._current_li_text = ""
        self._capture_li = False
        self._in_a = False
        self._current_a_text = ""
        self._current_a_href = ""
        self._in_note = False
        self._current_note = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "ul" and attrs.get("id") == "tag-sidebar":
            self._in_sidebar = True
            self._sidebar_depth = 1
            return

        if self._in_sidebar:
            if tag == "ul":
                self._sidebar_depth += 1
            elif tag == "li":
                self._current_li_class = attrs.get("class", "")
                self._current_li_text = ""
                self._capture_li = True
            elif tag == "a" and self._capture_li:
                self._in_a = True
                self._current_a_text = ""
                self._current_a_href = attrs.get("href", "")

        if tag == "div" and "note-body" in attrs.get("class", ""):
            self._in_note = True
            self._current_note = ""

    def handle_endtag(self, tag):
        if self._in_sidebar:
            if tag == "ul":
                self._sidebar_depth -= 1
                if self._sidebar_depth <= 0:
                    self._in_sidebar = False
                    self._sidebar_depth = 0

            elif tag == "a" and self._in_a:
                a_text = self._current_a_text.strip()
                a_href = self._current_a_href
                cls = self._current_li_class

                if "Source:" in self._current_li_text and a_href.startswith("http"):
                    self.source_urls.append(a_href)

                if a_text and a_text != "?" and not a_text.startswith("Source"):
                    for tt in self.TAG_TYPES:
                        if tt in cls:
                            self.tags.append((a_text, tt))
                            break

                self._in_a = False
                self._current_a_text = ""
                self._current_a_href = ""

            elif tag == "li" and self._capture_li:
                li_text = self._current_li_text.strip()
                if "Posted:" in li_text:
                    self.date_raw = li_text
                self._capture_li = False
                self._current_li_text = ""

        if tag == "div" and self._in_note:
            if self._current_note.strip():
                self.notes.append(self._current_note.strip())
            self._in_note = False

    def handle_data(self, data):
        if self._in_sidebar:
            if self._capture_li:
                self._current_li_text += data
            if self._in_a:
                self._current_a_text += data
        if self._in_note:
            self._current_note += data


# ── Construire la scene Stash depuis le HTML parsé ────────────────────────────
def parse_post_html(html_bytes, post_url):
    try:
        html = html_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        log(f"HTML decode error: {e}")
        return {}

    parser = Rule34HTMLParser()
    parser.feed(html)

    performers = []
    studio = None
    all_artists = []
    general_tags = []
    seen = set()

    for tag_text, tag_class in parser.tags:
        if tag_text in seen:
            continue
        seen.add(tag_text)

        if "tag-type-character" in tag_class:
            performers.append({"name": tag_text})
        elif "tag-type-artist" in tag_class:
            all_artists.append(tag_text)
            if studio is None:
                studio = {"name": tag_text}
        elif "tag-type-general" in tag_class or "tag-type-metadata" in tag_class:
            general_tags.append({"name": tag_text})

    log(f"Parsed: {len(performers)} performers, "
        f"{len(all_artists)} artist(s)={all_artists}, "
        f"{len(general_tags)} tags, "
        f"date='{parser.date_raw[:50]}')")

    date_str = None
    if parser.date_raw:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", parser.date_raw)
        if m:
            date_str = m.group(1)
        else:
            m2 = re.search(r"(\w{3})\s+(\d{1,2})\s+(\d{4})", parser.date_raw)
            if m2:
                months = {
                    "Jan":"01","Feb":"02","Mar":"03","Apr":"04",
                    "May":"05","Jun":"06","Jul":"07","Aug":"08",
                    "Sep":"09","Oct":"10","Nov":"11","Dec":"12"
                }
                mo = months.get(m2.group(1), "01")
                day = m2.group(2).zfill(2)
                year = m2.group(3)
                date_str = f"{year}-{mo}-{day}"

    if not performers and not general_tags and not studio and not date_str:
        log("No data extracted from HTML — post may be deleted or page returned an error")
        return None

    urls = [post_url]
    for src in parser.source_urls:
        if src not in urls:
            urls.append(src)

    scene = {}
    if date_str:
        scene["date"] = date_str
    if performers:
        scene["performers"] = performers
    if studio:
        scene["studio"] = studio
    if general_tags:
        scene["tags"] = general_tags
    if urls:
        scene["urls"] = urls

    # Toujours ecrire la ligne Artists: (meme pour 1 seul artiste)
    details_parts = []
    if all_artists:
        details_parts.append("Artists: " + " | ".join(all_artists))
    if parser.notes:
        details_parts.extend(parser.notes)
    if details_parts:
        scene["details"] = "\n".join(details_parts)

    return scene


# ── Helpers ───────────────────────────────────────────────────────────────────
def read_input():
    return json.loads(sys.stdin.read())


def output(data):
    print(json.dumps(data))


def filename_to_url(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    base = re.sub(r"[^a-zA-Z0-9\-_]", "", base)
    m = re.match(r"^rule34_(\d+)", base)
    if m:
        post_id = m.group(1)
        return f"https://rule34.xxx/index.php?page=post&s=view&id={post_id}", post_id
    return None, None


def url_to_post_id(url):
    m = re.search(r"[?&]id=(\d+)", url)
    return m.group(1) if m else None


# ── Entry points ──────────────────────────────────────────────────────────────
def scene_by_fragment(inp):
    candidates = [
        inp.get("title", ""),
        inp.get("path", ""),
        inp.get("name", ""),
        *[f.get("path", "") for f in (inp.get("files") or []) if isinstance(f, dict)],
    ]

    post_url, post_id = None, None
    for candidate in candidates:
        if candidate:
            post_url, post_id = filename_to_url(candidate)
            if post_id:
                log(f"Matched post ID {post_id} from: {candidate[:80]}")
                break

    if not post_id:
        log(f"Could not extract rule34 post ID. Candidates: {candidates}")
        output(None)
        return

    log(f"Fetching HTML: {post_url}")
    html = rate_limited_get(post_url)
    if not html:
        log("No HTML returned")
        output(None)
        return

    scene = parse_post_html(html, post_url)
    if scene:
        scene = enrich_scene(scene)
    output(scene)


def scene_by_url(inp):
    url = inp.get("url", "")
    log(f"sceneByURL: {url}")
    post_id = url_to_post_id(url)
    if not post_id:
        log("Could not extract post ID from URL")
        output(None)
        return

    log(f"Fetching HTML: {url}")
    html = rate_limited_get(url)
    if not html:
        log("No HTML returned")
        output(None)
        return

    scene = parse_post_html(html, url)
    if scene:
        scene = enrich_scene(scene)
    output(scene)


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    config_load()
    log(f"rule34-python v{VERSION}")
    inp = read_input()

    action = sys.argv[1] if len(sys.argv) > 1 else inp.get("action", "")
    log(f"Action: {action}")

    if action in ("sceneByFragment", "imageByFragment"):
        scene_by_fragment(inp)
    elif action in ("sceneByURL", "imageByURL"):
        scene_by_url(inp)
    else:
        log(f"Unknown action: {action}")
        output(None)
