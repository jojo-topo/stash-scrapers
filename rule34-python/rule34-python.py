#!/usr/bin/env python3
"""
rule34.xxx scraper for Stash - official API mode

Setup: put your rule34.xxx api_key and user_id in rule34-config.json (created next to
this script on first run). Optional: run `python import_tags.py` once to download the
tag database (about 30-40 minutes) so scrapes don't have to look tags up one by one.

- Resolves studio/artist ids through the local Stash API (name + alias)
- Encodes the ids in the "Artists: name[id] | name2[id2]" line of the details field
- Performers/tags are matched natively by Stash (performers are also resolved here)
- File names recognised: <id>, <id>_2, rule34_<id>, rule34.xxx-<id>, "<name> - r34xxx <id>"
"""

import sys
import json
import time
import re
import os
import random
import threading
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
    "tag_interval": 1.0,       # delai entre 2 lookups de type de tag (API)
    "jitter_max":   0.5,       # alea ajoute a min_interval entre 2 posts (0 a jitter_max s)
    "use_change_date": True,   # repli si created_at est absent : date de derniere modif du post
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


def rate_limited_get(url, retries=None, min_interval=None, jitter_max=None):
    if retries is None:
        retries = cfg("retries")
    if min_interval is None:
        min_interval = cfg("min_interval")
    if jitter_max is None:
        jitter_max = cfg("jitter_max")

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
        jitter = random.uniform(0, jitter_max)
        interval = min_interval + jitter
        if elapsed < interval:
            wait = interval - elapsed
            log(f"Rate limit: waiting {wait:.2f}s (inter-process, jitter={jitter:.2f}s)")
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
                # Ne jamais logguer l'URL complete : elle contient api_key / user_id
                log(f"HTTP error {e.code} for {url.split('&api_key=')[0]}"
                    + (" (API key refused? check api_key / user_id in rule34-config.json)" if e.code in (401, 403) else ""))
                return None
        except URLError as e:
            log(f"URL error: {e.reason}")
            return None
        except OSError as e:  # timeout, connection dropped while reading
            log(f"Network error: {e}")
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
        self.image_url = ""

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

        # Post principal (image ou poster video) : <img id="image" src="...">
        if tag == "img" and attrs.get("id") == "image" and not self.image_url:
            self.image_url = attrs.get("src", "")

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
def parse_post_html(html_bytes, post_url, is_image=False):
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

    # Vignette : priorite au thumbnail statique derive du bloc JS
    # `image = {'domain':..., 'dir':N, 'img':'hash.ext', ...}` toujours
    # present dans le HTML (images ET videos) - contrairement a
    # <img id="image"> qui n'existe QUE pour les posts image (les posts
    # video chargent leur lecteur via fluidplayer.js cote client, donc
    # invisible pour un fetch HTTP statique). Fallback sur <img id="image">
    # si jamais ce bloc JS venait a disparaitre/changer de forme.
    m_dir = re.search(r"'dir'\s*:\s*(\d+)", html)
    m_img = re.search(r"'img'\s*:\s*'([^']+)'", html)
    m_domain = re.search(r"'domain'\s*:\s*'([^']+)'", html)
    if m_dir and m_img:
        domain = (m_domain.group(1) if m_domain else "https://wimg.rule34.xxx/").rstrip("/")
        img_hash = m_img.group(1).rsplit(".", 1)[0]
        scene["image"] = f"{domain}/thumbnails/{m_dir.group(1)}/thumbnail_{img_hash}.jpg"
    elif parser.image_url:
        scene["image"] = parser.image_url

    # ScrapedImage (contrairement a ScrapedScene) n'a pas de champ "image" cote
    # GraphQL Stash -> scene["image"] serait silencieusement jete pour un scrape
    # d'image. Seul repli possible : glisser la thumbnail dans "urls" (existe sur
    # ScrapedImage), UNIQUEMENT pour les scrapes d'image (is_image) afin de ne pas
    # polluer aussi les scrapes de scene (meme fonction partagee par les deux, et
    # ScrapedScene a deja son propre champ "image" qui fonctionne). Suffixe
    # "#it-scraped-thumb" en fragment (inerte pour un <img src>, jamais envoye au
    # serveur) : marqueur fiable quelle que soit la forme de l'URL (thumbnail CDN
    # ou fallback <img id="image">), contrairement a un pattern sur le chemin qui
    # ne couvrirait que l'un des deux cas. imageTagger.js (extractScrapedThumb /
    # isInjectedThumbUrl) s'appuie dessus pour NE JAMAIS sauvegarder cette entree
    # comme vraie URL source sur Apply.
    if is_image and scene.get("image"):
        tagged = scene["image"] + "#it-scraped-thumb"
        if tagged not in scene.get("urls", []):
            scene.setdefault("urls", []).append(tagged)

    # Toujours ecrire la ligne Artists: (meme pour 1 seul artiste)
    details_parts = []
    if all_artists:
        details_parts.append("Artists: " + " | ".join(all_artists))
    if parser.notes:
        details_parts.extend(parser.notes)
    if details_parts:
        scene["details"] = "\n".join(details_parts)

    return scene


