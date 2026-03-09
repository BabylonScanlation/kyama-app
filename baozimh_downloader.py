"""
BAOZIMH DOWNLOADER v1.6
Fuentes:
  metadatos/búsqueda → baozimh.org
  lista capítulos    → baozimh.com / twmanga.com / mirrors
  imágenes           → webmota.com / kukuc.co / czmanga.com

Instalación:
    pip install requests pillow beautifulsoup4 lxml

Uso:
    python baozimh_downloader.py
"""

import os
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    Image = None
    HAS_PILLOW = False


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
SITE_ORG = "https://baozimh.org"

# Mirrors para lista de capítulos — probados en orden hasta encontrar uno activo
# Todos comparten el mismo slug y la misma estructura HTML
COM_MIRRORS = [
    "https://www.twmanga.com",  # ← más confiable, mismo slug que .com
    "https://www.baozimh.com",
    "https://baozimh.com",
    "https://www.webmota.com",
    "https://cn.webmota.com",
    "https://tw.webmota.com",
    "https://www.kukuc.co",
    "https://cn.kukuc.co",
    "https://www.czmanga.com",
]

OUTPUT_TYPE = "zip"  # 'zip' | 'cbz' | 'pdf'
USER_FORMAT = "webp"  # 'original' | 'jpg' | 'png' | 'webp'
DELETE_TEMP = True
MAX_WORKERS = 8
TIMEOUT = (15, 45)
RETRY_DELAY = 2.0
DEBUG = False  # True → imprime detalles de cada petición


# ══════════════════════════════════════════════════════════════════════════════
#  UI / COLORES
# ══════════════════════════════════════════════════════════════════════════════
class C:
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


def header() -> None:
    # _ = os.system("cls" if os.name == "nt" else "clear")
    print(f"{C.BLUE}╔══════════════════════════════════════════╗")
    print(f"║  {C.BOLD}BAOZIMH DOWNLOADER v1.6{C.END}{C.BLUE}                  ║")
    print(f"║  {C.DIM}baozimh.org / baozimh.com{C.END}{C.BLUE}                 ║")
    print(f"╚══════════════════════════════════════════╝{C.END}\n")


def bar(done: int, total: int, width: int = 32) -> str:
    pct = done / max(total, 1)
    fill = int(width * pct)
    return f"[{C.CYAN}{'█' * fill}{C.DIM}{'─' * (width - fill)}{C.END}] {done}/{total}"


def dbg(msg: str) -> None:
    if DEBUG:
        print(f"  {C.DIM}[dbg] {msg}{C.END}")


# ══════════════════════════════════════════════════════════════════════════════
#  SESIONES HTTP  (una por dominio para evitar contaminación de cookies)
# ══════════════════════════════════════════════════════════════════════════════
_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;"
    "q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Sesión para baozimh.org
SESSION_ORG: requests.Session = None
# Sesión para baozimh.com / webmota / mirrors (se crea con warmup propio)
SESSION_COM: requests.Session = None
# Mirror activo (se descubre en _find_active_mirror)
_ACTIVE_MIRROR: str = ""


def _make_session(base_url: str) -> requests.Session:
    """Crea sesión limpia y hace warmup GET al home para obtener cookies."""
    s = requests.Session()
    s.headers.update(_BASE_HEADERS)
    s.headers["Referer"] = base_url + "/"
    try:
        r = s.get(base_url + "/", timeout=8)
        dbg(f"warmup {base_url}: {r.status_code}")
    except Exception as e:
        dbg(f"warmup {base_url} falló: {e}")
    return s


def _find_active_mirror() -> str:
    """
    Prueba los mirrors en orden y devuelve el primero que responda con
    una página de cómic válida (tiene "page_direct" o "chapter_slot").
    """
    global SESSION_COM
    slug = "yirenzhixia-dongmantang"  # serie que existe en todos los mirrors

    for mirror in COM_MIRRORS:
        try:
            s = requests.Session()
            s.headers.update(_BASE_HEADERS)
            s.headers["Referer"] = mirror + "/"
            # Warmup
            s.get(mirror + "/", timeout=6)
            # Test con página de cómic conocida
            r = s.get(f"{mirror}/comic/{slug}", timeout=10)
            if r.status_code == 200 and (
                "page_direct" in r.text or "chapter_slot" in r.text
            ):
                dbg(f"mirror activo: {mirror}")
                SESSION_COM = s
                return mirror
        except Exception as e:
            dbg(f"mirror {mirror}: {e}")
        time.sleep(0.4)

    # Ningún mirror respondió — crear sesión dummy
    SESSION_COM = requests.Session()
    SESSION_COM.headers.update(_BASE_HEADERS)
    return ""


def _get_raw(
    session: requests.Session, url: str, referer: str = "", retries: int = 3
) -> Optional[bytes]:
    hdrs = {"Referer": referer} if referer else {}
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=TIMEOUT, headers=hdrs)
            dbg(f"GET {url[:80]} → {r.status_code} ({len(r.content)} B)")
            if r.status_code == 200 and r.content:
                return r.content
            if r.status_code in (403, 404):
                return None
        except requests.RequestException as e:
            dbg(f"GET {url[:60]} attempt {attempt + 1}: {e}")
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def fetch_org(url: str, referer: str = "") -> Optional[str]:
    raw = _get_raw(SESSION_ORG, url, referer or SITE_ORG)
    return raw.decode("utf-8", errors="replace") if raw else None


