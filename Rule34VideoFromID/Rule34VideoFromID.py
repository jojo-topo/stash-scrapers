"""
Rule34VideoFromID - Stash scraper
==================================
Purpose: enable "Scrape by Fragment" on rule34video even when the filename
only contains the ID (e.g. 4405657_Midna...mp4 or a title with [4405657]).

rule34video runs on the KVS engine. A /video/ID URL WITHOUT a slug returns
404, but /video/ID/<anything>/ works because KVS only reads the ID. This
script resolves the ID into a full URL, then scrapes the page.

SETUP REQUIRED: this site is protected by DDoS-Guard, which requires a set
of session cookies to let requests through. Fill in the COOKIES dict below
with your own values before using this scraper:
1. Open rule34video.com in your browser (logged in or not, either works).
2. Open DevTools (F12) -> Storage/Application -> Cookies -> rule34video.com.
3. Copy the values for __ddg1_, __ddg8_, __ddg9_, __ddg10_, PHPSESSID,
   kt_ips, kt_tcookie into COOKIES below.
These cookies expire periodically - if scraping starts failing with a 403 or
a challenge page, repeat the steps above to refresh them.

DEPENDENCIES: pip install requests lxml
(lxml is optional but recommended - without it the scraper falls back to a
much more limited regex parser that only extracts title/date/image.)
"""

import sys
import json
import re

import requests

# ---------------------------------------------------------------------------
# DDoS-Guard + session cookies - fill in your own values, see the module
# docstring above for how to get them. Left empty on purpose: these are
# personal to your browser session and must never be committed/shared.
# ---------------------------------------------------------------------------
COOKIES = {
    "__ddg1_": "",
    "__ddg8_": "",
    "__ddg9_": "",
    "__ddg10_": "",
    "PHPSESSID": "",
    "kt_ips": "",
    "kt_tcookie": "1",
    # "__ddg2_": "...",   # add if present in your browser
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
        "Gecko/20100101 Firefox/128.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr,fr-FR;q=0.8,en-US;q=0.5,en;q=0.3",
}

BASE = "https://rule34video.com"


# ---------------------------------------------------------------------------
# Logging Stash (scraper) : tout va sur STDERR avec le protocole \x01<lvl>\x02
# STDOUT est reserve au JSON final.
# ---------------------------------------------------------------------------
def log(level, msg):
    print("\x01" + level + "\x02" + str(msg).replace("\n", "\\n"),
          file=sys.stderr, flush=True)


def log_debug(m): log("d", m)
def log_info(m):  log("i", m)
def log_warn(m):  log("w", m)
def log_error(m): log("e", m)


def read_stdin_json():
    try:
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return {}
        return json.loads(raw)
    except Exception as e:
        log_error("stdin JSON invalide: " + str(e))
        return {}


def output(obj):
    print(json.dumps(obj), flush=True)


# ---------------------------------------------------------------------------
# Session HTTP
# ---------------------------------------------------------------------------
def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    for k, v in COOKIES.items():
        s.cookies.set(k, v, domain=".rule34video.com")
    return s


def fetch(session, url):
    try:
        r = session.get(url, timeout=30, allow_redirects=True)
        return r
    except Exception as e:
        log_error("Erreur reseau sur " + url + " : " + str(e))
        return None


def is_video_page(resp):
    """Vrai si la reponse est bien une page video (pas un 404 KVS)."""
    if resp is None or resp.status_code != 200:
        return False
    txt = resp.text
    if "404 / Page not found" in txt:
        return False
    # une page video KVS contient un VideoObject JSON-LD ou un canonical /video/
    if '"@type": "VideoObject"' in txt or '"@type":"VideoObject"' in txt:
        return True
    if 'rel="canonical"' in txt and "/video/" in txt:
        return True
    return False