# ── Mode API (rule34.xxx a mis un CAPTCHA devant le HTML, 2026-10) ────────────
# Le parseur HTML ci-dessus n'est plus utilise (la page renvoie un 403 CAPTCHA
# aux scripts) ; tout passe maintenant par l'API officielle avec api_key/user_id
# de rule34-config.json.
API_BASE = "https://api.rule34.xxx/index.php?page=dapi&q=index"
TAG_CACHE_FILE = os.path.join(SCRIPT_DIR, "rule34-tag-cache.json")
# Types de tag rule34 : 0 general, 1 artist, 3 copyright, 4 character, 5 metadata
_tag_cache = None


OPTIONS_URL = "https://rule34.xxx/index.php?page=account&s=options"


def api_auth_error():
    """Why the credentials can't be used, as a message for the log (None if they look usable)."""
    key = str(cfg("api_key") or "").strip()
    uid = str(cfg("user_id") or "").strip()
    where = f"Edit {CONFIG_FILE} and retry."
    if any(c in v for v in (key, uid) for c in "&="):
        return ("api_key / user_id must contain only the values, not the text copied from the options "
                "page. From '&api_key=KEY&user_id=ID', put KEY in api_key and ID in user_id. " + where)
    if uid and not key:
        return ("api_key is empty: your rule34.xxx account has no API key yet. On " + OPTIONS_URL +
                " tick 'Generate New Key?', save, then copy the long key into api_key. " + where)
    if key and not uid:
        return "user_id is empty: copy the number after 'user_id=' on " + OPTIONS_URL + ". " + where
    if not key and not uid:
        return ("api_key / user_id are missing. rule34.xxx requires an API key: get yours at " +
                OPTIONS_URL + ", then fill both fields. " + where)
    return None


def api_auth():
    if api_auth_error():
        return None
    return f"&api_key={str(cfg('api_key')).strip()}&user_id={str(cfg('user_id')).strip()}"


TAG_DB_FILE = os.path.join(SCRIPT_DIR, "rule34-tags.db")