def fetch_com(url: str, referer: str = "", mirror: str = "") -> Optional[str]:
    """Intenta primero con SESSION_COM. Si falla, prueba otros mirrors."""
    base = mirror or _ACTIVE_MIRROR
    raw = _get_raw(SESSION_COM, url, referer or (base + "/"))
    if raw:
        return raw.decode("utf-8", errors="replace")

    # Fallback: probar otros mirrors con sesión temporal
    slug_path = re.sub(r"^https?://[^/]+", "", url)
    for m in COM_MIRRORS:
        if m == base:
            continue
        alt_url = m + slug_path
        try:
            s = requests.Session()
            s.headers.update(_BASE_HEADERS)
            s.headers["Referer"] = m + "/"
            s.get(m + "/", timeout=5)
            r = s.get(alt_url, timeout=TIMEOUT)
            if r.status_code == 200 and r.content:
                dbg(f"fallback mirror {m}: OK")
                return r.content.decode("utf-8", errors="replace")
        except Exception:
            pass
        time.sleep(0.3)
    return None


def fetch_json(
    url: str, session: requests.Session = None, referer: str = ""
) -> Optional[object]:
    s = session or SESSION_ORG
    hdrs = {"Accept": "application/json, */*", "Referer": referer or SITE_ORG + "/"}
    try:
        r = s.get(url, timeout=TIMEOUT, headers=hdrs)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ══════════════════════════════════════════════════════════════════════════════
#  BÚSQUEDA  —  baozimh.org /s?q=QUERY
# ══════════════════════════════════════════════════════════════════════════════
def search_series(query: str) -> list:
    url = f"{SITE_ORG}/s?q={requests.utils.quote(query)}"
    html = fetch_org(url)
    return _parse_cards(html) if html else []


def _parse_cards(html: str) -> list:
    soup = _soup(html)
    results = []
    seen = set()
    for a in soup.find_all("a", href=re.compile(r"^/manga/[^/]+$")):
        slug = re.match(r"^/manga/([^/]+)$", a.get("href", ""))
        if not slug:
            continue
        slug = slug.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        h3 = a.find(["h3", "h4", "p"])
        title = (
            h3.get_text(strip=True)
            if h3
            else (a.find("img") or {}).get("alt", "") or slug
        )
        if title:
            results.append({"slug": slug, "title": title})
    return results


# ══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO  —  secciones de baozimh.org
# ══════════════════════════════════════════════════════════════════════════════
_CAT_SECS = {
    "hots": (f"{SITE_ORG}/hots", "人气推荐"),
    "dayup": (f"{SITE_ORG}/dayup", "热门更新"),
    "cn": (f"{SITE_ORG}/manga-genre/cn", "国漫"),
    "kr": (f"{SITE_ORG}/manga-genre/kr", "韩漫"),
    "jp": (f"{SITE_ORG}/manga-genre/jp", "日漫"),
    "new": (f"{SITE_ORG}/newss", "最新上架"),
    "all": (f"{SITE_ORG}/manga", "全部"),
}


def fetch_catalog(section: str = "all") -> list:
    results = []
    seen = set()
    urls = (
        list(_CAT_SECS.values())
        if section == "all"
        else [_CAT_SECS.get(section, (f"{SITE_ORG}/{section}", section))]
    )
    for url, label in urls:
        html = fetch_org(url)
        if not html:
            print(f"  {C.YELLOW}[!] Sin acceso: {url}{C.END}")
            continue
        batch = _parse_cards(html)
        nuevos = sum(
            1
            for item in batch
            if item["slug"] not in seen
            and not seen.add(item["slug"])
            and results.append(item) is None
        )
        print(f"  {C.DIM}{label}: +{nuevos}  total={len(results)}{C.END}")
        time.sleep(0.3)
    return results


# ══════════════════════════════════════════════════════════════════════════════
#  METADATOS  —  baozimh.org /manga/{slug}  +  fallback mirror .com
# ══════════════════════════════════════════════════════════════════════════════
def parse_series_meta(slug: str) -> Optional[dict]:
    """
    Obtiene metadatos desde baozimh.org. Si el slug no existe en .org
    (porque viene de una URL de .com/webmota/twmanga), lo busca en el sitemap
    para obtener el slug equivalente en .org, o parsea los metadatos
    directamente desde el mirror .com.
    """
    # ── Intentar en .org con el slug tal cual ─────────────────────────────
    html_org = fetch_org(f"{SITE_ORG}/manga/{slug}")

    # Si .org devuelve 404, buscar el slug .org equivalente vía sitemap
    # (el sitemap del .com tiene slugs .com; el .org tiene su propio slug)
    # Estrategia: buscar en .org/s?q usando el slug como query → primer resultado
    if not html_org or f'"/manga/{slug}"' not in (html_org or ""):
        # Intentar buscar en .org por partes del slug
        parts = sorted(re.findall(r"[a-z]{6,}", slug), key=len, reverse=True)
        for part in parts[:2]:
            sq = fetch_org(f"{SITE_ORG}/s?q={requests.utils.quote(part)}")
            if sq:
                for a in _soup(sq).find_all("a", href=re.compile(r"^/manga/")):
                    org_slug = re.match(r"^/manga/([^/]+)$", a.get("href", ""))
                    if not org_slug:
                        continue
                    org_slug = org_slug.group(1)
                    candidate_html = fetch_org(f"{SITE_ORG}/manga/{org_slug}")
                    if candidate_html:
                        html_org = candidate_html
                        slug = org_slug
                        break
                if html_org and f'"/manga/{slug}"' in html_org:
                    break

    # ── Si .org no tiene nada, parsear desde el mirror .com ──────────────
    if not html_org:
        return _parse_meta_from_com(slug)

    soup = _soup(html_org)

    title = slug
    for node in soup.find_all("h1"):
        t = node.get_text(strip=True)
        if t and "包子" not in t and len(t) > 1:
            title = t
            break
    title = re.sub(r"\s*(完結|連載中|连载中|完结)\s*$", "", title).strip()

    autor = "  ".join(
        a.get_text(strip=True) for a in soup.select("a[href*='/manga-author/']")[:3]
    )
    status = (
        "完結 (Completo)"
        if "完結" in html_org
        else "連載 (En curso)"
        if ("連載" in html_org or "连载" in html_org)
        else ""
    )
    genres_raw = [
        a.get_text(strip=True).strip(" ,")
        for a in soup.select("a[href*='/manga-genre/'], a[href*='/manga-tag/']")
    ]
    genres = list(dict.fromkeys(g for g in genres_raw if g))[:8]

    summary = ""
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        if len(t) > 30 and "包子" not in t and "下载" not in t:
            summary = t[:500]
            break

    return {
        "slug": slug,
        "title": title,
        "autor": autor,
        "status": status,
        "genres": genres,
        "summary": summary,
        "url": f"{SITE_ORG}/manga/{slug}",
    }


