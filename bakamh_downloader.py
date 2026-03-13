"""
╔══════════════════════════════════════════╗
║  BAKAMH DOWNLOADER  v1.1                 ║
║       bakamh.com — 巴卡漫画              ║
╚══════════════════════════════════════════╝

Dependencias:
    pip install curl_cffi beautifulsoup4 pillow

Uso:
    python bakamh_downloader.py
    python bakamh_downloader.py --debug
    python bakamh_downloader.py --url https://bakamh.com/manga/SLUG/
"""

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG  (editar aquí)
# ══════════════════════════════════════════════════════════════════════════════
OUTPUT_DIR = "bakamh_downloads"
OUTPUT_TYPE = "cbz"  # zip | cbz | pdf
USER_FORMAT = "original"  # original | jpg | png | webp
DELETE_TEMP = True
MAX_WORKERS = 4
REQUEST_DELAY = 0.5

# ══════════════════════════════════════════════════════════════════════════════
#  IMPORTS
# ══════════════════════════════════════════════════════════════════════════════
import json
import os
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from urllib.parse import quote, unquote, urlencode, urljoin

DEBUG = "--debug" in sys.argv

try:
    from curl_cffi.requests import Session as CurlSession

    _USE_CURL = True
except ImportError:
    CurlSession = None
    _USE_CURL = False

try:
    from bs4 import BeautifulSoup

    _HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    _HAS_BS4 = False

try:
    from PIL import Image

    _HAS_PIL = True
except ImportError:
    Image = None
    _HAS_PIL = False

# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════
BASE_URL = "https://bakamh.com"
AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": BASE_URL + "/",
}


# ══════════════════════════════════════════════════════════════════════════════
#  COLORES
# ══════════════════════════════════════════════════════════════════════════════
class C:
    PU = "\033[95m"
    CY = "\033[96m"
    BL = "\033[94m"
    GR = "\033[92m"
    YE = "\033[93m"
    RE = "\033[91m"
    BO = "\033[1m"
    EN = "\033[0m"


def dbg(msg):
    if DEBUG:
        print(f"  {C.CY}[DBG]{C.EN} {msg}")


def warn(msg):
    print(f"  {C.YE}[WARN]{C.EN} {msg}")


def err(msg):
    print(f"  {C.RE}[ERR]{C.EN} {msg}")


def clr():
    pass


def banner(sub=""):
    clr()
    print(f"{C.BL}╔══════════════════════════════════════════╗{C.EN}")
    print(
        f"{C.BL}║{C.EN}  {C.BO}{C.PU}BAKAMH DOWNLOADER{C.EN}  {C.CY}v1.1{C.EN}               {C.BL}║{C.EN}"
    )
    if sub:
        s = sub[:40]
        print(f"{C.BL}║{C.EN}  {C.CY}{s:<40}{C.EN}  {C.BL}║{C.EN}")
    print(f"{C.BL}╚══════════════════════════════════════════╝{C.EN}")
    if DEBUG:
        print(f"  {C.YE}[debug]{C.EN}")
    print()


# ══════════════════════════════════════════════════════════════════════════════
#  SESIÓN HTTP
# ══════════════════════════════════════════════════════════════════════════════
if _USE_CURL:
    _sess = CurlSession(impersonate="chrome123")
    _sess.headers.update(HEADERS)
    dbg("curl_cffi chrome123 listo")
else:
    try:
        import requests as _req

        _sess = _req.Session()
        _sess.headers.update(HEADERS)
        warn("curl_cffi no instalado — puede fallar con Cloudflare")
        warn("  pip install curl_cffi")
    except ImportError:
        print(f"\n{C.RE}Error: falta curl_cffi o requests.{C.EN}")
        print(f"  pip install curl_cffi beautifulsoup4 pillow")
        sys.exit(1)


def _get(url, params=None, referer=None, stream=False, retries=3):
    hdrs = {}
    if referer:
        hdrs["Referer"] = referer
    dbg(f"GET {url}" + (f" ?{params}" if params else ""))
    for i in range(retries):
        try:
            r = _sess.get(url, params=params, headers=hdrs, stream=stream, timeout=25)
            dbg(f"  {r.status_code}  len={len(r.content) if not stream else '?'}")
            if r.status_code == 200:
                return r
            if r.status_code in (403, 404):
                dbg(f"  HTTP {r.status_code} — sin reintentos")
                return None
            warn(f"  HTTP {r.status_code} — intento {i + 1}/{retries}")
        except Exception as e:
            warn(f"  Error — intento {i + 1}/{retries}: {e}")
        if i < retries - 1:
            time.sleep(2)
    return None


def _post(url, data, referer=None, retries=3):
    hdrs = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    if referer:
        hdrs["Referer"] = referer
    dbg(f"POST {url}  {data}")
    for i in range(retries):
        try:
            r = _sess.post(url, data=data, headers=hdrs, timeout=25)
            dbg(f"  {r.status_code}  len={len(r.content)}")
            if r.status_code == 200:
                return r
            warn(f"  HTTP {r.status_code} — intento {i + 1}/{retries}")
        except Exception as e:
            warn(f"  Error POST — intento {i + 1}/{retries}: {e}")
        if i < retries - 1:
            time.sleep(2)
    return None


def _soup(html):
    if not _HAS_BS4:
        err("beautifulsoup4 no instalado: pip install beautifulsoup4")
        return None
    return BeautifulSoup(html, "html.parser")


# ══════════════════════════════════════════════════════════════════════════════
#  PARSING DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════
def parse_input(raw):
    """Devuelve slug del manga (puede ser chino o inglés)."""
    raw = raw.strip().rstrip("/")
    m = re.search(r"bakamh\.com/manga/([^/?#]+)", raw)
    if m:
        return unquote(m.group(1))
    m = re.search(r"/manga/([^/?#]+)", raw)
    if m:
        return unquote(m.group(1))
    if raw and " " not in raw and not raw.startswith("http"):
        return raw
    return raw