class TagDB:
    """Types de tags dans une base sqlite (remplie par import_tags.py, completee
    par les lookups API). Se comporte comme un dict : `name in db`, db[name],
    db[name] = type."""

    def __init__(self, path):
        import sqlite3
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS tags(name TEXT PRIMARY KEY, type INTEGER NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        # Migration unique de l'ancien cache JSON
        if os.path.exists(TAG_CACHE_FILE):
            try:
                with open(TAG_CACHE_FILE, "r", encoding="utf-8") as f:
                    old = json.load(f)
                self._db.executemany("INSERT OR IGNORE INTO tags VALUES (?, ?)", list(old.items()))
                self._db.commit()
                os.replace(TAG_CACHE_FILE, TAG_CACHE_FILE + ".migrated")
            except Exception as e:
                log(f"Could not migrate the old JSON tag cache: {e}")

    def __contains__(self, name):
        return self._get(name) is not None

    def __getitem__(self, name):
        v = self._get(name)
        if v is None:
            raise KeyError(name)
        return v

    def __setitem__(self, name, ttype):
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO tags VALUES (?, ?)", (name, int(ttype)))
            self._db.commit()

    def _get(self, name):
        with self._lock:
            row = self._db.execute("SELECT type FROM tags WHERE name=?", (name,)).fetchone()
        return row[0] if row else None


def tag_cache_load():
    global _tag_cache
    if _tag_cache is None:
        try:
            _tag_cache = TagDB(TAG_DB_FILE)
        except Exception as e:
            # database locked / read-only folder: in-memory cache, the scrape goes on
            log(f"Tag database unavailable, using an in-memory cache: {e}")
            _tag_cache = {}
    return _tag_cache


def tag_cache_save():
    pass  # chaque ecriture est deja commitee (sqlite)


# L'API rule34 coupe (429) vers 60 requetes / minute (mesure 2026-10-05). Budget
# glissant partage entre tous les processus via un petit fichier d'horodatages :
# rapide tant qu'il reste du budget, attend seulement quand il est epuise.
API_BUDGET = 50
API_WINDOW = 60.0
API_TIMES_FILE = os.path.join(SCRIPT_DIR, ".api_request_times")
_api_lock = threading.Lock()


def _api_times_read(now):
    try:
        with open(API_TIMES_FILE, "r") as f:
            return [t for t in (float(x) for x in f.read().split()) if now - t < API_WINDOW]
    except Exception:
        return []


def api_throttle():
    with _api_lock:
        while True:
            now = time.time()
            times = _api_times_read(now)
            if len(times) < API_BUDGET:
                break
            wait = API_WINDOW - (now - min(times)) + 0.2
            log(f"API budget reached ({len(times)}/{API_BUDGET} per minute): waiting {wait:.0f}s")
            time.sleep(max(0.2, wait))
        times.append(now)
        try:
            with open(API_TIMES_FILE, "w") as f:
                f.write(" ".join(f"{t:.2f}" for t in times))
        except Exception:
            pass


def _fast_get(url, retries=3):
    """GET API limite par le budget glissant (lookups de tags / notes)."""
    for attempt in range(retries):
        api_throttle()
        try:
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as resp:
                return resp.read()
        except HTTPError as e:
            if e.code == 429:
                wait = 20 * (attempt + 1)
                log(f"429 from the API, pausing {wait}s")
                time.sleep(wait)
                continue
            return None
        except URLError:
            return None
        except OSError as e:  # timeout, connection dropped while reading
            log(f"Network error: {e}")
            return None
    return None


def prefetch_tag_types(names, auth):
    """Resout en parallele le type des tags absents du cache (6 threads)."""
    from concurrent.futures import ThreadPoolExecutor
    cache = tag_cache_load()
    todo = [n for n in names if n not in cache]
    if not todo:
        return
    log(f"Looking up {len(todo)} unknown tag(s) in parallel "
        f"(run import_tags.py once to download them all and speed this up)")
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(lambda n: get_tag_type(n, auth), todo))


def get_tag_type(name, auth):
    """Type du tag (cache d'abord, sinon 1 appel API). None si introuvable."""
    cache = tag_cache_load()
    if name in cache:
        return cache[name]
    from urllib.parse import quote
    raw = _fast_get(f"{API_BASE}&s=tag&json=1&name={quote(name, safe='')}{auth}")
    if not raw:
        return None
    # L'API repond en XML (meme avec json=1) : ET decode les entites (&amp; etc.),
    # sinon les tags avec & < > = ne matchent jamais et sont redemandes a chaque fois.
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None
    for t in root.iter("tag"):
        if t.get("name") == name:
            cache[name] = int(t.get("type", 0))
            return cache[name]
    # Reponse valide mais tag inconnu de l'API : on le memorise comme tag normal
    # pour ne pas le redemander a chaque scrape.
    cache[name] = 0
    return 0


