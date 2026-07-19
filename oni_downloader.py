"""
╔══════════════════════════════════════════╗
║  MANGA-ONI DOWNLOADER  v1.0              ║
║       manga-oni.com — MangaOni           ║
╚══════════════════════════════════════════╝

Dependencias:
    pip install requests beautifulsoup4 pillow

Uso:
    python oni_downloader.py
    python oni_downloader.py --debug
    python oni_downloader.py --url https://manga-oni.com/manhwa/lets-play
"""
from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG  (editar aquí)
# ══════════════════════════════════════════════════════════════════════════════
OUTPUT_DIR = "oni_downloads"
OUTPUT_TYPE = "zip"       # zip | cbz | pdf
USER_FORMAT = "webp"  # original | jpg | png | webp
DELETE_TEMP = True
MAX_WORKERS = 8
REQUEST_DELAY = 0.3

# ══════════════════════════════════════════════════════════════════════════════
#  IMPORTS
# ══════════════════════════════════════════════════════════════════════════════
import os
import subprocess
if os.name == 'nt':
    subprocess.run([""], shell=True)

import base64
import io
import json
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, unquote

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DEBUG = "--debug" in sys.argv

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

try:
    import requests as _req
    _HAS_REQ = True
except ImportError:
    _req = None
    _HAS_REQ = False

# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════
BASE_URL = "https://manga-oni.com"
CDN_HOST = "https://oni.ntr-files.online"

# Tipos de serie reconocidos en URLs
SERIES_TYPES = ("manga", "manhwa", "manhua", "novela")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
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


def banner(sub=""):
    sub = str(sub or "")
    print()
    print(f"{C.BL}╔══════════════════════════════════════════╗{C.EN}")
    print(
        f"{C.BL}║{C.EN}  {C.BO}{C.PU}MANGA-ONI DOWNLOADER{C.EN}  {C.CY}v1.0{C.EN}           {C.BL}║{C.EN}"
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
SESSION = _req.Session() if _HAS_REQ else None  # type: ignore
if SESSION:
    _adp = _req.adapters.HTTPAdapter(  # type: ignore
        pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS
    )
    SESSION.mount("http://", _adp)
    SESSION.mount("https://", _adp)
    SESSION.headers.update(HEADERS)


def _get(url, params=None, referer=None, retries=3):
    if not SESSION:
        return None
    hdrs = {}
    if referer:
        hdrs["Referer"] = referer
    dbg(f"GET {url}" + (f" ?{params}" if params else ""))
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, headers=hdrs, timeout=25)
            dbg(f"  {r.status_code}  len={len(r.content)}")
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


def _soup(html):
    if not _HAS_BS4:
        err("beautifulsoup4 no instalado: pip install beautifulsoup4")
        return None
    return BeautifulSoup(html, "html.parser")  # type: ignore


# ══════════════════════════════════════════════════════════════════════════════
#  PARSING DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════
def parse_input(raw):
    """
    Extrae (tipo, slug) de una URL o entrada de texto.
    tipo = manga | manhwa | manhua | novela
    """
    raw = raw.strip().rstrip("/")

    # URL completa: manga-oni.com/{tipo}/{slug}
    for stype in SERIES_TYPES:
        m = re.search(rf"manga-oni\.com/{stype}/([^/?#]+)", raw)
        if m:
            return stype, unquote(m.group(1))

    # URL de lector: manga-oni.com/lector/{slug}/{id}/...
    m = re.search(r"manga-oni\.com/lector/([^/?#]+)/(\d+)", raw)
    if m:
        return "lector", unquote(m.group(1))

    # Solo un slug
    if raw and " " not in raw and not raw.startswith("http"):
        return "manga", raw

    return "query", raw