def _parse_meta_from_com(slug: str) -> Optional[dict]:
    """
    Parsea metadatos directamente desde el mirror .com cuando .org no tiene el slug.
    La estructura de .com es diferente pero contiene la misma info.
    """
    mirror = _ACTIVE_MIRROR or COM_MIRRORS[0]
    html = fetch_com(f"{mirror}/comic/{slug}", referer=mirror + "/")
    if not html:
        return None
    soup = _soup(html)

    # Título — en .com está en h1 o en el <title>
    title = slug
    for node in soup.find_all("h1"):
        t = node.get_text(strip=True)
        # Quitar sufijo del sitio
        t = re.sub(r"\s*[-–|].*包子.*$", "", t).strip()
        t = re.sub(r"\s*(完結|連載中|连载中|完结)\s*$", "", t).strip()
        if t and len(t) > 1:
            title = t
            break
    if title == slug:
        # Intentar desde <title>
        tag = soup.find("title")
        if tag:
            t = tag.get_text(strip=True)
            t = re.sub(r"\s*[-–|].*$", "", t).strip()
            if t and len(t) > 1:
                title = t

    # Autor — .com usa texto libre en la sección de info
    autor = ""
    for sel in ["#comic-author", ".comic-author", ".author"]:
        node = soup.select_one(sel)
        if node:
            autor = node.get_text(strip=True)
            break
    if not autor:
        # Buscar "作者" en el texto
        m = re.search(r"作者[：:]\s*([^\n<]{2,40})", html)
        if m:
            autor = m.group(1).strip()

    # Estado
    status = (
        "完結 (Completo)"
        if ("完結" in html or "完结" in html)
        else "連載 (En curso)"
        if ("連載" in html or "连载" in html)
        else ""
    )

    # Géneros — .com usa links /classify o /s?type=
    genres = list(
        dict.fromkeys(
            a.get_text(strip=True).strip(" ,")
            for a in soup.select("a[href*='classify'], a[href*='type=']")
            if a.get_text(strip=True).strip(" ,")
        )
    )[:8]

    # Sinopsis
    summary = ""
    for sel in [".comic-intro", ".intro", "#comic-intro", ".description"]:
        node = soup.select_one(sel)
        if node:
            summary = node.get_text(strip=True)[:500]
            break
    if not summary:
        for p in soup.find_all("p"):
            t = p.get_text(strip=True)
            if len(t) > 30:
                summary = t[:500]
                break

    return {
        "slug": slug,
        "title": title,
        "autor": autor,
        "status": status,
        "genres": genres,
        "summary": summary,
        "url": f"{mirror}/comic/{slug}",
    }


# ══════════════════════════════════════════════════════════════════════════════
#  LISTA DE CAPÍTULOS  —  baozimh.com /comic/{slug}
# ══════════════════════════════════════════════════════════════════════════════
_SKIP_TITLES = {
    "開始閱讀",
    "开始阅读",
    "收藏",
    "立即閱讀",
    "立即阅读",
    "查看所有章節",
    "查看所有章节",
}
# Excluir links del header/nav
_NAV_HREFS = re.compile(
    r"/(hots|dayup|newss|manga|donate|bookmark|classify|list|app_gb|user/bookshelf)"
)


_BAD_SLUGS = {
    "sitemap",
    "classify",
    "hots",
    "dayup",
    "newss",
    "bookmark",
    "list",
    "app_gb",
    "donate",
    "privacy",
    "dmca",
    "user",
    "search",
    "comic",
    "manga",
    "about",
    "contact",
    "index",
}

# Cache del sitemap — descargado una vez por sesión (~240 KB con todos los comic_id)
_SITEMAP_CACHE: Optional[str] = None


def _get_sitemap() -> str:
    """
    Descarga y cachea el sitemap de baozimh.com.
    El sitemap contiene links /comic/SLUG (NO page_direct).
    Se descarga una sola vez por sesión.
    """
    global _SITEMAP_CACHE
    if _SITEMAP_CACHE:
        return _SITEMAP_CACHE
    # Deduplicar mirrors para no descargar el mismo dominio dos veces
    tried: set = set()
    order = ([_ACTIVE_MIRROR] if _ACTIVE_MIRROR else []) + COM_MIRRORS
    for base in order:
        if base in tried:
            continue
        tried.add(base)
        raw = _get_raw(
            SESSION_COM, f"{base}/comic/sitemap", referer=base + "/", retries=2
        )
        if not raw or len(raw) < 50000:
            continue
        html = raw.decode("utf-8", errors="replace")
        # Verificar que es el sitemap real: tiene links /comic/SLUG (no page_direct)
        n_comic_links = html.count("/comic/")
        if n_comic_links > 100:
            dbg(f"sitemap OK [{base}]: {n_comic_links} links /comic/")
            _SITEMAP_CACHE = html
            return html
    return ""


