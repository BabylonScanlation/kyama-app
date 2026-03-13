"""
FANFOX DOWNLOADER v2.2
Sitio: fanfox.net (Mangafox)

Instalación:
    pip install requests pillow beautifulsoup4 lxml

Uso:
    python fanfox_downloader.py
"""

import json
import os
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Optional

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
BASE_URL = "https://fanfox.net"
OUTPUT_TYPE = "zip"  # 'zip' | 'cbz' | 'pdf'
USER_FORMAT = "webp"  # 'original' | 'jpg' | 'png' | 'webp'
DELETE_TEMP = True
MAX_WORKERS_DL = 8
REQUEST_TIMEOUT = (15, 45)
RETRY_DELAY = 2.0
MAX_RESULTS = 20


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
    print(f"{C.BLUE}╔══════════════════════════════════════════╗")
    print(f"║  {C.BOLD}FANFOX DOWNLOADER v2.2{C.END}{C.BLUE}                   ║")
    print(f"║  {C.DIM}fanfox.net  ·  Manga & Manhwa{C.END}{C.BLUE}             ║")
    print(f"╚══════════════════════════════════════════╝{C.END}\n")


def bar(done: int, total: int, width: int = 32) -> str:
    pct = done / max(total, 1)
    fill = int(width * pct)
    return f"[{C.CYAN}{'█' * fill}{C.DIM}{'─' * (width - fill)}{C.END}] {done}/{total}"


# ══════════════════════════════════════════════════════════════════════════════
#  SESIÓN HTTP
# ══════════════════════════════════════════════════════════════════════════════
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
    "Referer": BASE_URL + "/",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;"
    "q=0.9,image/avif,image/webp,*/*;q=0.8",
}

SESSION: requests.Session = None  # type: ignore


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(BASE_URL, timeout=10)
    except Exception:
        pass
    return s


def fetch_html(
    url: str, retries: int = 3, referer: Optional[str] = None
) -> Optional[str]:
    hdrs = {}
    if referer:
        hdrs["Referer"] = referer
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=REQUEST_TIMEOUT, headers=hdrs)
            if r.status_code == 200:
                return r.text
            print(f"  {C.YELLOW}HTTP {r.status_code}{C.END} → {url}")
            if r.status_code in (403, 404):
                return None
        except requests.RequestException as e:
            print(f"  {C.YELLOW}Intento {attempt + 1}/{retries}: {e}{C.END}")
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ══════════════════════════════════════════════════════════════════════════════
#  BÚSQUEDA  —  /search?title=...
# ══════════════════════════════════════════════════════════════════════════════
def search_series(query: str) -> list:
    url = f"{BASE_URL}/search?title={requests.utils.quote(query)}"
    html = fetch_html(url)
    if not html:
        return []
    return _parse_manga_list(html)


# ══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO COMPLETO  —  /directory/N.html
# ══════════════════════════════════════════════════════════════════════════════
def fetch_full_catalog(max_pages: int = 143) -> list:
    results = []
    seen = set()

    for page in range(1, max_pages + 1):
        html = None
        for url in [
            f"{BASE_URL}/directory/{page}.html",
            f"{BASE_URL}/directory/?page={page}",
            f"{BASE_URL}/directory/{page}/",
        ]:
            html = fetch_html(url)
            if html:
                break

        if not html:
            print(f"\n  {C.YELLOW}Página {page} inaccesible, deteniendo.{C.END}")
            break

        batch = _parse_manga_list(html)
        nuevos = 0
        for item in batch:
            if item["slug"] not in seen:
                seen.add(item["slug"])
                results.append(item)
                nuevos += 1

        sys.stdout.write(
            f"\r  {C.DIM}Pág {page:3d}: +{nuevos:3d}  "
            f"total={C.CYAN}{len(results)}{C.END}{C.DIM}{C.END}   "
        )
        sys.stdout.flush()

        if nuevos == 0 and page > 2:
            break
        time.sleep(0.35)

    print()
    return results