# ---------------------------------------------------------------------------
# Extraction de l'ID depuis le fragment envoye par Stash
# ---------------------------------------------------------------------------
def extract_id(fragment):
    # 1) depuis une URL deja presente
    urls = []
    if isinstance(fragment.get("url"), str):
        urls.append(fragment["url"])
    if isinstance(fragment.get("urls"), list):
        urls.extend([u for u in fragment["urls"] if isinstance(u, str)])
    for u in urls:
        m = re.search(r"/video/(\d+)", u)
        if m:
            return m.group(1)

    # 2) depuis le nom de fichier
    files = fragment.get("files") or []
    for f in files:
        path = f.get("path") if isinstance(f, dict) else (f if isinstance(f, str) else "")
        if not path:
            continue
        base = re.split(r"[\\\/]", path)[-1]
        # format Parabolic : ... [4405657].mp4
        m = re.search(r"\[(\d{4,})\]", base)
        if m:
            return m.group(1)
        # format ID en tete : 4405657_...
        m = re.match(r"(\d+)_", base)
        if m:
            return m.group(1)
        # dernier recours : la plus longue suite de chiffres
        m = re.findall(r"\d{4,}", base)
        if m:
            return max(m, key=len)

    # 3) depuis le titre
    title = fragment.get("title") or ""
    m = re.search(r"\[(\d{4,})\]", title)
    if m:
        return m.group(1)
    m = re.match(r"(\d+)_", title)
    if m:
        return m.group(1)
    m = re.findall(r"\d{4,}", title)
    if m:
        return max(m, key=len)

    return None


def resolve_url(session, video_id):
    """Resout un ID en URL video valide en testant plusieurs formes KVS."""
    candidates = [
        BASE + "/video/" + video_id + "/",
        BASE + "/video/" + video_id + "/x/",
        BASE + "/videos/" + video_id + "/",
    ]
    for url in candidates:
        log_debug("Essai URL: " + url)
        r = fetch(session, url)
        if is_video_page(r):
            log_debug("URL valide: " + r.url)
            return r
    log_warn("Aucune URL directe valide pour l'ID " + video_id)
    return None


# ---------------------------------------------------------------------------
# Parsing de la page (lxml si dispo, sinon regex de secours)
# ---------------------------------------------------------------------------
def _jsonld_field(html, field):
    """Extrait un champ du bloc JSON-LD VideoObject par regex (robuste)."""
    # thumbnailUrl peut etre une chaine OU un tableau
    m = re.search(r'"' + field + r'"\s*:\s*"([^"]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'"' + field + r'"\s*:\s*\[\s*"([^"]+)"', html)
    if m:
        return m.group(1)
    return None


def parse_with_lxml(html, final_url):
    from lxml import html as lh
    doc = lh.fromstring(html)

    def first(xpath):
        res = doc.xpath(xpath)
        if not res:
            return None
        val = res[0]
        if hasattr(val, "text_content"):
            val = val.text_content()
        return str(val).strip()

    scene = {}

    # URL canonique
    canon = first('//link[@rel="canonical"]/@href')
    scene["url"] = canon or final_url

    # Titre
    title = first('//div[@class="heading"]//h1')
    if title:
        scene["title"] = title

    # Details = 1er label de tab_video_info
    details = doc.xpath(
        '(//div[@id="tab_video_info"]/div[@class="row"]/div[@class="label"])[1]//text()'
    )
    details = [d.strip() for d in details if d.strip()]
    if details:
        scene["details"] = "\n".join(details)

    # Date + image via JSON-LD (plus fiable que le DOM)
    date = _jsonld_field(html, "uploadDate")
    if date:
        scene["date"] = date[:10]  # garder YYYY-MM-DD
    img = _jsonld_field(html, "thumbnailUrl")
    if img:
        scene["image"] = img

    # Tags
    tags = doc.xpath('//a[@class="tag_item"]')
    names = []
    for t in tags:
        n = t.text_content().strip()
        if n and n not in names:
            names.append(n)
    if names:
        scene["tags"] = [{"name": n} for n in names]

    # Studio = "Artist" dans video_tools
    studio_name = first(
        '//div[@class="video_tools"]//div[text()="Artist"]'
        '/following-sibling::span/a/span'
    )
    studio_url = first(
        '//div[@class="video_tools"]//div[text()="Artist"]'
        '/following-sibling::span/a/@href'
    )
    if studio_name:
        st = {"name": studio_name}
        if studio_url:
            st["url"] = studio_url
        scene["studio"] = st

    return scene