# Cache plana de slugs del sitemap
_SITEMAP_SLUGS: list = []


def _get_sitemap_slugs() -> list:
    """Construye y cachea la lista de todos los slugs del sitemap."""
    global _SITEMAP_SLUGS
    if _SITEMAP_SLUGS:
        return _SITEMAP_SLUGS
    html = _get_sitemap()
    if not html:
        return []
    # Extraer slugs de href="/comic/SLUG"
    _SITEMAP_SLUGS = list(
        dict.fromkeys(
            s
            for s in re.findall(r'href=["\'][^"\']*?/comic/([^"\'\\s/?#>]{4,})', html)
            if s not in _BAD_SLUGS and not s.startswith("http")
        )
    )
    # Fallback más simple si el anterior no encuentra nada
    if not _SITEMAP_SLUGS:
        _SITEMAP_SLUGS = list(
            dict.fromkeys(
                s
                for s in re.findall(r"/comic/([a-z0-9][a-z0-9\-]{4,})", html)
                if s not in _BAD_SLUGS
            )
        )
    dbg(f"sitemap: {len(_SITEMAP_SLUGS)} slugs indexados")
    return _SITEMAP_SLUGS


def _slug_from_sitemap(needle: str) -> str:
    """
    Busca el primer slug del sitemap que contenga needle como subcadena.
    """
    if not needle or len(needle) < 4:
        return ""
    slugs = _get_sitemap_slugs()
    if not slugs:
        return ""
    nl = needle.lower()
    for slug in slugs:
        if nl in slug.lower():
            dbg(f"sitemap: '{needle}' -> '{slug}'")
            return slug
    return ""


def _com_comic_exists(mirror: str, slug: str) -> bool:
    """Verifica que el slug existe en el sitemap o en una petición directa."""
    if not slug or slug in _BAD_SLUGS or len(slug) < 4:
        return False
    if _SITEMAP_CACHE and f"comic_id={slug}" in _SITEMAP_CACHE:
        return True
    url = f"{mirror}/comic/{slug}"
    raw = _get_raw(SESSION_COM, url, referer=mirror + "/", retries=1)
    if not raw:
        return False
    html = raw.decode("utf-8", errors="replace")
    return f"comic_id={slug}" in html


def _resolve_com_slug(org_slug: str, title: str = "") -> str:
    """
    Resuelve el slug de baozimh.com a partir del slug de baozimh.org.

    Por que difieren:
      .org: gaowuefbc9adengliuweilaiyimonian
            "gaowu" + efbc9a (= fullwidth colon) + "dengliuweilaiyimonian"
      .com: dengluweilaiyimonian-changpanyongzhe

    El SUFIJO tras el primer bloque hex del slug .org siempre aparece
    en el slug .com. Lo buscamos en el sitemap (una descarga de ~240 KB).

    Estrategias:
    1. Slug directo (si son iguales)
    2. Sufijo tras primer bloque hex -> buscar en sitemap
    3. Slug completo limpio de hex -> buscar en sitemap
    4. Partes latinas mas largas del slug -> buscar en sitemap
    """
    mirror = _ACTIVE_MIRROR or COM_MIRRORS[0]

    # 1. Slug directo
    if _com_comic_exists(mirror, org_slug):
        return org_slug

    # 2. Sufijo después del primer bloque hex unicode (pinyin tras carácter especial)
    #    "gaowuefbc9adengliuweilaiyimonian" → sufijo = "dengliuweilaiyimonian"
    #    El pinyin puede diferir ligeramente entre .org y .com, así que probamos
    #    el sufijo desde distintos offsets para encontrar la parte que coincide.
    hex_parts = re.split(r"[0-9a-f]{4,6}", org_slug, maxsplit=1)
    if len(hex_parts) == 2:
        suffix = hex_parts[1].lstrip("-")
        # Probar el sufijo desde el inicio y desde distintos offsets
        # (por diferencias de pinyin como "dengliuwei" vs "dengluwei")
        for start in range(0, max(1, len(suffix) - 8), 1):
            part = suffix[start:]
            if len(part) < 8:
                break
            found = _slug_from_sitemap(part)
            if found:
                dbg(f"slug via sufijo[{start}:] '{part}': {org_slug} -> {found}")
                return found

    # 3. Slug completo sin bloques hex
    clean = re.sub(r"[0-9a-f]{4,6}", "", org_slug)
    clean = re.sub(r"-{2,}", "-", clean).strip("-")
    if clean != org_slug and len(clean) >= 5:
        # También probar desde distintos offsets del clean
        for start in range(0, max(1, len(clean) - 8), 1):
            part = clean[start:]
            if len(part) < 8:
                break
            found = _slug_from_sitemap(part)
            if found:
                dbg(f"slug via clean[{start}:] '{part}': {org_slug} -> {found}")
                return found

    # 4. Partes latinas del slug (de mayor a menor)
    parts = sorted(re.findall(r"[a-z]{5,}", org_slug), key=len, reverse=True)
    for part in parts:
        if part in _BAD_SLUGS:
            continue
        found = _slug_from_sitemap(part)
        if found and found != org_slug:
            dbg(f"slug via part '{part}': {org_slug} -> {found}")
            return found

    dbg(f"slug no resuelto: {org_slug}")
    return org_slug