def _parse_manga_list(html: str) -> list:
    soup = _soup(html)
    results = []
    seen_s = set()

    ITEM_SELS = [
        "ul.manga-list-4-list li",
        "ul.manga-list-4 li",
        "ul.manga-list-2 li",
        "ul.manga-list li",
        ".manga-list li",
        "li.manga-list-4-list-item",
        ".manga-list-2-cover",
    ]
    items = []
    for sel in ITEM_SELS:
        items = soup.select(sel)
        if items:
            break

    for item in items:
        a = item.select_one(
            "p.manga-list-4-item-title a, p.title a, h3 a, .title a, a[href*='/manga/']"
        )
        if not a:
            a = item.find("a", href=re.compile(r"/manga/[^/]+/?$"))
        if not a:
            continue
        href = a.get("href", "")
        title = a.get_text(strip=True)
        m = re.search(r"/manga/([^/?#]+)/?", href)
        if not m or not title:
            continue
        slug = m.group(1)
        if slug in seen_s:
            continue
        seen_s.add(slug)
        rating, status = "", ""
        r_el = item.select_one(".rating em, .item-rate em, .score em")
        if r_el:
            rating = r_el.get_text(strip=True)
        s_el = item.select_one(".manga-list-4-item-tip span, .status")
        if s_el:
            status = s_el.get_text(strip=True)
        results.append(
            {"slug": slug, "title": title, "rating": rating, "status": status}
        )

    if results:
        return results

    _ML = re.compile(r"/manga/([a-z0-9_\-]+)/?$")
    for a in soup.find_all("a", href=_ML):
        href = a.get("href", "")
        title = (a.get("title") or a.get_text(strip=True)).strip()
        m = _ML.search(href)
        if not m or not title or len(title) < 2:
            continue
        slug = m.group(1)
        if slug in seen_s:
            continue
        seen_s.add(slug)
        results.append({"slug": slug, "title": title, "rating": "", "status": ""})

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  PARSING DE SERIE  —  /manga/{slug}/
# ══════════════════════════════════════════════════════════════════════════════
def _clean_chap_title(raw: str, series_title: str) -> str:
    t = raw.strip()
    if not t:
        return ""
    if t.lower().startswith(series_title.lower()):
        t = t[len(series_title) :].strip()
    if re.match(r"^(?:Vol\.[^\s]+\s+)?Ch\.[^\s]+\s*$", t, re.I):
        return ""
    return t


_CHAP_URL_RE = re.compile(r"/manga/[^/]+/(?:v([^/]+)/)?c([^/]+)/\d+\.html")