def fetch_notes_api(post_id, auth):
    """Notes de traduction du post (XML), meme contenu que l'ancien parse HTML."""
    import xml.etree.ElementTree as ET
    from html import unescape
    raw = _fast_get(f"{API_BASE}&s=note&post_id={post_id}{auth}")
    if not raw:
        return []
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except Exception as e:
        log(f"Could not read the notes XML: {e}")
        return []
    notes = []
    for n in sorted(root.iter("note"), key=lambda x: int(x.get("id", "0"))):
        if n.get("is_active") == "false":
            continue
        body = unescape(re.sub(r"<[^>]+>", "", unescape(n.get("body", "")))).strip()
        if body:
            notes.append(body)
    return notes


def fetch_post_api(post_id):
    auth = api_auth()
    if not auth:
        log(api_auth_error())
        return None
    # XML (et pas json=1) : seule la reponse XML contient created_at (vraie date de post)
    raw = rate_limited_get(f"{API_BASE}&s=post&id={post_id}{auth}")
    if not raw:
        return None
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    except Exception:
        log(f"Unreadable API response: {raw[:200]!r}")
        return None
    node = root.find("post")
    if node is None:
        log(f"Post {post_id} not found (deleted?)")
        return None
    return dict(node.attrib)


MONTHS = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
          "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}


def post_date(post):
    """Date de publication (created_at, ex: 'Tue Aug 17 16:43:49 +0200 2021')."""
    m = re.search(r"\w{3}\s+(\w{3})\s+(\d{1,2})\s+[\d:]+\s+[+-]\d{4}\s+(\d{4})", post.get("created_at") or "")
    if m and m.group(1) in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1)]}-{m.group(2).zfill(2)}"
    # Repli : date de derniere modification
    if cfg("use_change_date") and post.get("change"):
        try:
            return time.strftime("%Y-%m-%d", time.gmtime(int(post["change"])))
        except Exception:
            pass
    return None


def parse_post_api(post, post_url, is_image=False):
    auth = api_auth()
    performers, all_artists, general_tags = [], [], []
    studio = None
    post_tags = (post.get("tags") or "").split()
    prefetch_tag_types(post_tags, auth)
    seen = set()  # comme l'ancien parseur HTML : un meme nom affiche une seule fois
    for raw_tag in post_tags:
        ttype = get_tag_type(raw_tag, auth)
        display = raw_tag.replace("_", " ")
        if display in seen:
            continue
        seen.add(display)
        if ttype == 4:
            performers.append({"name": display})
        elif ttype == 1:
            all_artists.append(display)
            if studio is None:
                studio = {"name": display}
        elif ttype in (None, 0, 5):
            general_tags.append({"name": display})
        # 3 = copyright : ignore (comme avant)
    tag_cache_save()

    log(f"Parsed (API): {len(performers)} performers, "
        f"{len(all_artists)} artist(s)={all_artists}, {len(general_tags)} tags")

    date_str = post_date(post)

    urls = [post_url]
    src = (post.get("source") or "").strip()
    for s in re.split(r"\s+", src):
        if s.startswith("http") and s not in urls:
            urls.append(s)

    scene = {}
    if date_str:
        scene["date"] = date_str
    if performers:
        scene["performers"] = performers
    if studio:
        scene["studio"] = studio
    if general_tags:
        scene["tags"] = general_tags
    scene["urls"] = urls
    if post.get("preview_url"):
        scene["image"] = post["preview_url"]

    # Meme repli que le mode HTML pour les scrapes d'image (voir parse_post_html)
    if is_image and scene.get("image"):
        tagged = scene["image"] + "#it-scraped-thumb"
        if tagged not in scene["urls"]:
            scene["urls"].append(tagged)

    details_parts = []
    if all_artists:
        details_parts.append("Artists: " + " | ".join(all_artists))
    if post.get("has_notes") == "true":
        details_parts.extend(fetch_notes_api(post.get("id"), auth))
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
    raw = base
    base = re.sub(r"[^a-zA-Z0-9\-_]", "", base)
    # CUSTOM (Sina) : accepte aussi un nom purement numerique ("1234567",
    # "1234567_2") en plus de "rule34_<id>"
    m = re.match(r"^(?:rule34_)?(\d+)(?:_\d+)?$", base) or re.match(r"^rule34_(\d+)", base)
    # CUSTOM (Sina) : accepte en plus le nom du bouton Telecharger de creatorAlerts,
    # "<studio> - r34xxx <id>" (teste sur le nom brut, avant retrait des espaces)
    if not m:
        m = re.search(r"r34xxx\s*(\d+)$", raw)
    # CUSTOM (Sina) : format "rule34.xxx-<id>" (ou _ / espace) - utilisateur du forum
    if not m:
        m = re.search(r"rule34\.xxx[-_ ]*(\d+)", raw, re.IGNORECASE)
    if m:
        post_id = m.group(1)
        return f"https://rule34.xxx/index.php?page=post&s=view&id={post_id}", post_id
    return None, None