def parse_with_regex(html, final_url):
    """Secours si lxml absent : titre/date/image via JSON-LD uniquement."""
    scene = {"url": final_url}
    name = _jsonld_field(html, "name")
    if name:
        scene["title"] = name
    date = _jsonld_field(html, "uploadDate")
    if date:
        scene["date"] = date[:10]
    img = _jsonld_field(html, "thumbnailUrl")
    if img:
        scene["image"] = img
    log_warn("lxml indisponible : tags/studio non recuperes (titre/date/image OK).")
    return scene


def parse_scene(resp):
    html = resp.text
    final_url = resp.url
    try:
        return parse_with_lxml(html, final_url)
    except ImportError:
        return parse_with_regex(html, final_url)
    except Exception as e:
        log_error("Erreur parsing lxml, repli regex : " + str(e))
        return parse_with_regex(html, final_url)


# ---------------------------------------------------------------------------
# Recherche (sceneByName)
# ---------------------------------------------------------------------------
def search_scenes(session, term):
    # Si le terme contient un ID entre crochets (format Parabolic ou titre Stash),
    # on resout directement l'ID sans passer par la recherche textuelle.
    m = re.search(r"\[(\d{4,})\]", term)
    if m:
        vid = m.group(1)
        log_info("ID detecte dans le terme de recherche : " + vid + " -> resolution directe")
        resp = resolve_url(session, vid)
        if resp and is_video_page(resp):
            scene = parse_scene(resp)
            # retourner sous forme de liste pour sceneByName
            return [scene] if scene else []
        log_warn("Resolution directe echouee, repli sur recherche textuelle.")

    # Recherche textuelle : espaces -> +, comme le fait le navigateur sur KVS
    # Supprimer les crochets et leur contenu (IDs, tags) pour ne garder que le titre
    clean = re.sub(r"\[[^\]]*\]", "", term).strip()
    encoded = requests.utils.quote(clean, safe="").replace("%20", "+")
    url = BASE + "/search/" + encoded + "/"
    log_debug("Recherche URL : " + url)
    r = fetch(session, url)
    if r is None or r.status_code != 200:
        return []
    try:
        from lxml import html as lh
        doc = lh.fromstring(r.text)
    except Exception:
        log_error("lxml requis pour la recherche.")
        return []

    results = []
    items = doc.xpath('//div[contains(@class,"item") and contains(@class,"thumb")]')
    for it in items:
        a = it.xpath('.//a[contains(@class,"th")]/@href')
        title = it.xpath('.//div[@class="thumb_title"]/text()')
        img = it.xpath('.//img/@data-original') or it.xpath('.//img/@src')
        if not a:
            continue
        res = {"url": a[0]}
        if title:
            res["title"] = title[0].strip()
        if img:
            res["image"] = img[0]
        results.append(res)
    return results


# ---------------------------------------------------------------------------
# Point d'entree
# ---------------------------------------------------------------------------
def main():
    op = sys.argv[1] if len(sys.argv) > 1 else "query"
    data = read_stdin_json()
    session = make_session()

    if op in ("query", "fragment", "queryfragment"):
        # Stash envoie le fragment de scene (titre, files, url...)
        vid = extract_id(data)
        if not vid:
            log_error("Impossible d'extraire un ID depuis le fragment.")
            output(None)
            return
        log_info("ID detecte : " + vid)
        resp = resolve_url(session, vid)
        if resp is None:
            log_error("Page introuvable pour l'ID " + vid +
                      " (cookies DDoS expires ? video supprimee ?).")
            output(None)
            return
        output(parse_scene(resp))

    elif op == "url":
        url = data.get("url", "")
        if not url:
            output(None)
            return
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = BASE + url
        resp = fetch(session, url)
        if not is_video_page(resp):
            log_error("URL invalide ou page 404 : " + url)
            output(None)
            return
        output(parse_scene(resp))

    elif op in ("search", "name"):
        term = data.get("name", "")
        output(search_scenes(session, term))

    else:
        log_error("Operation inconnue : " + op)
        output({})


if __name__ == "__main__":
    main()