# ══════════════════════════════════════════════════════════════════════════════
#  INFO DEL MANGA
# ══════════════════════════════════════════════════════════════════════════════
def get_manga_info(slug):
    encoded = quote(slug, safe="")
    url = f"{BASE_URL}/manga/{encoded}/"
    dbg(f"Manga URL: {url}")
    time.sleep(REQUEST_DELAY)
    r = _get(url, referer=BASE_URL + "/")
    if not r:
        err(f"No se pudo cargar la página del manga: {slug}")
        return None, []

    html = r.text
    soup = _soup(html)
    if not soup:
        return None, []

    title = ""
    for sel in [
        ".post-title h1",
        ".post-title h3",
        "h1.entry-title",
        ".manga-title h1",
        "h1",
    ]:
        el = soup.select_one(sel)
        if el:
            title = el.get_text(strip=True)
            break
    if not title:
        title = slug

    author = _meta_field(soup, ".author-content a, .manga-authors a")
    artist = _meta_field(soup, ".artist-content a")
    status = ""
    for sel in [
        ".post-status .summary-content",
        ".manga-status .summary-content",
        ".status .summary-content",
    ]:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(strip=True)
            if txt and len(txt) < 20:
                status = txt
                break
    genres = [
        a.get_text(strip=True)
        for a in soup.select(".genres-content a, .manga-genres a")
    ]
    summary_el = soup.select_one(".summary__content, .description-summary")
    summary = summary_el.get_text(" ", strip=True)[:200] if summary_el else ""

    meta = {
        "slug": slug,
        "title": title,
        "author": author,
        "artist": artist,
        "status": status,
        "genres": genres,
        "summary": summary,
    }
    dbg(f"Meta: {meta}")

    chapters = _chapters_from_html(soup, slug)
    if not chapters:
        dbg("  No hay capítulos en HTML — probando AJAX...")
        manga_id = _manga_id(soup, html)
        nonce = _nonce_from_html(html)
        chapters = _chapters_ajax(slug, manga_id, nonce)

    dbg(f"  {len(chapters)} capítulos")
    return meta, chapters


def _meta_field(soup, selector):
    el = soup.select_one(selector)
    return el.get_text(strip=True) if el else ""


def _manga_id(soup, html):
    el = soup.select_one("#manga-chapters-holder, [data-id]")
    if el and el.get("data-id"):
        return el["data-id"]
    m = re.search(r'"manga_id"\s*:\s*"?(\d+)"?', html)
    return m.group(1) if m else ""


def _nonce_from_html(html):
    for pat in [
        r'"wpmangaloadmore"\s*:\s*\{[^}]*"nonce"\s*:\s*"([a-f0-9]+)"',
        r'nonce["\']?\s*:\s*["\']([a-f0-9]{8,})["\']',
    ]:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return ""


_UI_BUTTON_TEXTS = {
    "取消回复",
    "观看最新话",
    "观看第一话",
    "发表评论",
    "留言",
    "登录",
    "注册",
    "回复",
    "举报",
    "上一章",
    "下一章",
    "返回目录",
    "cancel reply",
    "leave a reply",
    "read first",
    "read latest",
    "first chapter",
    "latest chapter",
    "previous chapter",
    "next chapter",
    "login",
    "register",
    "report",
}


def _is_ui_button(text):
    if not text:
        return False
    t = text.strip().lower()
    if t in {x.lower() for x in _UI_BUTTON_TEXTS}:
        return True
    if len(t) <= 2 and not re.search(r"\d", t):
        return True
    return False


def _chapters_from_html(soup, manga_slug=""):
    chapters = []

    for a in soup.select("a[chapter-data-url]"):
        href = a.get("chapter-data-url", "").strip().rstrip("/")
        if not href or "/manga/" not in href:
            continue
        ch_slug = href.split("/")[-1]
        title = a.get_text(strip=True)
        if _is_ui_button(title):
            continue
        chapters.append({"title": title, "url": href, "slug": ch_slug})

    if chapters:
        chapters.reverse()
        return chapters

    for li in soup.select(
        "li.wp-manga-chapter, .chapter-list li, .chapters-list li, .listing-chapters_wrap li"
    ):
        a = li.select_one("a")
        if not a or not a.get("href"):
            continue
        href = a["href"].rstrip("/")
        ch_slug = href.split("/")[-1]
        title = a.get_text(strip=True)
        if ch_slug and "/manga/" in href:
            chapters.append({"title": title, "url": href, "slug": ch_slug})
    if chapters:
        chapters.reverse()
        return chapters

    if manga_slug:
        encoded = quote(manga_slug, safe="")
        for pat_slug in [manga_slug, encoded]:
            for a in soup.find_all(
                "a", href=re.compile(rf"/manga/{re.escape(pat_slug)}/")
            ):
                href = a["href"].rstrip("/")
                parts = [p for p in href.split("/") if p]
                if len(parts) < 2:
                    continue
                ch_slug = parts[-1]
                if ch_slug == pat_slug or ch_slug == manga_slug:
                    continue
                title = a.get_text(strip=True)
                if _is_ui_button(title):
                    continue
                chapters.append(
                    {"title": title or ch_slug, "url": href, "slug": ch_slug}
                )
            if chapters:
                seen_s: set = set()
                chapters = [
                    c
                    for c in chapters
                    if not (c["slug"] in seen_s or seen_s.add(c["slug"]))
                ]
                break
    if chapters:
        chapters.reverse()
        return chapters

    for script in soup.find_all("script"):
        txt = script.string or ""
        for var in [
            "chapterList",
            "chapList",
            "chapters",
            "chapter_list",
            "chapterData",
            "mangaChapters",
        ]:
            m = re.search(rf"{var}\s*[=:]\s*(\[.+?\])", txt, re.DOTALL)
            if not m:
                continue
            try:
                data = json.loads(m.group(1))
                for item in data:
                    if isinstance(item, dict):
                        url = (
                            item.get("url")
                            or item.get("link")
                            or item.get("href")
                            or ""
                        )
                        title = (
                            item.get("title")
                            or item.get("name")
                            or item.get("chapter_name")
                            or ""
                        )
                        slug = (
                            url.rstrip("/").split("/")[-1]
                            if url
                            else (item.get("slug") or item.get("chapter_slug") or "")
                        )
                        if slug:
                            chapters.append(
                                {"title": title or slug, "url": url, "slug": slug}
                            )
                if chapters:
                    dbg(f"  Capítulos via JSON var '{var}': {len(chapters)}")
                    return chapters
            except Exception as e:
                dbg(f"  JSON parse '{var}': {e}")

    if manga_slug:
        enc = quote(manga_slug, safe="")
        seen_slugs: set = set()
        for test_slug in dict.fromkeys([manga_slug, enc]):
            found_any = False
            for a in soup.find_all("a", href=True):
                href = a["href"].rstrip("/")
                needle = f"/manga/{test_slug}/"
                if needle not in href:
                    continue
                ch_slug = href.split("/")[-1]
                if not ch_slug or ch_slug == test_slug or ch_slug in seen_slugs:
                    continue
                title = a.get_text(strip=True) or ch_slug
                if _is_ui_button(title):
                    continue
                seen_slugs.add(ch_slug)
                chapters.append({"title": title, "url": href, "slug": ch_slug})
                found_any = True
            if found_any:
                break

    if chapters:
        dbg(f"  Capítulos estrategia D: {len(chapters)}")
        chapters.reverse()

    if not chapters and DEBUG:
        fname = f"debug_manga_{manga_slug[:20]}.html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(str(soup))
        print(f"  {C.YE}HTML del manga guardado: {fname}{C.EN}")

    return chapters