def parse_series(slug: str) -> Optional[dict]:
    series_url = f"{BASE_URL}/manga/{slug}/"
    html = fetch_html(series_url)
    if not html:
        return None
    soup = _soup(html)

    title = ""
    for sel in ["span.detail-info-right-title-font", "h1.title", "h1"]:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            title = node.get_text(strip=True)
            break
    if not title:
        title = slug.replace("_", " ").title()

    autor = ""
    for sel in [".detail-info-right-say a", "a[href*='/search/author/']"]:
        node = soup.select_one(sel)
        if node:
            autor = node.get_text(strip=True)
            break

    status = ""
    node = soup.select_one(".detail-info-right-title-tip")
    if node:
        status = node.get_text(strip=True)

    genres = [
        a.get_text(strip=True) for a in soup.select(".detail-info-right-tag-list a")
    ]
    summary = ""
    for sel in [".detail-info-right-content", "#show"]:
        node = soup.select_one(sel)
        if node:
            summary = node.get_text(strip=True)[:500]
            break

    chapters = []
    seen_chaps = set()

    for a in soup.find_all("a", href=_CHAP_URL_RE):
        href = a.get("href", "")
        m = _CHAP_URL_RE.search(href)
        if not m:
            continue
        vol = m.group(1) or "TBD"
        chap = m.group(2)
        if chap in seen_chaps:
            continue
        seen_chaps.add(chap)
        raw_t = (a.get("title") or "").strip()
        chap_title = _clean_chap_title(raw_t, title)
        full_url = BASE_URL + href if href.startswith("/") else href
        chapters.append(
            {"vol": vol, "chap": chap, "title": chap_title, "url": full_url}
        )

    def _key(c):
        try:
            return float(c["chap"])
        except Exception:
            return 0.0

    chapters.sort(key=_key, reverse=True)

    return {
        "slug": slug,
        "title": title,
        "autor": autor,
        "status": status,
        "genres": genres,
        "summary": summary,
        "chapters": chapters,
        "url": series_url,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  EXTRACCIÓN DE IMÁGENES
# ══════════════════════════════════════════════════════════════════════════════
_RE_CHAPTERID = re.compile(r'chapterid\s*=\s*["\']?(\d+)["\']?', re.I)
_RE_IMAGECOUNT = re.compile(r'imagecount\s*=\s*["\']?(\d+)["\']?', re.I)
_RE_WORD = re.compile(
    r'["\']word["\']\s*:\s*["\']([^"\']{3,})["\']'
    r'|var\s+word\s*=\s*["\']([^"\']{3,})["\']',
    re.I,
)
_RE_IMGURL = re.compile(
    r'(https?://(?:fmcdn|img\.mfcdn)[^"\'<>\s]+'
    r'\.(?:jpe?g|png|webp|gif)(?:\?[^"\'<>\s]*)?)',
    re.IGNORECASE,
)


def _js_vars(html: str) -> tuple:
    soup = _soup(html)
    js_text = "\n".join(s.get_text() for s in soup.find_all("script"))
    combined = js_text + "\n" + html
    m_id = _RE_CHAPTERID.search(combined)
    m_cnt = _RE_IMAGECOUNT.search(combined)
    m_w = _RE_WORD.search(combined)
    chid = m_id.group(1) if m_id else None
    cnt = int(m_cnt.group(1)) if m_cnt else 0
    word = (m_w.group(1) or m_w.group(2)) if m_w else None
    return chid, cnt, word


def _api_images(slug: str, chapter_id: str, n_pages: int, word: Optional[str]) -> list:
    images = []
    api_base = f"{BASE_URL}/roll_manga/apiv1/manga/{slug}/chapters/{chapter_id}/images/"
    token = word or SESSION.cookies.get("word", "")
    for page in range(1, n_pages + 1):
        params: dict = {"page": page}
        if token:
            params["token"] = token
        try:
            r = SESSION.get(
                api_base,
                params=params,
                timeout=REQUEST_TIMEOUT,
                headers={"Referer": BASE_URL + "/"},
            )
            if r.status_code != 200:
                break
            data = r.json()
            if isinstance(data, dict):
                if "images" in data:
                    for img in data["images"]:
                        u = img.get("url", "")
                        if u:
                            images.append(u if u.startswith("http") else "https:" + u)
                    continue
                if "url" in data:
                    u = data["url"]
                    if u:
                        images.append(u if u.startswith("http") else "https:" + u)
                    continue
            if isinstance(data, list):
                for item in data:
                    u = item.get("url", "") if isinstance(item, dict) else str(item)
                    if u:
                        images.append(u if u.startswith("http") else "https:" + u)
        except Exception:
            break
    return images


def _page_image(page_url: str, referer: str) -> Optional[str]:
    html = fetch_html(page_url, referer=referer)
    if not html:
        return None
    soup = _soup(html)
    for sel in [
        "img#image",
        "img.reader-main-img",
        "#viewer img",
        ".read-manga-page img",
        "section.reader-main img",
    ]:
        img = soup.select_one(sel)
        if img:
            for attr in ("data-original", "data-src", "src"):
                src = img.get(attr, "")
                if src and any(x in src for x in ("fmcdn", "mfcdn")):
                    return src if src.startswith("http") else "https:" + src
    for m in _RE_IMGURL.finditer(html):
        src = m.group(1)
        if "/logo" not in src and "/icon" not in src:
            return src
    return None


def extract_chapter_images(chap_url: str, slug: str) -> list:
    base_chap = re.sub(r"/\d+\.html$", "", chap_url)
    html = fetch_html(chap_url, referer=BASE_URL)
    if not html:
        return []
    chapter_id, n_pages, word = _js_vars(html)
    if n_pages == 0:
        soup = _soup(html)
        nums = set()
        for a in soup.find_all("a", href=re.compile(r"/\d+\.html$")):
            m = re.search(r"/(\d+)\.html$", a.get("href", ""))
            if m:
                nums.add(int(m.group(1)))
        if nums:
            n_pages = max(nums)
    print(
        f"\n    {C.DIM}chapterid={chapter_id}  imagecount={n_pages}  "
        f"word={'✓' if word else '✗'}{C.END}",
        end="",
        flush=True,
    )
    if chapter_id and n_pages > 0:
        images = _api_images(slug, chapter_id, n_pages, word)
        if len(images) == n_pages:
            return images
        if images:
            print(
                f"\n    {C.DIM}API: {len(images)}/{n_pages}, completando con scraping…{C.END}",
                end="",
            )
    max_scan = n_pages if n_pages > 0 else 60
    imgs_by_page: dict = {}
    with ThreadPoolExecutor(max_workers=4) as exe:
        futures = {
            exe.submit(_page_image, f"{base_chap}/{p}.html", chap_url): p
            for p in range(1, max_scan + 1)
        }
        for fut in as_completed(futures):
            p = futures[fut]
            img = fut.result()
            if img:
                imgs_by_page[p] = img
    return [imgs_by_page[p] for p in sorted(imgs_by_page)]


# ══════════════════════════════════════════════════════════════════════════════
#  DESCARGA DE IMÁGENES
# ══════════════════════════════════════════════════════════════════════════════
def _ext_for(url: str) -> str:
    if HAS_PILLOW and USER_FORMAT != "original":
        return USER_FORMAT
    ext = os.path.splitext(url.split("?")[0])[-1].lower().lstrip(".")
    return ext if ext in ("jpg", "jpeg", "png", "webp", "gif") else "jpg"


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
    for attempt in range(3):
        try:
            r = SESSION.get(
                url, timeout=REQUEST_TIMEOUT, headers={"Referer": BASE_URL + "/"}
            )
            if r.status_code == 200 and r.content:
                save_image(r.content, path)
                return True
        except Exception:
            time.sleep(RETRY_DELAY * (attempt + 1))
    print(f"\n  {C.RED}[FAIL] {url}{C.END}")
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  EMPAQUETADO
# ══════════════════════════════════════════════════════════════════════════════
def pack_chapter(src_folder: str, out_path: str, fmt: str) -> None:
    files = sorted(
        os.path.join(src_folder, f)
        for f in os.listdir(src_folder)
        if os.path.isfile(os.path.join(src_folder, f)) and not f.endswith(".json")
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
            pages[0].save(out_path, save_all=True, append_images=pages[1:])
    else:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, os.path.basename(f))


# ══════════════════════════════════════════════════════════════════════════════
#  FLUJO DE DESCARGA
# ══════════════════════════════════════════════════════════════════════════════
def _safe_name(s: str) -> str:
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


def download_series(slug: str) -> None:
    print(f"\n{C.CYAN}[*] Cargando serie '{slug}'…{C.END}")
    data = parse_series(slug)
    if not data:
        print(f"{C.RED}[!] No se pudo obtener la serie.{C.END}")
        return

    title = data["title"]
    chapters = data["chapters"]

    print(f"\n  {C.GREEN}{C.BOLD}{title}{C.END}")
    print(f"  Autor  : {data['autor'] or 'N/A'}")
    print(f"  Estado : {data['status'] or 'N/A'}")
    print(f"  Géneros: {', '.join(data['genres'][:6]) or 'N/A'}")
    if data["summary"]:
        s = data["summary"]
        print(f"  Sinopsis: {s[:100] + '…' if len(s) > 100 else s}")
    print(f"  Caps   : {C.GREEN}{len(chapters)}{C.END}")

    if not chapters:
        print(f"{C.RED}[!] 0 capítulos encontrados.{C.END}")
        return

    PAGE = 20
    show_off = 0
    selection = ""

    while True:
        end_idx = min(show_off + PAGE, len(chapters))
        print(f"\n  {C.PURPLE}{'─' * 60}{C.END}")
        for i in range(show_off, end_idx):
            c = chapters[i]
            vol_s = f"Vol.{c['vol']} " if c["vol"] not in ("TBD", "TBE", "") else ""
            label = c["title"] if c["title"] else f"{C.DIM}(sin título){C.END}"
            print(f"  {C.BOLD}{i + 1:4d}.{C.END} {vol_s}Ch.{c['chap']}  {label[:55]}")
        print(f"  {C.PURPLE}{'─' * 60}{C.END}")
        nav = ""
        if end_idx < len(chapters):
            nav += f"  {C.CYAN}n{C.END}=siguiente  "
        if show_off > 0:
            nav += f"  {C.CYAN}p{C.END}=anterior"
        if nav:
            print(nav)
        raw = input(
            f"\n  {C.YELLOW}Caps a bajar ('1', '3-5,9', 'all') ➜ {C.END}"
        ).strip()
        if raw.lower() == "n" and end_idx < len(chapters):
            show_off += PAGE
        elif raw.lower() == "p" and show_off > 0:
            show_off -= PAGE
        elif raw == "":
            continue
        else:
            selection = raw
            break

    if selection.lower() == "all":
        selected = list(chapters)
    else:
        selected = [chapters[i] for i in _parse_positions(selection, len(chapters))]

    if not selected:
        print(f"{C.RED}[!] Selección vacía.{C.END}")
        return

    print(f"\n  {C.BOLD}Capítulos seleccionados:{C.END}")
    for i, c in enumerate(selected, 1):
        vol_s = f"Vol.{c['vol']} " if c["vol"] not in ("TBD", "TBE", "") else ""
        label = c["title"] or "(sin título)"
        print(f"    {i}. {vol_s}Ch.{c['chap']}  {label[:60]}")

    confirm = (
        input(f"\n  {C.YELLOW}¿Confirmar descarga? [Enter=sí / n=cancelar] ➜ {C.END}")
        .strip()
        .lower()
    )
    if confirm == "n":
        return

    safe_title = _safe_name(title)
    out_folder = f"{safe_title} [{slug}]"
    os.makedirs(out_folder, exist_ok=True)

    with open(os.path.join(out_folder, "info.json"), "w", encoding="utf-8") as f:
        json.dump(
            {k: v for k, v in data.items() if k != "chapters"},
            f,
            indent=4,
            ensure_ascii=False,
        )

    ext_out = OUTPUT_TYPE.lower()
    ok = 0

    print(f"\n{C.CYAN}[*] Descargando {len(selected)} capítulo(s)…{C.END}\n")

    for i, chap in enumerate(selected, 1):
        vol_s = f"Vol.{chap['vol']} " if chap["vol"] not in ("TBD", "TBE", "") else ""
        label = f"[{i}/{len(selected)}] {vol_s}Ch.{chap['chap']}  {(chap['title'] or '')[:40]}"
        print(f"  {C.BOLD}{label}{C.END}", end=" ", flush=True)

        imgs = extract_chapter_images(chap["url"], slug)
        if not imgs:
            print(f"\n  {C.RED}  × 0 imágenes{C.END}")
            continue

        print(f"\n    → {len(imgs)} págs", flush=True)

        chap_num = chap["chap"].replace(".", "_")
        chap_dir = os.path.join(out_folder, f"ch{chap_num}_temp")
        os.makedirs(chap_dir, exist_ok=True)

        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
            futures = {
                exe.submit(dl_worker, (url, chap_dir, idx)): idx
                for idx, url in enumerate(imgs)
            }
            for _ in as_completed(futures):
                done += 1
                sys.stdout.write(f"\r    {bar(done, len(imgs))}")
                sys.stdout.flush()
        print()

        safe_chap_title = _safe_name(chap["title"]) if chap["title"] else ""
        file_name = f"Ch.{chap['chap']}"
        if safe_chap_title:
            file_name += f" - {safe_chap_title}"

        out_file = os.path.join(out_folder, f"{file_name}.{ext_out}")
        pack_chapter(chap_dir, out_file, ext_out)

        if DELETE_TEMP:
            shutil.rmtree(chap_dir, ignore_errors=True)

        ok += 1
        print(f"    {C.GREEN}✓ → {out_file}{C.END}")

    print(
        f"\n{C.GREEN}[+] Completado: {ok}/{len(selected)} caps  →  {out_folder}/{C.END}"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ DE RESULTADOS  (con toggle paginación)
# ══════════════════════════════════════════════════════════════════════════════
def results_menu(results: list, label: str, paginated: bool = True) -> None:
    page = 0
    PAGE = MAX_RESULTS
    while True:
        header()
        if paginated:
            start = page * PAGE
            end = min(start + PAGE, len(results))
            chunk = results[start:end]
        else:
            start = 0
            end = len(results)
            chunk = results

        print(f"  {C.PURPLE}'{label}'  ({start + 1}–{end} de {len(results)}){C.END}")
        print(f"  {'━' * 60}")
        for i, r in enumerate(chunk):
            num = start + i + 1
            rating = f"  ★{r['rating']}" if r.get("rating") else ""
            status = f"  [{r['status']}]" if r.get("status") else ""
            print(
                f"  {C.BOLD}{num:3d}.{C.END} {r['title'][:48]}{C.DIM}{status}{rating}{C.END}"
            )
            print(f"       {C.CYAN}{r['slug']}{C.END}")
        print(f"  {'━' * 60}")

        nav = []
        if paginated and end < len(results):
            nav.append(f"{C.CYAN}n{C.END}=siguiente")
        if paginated and page > 0:
            nav.append(f"{C.CYAN}p{C.END}=anterior")
        if paginated:
            nav.append(f"{C.CYAN}t{C.END}=ver todo sin paginación")
        else:
            nav.append(f"{C.CYAN}t{C.END}=volver a paginado")
        nav.append(f"{C.CYAN}q{C.END}=volver")
        print("  " + "  ".join(nav) + "  o número para descargar")

        sel = input(f"\n  {C.YELLOW}Acción ➜ {C.END}").strip().lower()
        if sel == "n" and paginated and end < len(results):
            page += 1
        elif sel == "p" and paginated and page > 0:
            page -= 1
        elif sel == "t":
            paginated = not paginated
            page = 0
        elif sel == "q":
            break
        elif sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(results):
                download_series(results[idx]["slug"])
                input(f"\n  {C.GREEN}Enter para continuar…{C.END}")
                break
        elif "," in sel or "-" in sel:
            idxs = _parse_positions(sel, len(results))
            if idxs:
                for idx in idxs:
                    download_series(results[idx]["slug"])
                input(f"\n  {C.GREEN}Cola terminada. Enter…{C.END}")
                break


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
def _slug_from_input(raw: str) -> str:
    """Extrae el slug de una URL fanfox o lo devuelve tal cual."""
    m = re.search(r"fanfox\.net/manga/([^/?#\s]+)", raw)
    if m:
        return m.group(1).strip("/")
    return raw.strip().strip("/")


def main() -> None:
    global SESSION
    SESSION = make_session()

    while True:
        header()
        print(f"  {C.PURPLE}{C.BOLD}Menú Principal{C.END}")
        print(f"  ├─ {C.BOLD}1.{C.END} Buscar / descargar por nombre, slug o URL")
        print(f"  ├─ {C.BOLD}2.{C.END} 📂  Ver catálogo completo")
        print(f"  └─ {C.BOLD}3.{C.END} Salir")
        print(
            f"\n  {C.PURPLE}Config:{C.END}  salida={C.CYAN}{OUTPUT_TYPE.upper()}{C.END}  imagen={C.CYAN}{USER_FORMAT.upper()}{C.END}"
        )

        op = input(f"\n  {C.YELLOW}Opción ➜ {C.END}").strip()

        if op == "1":
            raw = input(f"  {C.CYAN}Nombre, slug o URL: {C.END}").strip()
            if not raw:
                continue
            # Si parece un slug/URL directo (sin espacios) intentar buscar primero
            if " " not in raw and "fanfox.net" not in raw:
                # Podría ser slug directo — buscar igualmente y si hay resultado exacto usar ese
                results = search_series(raw)
                exact = [r for r in results if r["slug"].lower() == raw.lower()]
                if exact:
                    download_series(exact[0]["slug"])
                    input(f"\n  {C.CYAN}Enter para continuar…{C.END}")
                    continue
                elif results:
                    results_menu(results, raw)
                    continue
                else:
                    # Tratar como slug directo
                    download_series(_slug_from_input(raw))
                    input(f"\n  {C.CYAN}Enter para continuar…{C.END}")
                    continue
            # URL completa
            if "fanfox.net" in raw:
                download_series(_slug_from_input(raw))
                input(f"\n  {C.CYAN}Enter para continuar…{C.END}")
                continue
            # Nombre con espacios → búsqueda
            print(f"  {C.CYAN}[*] Buscando '{raw}'…{C.END}")
            results = search_series(raw)
            if not results:
                print(f"  {C.RED}Sin resultados para '{raw}'.{C.END}")
                time.sleep(2)
            else:
                results_menu(results, raw)

        elif op == "2":
            print(f"\n  {C.CYAN}[*] Cargando catálogo completo…{C.END}")
            results = fetch_full_catalog()
            if not results:
                print(f"\n  {C.RED}No se pudo cargar el catálogo.{C.END}")
                time.sleep(3)
                continue
            print(f"\n  {C.GREEN}✓ {len(results)} series cargadas.{C.END}")
            ft = (
                input(f"  {C.CYAN}Filtrar por nombre (Enter=ver todas): {C.END}")
                .strip()
                .lower()
            )
            if ft:
                results = [
                    r
                    for r in results
                    if ft in r["title"].lower() or ft in r["slug"].lower()
                ]
            modo = (
                input(
                    f"  {C.CYAN}¿Con paginación? (Enter=sí / n=todo de una vez): {C.END}"
                )
                .strip()
                .lower()
            )
            results_menu(results, "catálogo" if not ft else ft, paginated=(modo != "n"))

        elif op == "3":
            print(f"\n  {C.GREEN}¡Hasta luego!{C.END}\n")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Interrumpido.{C.END}\n")