def get_chapter_list(slug: str, title: str = "") -> list:
    """
    Obtiene todos los capítulos desde baozimh.com (o mirror activo).

    El slug de .org puede ser diferente al de .com — se resuelve automáticamente.
    Formato de links en .com:
      /user/page_direct?comic_id=SLUG&section_slot=SS&chapter_slot=CS
    """
    mirror = _ACTIVE_MIRROR
    if not mirror:
        print(f"  {C.YELLOW}[!] Ningún mirror activo.{C.END}")
        return []

    # Resolver slug correcto para .com
    com_slug = _resolve_com_slug(slug, title)
    if com_slug != slug:
        dbg(f"usando slug .com: {com_slug}")

    url = f"{mirror}/comic/{com_slug}"
    html = fetch_com(url, referer=mirror + "/", mirror=mirror)

    if not html:
        dbg(f"sin respuesta de {url}")
        return []

    dbg(f"chapter list HTML: {len(html)} chars")
    chapters = _parse_com_chapters(html, com_slug)

    # Fallback: probar otros mirrors con el slug resuelto
    if not chapters:
        for alt in COM_MIRRORS:
            if alt == mirror:
                continue
            html2 = fetch_com(f"{alt}/comic/{com_slug}", referer=alt + "/", mirror=alt)
            if html2:
                chapters = _parse_com_chapters(html2, com_slug)
                if chapters:
                    dbg(f"caps en mirror alternativo: {alt}")
                    break

    return chapters


def _parse_com_chapters(html: str, slug: str) -> list:
    """
    Parsea el HTML de baozimh.com/comic/SLUG extrayendo todos los capítulos.
    """
    soup = _soup(html)
    chapters = []
    seen = set()

    for a in soup.find_all("a", href=re.compile(r"page_direct")):
        href = a.get("href", "")
        if _NAV_HREFS.search(href):
            continue

        qs = parse_qs(urlparse(href).query)
        comic_id = qs.get("comic_id", [""])[0]
        ss = qs.get("section_slot", ["0"])[0]
        cs = qs.get("chapter_slot", ["-1"])[0]

        if cs == "-1":
            continue
        # Aceptar si comic_id coincide con el slug (puede ser .com o .org)
        # o si comic_id está vacío en el href
        if comic_id and comic_id != slug:
            # Tolerar slugs con diferente sufijo de autor
            # (mismo prefijo de slug)
            base_slug = slug.split("-")[0]
            base_cid = comic_id.split("-")[0]
            if base_slug.lower() != base_cid.lower() and len(base_slug) > 5:
                continue

        title = a.get_text(strip=True)
        if not title or title in _SKIP_TITLES:
            continue

        key = f"{ss}_{cs}"
        if key in seen:
            continue
        seen.add(key)

        chapters.append(
            {
                "title": title,
                "section_slot": ss,
                "chapter_slot": cs,
                "key": key,  # "SS_CS" — usado para URL de imagen
            }
        )

    def _sort_key(c):
        try:
            return (int(c["section_slot"]), int(c["chapter_slot"]))
        except ValueError:
            return (0, 0)

    chapters.sort(key=_sort_key)
    dbg(f"capítulos parseados: {len(chapters)}")
    return chapters


# ══════════════════════════════════════════════════════════════════════════════
#  EXTRACCIÓN DE IMÁGENES
# ══════════════════════════════════════════════════════════════════════════════
_CLOUDME_RE = re.compile(r"https://cloudme\.one/refs/(\d+)/(\d+)", re.I)
_IMG_RE = re.compile(
    r'(https?://[^\s"\'<>]+\.(?:jpe?g|png|webp)(?:\?[^\s"\'<>]*)?)',
    re.I,
)
_EXCLUDE_IMG = (
    "/logo",
    "/icon",
    "/ads",
    "ad/",
    "cover/",
    "g-mh",
    ".gif",
    "monotag",
    "18mh",
    "mangabuddy.in",
)


def _valid_img(u: str) -> bool:
    return bool(u and u.startswith("http") and not any(x in u for x in _EXCLUDE_IMG))


def _dedup(lst: list) -> list:
    seen: set = set()
    return [u for u in lst if not (u in seen or seen.add(u))]


def _try_cloudme(file_id: str, source: str) -> Optional[bytes]:
    for url in [
        f"https://cloudme.one/api/download/{file_id}",
        f"https://cloudme.one/api/v1/file/{file_id}",
        f"https://cloudme.one/download/{source}/{file_id}",
        f"https://s3.cloudme.one/{source}/{file_id}.cbz",
    ]:
        try:
            r = SESSION_ORG.get(
                url, timeout=TIMEOUT, headers={"Referer": "https://cloudme.one/"}
            )
            ct = r.headers.get("Content-Type", "")
            if r.status_code == 200 and (
                "zip" in ct or "octet-stream" in ct or r.content[:2] == b"PK"
            ):
                return r.content
        except Exception:
            pass
    return None


def _images_from_mirror(slug: str, key: str) -> list:
    """
    Descarga la página del lector para un capítulo y extrae las URLs de imagen.
    URL: {mirror}/comic/chapter/{slug}/{key}.html
    donde key = "SS_CS"
    """
    for base in ([_ACTIVE_MIRROR] if _ACTIVE_MIRROR else []) + COM_MIRRORS:
        url = f"{base}/comic/chapter/{slug}/{key}.html"
        raw = _get_raw(SESSION_COM, url, referer=base + "/")
        if not raw:
            continue
        html = raw.decode("utf-8", errors="replace")
        imgs = _extract_content_imgs(html)
        if imgs:
            dbg(f"imgs de {base}: {len(imgs)}")
            return imgs
    return []