def _chapters_ajax(manga_slug, manga_id, nonce):
    ref = f"{BASE_URL}/manga/{quote(manga_slug, safe='')}/"

    for action in [
        "manga_get_chapters",
        "wp_manga_get_chapters",
        "manga_get_chapter_list",
    ]:
        if not manga_id and "list" not in action:
            continue
        data = {"action": action, "_wpnonce": nonce}
        if manga_id:
            data["manga"] = manga_id
        r = _post(AJAX_URL, data=data, referer=ref)
        if not r:
            continue
        try:
            j = r.json()
            frag = j.get("data", "") if isinstance(j, dict) else r.text
        except Exception:
            frag = r.text
        if not frag or len(frag) < 50:
            continue
        s2 = _soup(frag)
        if not s2:
            continue
        chapters = _chapters_from_html(s2, manga_slug)
        if chapters:
            dbg(f"  Capítulos via AJAX '{action}': {len(chapters)}")
            return chapters

    return []


# ══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO Y GÉNEROS
# ══════════════════════════════════════════════════════════════════════════════
_GENRE_URL_TYPE = "manga-genre"

SORT_LABELS = {
    "latest": "Más recientes",
    "alphabet": "A-Z",
    "rating": "Mejor rating",
    "trending": "Tendencia",
    "views": "Más vistos",
    "new-manga": "Nuevos",
}


def get_genres():
    """
    Extrae géneros probando múltiples páginas y patrones de URL.
    Orden de búsqueda:
      1. Elementos de navegación (nav, header, aside, menús)
      2. Página completa
      3. Fallback: extrae géneros desde las tarjetas del catálogo
    """
    global _GENRE_URL_TYPE

    cat_pats = [
        ("/manga-genre/", "manga-genre"),
        ("/category/", "category"),
        ("/genre/", "genre"),
        ("/tag/", "tag"),
        ("/漫画类型/", "漫画类型"),  # por si usa chino en la URL
    ]

    # Selectores de contenedores de navegación donde suelen estar los géneros
    nav_sels = [
        "nav",
        "header",
        ".nav",
        ".menu",
        ".genres",
        ".genre-list",
        "#menu",
        ".navbar",
        ".site-nav",
        ".manga-nav",
        "aside",
        ".widget",
        ".sidebar",
        ".navigation",
        "#navigation",
        ".top-bar",
        ".header-menu",
        ".main-nav",
        ".primary-menu",
        "[class*='genre']",
        "[class*='category']",
        "[id*='genre']",
        "[id*='category']",
    ]

    # Páginas a probar, en orden de probabilidad
    pages_to_try = [
        BASE_URL + "/",
        f"{BASE_URL}/blgl/",
        f"{BASE_URL}/manga/",
        f"{BASE_URL}/genres/",
        f"{BASE_URL}/manga-genre/",
        f"{BASE_URL}/category/",
    ]

    for try_url in pages_to_try:
        time.sleep(REQUEST_DELAY)
        r = _get(try_url)
        if not r:
            continue
        html = r.text
        soup = _soup(html)
        if not soup:
            continue

        if DEBUG:
            fname = f"debug_genres_{try_url.split('/')[-2] or 'home'}.html"
            with open(fname, "w", encoding="utf-8") as f:
                f.write(html)
            dbg(f"HTML guardado: {fname}")

        for prefix, url_type in cat_pats:
            genres = []
            seen = set()

            # ── Paso 1: buscar en contenedores de navegación ──────────────────
            for nav_sel in nav_sels:
                try:
                    nav_els = soup.select(nav_sel)
                except Exception:
                    continue
                for nav_el in nav_els:
                    for a in nav_el.find_all("a", href=True):
                        href = a["href"]
                        if prefix not in href:
                            continue
                        idx = href.index(prefix) + len(prefix)
                        rest = href[idx:].split("/")[0].split("?")[0].strip()
                        if not rest or rest in seen or re.match(r"^\d+$", rest):
                            continue
                        name = a.get_text(strip=True)
                        if not name or len(name) > 45:
                            continue
                        seen.add(rest)
                        genres.append({"name": name, "slug": rest})

            # ── Paso 2: si no encontró nada en nav, buscar en toda la página ──
            if not genres:
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if prefix not in href:
                        continue
                    idx = href.index(prefix) + len(prefix)
                    rest = href[idx:].split("/")[0].split("?")[0].strip()
                    if not rest or rest in seen or re.match(r"^\d+$", rest):
                        continue
                    name = a.get_text(strip=True)
                    if not name or len(name) > 45:
                        continue
                    seen.add(rest)
                    genres.append({"name": name, "slug": rest})

            if genres:
                dbg(f"  {len(genres)} géneros con prefijo '{prefix}' en {try_url}")
                _GENRE_URL_TYPE = url_type
                return genres

    # ── Fallback: extraer géneros desde las tarjetas del catálogo ─────────────
    dbg("  Ninguna página con géneros encontrada — intentando fallback desde catálogo")
    genres = _genres_from_catalog()
    if genres:
        dbg(f"  {len(genres)} géneros extraídos desde catálogo (fallback)")
        return genres

    warn("No se encontraron géneros. Usando solo 'Todos'.")
    return []


def _genres_from_catalog():
    """
    Fallback: carga el catálogo y extrae géneros desde los links de las tarjetas
    de manga (que suelen tener badges/tags de género).
    """
    cat_pats = [
        ("/manga-genre/", "manga-genre"),
        ("/category/", "category"),
        ("/genre/", "genre"),
        ("/tag/", "tag"),
    ]
    time.sleep(REQUEST_DELAY)
    r = _get(f"{BASE_URL}/blgl/", referer=BASE_URL + "/")
    if not r:
        r = _get(BASE_URL + "/", referer=BASE_URL + "/")
    if not r:
        return []

    soup = _soup(r.text)
    if not soup:
        return []

    for prefix, url_type in cat_pats:
        genres = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if prefix not in href:
                continue
            # Excluir paginación y URLs de capítulos
            if re.search(r"/page/\d+", href) or "/manga/" in href:
                continue
            idx = href.index(prefix) + len(prefix)
            rest = href[idx:].split("/")[0].split("?")[0].strip()
            if not rest or rest in seen or re.match(r"^\d+$", rest):
                continue
            name = a.get_text(strip=True)
            if not name or len(name) > 45:
                continue
            seen.add(rest)
            genres.append({"name": name, "slug": rest})
        if genres:
            global _GENRE_URL_TYPE
            _GENRE_URL_TYPE = url_type
            return genres

    return []