def url_to_post_id(url):
    m = re.search(r"[?&]id=(\d+)", url)
    return m.group(1) if m else None


# ── Entry points ──────────────────────────────────────────────────────────────
def scene_by_fragment(inp, is_image=False):
    candidates = [
        inp.get("title", ""),
        inp.get("path", ""),
        inp.get("name", ""),
        *[f.get("path", "") for f in (inp.get("files") or []) if isinstance(f, dict)],
    ]

    post_url, post_id = None, None
    # CUSTOM (Sina) : sceneTagger envoie l'URL collee dans scene_input.urls
    # (le scraper est appele explicitement, au lieu de laisser Stash choisir)
    for u in [*(inp.get("urls") or []), inp.get("url", "")]:
        if isinstance(u, str) and "rule34.xxx" in u:
            pid = url_to_post_id(u)
            if pid:
                post_url, post_id = u, pid
                log(f"Matched post ID {post_id} from URL: {u[:80]}")
                break
    for candidate in ([] if post_id else candidates):
        if candidate:
            post_url, post_id = filename_to_url(candidate)
            if post_id:
                log(f"Matched post ID {post_id} from: {candidate[:80]}")
                break

    if not post_id:
        log(f"Could not extract rule34 post ID. Candidates: {candidates}")
        output(None)
        return

    log(f"Fetching API: post {post_id}")
    post = fetch_post_api(post_id)
    if not post:
        log("No post returned")
        output(None)
        return

    scene = parse_post_api(post, post_url, is_image)
    if scene:
        scene = enrich_scene(scene)
    output(scene)


def scene_by_url(inp, is_image=False):
    url = inp.get("url", "")
    log(f"sceneByURL: {url}")
    post_id = url_to_post_id(url)
    if not post_id:
        log("Could not extract post ID from URL")
        output(None)
        return

    log(f"Fetching API: post {post_id}")
    post = fetch_post_api(post_id)
    if not post:
        log("No post returned")
        output(None)
        return

    scene = parse_post_api(post, url, is_image)
    if scene:
        scene = enrich_scene(scene)
    output(scene)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    config_load()
    log(f"rule34-python v{VERSION}")
    inp = read_input()

    action = sys.argv[1] if len(sys.argv) > 1 else inp.get("action", "")
    log(f"Action: {action}")

    is_image = action.startswith("image")
    if action in ("sceneByFragment", "imageByFragment"):
        scene_by_fragment(inp, is_image)
    elif action in ("sceneByURL", "imageByURL"):
        scene_by_url(inp, is_image)
    else:
        log(f"Unknown action: {action}")
        output(None)


if __name__ == "__main__":
    # CUSTOM (Sina) : un crash ne doit jamais laisser stdout vide (Stash : "could not
    # unmarshal json from script output: EOF"). Traceback dans les logs, "null" en sortie.
    try:
        main()
    except Exception:
        import traceback
        log(traceback.format_exc())
        output(None)