def _extract_content_imgs(html: str) -> list:
    soup = _soup(html)
    candidates = []

    for img in soup.find_all("img"):
        for attr in ("data-src", "data-original", "src"):
            u = img.get(attr, "")
            if u and not u.startswith("data:") and _valid_img(u):
                if not u.startswith("http"):
                    u = "https:" + u
                candidates.append(u)
                break

    if not candidates:
        for u in _IMG_RE.findall(html):
            if _valid_img(u):
                candidates.append(u)

    # Quitar portadas / UI / thumbnails
    return _dedup(
        [
            u
            for u in candidates
            if not any(x in u for x in ("/cover/", "/ui/", "logo", "icon"))
        ]
    )


def extract_chapter_images(chap: dict, slug: str, org_url: str = "") -> tuple:
    """
    Devuelve (mode, data):
      ('cbz',    bytes)     → CBZ directo
      ('images', list[str]) → URLs de páginas
      ('none',   None)      → fallo
    """
    # A: CBZ directo de cloudme (solo disponible si tenemos URL .org)
    if org_url:
        html = fetch_org(org_url)
        if html:
            m = _CLOUDME_RE.search(html)
            if m:
                source, fid = m.group(1), m.group(2)
                print(
                    f"\n    {C.DIM}[cloudme {source}/{fid}]{C.END}", end="", flush=True
                )
                cbz = _try_cloudme(fid, source)
                if cbz:
                    return ("cbz", cbz)
                print(f"  {C.DIM}→ no disponible{C.END}", end="", flush=True)

    # B: imágenes desde mirror de baozimh.com
    key = chap.get("key", "")
    if key:
        imgs = _images_from_mirror(slug, key)
        if imgs:
            return ("images", imgs)

    return ("none", None)


# ══════════════════════════════════════════════════════════════════════════════
#  DESCARGA DE IMÁGENES
# ══════════════════════════════════════════════════════════════════════════════
def _ext_for(url: str) -> str:
    if HAS_PILLOW and USER_FORMAT != "original":
        return USER_FORMAT
    ext = os.path.splitext(url.split("?")[0])[-1].lower().lstrip(".")
    return ext if ext in ("jpg", "jpeg", "png", "webp") else "jpg"


def save_image(raw: bytes, path: str) -> None:
    if not HAS_PILLOW or USER_FORMAT == "original" or Image is None:
        with open(path, "wb") as f:
            f.write(raw)
        return
    try:
        img = Image.open(BytesIO(raw))
        if USER_FORMAT in ("jpg", "jpeg") and img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        img.save(path, quality=92)
    except Exception:
        with open(path, "wb") as f:
            f.write(raw)


def dl_worker(args: tuple) -> bool:
    url, folder, idx = args
    if not url or not url.startswith("http"):
        return False
    ext = _ext_for(url)
    path = os.path.join(folder, f"{idx + 1:03d}.{ext}")
    if os.path.exists(path):
        return True
    ref = _ACTIVE_MIRROR or SITE_ORG
    for attempt in range(3):
        try:
            r = SESSION_COM.get(url, timeout=TIMEOUT, headers={"Referer": ref})
            if r.status_code == 200 and r.content:
                save_image(r.content, path)
                return True
        except Exception:
            pass
        time.sleep(RETRY_DELAY * (attempt + 1))
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  EMPAQUETADO
# ══════════════════════════════════════════════════════════════════════════════
def pack_folder(src: str, out: str, fmt: str) -> None:
    files = sorted(
        os.path.join(src, f)
        for f in os.listdir(src)
        if os.path.isfile(os.path.join(src, f)) and not f.endswith(".json")
    )
    if not files:
        return
    if fmt == "pdf" and HAS_PILLOW and Image is not None:
        pages = []
        for p in files:
            try:
                pages.append(Image.open(p).convert("RGB"))
            except Exception:
                pass
        if pages:
            pages[0].save(out, save_all=True, append_images=pages[1:])
    else:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, os.path.basename(f))


# ══════════════════════════════════════════════════════════════════════════════
#  FLUJO PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


def _parse_positions(s: str, length: int) -> list:
    idxs: set = set()
    s = s.replace(" ", "")
    for part in s.split(","):
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                idxs.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        elif part.isdigit():
            idxs.add(int(part))
    return sorted(i - 1 for i in idxs if 1 <= i <= length)