def _extract_card(card, items, seen):
    a = card.select_one("a[href*='/manga/']")
    if not a:
        return
    href = a["href"].rstrip("/")
    m = re.search(r"/manga/([^/?#]+)", href)
    if not m:
        return
    slug = unquote(m.group(1))
    if slug in seen:
        return
    seen.add(slug)

    # Intentar obtener el título con múltiples estrategias en orden de fiabilidad
    title = ""

    # 1. Selectores de título específicos del tema
    for sel in [
        ".manga-name a",
        ".manga-name",
        ".post-title a",
        ".post-title",
        "h3 a",
        "h4 a",
        "h3",
        "h4",
        "h2 a",
        "h2",
        ".tab-meta-title a",
        ".tab-meta-title",
        ".item-summary .post-title",
        ".item-summary h3",
        "[class*='title'] a",
        "[class*='title']",
        "[class*='name'] a",
        "[class*='name']",
    ]:
        el = card.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) >= 2:
                title = t
                break

    # 2. Atributo title o aria-label de cualquier enlace al manga
    if not title:
        for lnk in card.find_all("a", href=True):
            if "/manga/" in lnk.get("href", ""):
                for attr in ("title", "aria-label"):
                    t = lnk.get(attr, "").strip()
                    if t and len(t) >= 2:
                        title = t
                        break
            if title:
                break

    # 3. Alt de la imagen de portada
    if not title:
        img = card.select_one("img")
        if img:
            t = (img.get("alt") or img.get("title") or "").strip()
            if t and len(t) >= 2:
                title = t

    # 4. Cualquier texto directo del card que no sea vacío
    if not title:
        t = card.get_text(" ", strip=True)
        # Tomar solo la primera línea significativa
        for line in t.splitlines():
            line = line.strip()
            if line and len(line) >= 2 and line != slug:
                title = line
                break

    # 5. Último recurso: slug decodificado legible
    if not title:
        title = slug.replace("-", " ").replace("_", " ").strip()

    latest_el = card.select_one(
        ".chapter a, .font-meta a, .chapter-item a, .latest-chap a, "
        "[class*='chapter'] a, [class*='latest'] a"
    )
    latest = latest_el.get_text(strip=True) if latest_el else ""
    items.append({"slug": slug, "title": title, "latest": latest})


def _parse_manga_cards(soup):
    items = []
    seen = set()

    container_sels = [
        ".page-item-detail",
        ".manga-item",
        ".c-image-hover",
        ".tab-meta-title",
        "article.manga",
        ".manga-content .manga",
        "li.manga-item",
        ".post-content-item",
        ".col-6.col-sm-3",
    ]
    for sel in container_sels:
        cards = soup.select(sel)
        if not cards:
            continue
        for card in cards:
            _extract_card(card, items, seen)
        if items:
            dbg(f"  Cards con selector '{sel}': {len(items)}")
            return items

    for container_sel in ["main", "article", ".site-content", "#content", "body"]:
        container = soup.select_one(container_sel)
        if not container:
            continue
        for a in container.find_all("a", href=re.compile(r"/manga/[^/\s]")):
            href = a.get("href", "").rstrip("/")
            if re.search(r"/manga/[^/]+/[^/]+$", href):
                continue
            text = a.get_text(strip=True)
            if not text or len(text) < 2:
                continue
            m = re.search(r"/manga/([^/?#]+)", href)
            if not m:
                continue
            slug = unquote(m.group(1))
            if slug in seen:
                continue
            seen.add(slug)
            items.append({"slug": slug, "title": text, "latest": ""})
        if items:
            dbg(f"  Cards estrategia B (container={container_sel}): {len(items)}")
            return items

    dbg("  _parse_manga_cards: sin resultados con ningún selector")
    return items


def get_catalog(page=1, genre_slug="", sort="latest"):
    candidates = []
    if genre_slug:
        candidates = [
            f"{BASE_URL}/{_GENRE_URL_TYPE}/{genre_slug}/page/{page}/",
            f"{BASE_URL}/manga-genre/{genre_slug}/page/{page}/",
            f"{BASE_URL}/category/{genre_slug}/page/{page}/",
            f"{BASE_URL}/genre/{genre_slug}/page/{page}/",
        ]
    else:
        if page == 1:
            candidates = [f"{BASE_URL}/blgl/", f"{BASE_URL}/manga/"]
        else:
            candidates = [
                f"{BASE_URL}/blgl/page/{page}/",
                f"{BASE_URL}/manga/page/{page}/",
            ]

    params = {"m_orderby": sort} if sort != "latest" else None

    r = None
    used_url = ""
    for url in candidates:
        time.sleep(0.2)  # delay reducido para carga en paralelo
        r = _get(url, params=params, referer=f"{BASE_URL}/")
        if r:
            used_url = url
            break

    if not r:
        return [], 1

    soup = _soup(r.text)
    if not soup:
        return [], 1

    items = _parse_manga_cards(soup)

    if not items and DEBUG:
        fname = f"debug_catalog_p{page}.html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(r.text)
        dbg(f"  Sin resultados en {used_url} — HTML guardado: {fname}")

    total = _detect_total_pages(soup, r.text, genre_slug)
    dbg(f"  Catálogo {used_url} pág {page}/{total}: {len(items)} series")
    return items, total