# ══════════════════════════════════════════════════════════════════════════════
#  INFO DEL MANGA
# ══════════════════════════════════════════════════════════════════════════════
def get_manga_info(slug, series_type="manga"):
    """
    Carga la página del manga y extrae metadata + lista de capítulos.
    Prueba /manga/, /manhwa/, /manhua/ si el tipo no funciona.
    """
    types_to_try = [series_type] + [t for t in SERIES_TYPES if t != series_type]

    html = None
    used_type = series_type
    for stype in types_to_try:
        url = f"{BASE_URL}/{stype}/{slug}/"
        dbg(f"Probando: {url}")
        time.sleep(REQUEST_DELAY)
        r = _get(url, referer=BASE_URL + "/")
        if r and r.status_code == 200:
            # Verificar que la página realmente existe (no es un redirect a home)
            if f"/{slug}" in r.url or f"post-title" in r.text or "entry-manga" in r.text:
                html = r.text
                used_type = stype
                break
        time.sleep(0.3)

    if not html:
        err(f"No se pudo cargar la página del manga: {slug}")
        return None, []

    soup = _soup(html)
    if not soup:
        return None, []

    # Título
    title = ""
    h1 = soup.select_one("h1.post-title a, h1.post-title, h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        title = slug.replace("-", " ").title()

    # Metadata
    author = ""
    status = ""
    year = ""
    genres = []
    summary = ""
    ranking = ""

    info_div = soup.select_one("#info-i")
    if info_div:
        info_text = info_div.get_text(" ", strip=True)
        m = re.search(r"Autor:\s*(.+?)(?:\s+Fecha:|$)", str(info_text))
        if m:
            author = m.group(1).strip()
        m = re.search(r"Fecha:\s*(\d{4})", str(info_text))
        if m:
            year = m.group(1)
        # Estado: usar #desarrollo específicamente (no .estado que también matchea #ranking)
        estado_el = info_div.select_one("#desarrollo")
        if estado_el:
            status = estado_el.get_text(strip=True)
        # Ranking
        ranking_el = info_div.select_one("#ranking")
        ranking = ranking_el.get_text(strip=True) if ranking_el else ""

    # Géneros
    categ_div = soup.select_one("#categ")
    if categ_div:
        genres = [a.get_text(strip=True) for a in categ_div.select("a") if a.get_text(strip=True)]

    # Sinopsis
    sinopsis_div = soup.select_one("#sinopsis")
    if sinopsis_div:
        # Remover el <h3>Sinopsis</h3>
        h3 = sinopsis_div.select_one("h3")
        if h3:
            h3.decompose()
        summary = sinopsis_div.get_text(" ", strip=True)[:300]

    # Tipo de serie (manga/manhwa/manhua)
    portada = soup.select_one(".portada span")
    manga_type = portada.get_text(strip=True) if portada else used_type

    meta = {
        "slug": slug,
        "type": used_type,
        "manga_type": manga_type,
        "title": title,
        "author": author,
        "year": year,
        "status": status,
        "ranking": ranking,
        "genres": genres,
        "summary": summary,
    }
    dbg(f"Meta: {meta}")

    # Capítulos — están en #c_list > a[href*='/lector/']
    chapters = []
    c_list = soup.select_one("#c_list")
    if c_list:
        for a in c_list.select("a[href*='/lector/']"):
            href = str(a.get("href", "")).rstrip("/")
            if not href:
                continue
            # Extraer chapter_id y título
            m = re.search(r"/lector/[^/]+/(\d+)", href)
            if not m:
                continue
            chapter_id = m.group(1)

            h3 = a.select_one("h3")
            ch_title = h3.get_text(strip=True) if h3 else f"Capítulo {chapter_id}"

            # data-num del timeago span
            timeago = a.select_one("span.timeago")
            ch_num = timeago.get("data-num", "") if timeago else ""

            chapters.append({
                "id": chapter_id,
                "title": ch_title,
                "num": ch_num,
                "url": href,
            })

    # Los capítulos vienen del más nuevo al más viejo; invertir
    chapters.reverse()

    # Fallback: buscar en toda la página
    if not chapters:
        for a in soup.find_all("a", href=re.compile(rf"/lector/{re.escape(slug)}/\d+")):
            href = str(a.get("href", "")).rstrip("/")
            m = re.search(r"/lector/[^/]+/(\d+)", href)
            if not m:
                continue
            chapter_id = m.group(1)
            ch_title = a.get_text(strip=True) or f"Capítulo {chapter_id}"
            if chapter_id not in {c["id"] for c in chapters}:
                chapters.append({
                    "id": chapter_id,
                    "title": ch_title,
                    "num": "",
                    "url": href,
                })
        chapters.reverse()

    dbg(f"  {len(chapters)} capítulos encontrados")
    return meta, chapters


# ══════════════════════════════════════════════════════════════════════════════
#  IMÁGENES DE CAPÍTULO (decodificación base64 de 'unicap')
# ══════════════════════════════════════════════════════════════════════════════
def get_chapter_images(slug, chapter_id):
    """
    Carga la página del lector y extrae las URLs de imágenes.
    MangaOni usa una variable JS 'unicap' codificada en base64:
        base_url||["img1","img2",...]||next_chapter_id
    """
    ch_url = f"{BASE_URL}/lector/{slug}/{chapter_id}/cascada/"
    dbg(f"Capítulo URL: {ch_url}")
    time.sleep(REQUEST_DELAY)
    r = _get(ch_url, referer=f"{BASE_URL}/")
    if not r:
        return []

    html = r.text

    # Buscar var unicap = 'base64...';
    m = re.search(r"var\s+unicap\s*=\s*'([A-Za-z0-9+/=]+)'\s*;", html)
    if not m:
        dbg("  'unicap' no encontrada — probando patrones alternativos...")
        # Alternativa: buscar images directas en el HTML
        m = re.search(r"var\s+unicap\s*=\s*\"([A-Za-z0-9+/=]+)\"\s*;", html)

    if m:
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8")
            dbg(f"  unicap decodificada: {decoded[:150]}...")

            # Formato: base_url||["img1","img2",...]||next_chapter_id
            parts = decoded.split("||")
            if len(parts) >= 2:
                img_base_url = parts[0].rstrip("/")
                img_list = json.loads(parts[1])

                urls = []
                for img_file in img_list:
                    img_file = str(img_file).strip()
                    if img_file.startswith("http"):
                        urls.append(img_file)
                    else:
                        urls.append(f"{img_base_url}/{img_file}")

                dbg(f"  {len(urls)} imágenes encontradas via unicap")
                return urls
        except Exception as e:
            dbg(f"  Error decodificando unicap: {e}")

    # Fallback: buscar imágenes directamente en el HTML
    soup = _soup(html)
    if soup:
        urls = []
        for img in soup.select("#slider img, .reading-content img, img[data-src]"):
            src = str(
                img.get("data-src") or img.get("data-lazy-src") or img.get("src") or ""
            ).strip()
            if src and not src.startswith("data:") and "ntr-files" in src:
                if src.startswith("//"):
                    src = "https:" + src
                if src not in urls:
                    urls.append(src)
        if urls:
            dbg(f"  {len(urls)} imágenes encontradas en HTML")
            return urls

    if DEBUG:
        fname = f"debug_oni_ch_{chapter_id}.html"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  {C.YE}HTML capítulo guardado: {fname}{C.EN}")

    return []


# ══════════════════════════════════════════════════════════════════════════════
#  BÚSQUEDA
# ══════════════════════════════════════════════════════════════════════════════
def search(query):
    """
    Busca series en manga-oni.com.
    Usa la página /buscar?s={query} y también /directorio/ con filtro local.
    """
    results = []
    seen = set()

    # Estrategia 1: búsqueda directa
    url = f"{BASE_URL}/buscar"
    time.sleep(REQUEST_DELAY)
    r = _get(url, params={"s": query}, referer=BASE_URL + "/")
    if r:
        soup = _soup(r.text)
        if soup:
            for stype in SERIES_TYPES:
                for a in soup.find_all("a", href=re.compile(rf"/{stype}/[A-Za-z0-9\-]+")):
                    href = str(a.get("href", "")).rstrip("/")
                    m = re.search(rf"/{stype}/([^/?#]+)", href)
                    if not m:
                        continue
                    s = unquote(m.group(1))
                    if s in seen:
                        continue
                    seen.add(s)
                    name = a.get("title") or a.get_text(strip=True) or s
                    name = re.sub(r"\s+", " ", str(name)).strip()
                    if len(name) >= 2:
                        results.append({
                            "slug": s,
                            "type": stype,
                            "title": name,
                        })

    # Estrategia 2: recorrer directorio y filtrar localmente
    if not results:
        dbg("  Búsqueda por /buscar sin resultados — intentando directorio...")
        for page_num in range(1, 6):  # primeras 5 páginas
            time.sleep(REQUEST_DELAY)
            r = _get(f"{BASE_URL}/directorio", params={"p": page_num}, referer=BASE_URL + "/")
            if not r:
                break
            soup = _soup(r.text)
            if not soup:
                break
            found_any = False
            for stype in SERIES_TYPES:
                for a in soup.find_all("a", href=re.compile(rf"/{stype}/[A-Za-z0-9\-]+")):
                    href = str(a.get("href", "")).rstrip("/")
                    m = re.search(rf"/{stype}/([^/?#]+)", href)
                    if not m:
                        continue
                    s = unquote(m.group(1))
                    if s in seen:
                        continue
                    name = a.get_text(strip=True) or s
                    name = re.sub(r"\s+", " ", name).strip()
                    if query.lower() in name.lower() or query.lower() in s.lower():
                        seen.add(s)
                        results.append({
                            "slug": s,
                            "type": stype,
                            "title": name,
                        })
                        found_any = True
            if not found_any and page_num > 2:
                break

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO / DIRECTORIO
# ══════════════════════════════════════════════════════════════════════════════
def get_catalog(page=1):
    """Carga una página del directorio."""
    time.sleep(REQUEST_DELAY)
    r = _get(f"{BASE_URL}/directorio", params={"p": page}, referer=BASE_URL + "/")
    if not r:
        return [], 1

    soup = _soup(r.text)
    if not soup:
        return [], 1

    items = []
    seen = set()
    for stype in SERIES_TYPES:
        for a in soup.select(f"a[href*='/{stype}/']"):
            href = str(a.get("href", "")).rstrip("/")
            m = re.search(rf"/{stype}/([^/?#]+)", href)
            if not m:
                continue
            s = unquote(m.group(1))
            if s in seen:
                continue
            seen.add(s)
            # El texto puede contener "manga{titulo}\n{año} - {autor}"
            raw_text = a.get_text(" ", strip=True)
            # Limpiar prefijo de tipo
            for prefix in SERIES_TYPES:
                if raw_text.lower().startswith(prefix):
                    raw_text = raw_text[len(prefix):].strip()
                    break
            # Separar título de la info del año/autor
            parts = raw_text.split("\n")
            title = parts[0].strip() if parts else s.replace("-", " ").title()
            extra = parts[1].strip() if len(parts) > 1 else ""

            items.append({
                "slug": s,
                "type": stype,
                "title": title or s.replace("-", " ").title(),
                "extra": extra,
            })

    # Total de páginas
    total = 1
    for a in soup.find_all("a", href=re.compile(r"/directorio\?p=\d+")):
        m = re.search(r"p=(\d+)", str(a.get("href", "")))
        if m:
            total = max(total, int(m.group(1)))

    dbg(f"  Directorio pág {page}/{total}: {len(items)} series")
    return items, total


# ══════════════════════════════════════════════════════════════════════════════
#  DESCARGA
# ══════════════════════════════════════════════════════════════════════════════
def _sanitize(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "untitled"


def _ext(url):
    if USER_FORMAT != "original":
        return f".{USER_FORMAT}"
    m = re.search(r"\.(jpe?g|png|webp|gif|avif)(\?|$)", url, re.I)
    return "." + (m.group(1).lower() if m else "jpg")


def _dl_image(args):
    idx, url, dest, referer = args
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = CDN_HOST + url
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
            r = SESSION.get(url, headers=headers, timeout=30)  # type: ignore
            dbg(f"  img[{idx}] {r.status_code}  {url[:70]}")
            if r.status_code == 403:
                # Reintentar sin Referer
                r = SESSION.get(url, headers={"Accept": headers["Accept"]}, timeout=30)  # type: ignore
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
                    img = Image.open(BytesIO(raw)).convert("RGB")  # type: ignore
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
                imgs.append(Image.open(f).convert("RGB"))  # type: ignore
            except Exception:
                pass
        if imgs:
            imgs[0].save(out_path, save_all=True, append_images=imgs[1:])


def download_chapter(slug, manga_title, chapter, ch_num, total):
    ch_id = chapter["id"]
    ch_title = _sanitize(chapter["title"])
    title = _sanitize(manga_title)

    print(f"\n  {C.YE}↓{C.EN}  [{ch_num}/{total}]  {chapter['title'][:55]}")

    urls = get_chapter_images(slug, ch_id)
    if not urls:
        err(f"     Sin imágenes")
        return False

    tmp = os.path.join(OUTPUT_DIR, title, f"__tmp_{ch_id}")
    os.makedirs(tmp, exist_ok=True)

    ref = f"{BASE_URL}/lector/{slug}/{ch_id}/cascada/"
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

    # Usar número de capítulo si está disponible
    ch_num_str = chapter.get("num", "")
    if ch_num_str:
        # Formatear con padding apropiado
        try:
            num_float = float(ch_num_str)
            if num_float == int(num_float):
                ch_label = f"{int(num_float):04d}"
            else:
                ch_label = f"{num_float:07.2f}"
        except ValueError:
            ch_label = f"{ch_num:04d}"
    else:
        ch_label = f"{ch_num:04d}"

    out_file = os.path.join(
        out_dir, f"{ch_label} - {ch_title}{ext_map.get(OUTPUT_TYPE, '.cbz')}"
    )

    _pack(ok_files, out_file, OUTPUT_TYPE)
    print(f"     {C.GR}✔  {os.path.basename(out_file)}{C.EN}")

    if DELETE_TEMP:
        shutil.rmtree(tmp, ignore_errors=True)
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS UI
# ══════════════════════════════════════════════════════════════════════════════
PAGE_SIZE = 20


def print_list(items, offset=0):
    w = shutil.get_terminal_size((80, 20)).columns
    for i, it in enumerate(items, offset + 1):
        extra = ""
        if it.get("extra"):
            extra = f"  {C.BL}│{C.EN} {C.CY}{it['extra'][:25]}{C.EN}"
        elif it.get("type") and it["type"] != "manga":
            extra = f"  {C.BL}│{C.EN} {C.PU}{it['type']}{C.EN}"
        title = it["title"][: w - 35]
        print(f"  {C.BO}{i:>4}.{C.EN}  {title}{extra}")


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


def show_and_download(slug, series_type="manga"):
    banner(slug[:40])
    print(f"  {C.YE}Cargando manga...{C.EN}")
    meta, chapters = get_manga_info(slug, series_type)

    if not meta:
        err("No se pudo cargar el manga.")
        input(f"\n  Enter para volver...")
        return

    banner(meta["title"][:40])
    print(f"  {C.BO}{C.GR}{meta['title']}{C.EN}")
    if meta.get("manga_type"):
        print(f"  {C.PU}{meta['manga_type']}{C.EN}")
    if meta.get("author"):
        print(f"  {C.CY}Autor:{C.EN}   {meta['author']}")
    if meta.get("year"):
        print(f"  {C.CY}Año:{C.EN}     {meta['year']}")
    if meta.get("status"):
        print(f"  {C.CY}Estado:{C.EN}  {meta['status']}")
    if meta.get("ranking"):
        print(f"  {C.CY}Ranking:{C.EN} {meta['ranking']}")
    if meta.get("genres"):
        print(f"  {C.PU}{', '.join(meta['genres'][:6])}{C.EN}")
    if meta.get("summary"):
        print(f"\n  {meta['summary'][:200]}...")
    print(f"\n  {C.BL}{'─' * 44}{C.EN}")
    print(f"  {C.BL}{len(chapters)} capítulos{C.EN}\n")

    if not chapters:
        err("Sin capítulos disponibles.")
        input(f"\n  Enter para volver...")
        return

    # Mostrar capítulos con paginación
    offset = 0
    while True:
        end = min(offset + PAGE_SIZE, len(chapters))
        for i in range(offset, end):
            ch = chapters[i]
            num_str = f" (#{ch['num']})" if ch.get("num") else ""
            print(f"  {C.BO}{i + 1:>4}.{C.EN}  {ch['title'][:55]}{C.CY}{num_str}{C.EN}")

        nav = []
        if end < len(chapters):
            nav.append(f"{C.BL}[n]{C.EN}sig")
        if offset > 0:
            nav.append(f"{C.BL}[p]{C.EN}ant")
        nav.append(f"{C.BL}[q]{C.EN}volver")
        print(f"\n  {'  '.join(nav)}")
        print(f"  Ej: {C.YE}1{C.EN} · {C.YE}3-7{C.EN} · {C.YE}1,3,5{C.EN} · {C.YE}all{C.EN}")

        sel = input(f"\n  {C.YE}Capítulos ➜ {C.EN}").strip()

        if sel.lower() == "q":
            return
        elif sel.lower() == "n" and end < len(chapters):
            offset += PAGE_SIZE
            continue
        elif sel.lower() == "p" and offset > 0:
            offset -= PAGE_SIZE
            continue
        else:
            idxs = parse_sel(sel, len(chapters))
            if idxs:
                break

    print(f"\n  {C.GR}Descargando {len(idxs)} capítulo(s)...{C.EN}")
    print(f"  Destino: {C.CY}{os.path.abspath(OUTPUT_DIR)}{C.EN}")

    ok = sum(
        download_chapter(slug, meta["title"], chapters[idx], rank, len(idxs))
        for rank, idx in enumerate(idxs, 1)
    )

    print(f"\n  {C.GR}✔  {ok}/{len(idxs)} capítulos descargados.{C.EN}")
    input(f"\n  Enter para volver...")


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 1 — DESCARGAR POR URL / NOMBRE
# ══════════════════════════════════════════════════════════════════════════════
def menu_download():
    banner("🔍  Buscar / Descargar")
    print(
        f"  Pegá una {C.CY}URL{C.EN} de manga-oni.com o escribí el {C.CY}nombre{C.EN} del manga\n"
    )
    print(f"  Ejemplos:")
    print(f"    {C.CY}https://manga-oni.com/manhwa/lets-play{C.EN}")
    print(f"    {C.CY}one piece{C.EN}")
    print(f"    {C.CY}lets-play{C.EN}  (slug directo)\n")

    val = input(f"  {C.YE}➜ {C.EN}").strip()
    if not val:
        return

    tipo, slug = parse_input(val)
    dbg(f"  Input parseado: tipo={tipo}, slug={slug}")

    # URL directa a manga/manhwa/manhua/novela
    if tipo in SERIES_TYPES:
        show_and_download(slug, tipo)
        return

    # URL de lector
    if tipo == "lector":
        show_and_download(slug, "manga")
        return

    # Búsqueda por nombre
    banner("🔍  Buscar")
    print(f"  {C.YE}Buscando: {val}{C.EN}\n")
    results = search(val)

    if not results:
        print(f"  {C.RE}Sin resultados para: {val}{C.EN}")
        time.sleep(1.5)
        return

    while True:
        banner("🔍  Resultados")
        print(f"  {C.YE}{val}{C.EN}  {C.BL}│{C.EN}  {C.BO}{len(results)}{C.EN} resultados\n")

        for i, it in enumerate(results[:20]):
            type_tag = f"  {C.PU}[{it['type']}]{C.EN}" if it.get("type") else ""
            print(f"  {C.BO}{i + 1:>3}.{C.EN}  {it['title'][:50]}{type_tag}")

        print(
            f"\n  {C.BL}[q]{C.EN}volver  {C.BL}[número]{C.EN} descargar"
        )
        cmd = input(f"\n  {C.YE}➜ {C.EN}").strip().lower()

        if cmd == "q":
            return
        elif cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(results):
                show_and_download(results[idx]["slug"], results[idx].get("type", "manga"))
                return


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 2 — CATÁLOGO
# ══════════════════════════════════════════════════════════════════════════════
def menu_catalog():
    page = 1
    all_items = []
    filtered = []
    filter_text = ""
    total_pages = 1

    def load_page():
        nonlocal all_items, total_pages, filtered, filter_text
        banner("📂  Catálogo")
        print(f"  {C.YE}Cargando directorio (página {page})...{C.EN}")
        all_items, total_pages = get_catalog(page)
        filter_text = ""
        filtered = all_items[:]

    load_page()

    while True:
        banner("📂  Catálogo")
        header_txt = (
            f"  {C.PU}Directorio{C.EN}  {C.BL}│{C.EN}  "
            f"pág {C.BO}{page}/{total_pages}{C.EN}  {C.BL}│{C.EN}  "
            f"{C.BO}{len(filtered)}{C.EN} series"
        )
        if filter_text:
            header_txt += f"  {C.BL}│{C.EN}  filtro: {C.YE}{filter_text}{C.EN}"
        print(header_txt + "\n")

        if not filtered:
            print(f"  {C.RE}Sin resultados.{C.EN}")
        else:
            print_list(filtered)

        print(
            f"\n  {C.BL}[n]{C.EN}sig  {C.BL}[p]{C.EN}ant  {C.BL}[f]{C.EN}filtrar  "
            f"{C.BL}[q]{C.EN}volver  {C.BL}[número]{C.EN} descargar"
        )
        cmd = input(f"\n  {C.YE}➜ {C.EN}").strip()

        if cmd.lower() == "q":
            return

        elif cmd.lower() == "n" and page < total_pages:
            page += 1
            load_page()

        elif cmd.lower() == "p" and page > 1:
            page -= 1
            load_page()

        elif cmd.lower() == "f":
            ft = input(
                f"  {C.YE}Filtrar por nombre (vacío = mostrar todos) ➜ {C.EN}"
            ).strip()
            filter_text = ft
            if ft:
                filtered = [
                    it for it in all_items if ft.lower() in it["title"].lower()
                ]
            else:
                filtered = all_items[:]

        elif cmd.isdigit():
            idx = int(cmd) - 1
            if filtered and 0 <= idx < len(filtered):
                show_and_download(
                    filtered[idx]["slug"],
                    filtered[idx].get("type", "manga"),
                )


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 3 — RECIENTES
# ══════════════════════════════════════════════════════════════════════════════
def menu_recientes():
    banner("🕐  Recientes")
    print(f"  {C.YE}Cargando capítulos recientes...{C.EN}\n")

    time.sleep(REQUEST_DELAY)
    r = _get(f"{BASE_URL}/recientes/", referer=BASE_URL + "/")
    if not r:
        err("No se pudieron cargar los recientes.")
        input(f"\n  Enter para volver...")
        return

    soup = _soup(r.text)
    if not soup:
        return

    items = []
    seen = set()
    for stype in SERIES_TYPES:
        for a in soup.find_all("a", href=re.compile(rf"/{stype}/[A-Za-z0-9\-]+")):
            href = str(a.get("href", "")).rstrip("/")
            m = re.search(rf"/{stype}/([^/?#]+)", href)
            if not m:
                continue
            s = unquote(m.group(1))
            if s in seen:
                continue
            seen.add(s)
            name = a.get_text(strip=True) or s
            if len(name) >= 2:
                items.append({"slug": s, "type": stype, "title": name})

    if not items:
        print(f"  {C.RE}Sin series recientes.{C.EN}")
        input(f"\n  Enter para volver...")
        return

    while True:
        banner("🕐  Recientes")
        print(f"  {C.BO}{len(items)}{C.EN} series recientes\n")

        for i, it in enumerate(items[:30]):
            type_tag = f"  {C.PU}[{it['type']}]{C.EN}" if it.get("type") else ""
            print(f"  {C.BO}{i + 1:>3}.{C.EN}  {it['title'][:50]}{type_tag}")

        print(f"\n  {C.BL}[q]{C.EN}volver  {C.BL}[número]{C.EN} descargar")
        cmd = input(f"\n  {C.YE}➜ {C.EN}").strip()

        if cmd.lower() == "q":
            return
        elif cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(items):
                show_and_download(items[idx]["slug"], items[idx].get("type", "manga"))
                return


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def check_deps():
    missing = []
    if not _HAS_REQ:
        missing.append("requests")
    if not _HAS_BS4:
        missing.append("beautifulsoup4")
    if missing:
        print(f"\n{C.RE}Faltan dependencias:{C.EN} {', '.join(missing)}")
        print(f"  pip install {' '.join(missing)}\n")
        sys.exit(1)


def main():
    check_deps()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Soporte para --url
    url_arg = ""
    for i, a in enumerate(sys.argv[1:], 1):
        if a == "--url" and i < len(sys.argv) - 1:
            url_arg = sys.argv[i + 1]
        elif not a.startswith("--"):
            url_arg = a

    if url_arg:
        tipo, slug = parse_input(url_arg)
        stype = tipo if tipo in SERIES_TYPES else "manga"
        show_and_download(slug, stype)
        return

    while True:
        banner()
        print(f"  {C.BO}1{C.EN}  🔍  Buscar / descargar por URL o nombre")
        print(f"  {C.BO}2{C.EN}  📂  Explorar directorio")
        print(f"  {C.BO}3{C.EN}  🕐  Recientes")
        print(f"  {C.BO}4{C.EN}  ✖   Salir")
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
            menu_recientes()
        elif op == "4":
            print(f"\n  {C.GR}Hasta luego!{C.EN}\n")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YE}  Interrumpido.{C.EN}")