def show_and_download(slug: str) -> None:
    print(f"\n{C.CYAN}[*] Cargando '{slug}'…{C.END}")

    meta = parse_series_meta(slug) or {
        "slug": slug,
        "title": slug,
        "autor": "",
        "status": "",
        "genres": [],
        "summary": "",
        "url": "",
    }
    title = meta["title"]

    print(f"  {C.DIM}Obteniendo lista de capítulos desde mirror…{C.END}", end="\r")
    chapters = get_chapter_list(slug, title=title)

    print(f"  {' ' * 55}", end="\r")  # limpiar línea
    print(f"\n  {C.BOLD}{C.GREEN}{title}{C.END}")
    if meta["autor"]:
        print(f"  Autor  : {meta['autor']}")
    print(f"  Estado : {meta['status'] or 'N/A'}")
    if meta["genres"]:
        print(f"  Géneros: {', '.join(meta['genres'][:6])}")
    if meta["summary"]:
        s = meta["summary"]
        print(f"  Sinopsis: {(s[:100] + '…') if len(s) > 100 else s}")
    print(f"  Caps   : {C.GREEN}{len(chapters)}{C.END}")

    if not chapters:
        print(f"\n  {C.YELLOW}[!] 0 capítulos detectados.")
        print(f"  Mirror activo: {_ACTIVE_MIRROR or 'ninguno'}")
        print(f"  Prueba activar DEBUG=True en el script para diagnosticar.{C.END}")
        return

    # ── Selección ─────────────────────────────────────────────────────────
    PAGE = 20
    show_off = 0
    selection = ""
    while True:
        end_idx = min(show_off + PAGE, len(chapters))
        print(f"\n  {C.PURPLE}{'─' * 58}{C.END}")
        for i in range(show_off, end_idx):
            c = chapters[i]
            print(f"  {C.BOLD}{i + 1:4d}.{C.END} {c['title'][:58]}")
        print(f"  {C.PURPLE}{'─' * 58}{C.END}")
        nav = ""
        if end_idx < len(chapters):
            nav += f"  {C.CYAN}n{C.END}=sig  "
        if show_off > 0:
            nav += f"  {C.CYAN}p{C.END}=ant"
        if nav:
            print(nav)

        raw = input(
            f"\n  {C.YELLOW}Caps ('1', '3-5,9', 'all', q=volver) ➜ {C.END}"
        ).strip()
        if raw.lower() == "n" and end_idx < len(chapters):
            show_off += PAGE
        elif raw.lower() == "p" and show_off > 0:
            show_off -= PAGE
        elif raw.lower() == "q":
            return  # volver sin error
        elif raw == "":
            continue
        else:
            selection = raw
            break

    selected = (
        list(chapters)
        if selection.lower() == "all"
        else [chapters[i] for i in _parse_positions(selection, len(chapters))]
    )
    if not selected:
        print(f"{C.RED}[!] Selección vacía.{C.END}")
        return

    print(f"\n  {C.BOLD}Seleccionados:{C.END}")
    for i, c in enumerate(selected, 1):
        print(f"    {i}. {c['title'][:65]}")

    if (
        input(f"\n  {C.YELLOW}¿Confirmar? [Enter=sí / n=no] ➜ {C.END}").strip().lower()
        == "n"
    ):
        return

    _run_downloads(selected, title, slug)


def _run_downloads(selected: list, title: str, slug: str) -> None:
    out_folder = _safe(f"{title} [{slug[:40]}]")
    os.makedirs(out_folder, exist_ok=True)
    ext_out = OUTPUT_TYPE.lower()
    ok = 0

    print(f"\n{C.CYAN}[*] Descargando {len(selected)} cap(s)…{C.END}\n")

    for i, chap in enumerate(selected, 1):
        lbl = f"[{i}/{len(selected)}] {chap['title'][:50]}"
        print(f"  {C.BOLD}{lbl}{C.END}", end=" ", flush=True)

        mode, result = extract_chapter_images(chap, slug, chap.get("org_url", ""))

        key = chap.get("key", "") or f"{i}"
        safe_t = _safe(chap["title"]) or f"cap_{key}"
        out_f = os.path.join(out_folder, f"{safe_t}.{ext_out}")

        if mode == "cbz" and result:
            print(f"\n    {C.GREEN}✓ CBZ directo{C.END}")
            with open(out_f, "wb") as f:
                f.write(result)
            ok += 1
            print(f"    {C.GREEN}→ {out_f}{C.END}")

        elif mode == "images" and result:
            imgs = result
            print(f"\n    → {len(imgs)} págs", flush=True)
            tmp = os.path.join(out_folder, f"_tmp_{key}")
            os.makedirs(tmp, exist_ok=True)
            done = 0
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
                futures = {
                    exe.submit(dl_worker, (url, tmp, idx)): idx
                    for idx, url in enumerate(imgs)
                }
                for _ in as_completed(futures):
                    done += 1
                    sys.stdout.write(f"\r    {bar(done, len(imgs))}")
                    sys.stdout.flush()
            print()
            pack_folder(tmp, out_f, ext_out)
            if DELETE_TEMP:
                shutil.rmtree(tmp, ignore_errors=True)
            ok += 1
            print(f"    {C.GREEN}✓ → {out_f}{C.END}")

        else:
            print(f"\n    {C.RED}× Sin imágenes  (key={key}){C.END}")

    print(f"\n{C.GREEN}[+] {ok}/{len(selected)} completados  → {out_folder}/{C.END}")


# ══════════════════════════════════════════════════════════════════════════════
#  OPCIÓN 1 — Entrada universal
# ══════════════════════════════════════════════════════════════════════════════
def option1() -> None:
    raw = input(f"  {C.CYAN}URL, nombre o slug: {C.END}").strip()
    if not raw:
        return

    # URL capítulo .org
    if re.search(r"baozimh\.org/manga/[^/]+/\d+-\d+-\d+", raw):
        m = re.search(r"baozimh\.org/manga/([^/]+)/(\d+)-(\d+)-(\d+)", raw)
        if m:
            slug = m.group(1)
            seq = m.group(4)
            html = fetch_org(raw)
            title = f"第{seq}話"
            if html:
                for node in _soup(html).find_all("h1"):
                    t = node.get_text(strip=True)
                    if t and "包子" not in t:
                        title = t
                        break
            _run_downloads([{"title": title, "key": "", "org_url": raw}], slug, slug)
        return

    # URL serie .org
    m = re.search(r"baozimh\.org/manga/([^/?#]+)", raw)
    if m:
        show_and_download(m.group(1))
        return

    # URL de cualquier mirror conocido (/comic/SLUG)
    m = re.search(
        r"(?:baozimh\.com|webmota\.com|twmanga\.com|kukuc\.co|czmanga\.com)"
        r"/comic/([^/?#\s]+)",
        raw,
    )
    if m:
        show_and_download(m.group(1))
        return

    # Slug directo
    if re.match(r"^[a-z0-9][a-z0-9\-]+$", raw):
        test = parse_series_meta(raw)
        if test and test["title"] != raw:
            show_and_download(raw)
            return

    # Búsqueda por nombre
    print(f"  {C.CYAN}[*] Buscando '{raw}'…{C.END}")
    results = search_series(raw)
    if not results:
        print(f"  {C.RED}Sin resultados.{C.END}")
        time.sleep(2)
    else:
        results_menu(results, raw)