def _detect_total_pages(soup, html, genre_slug=""):
    """
    Detecta el total de páginas de forma robusta para cualquier URL de catálogo.
    Estrategias en orden de confianza:
      1. Mayor número en links de paginación (excluyendo capítulos).
      2. Texto 'Page X of Y' dentro del paginador.
      3. Búsqueda bruta de /page/N/ en el HTML según el tipo de URL.
    """
    for sel in [
        ".nav-links",
        ".pagination",
        ".wp-pagenavi",
        ".page-numbers",
        ".paginate",
        "[class*='pagin']",
    ]:
        container = soup.select_one(sel)
        if not container:
            continue
        nums = []
        for a in container.find_all("a", href=True):
            href = a["href"]
            if re.search(r"/manga/[^/]+/[^/]+", href):
                continue
            m = re.search(r"/page/(\d+)/?", href)
            if m:
                nums.append(int(m.group(1)))
        if nums:
            return max(nums)

    patterns = [
        rf"/{re.escape(genre_slug)}/page/(\d+)/" if genre_slug else None,
        r"/blgl/page/(\d+)/",
        r"/manga-genre/[^/]+/page/(\d+)/",
        r"/category/[^/]+/page/(\d+)/",
        r"/genre/[^/]+/page/(\d+)/",
        r"[?&]paged=(\d+)",
    ]
    all_nums = []
    for pat in patterns:
        if not pat:
            continue
        for m in re.finditer(pat, html):
            n = int(m.group(1))
            if n < 10000:
                all_nums.append(n)
    if all_nums:
        return max(all_nums)

    return 1


def _fetch_search_page(args):
    """Worker: descarga una pagina de resultados de busqueda."""
    pg, query = args
    url = BASE_URL + "/" if pg == 1 else f"{BASE_URL}/page/{pg}/"
    params = {"s": query, "post_type": "wp-manga"}
    time.sleep(0.1)
    r = _get(url, params=params, referer=BASE_URL + "/")
    if not r:
        return pg, None
    soup = _soup(r.text)
    if not soup:
        return pg, None
    # Detectar pagina real de no-results
    if soup.select_one(".no-posts-found, .no-results, .not-found"):
        return pg, []
    items = _parse_manga_cards(soup)
    return pg, items


def search(query, page=1):
    """
    Busqueda paralela: lanza SEARCH_WORKERS requests simultaneas,
    avanza en lotes hasta recibir pagina vacia.
    """
    if not hasattr(search, "_cache"):
        search._cache = {}

    key = query.lower().strip()

    if key not in search._cache:
        WORKERS = 8
        all_items = []
        seen = set()
        pg = 1

        sys.stdout.write(f"  {C.BL}Buscando '{query}'...{C.EN}   \r")
        sys.stdout.flush()

        while pg <= 500:
            # Lanzar un lote de WORKERS paginas en paralelo
            batch = list(range(pg, pg + WORKERS))
            pg += WORKERS

            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = {
                    pool.submit(_fetch_search_page, (p, query)): p for p in batch
                }
                batch_results = {}
                for fut in as_completed(futures):
                    p, items = fut.result()
                    batch_results[p] = items

            # Procesar en orden para mantener coherencia
            found_any = False
            hit_end = False
            for p in sorted(batch_results):
                items = batch_results[p]
                if items is None or items == []:
                    hit_end = True
                    break
                for it in items:
                    if it["slug"] not in seen:
                        seen.add(it["slug"])
                        all_items.append(it)
                found_any = True

            sys.stdout.write(
                f"  {C.BL}Buscando '{query}'... {len(all_items)} resultados{C.EN}   \r"
            )
            sys.stdout.flush()

            if hit_end or not found_any:
                break

        sys.stdout.write(" " * 70 + "\r")
        sys.stdout.flush()

        PAGE_SIZE = 20
        pages = [
            all_items[k : k + PAGE_SIZE]
            for k in range(0, max(len(all_items), 1), PAGE_SIZE)
        ]
        if not pages:
            pages = [[]]
        search._cache[key] = pages
        dbg(f"  '{query}': {len(all_items)} totales, {len(pages)} paginas UI")

    pages = search._cache[key]
    total = len(pages)
    idx = max(0, min(page - 1, total - 1))
    return pages[idx], total


def _fetch_catalog_page(args):
    """Worker: descarga una página del catálogo."""
    page, genre_slug, sort = args
    items, detected_total = get_catalog(page, genre_slug, sort)
    return page, items, detected_total


def _load_all_pages(genre_slug, sort, label, workers=8, hint_count=0):
    """
    Carga TODAS las páginas de un género en paralelo.

    Estrategia para determinar cuántas páginas hay (en orden de confianza):
      1. Si hint_count > 0 (del nombre del género, ej. "BL(968)"):
             total_pages = ceil(hint_count / items_per_page)
      2. _detect_total_pages() del HTML de la página 1.
      3. Seguir cargando en lotes hasta recibir página vacía (stop automático).

    Devuelve (all_items, seen_slugs).
    """
    import math

    all_items = []
    seen_slugs = set()

    # ── Página 1 síncrona: necesaria para calibrar items_per_page y total ──
    items1, detected_total = get_catalog(1, genre_slug, sort)
    items_per_page = max(len(items1), 1)

    for it in items1:
        if it["slug"] not in seen_slugs:
            seen_slugs.add(it["slug"])
            all_items.append(it)

    # Determinar total de páginas
    if hint_count > 0 and items_per_page > 0:
        computed_total = math.ceil(hint_count / items_per_page)
        total = max(detected_total, computed_total)
    else:
        total = detected_total if detected_total > 1 else 9999  # exploración abierta

    sys.stdout.write(
        f"  {C.BL}{label} — pág 1/{total if total < 9999 else '?'} "
        f"— {len(all_items)} series{C.EN}   \r"
    )
    sys.stdout.flush()

    if items_per_page == 0 or not items1:
        return all_items, seen_slugs

    # ── Páginas 2..total en paralelo (lotes de `workers`) ──────────────────
    page = 2
    consecutive_empty = 0

    while page <= total:
        batch = list(range(page, min(page + workers, total + 1)))
        page += len(batch)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_catalog_page, (p, genre_slug, sort)): p
                for p in batch
            }
            batch_results = {}
            for fut in as_completed(futures):
                p, page_items, _ = fut.result()
                batch_results[p] = page_items

        # Insertar en orden de página
        got_any = False
        for p in sorted(batch_results):
            items = batch_results[p]
            if not items:
                consecutive_empty += 1
            else:
                consecutive_empty = 0
                got_any = True
                for it in items:
                    if it["slug"] not in seen_slugs:
                        seen_slugs.add(it["slug"])
                        all_items.append(it)

        sys.stdout.write(
            f"  {C.BL}{label} — págs {batch[0]}-{batch[-1]}"
            f"/{total if total < 9999 else '?'} "
            f"— {len(all_items)} series{C.EN}   \r"
        )
        sys.stdout.flush()

        # Si 3 lotes consecutivos vacíos, asumimos fin del catálogo
        if consecutive_empty >= workers * 2:
            dbg(f"  {label}: {consecutive_empty} págs vacías consecutivas — fin")
            break

    return all_items, seen_slugs


def get_all_catalog(genre_slug="", sort="latest", genres=None):
    """
    Carga ABSOLUTAMENTE TODAS las series del sitio.

    • Si genre_slug está definido: todas las páginas de ese género.
    • Si genre_slug == "" (Todos): itera cada género individualmente y concatena,
      pasando el conteo del género (extraído del nombre) para calcular las páginas
      correctas sin depender de la paginación del HTML.
    """
    import re as _re

    all_items = []
    seen_slugs = set()

    def _extract_count(name):
        """Extrae el número entre paréntesis al final del nombre, ej 'BL(968)' → 968."""
        m = _re.search(r"\((\d+)\)\s*$", name)
        return int(m.group(1)) if m else 0

    if genre_slug:
        hint = 0
        if genres:
            for g in genres:
                if g["slug"] == genre_slug:
                    hint = _extract_count(g["name"])
                    break
        items, _ = _load_all_pages(genre_slug, sort, genre_slug, hint_count=hint)
        print()
        return items

    # ── Modo "Todos": iterar género por género ────────────────────────────────
    # /blgl/ primero (series sin género específico / featured)
    blgl_items, blgl_seen = _load_all_pages("", sort, "/blgl/", hint_count=0)
    for it in blgl_items:
        if it["slug"] not in seen_slugs:
            seen_slugs.add(it["slug"])
            all_items.append(it)

    if not genres:
        print()
        return all_items

    total_genres = len(genres)
    for gi, g in enumerate(genres, 1):
        hint = _extract_count(g["name"])
        label = f"[{gi}/{total_genres}] {g['name']}"
        g_items, _ = _load_all_pages(g["slug"], sort, label, hint_count=hint)
        added = 0
        for it in g_items:
            if it["slug"] not in seen_slugs:
                seen_slugs.add(it["slug"])
                all_items.append(it)
                added += 1
        dbg(f"  Género '{g['name']}': {len(g_items)} cargadas, {added} nuevas")

    print()
    return all_items


# ══════════════════════════════════════════════════════════════════════════════
#  DESCARGA
# ══════════════════════════════════════════════════════════════════════════════
def _sanitize(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


def _ext(url):
    if USER_FORMAT != "original":
        return f".{USER_FORMAT}"
    m = re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", url, re.I)
    return "." + (m.group(1).lower() if m else "jpg")


def _dl_image(args):
    idx, url, dest, referer = args
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = BASE_URL + url
    if not url.startswith("http"):
        dbg(f"  img[{idx}] URL inválida: {url[:80]}")
        return idx, None

    headers = {
        "Referer": referer,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
    }
    for attempt in range(3):
        try:
            r = _sess.get(url, headers=headers, timeout=30)
            dbg(f"  img[{idx}] {r.status_code}  {url[:70]}")
            if r.status_code == 403:
                r = _sess.get(url, headers={"Accept": headers["Accept"]}, timeout=30)
                dbg(f"  img[{idx}] retry sin Referer: {r.status_code}")
            if r.status_code != 200:
                time.sleep(1.5)
                continue
            raw = r.content
            if not raw or len(raw) < 500:
                dbg(f"  img[{idx}] respuesta vacía ({len(raw)} bytes)")
                time.sleep(1)
                continue
            if USER_FORMAT != "original" and _HAS_PIL:
                try:
                    img = Image.open(BytesIO(raw)).convert("RGB")
                    buf = BytesIO()
                    fmt = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(
                        USER_FORMAT, "JPEG"
                    )
                    img.save(buf, format=fmt, quality=92)
                    raw = buf.getvalue()
                except Exception:
                    pass
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(raw)
            return idx, dest
        except Exception as e:
            dbg(f"  img[{idx}] intento {attempt + 1}: {e}")
            time.sleep(1.5)
    return idx, None


def _pack(files, out_path, fmt):
    if fmt in ("zip", "cbz"):
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED) as zf:
            for f in sorted(files):
                zf.write(f, os.path.basename(f))
    elif fmt == "pdf" and _HAS_PIL:
        imgs = []
        for f in sorted(files):
            try:
                imgs.append(Image.open(f).convert("RGB"))
            except Exception:
                pass
        if imgs:
            imgs[0].save(out_path, save_all=True, append_images=imgs[1:])