# ══════════════════════════════════════════════════════════════════════════════
#  OPCIÓN 2 — Catálogo con submenú
# ══════════════════════════════════════════════════════════════════════════════
_CAT_OPTS = {
    "1": ("hots", "人气推荐"),
    "2": ("dayup", "热门更新"),
    "3": ("cn", "国漫"),
    "4": ("kr", "韩漫"),
    "5": ("jp", "日漫"),
    "6": ("new", "最新上架"),
    "7": ("all", "Todo"),
}


def catalog_menu() -> None:
    while True:
        header()
        print(f"  {C.PURPLE}{C.BOLD}Catálogo{C.END}")
        for k, (_, lbl) in _CAT_OPTS.items():
            print(f"  {C.BOLD}{k}.{C.END} {lbl}")
        print(f"  {C.BOLD}q.{C.END} Volver")

        op = input(f"\n  {C.YELLOW}Sección ➜ {C.END}").strip().lower()
        if op == "q":
            break
        if op not in _CAT_OPTS:
            continue
        section, lbl = _CAT_OPTS[op]
        print(f"\n  {C.CYAN}[*] Cargando '{lbl}'…{C.END}")
        results = fetch_catalog(section)
        if not results:
            print(f"  {C.RED}Sin resultados.{C.END}")
            time.sleep(2)
            continue
        filt = input(f"\n  {C.CYAN}Filtrar (Enter=ver todos): {C.END}").strip().lower()
        if filt:
            results = [
                r
                for r in results
                if filt in r["title"].lower() or filt in r["slug"].lower()
            ]
        if results:
            results_menu(results, lbl if not filt else filt)
        else:
            print(f"  {C.RED}Sin resultados para '{filt}'.{C.END}")
            time.sleep(2)


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ DE RESULTADOS
# ══════════════════════════════════════════════════════════════════════════════
def results_menu(results: list, label: str) -> None:
    PAGE = 20
    page = 0
    while True:
        header()
        start = page * PAGE
        end = min(start + PAGE, len(results))
        print(f"  {C.PURPLE}'{label}'  ({start + 1}–{end} / {len(results)}){C.END}")
        print(f"  {'━' * 58}")
        for i, r in enumerate(results[start:end]):
            num = start + i + 1
            print(f"  {C.BOLD}{num:3d}.{C.END} {r['title'][:52]}")
            print(f"       {C.DIM}{r['slug']}{C.END}")
        print(f"  {'━' * 58}")
        nav = []
        if end < len(results):
            nav.append(f"{C.CYAN}n{C.END}=sig")
        if page > 0:
            nav.append(f"{C.CYAN}p{C.END}=ant")
        nav.append(f"{C.CYAN}q{C.END}=volver")
        print("  " + "  ".join(nav) + "  o número")

        sel = input(f"\n  {C.YELLOW}➜ {C.END}").strip().lower()
        if sel == "n" and end < len(results):
            page += 1
        elif sel == "p" and page > 0:
            page -= 1
        elif sel == "q":
            break
        elif sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(results):
                show_and_download(results[idx]["slug"])
                input(f"\n  {C.CYAN}Enter para continuar…{C.END}")
                break


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    global SESSION_ORG, SESSION_COM, _ACTIVE_MIRROR

    print(f"{C.DIM}Iniciando sesiones…{C.END}", end=" ", flush=True)
    SESSION_ORG = _make_session(SITE_ORG)
    print(f"org ✓", end="  ", flush=True)

    print(f"buscando mirror activo…{C.END}", end=" ", flush=True)
    _ACTIVE_MIRROR = _find_active_mirror()
    if _ACTIVE_MIRROR:
        print(f"{C.GREEN}{_ACTIVE_MIRROR}{C.END}")
    else:
        print(f"{C.YELLOW}ninguno disponible (solo cloudme/búsqueda){C.END}")

    while True:
        header()
        mirror_lbl = _ACTIVE_MIRROR.replace("https://", "") if _ACTIVE_MIRROR else "—"
        print(f"  {C.PURPLE}{C.BOLD}Menú Principal{C.END}")
        print(f"  ├─ {C.BOLD}1.{C.END} Buscar / URL / Slug")
        print(f"  │     {C.DIM}↳ nombre, slug, URL de serie, URL de capítulo{C.END}")
        print(
            f"  ├─ {C.BOLD}2.{C.END} Catálogo  {C.DIM}(cn / kr / jp / populares){C.END}"
        )
        print(f"  └─ {C.BOLD}3.{C.END} Salir")
        print(
            f"\n  {C.DIM}Config: {OUTPUT_TYPE.upper()}  {USER_FORMAT.upper()}"
            f"  Mirror: {mirror_lbl}{C.END}"
        )

        op = input(f"\n  {C.YELLOW}Opción ➜ {C.END}").strip()
        if op == "1":
            option1()
            input(f"\n  {C.CYAN}Enter para continuar…{C.END}")
        elif op == "2":
            catalog_menu()
        elif op == "3":
            print(f"\n  {C.GREEN}¡Hasta luego!{C.END}\n")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Interrumpido.{C.END}\n")