def download_chapter(manga_slug, manga_title, chapter, ch_num, total):
    ch_slug = chapter["slug"]
    ch_title = _sanitize(chapter["title"])
    title = _sanitize(manga_title)

    print(f"\n  {C.YE}↓{C.EN}  [{ch_num}/{total}]  {chapter['title'][:55]}")

    urls = get_chapter_images(manga_slug, ch_slug)
    if not urls:
        err(f"     Sin imágenes")
        return False

    tmp = os.path.join(OUTPUT_DIR, title, f"__tmp_{ch_slug}")
    os.makedirs(tmp, exist_ok=True)

    ref = f"{BASE_URL}/manga/{quote(manga_slug, safe='')}/{ch_slug}/"
    tasks = []
    for i, url in enumerate(urls):
        dest = os.path.join(tmp, f"{i + 1:04d}{_ext(url)}")
        tasks.append((i, url, dest, ref))

    ok_files = []
    failed = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_dl_image, t): t for t in tasks}
        done = 0
        for fut in as_completed(futs):
            idx, path = fut.result()
            done += 1
            if path:
                ok_files.append(path)
            else:
                failed += 1
            pct = int(done / len(tasks) * 24)
            bar = f"{C.GR}{'█' * pct}{'░' * (24 - pct)}{C.EN}"
            print(
                f"\r     {C.CY}{len(urls)}p{C.EN}  {bar}  {done}/{len(tasks)} ",
                end="",
                flush=True,
            )
    print()

    if not ok_files:
        err("     Ninguna imagen descargada")
        return False
    if failed:
        warn(f"     {failed} fallaron")

    ext_map = {"zip": ".zip", "cbz": ".cbz", "pdf": ".pdf"}
    out_dir = os.path.join(OUTPUT_DIR, title)
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(
        out_dir, f"{ch_num:04d} - {ch_title}{ext_map.get(OUTPUT_TYPE, '.cbz')}"
    )

    _pack(ok_files, out_file, OUTPUT_TYPE)
    print(f"     {C.GR}✔  {os.path.basename(out_file)}{C.EN}")

    if DELETE_TEMP:
        shutil.rmtree(tmp, ignore_errors=True)
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  IMÁGENES DE CAPÍTULO
# ══════════════════════════════════════════════════════════════════════════════
def get_chapter_images(manga_slug, chapter_slug):
    encoded_manga = quote(manga_slug, safe="")
    ch_url = f"{BASE_URL}/manga/{encoded_manga}/{chapter_slug}/"
    dbg(f"Capítulo URL: {ch_url}")
    time.sleep(REQUEST_DELAY)
    r = _get(ch_url, referer=f"{BASE_URL}/manga/{encoded_manga}/")
    if not r:
        return []

    html = r.text
    soup = _soup(html)

    urls = []
    if soup:
        for img in soup.select(
            "div.reading-content img, "
            "div.page-break img, "
            "img.wp-manga-chapter-img, "
            ".reading-content noscript img, "
            "div#manga-reading-nav-head ~ div img"
        ):
            src = (
                img.get("data-lazy-src") or img.get("data-src") or img.get("src") or ""
            ).strip()
            if src and not src.startswith("data:"):
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = BASE_URL + src
                if src not in urls:
                    urls.append(src)

    dbg(f"  Método A: {len(urls)} imgs")
    if urls:
        return urls

    m = re.search(r"var\s+imageLinks\s*=\s*(\[[^\]]+\])", html, re.DOTALL)
    if m:
        try:
            raw_list = json.loads(m.group(1))
            for item in raw_list:
                item = item.strip()
                if item.startswith("http"):
                    urls.append(item)
                else:
                    try:
                        import base64

                        decoded = base64.b64decode(item).decode()
                        if decoded.startswith("http"):
                            urls.append(decoded)
                    except Exception:
                        pass
            dbg(f"  Método B (imageLinks): {len(urls)} imgs")
            if urls:
                return urls
        except Exception as e:
            dbg(f"  imageLinks parse error: {e}")

    dbg("  Método C: AJAX...")
    chapter_id = ""
    m2 = re.search(r'"chapter_id"\s*:\s*"?(\d+)"?', html)
    if m2:
        chapter_id = m2.group(1)
    if not chapter_id and soup:
        el = soup.select_one("[data-id]")
        if el:
            chapter_id = el.get("data-id", "")

    if chapter_id:
        nonce = _nonce_from_html(html)
        data = {
            "action": "manga_get_reading_style",
            "chapter_id": chapter_id,
            "_wpnonce": nonce,
        }
        ar = _post(AJAX_URL, data=data, referer=ch_url)
        if ar:
            try:
                j = ar.json()
                frag = j.get("data", "") if isinstance(j, dict) else ar.text
                if frag:
                    s3 = _soup(frag)
                    if s3:
                        for img in s3.select("img"):
                            src = (img.get("data-src") or img.get("src") or "").strip()
                            if src.startswith("http"):
                                urls.append(src)
                        dbg(f"  Método C: {len(urls)} imgs")
            except Exception as e:
                dbg(f"  AJAX error: {e}")

    if DEBUG:
        fname = f"debug_ch_{chapter_slug}.html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  {C.YE}HTML capítulo guardado: {fname}{C.EN}")

    return urls


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS UI
# ══════════════════════════════════════════════════════════════════════════════
def print_list(items):
    w = shutil.get_terminal_size((80, 20)).columns
    for i, it in enumerate(items):
        latest = (
            f"  {C.BL}│{C.EN} {C.CY}{it['latest'][:20]}{C.EN}"
            if it.get("latest")
            else ""
        )
        title = it["title"][: w - 30]
        print(f"  {C.BO}{i + 1:>3}.{C.EN}  {title}{latest}")


def parse_sel(s, n):
    s = s.strip().lower()
    if s in ("a", "all", "todo", "todos"):
        return list(range(n))
    idxs = set()
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                for i in range(int(a) - 1, min(int(b), n)):
                    idxs.add(i)
        elif part.isdigit():
            i = int(part) - 1
            if 0 <= i < n:
                idxs.add(i)
    return sorted(idxs)


def show_and_download(slug):
    banner(slug[:40])
    print(f"  {C.YE}Cargando manga...{C.EN}")
    meta, chapters = get_manga_info(slug)

    if not meta:
        err("No se pudo cargar el manga.")
        input("\n  Enter para volver...")
        return

    banner(meta["title"][:40])
    print(f"  {C.BO}{C.GR}{meta['title']}{C.EN}")
    if meta.get("author"):
        print(f"  {C.CY}Autor:{C.EN}  {meta['author']}")
    if meta.get("status"):
        print(f"  {C.CY}Estado:{C.EN} {meta['status']}")
    if meta.get("genres"):
        print(f"  {C.PU}{', '.join(meta['genres'][:6])}{C.EN}")
    if meta.get("summary"):
        print(f"\n  {meta['summary'][:160]}...")
    print(f"\n  {C.BL}{'─' * 44}{C.EN}")
    print(f"  {C.BL}{len(chapters)} capítulos{C.EN}\n")

    if not chapters:
        err("Sin capítulos disponibles.")
        input("\n  Enter para volver...")
        return

    for i, ch in enumerate(chapters):
        print(f"  {C.BO}{i + 1:>4}.{C.EN}  {ch['title'][:60]}")

    print()
    sel = input(f"  {C.YE}Capítulos  (ej: 1 · 3-7 · 1,3,5 · all)  ➜ {C.EN}").strip()
    idxs = parse_sel(sel, len(chapters))
    if not idxs:
        return

    print(f"\n  {C.GR}Descargando {len(idxs)} capítulo(s)...{C.EN}")
    print(f"  Destino: {C.CY}{os.path.abspath(OUTPUT_DIR)}{C.EN}")

    ok = sum(
        download_chapter(slug, meta["title"], chapters[idx], rank, len(idxs))
        for rank, idx in enumerate(idxs, 1)
    )

    print(f"\n  {C.GR}✔  {ok}/{len(idxs)} capítulos descargados.{C.EN}")
    input("\n  Enter para volver...")


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 1 — DESCARGAR POR URL / NOMBRE
# ══════════════════════════════════════════════════════════════════════════════
def menu_download():
    banner("🔍  Buscar / Descargar")
    print(
        f"  Pegá una {C.CY}URL{C.EN} de bakamh.com o escribí el {C.CY}nombre{C.EN} del manga\n"
    )
    val = input(f"  {C.YE}➜ {C.EN}").strip()
    if not val:
        return

    # Solo ir directo si es una URL real con /manga/ en la ruta
    is_url = (
        "bakamh.com/manga/" in val
        or val.startswith("http")
        or (val.startswith("/") and "/manga/" in val)
    )
    if is_url:
        slug = parse_input(val)
        show_and_download(slug)
        return

    # Todo lo demás (nombre, palabra suelta, frase) → buscar siempre
    # search() carga TODO via AJAX en el primer llamado y cachea internamente
    banner("🔍  Buscar")
    print(f"  {C.YE}Buscando: {val}{C.EN}\n")
    results, total = search(val, 1)  # primer llamado carga todo

    if not results:
        banner("🔍  Resultados")
        print(f"  {C.RE}Sin resultados para: {val}{C.EN}")
        time.sleep(1.5)
        return

    page = 1
    while True:
        results, total = search(val, page)

        banner("🔍  Resultados")
        print(f"  {C.YE}{val}{C.EN}  {C.BL}│{C.EN}  pág {C.BO}{page}/{total}{C.EN}\n")

        if not results:
            print(f"  {C.RE}Sin resultados.{C.EN}")
            time.sleep(1.5)
            return

        print_list(results)
        print(
            f"\n  {C.BL}[n]{C.EN}sig  {C.BL}[p]{C.EN}ant  {C.BL}[q]{C.EN}volver  "
            f"{C.BL}[número]{C.EN} descargar"
        )
        cmd = input(f"\n  {C.YE}➜ {C.EN}").strip().lower()

        if cmd == "q":
            return
        elif cmd == "n" and page < total:
            page += 1
        elif cmd == "p" and page > 1:
            page -= 1
        elif cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(results):
                show_and_download(results[idx]["slug"])
                return


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 2 — CATÁLOGO
# ══════════════════════════════════════════════════════════════════════════════
def menu_catalog():
    banner("📂  Catálogo")
    print(f"  {C.YE}Cargando géneros...{C.EN}")
    genres = get_genres()

    genre_slug = ""
    genre_name = "Todos"

    if genres:
        banner("📂  Catálogo — Género")
        print(f"  {C.BO}  0.{C.EN}  Todos\n")
        for i, g in enumerate(genres):
            print(f"  {C.BO}{i + 1:>3}.{C.EN}  {g['name']}")
        sel = input(f"\n  {C.YE}Género (0=todos) ➜ {C.EN}").strip()
        if sel.isdigit() and int(sel) > 0:
            idx = int(sel) - 1
            if 0 <= idx < len(genres):
                genre_slug = genres[idx]["slug"]
                genre_name = genres[idx]["name"]
    else:
        print(f"  {C.YE}No se encontraron géneros, mostrando todos.{C.EN}")
        time.sleep(1)

    sort = "latest"
    all_items = []
    filtered = []
    filter_text = ""

    def reload():
        nonlocal all_items, filtered, filter_text
        banner(f"📂  {genre_name}")
        if genre_slug == "" and genres:
            print(
                f"  {C.YE}Cargando TODAS las series ({SORT_LABELS[sort]}){C.EN}\n"
                f"  {C.CY}Recorriendo {len(genres)} géneros — puede tardar varios minutos...{C.EN}\n"
            )
        else:
            print(f"  {C.YE}Cargando todas las series ({SORT_LABELS[sort]})...{C.EN}\n")
        all_items = get_all_catalog(
            genre_slug, sort, genres if genre_slug == "" else None
        )
        filter_text = ""
        filtered = all_items[:]

    reload()

    while True:
        banner(f"📂  {genre_name}")
        header = (
            f"  {C.PU}{genre_name}{C.EN}  {C.BL}│{C.EN}  {C.CY}{SORT_LABELS[sort]}{C.EN}"
            f"  {C.BL}│{C.EN}  {C.BO}{len(filtered)}{C.EN} series"
        )
        if filter_text:
            header += f"  {C.BL}│{C.EN}  filtro: {C.YE}{filter_text}{C.EN}"
        print(header + "\n")

        if not filtered:
            print(f"  {C.RE}Sin resultados.{C.EN}")
        else:
            print_list(filtered)

        print(
            f"\n  {C.BL}[f]{C.EN}filtrar  {C.BL}[s]{C.EN}orden  "
            f"{C.BL}[q]{C.EN}volver  {C.BL}[número]{C.EN} descargar"
        )
        cmd = input(f"\n  {C.YE}➜ {C.EN}").strip()

        if cmd.lower() == "q":
            return

        elif cmd.lower() == "f":
            ft = input(
                f"  {C.YE}Filtrar por nombre (vacío = mostrar todos) ➜ {C.EN}"
            ).strip()
            filter_text = ft
            if ft:
                filtered = [it for it in all_items if ft.lower() in it["title"].lower()]
            else:
                filtered = all_items[:]

        elif cmd.lower() == "s":
            banner("📂  Ordenar")
            for i, (k, v) in enumerate(SORT_LABELS.items()):
                print(f"  {C.BO}{i + 1}.{C.EN}  {v}")
            sc = input(f"\n  {C.YE}➜ {C.EN}").strip()
            if sc.isdigit():
                idx = int(sc) - 1
                keys = list(SORT_LABELS.keys())
                if 0 <= idx < len(keys) and keys[idx] != sort:
                    sort = keys[idx]
                    reload()

        elif cmd.isdigit():
            idx = int(cmd) - 1
            if filtered and 0 <= idx < len(filtered):
                show_and_download(filtered[idx]["slug"])


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def check_deps():
    missing = []
    if not _USE_CURL:
        missing.append("curl_cffi")
    if not _HAS_BS4:
        missing.append("beautifulsoup4")
    if missing:
        print(f"\n{C.RE}Faltan dependencias:{C.EN} {', '.join(missing)}")
        print(f"  pip install {' '.join(missing)}\n")
        sys.exit(1)


def main():
    check_deps()

    url_arg = ""
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--url" and i < len(sys.argv) - 1:
            url_arg = sys.argv[i + 1]
        elif not a.startswith("--"):
            url_arg = a

    if url_arg:
        slug = parse_input(url_arg)
        show_and_download(slug)
        return

    while True:
        banner()
        print(f"  {C.BO}1{C.EN}  🔍  Buscar / descargar por URL o nombre")
        print(f"  {C.BO}2{C.EN}  📂  Explorar catálogo por género")
        print(f"  {C.BO}3{C.EN}  ✖   Salir")
        print(f"\n  {C.BL}{'─' * 40}{C.EN}")
        print(
            f"  salida {C.CY}{OUTPUT_TYPE.upper()}{C.EN}"
            f"   imagen {C.CY}{USER_FORMAT}{C.EN}"
            f"   workers {C.CY}{MAX_WORKERS}{C.EN}"
        )

        op = input(f"\n  {C.YE}➜ {C.EN}").strip()
        if op == "1":
            menu_download()
        elif op == "2":
            menu_catalog()
        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YE}  Interrumpido.{C.EN}")
