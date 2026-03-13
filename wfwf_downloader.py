"""
WFWF DOWNLOADER v2.0
Sitio: wfwf448.com
Soporta: Webtoons (ing/list/view) y Manhwas raw (cm/cl/cv)

Instalación:
    pip install requests pillow beautifulsoup4 lxml

Uso:
    python wfwf_downloader.py
"""

import base64
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Any

import requests
from bs4 import BeautifulSoup

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    HAS_PILLOW = False


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
BASE_URL = "https://wfwf448.com/"
OUTPUT_TYPE = "zip"  # 'zip' | 'cbz' | 'pdf'
USER_FORMAT = "webp"  # 'original' | 'jpg' | 'png' | 'webp'
DELETE_TEMP = True
MAX_WORKERS_DL = 16
MAX_RESULTS_PAGE = 20
REQUEST_TIMEOUT = (12, 20)
RETRY_DELAY = 1.5


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
    print(f"║  {C.BOLD}WFWF DOWNLOADER v2.0{C.END}{C.BLUE}                    ║")
    print(f"║  {C.DIM}wfwf448.com  ·  Webtoon + Manhwa{C.END}{C.BLUE}         ║")
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
    "Referer": BASE_URL,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;"
    "q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(BASE_URL, timeout=10)
    except Exception:
        pass
    return s


SESSION: requests.Session = make_session()
METADATA_CACHE: dict[str, Any] = {}


# ══════════════════════════════════════════════════════════════════════════════
#  MODOS
# ══════════════════════════════════════════════════════════════════════════════
class Mode:
    WEBTOON = "webtoon"
    MANHWA = "manhwa"

    def __init__(self, kind: str) -> None:
        assert kind in (self.WEBTOON, self.MANHWA)
        self.kind = kind

    @property
    def main_path(self) -> str:
        return "ing" if self.kind == self.WEBTOON else "cm"

    def series_url(self, toon_id: str, enc_title: str) -> str:
        path = "list" if self.kind == self.WEBTOON else "cl"
        safe = urllib.parse.quote(enc_title, safe="%+")
        return f"{BASE_URL}{path}?toon={toon_id}&title={safe}"

    def chapter_url(self, toon_id: str, num: int, enc_title: str) -> str:
        path = "view" if self.kind == self.WEBTOON else "cv"
        safe = urllib.parse.quote(enc_title, safe="%+")
        return f"{BASE_URL}{path}?toon={toon_id}&num={num}&title={safe}{num}%C8%AD"

    def chapter_href_re(self, toon_id: str) -> re.Pattern[str]:
        path = "view" if self.kind == self.WEBTOON else "cv"
        return re.compile(rf"{path}\?toon={toon_id}&num=(\d+)&title=", re.IGNORECASE)

    def __str__(self) -> str:
        return "Webtoon" if self.kind == self.WEBTOON else "Manhwa"


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPING
# ══════════════════════════════════════════════════════════════════════════════
_UI_PATHS = ("/images/", "/bann/", "/img/", "/icons/", "/logo", "/thumb")

CDN_RE = re.compile(
    r"https?://[a-z0-9\-]+\.(?:site|com|net|kr)/[^\s\"'<>]+"
    r"\.(?:jpe?g|png|webp|gif)",
    re.IGNORECASE,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def fetch_html(url: str, retries: int = 3) -> str | None:
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return r.text
            print(f"  {C.YELLOW}HTTP {r.status_code}{C.END} → {url}")
        except requests.RequestException as e:
            print(f"  {C.YELLOW}Intento {attempt + 1}/{retries}: {e}{C.END}")
            time.sleep(RETRY_DELAY * (attempt + 1))
    return None


# ── Categorías completas ───────────────────────────────────────────────────
_WEBTOON_CATS = [
    *[f"?o=n&type1=day&type2={i}" for i in range(1, 8)],
    "?o=n&type1=day&type2=10",
    "?o=n&type1=day&type2=recent",
    "?o=n&type1=day&type2=new",
]

_MANHWA_CATS = [
    *[f"?o=n&type1=complete&type2={i}" for i in [10, 11, 12, 13, 14, 15, 16, 20]],
    "?o=n&type1=complete&type2=recent",
]


def _parse_series_from_html(html: str, mode: Mode) -> list[dict[str, str]]:
    """Extrae pares (toon_id, enc_title, title) de un HTML de catálogo."""
    path_kw = "list" if mode.kind == Mode.WEBTOON else "cl"
    pat = re.compile(rf"/{path_kw}\?toon=(\d+)&title=([^&\s\"']+)")
    soup = _soup(html)
    items: list[dict[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        m = pat.search(a["href"])
        if not m:
            continue
        toon_id, enc_title = m.group(1), m.group(2)
        if toon_id in seen:
            continue
        seen.add(toon_id)
        text = a.get_text(" ", strip=True)
        title = urllib.parse.unquote(enc_title)
        if text and "더 읽기" not in text:
            title = text.split("/")[0].strip() or title
        items.append(
            {
                "toon_id": toon_id,
                "encoded_title": enc_title,
                "title": title,
                "mode": mode.kind,
            }
        )
    return items


def _fetch_cat_url(args: tuple[str, Mode]) -> list[dict[str, str]]:
    """Worker: descarga una URL de categoría y devuelve sus series."""
    url, mode = args
    html = fetch_html(url)
    if not html:
        return []
    return _parse_series_from_html(html, mode)


def fetch_series_list(mode: Mode, workers: int = 10) -> list[dict[str, str]]:
    """
    Carga TODAS las series de un modo (Webtoon o Manhwa) en paralelo.
    Combina la página principal + todas las categorías.
    """
    cats = _WEBTOON_CATS if mode.kind == Mode.WEBTOON else _MANHWA_CATS
    main_path = mode.main_path

    all_urls: list[str] = [f"{BASE_URL}{main_path}"] + [
        f"{BASE_URL}{main_path}{cat}" for cat in cats
    ]

    series: list[dict[str, str]] = []
    seen: set[str] = set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_cat_url, (url, mode)): url for url in all_urls}
        done = 0
        for fut in as_completed(futures):
            done += 1
            items = fut.result()
            nuevas = 0
            for it in items:
                if it["toon_id"] not in seen:
                    seen.add(it["toon_id"])
                    series.append(it)
                    nuevas += 1
            sys.stdout.write(
                f"  {C.CYAN}[{mode}] {done}/{len(all_urls)} URLs — "
                f"{len(series)} series{C.END}   \r"
            )
            sys.stdout.flush()

    print(f"  {C.GREEN}✔ {mode}: {len(series)} series{C.END}   ")
    return series


def fetch_full_catalog(workers: int = 10) -> list[dict[str, str]]:
    """
    Carga el catálogo COMPLETO: Webtoon + Manhwa en paralelo simultáneo.
    Devuelve lista unificada con campo 'mode' para distinguirlos.
    """
    all_series: list[dict[str, str]] = []
    seen: set[str] = set()

    mode_wt = Mode(Mode.WEBTOON)
    mode_mh = Mode(Mode.MANHWA)

    cats_wt = [f"{BASE_URL}{mode_wt.main_path}"] + [
        f"{BASE_URL}{mode_wt.main_path}{c}" for c in _WEBTOON_CATS
    ]
    cats_mh = [f"{BASE_URL}{mode_mh.main_path}"] + [
        f"{BASE_URL}{mode_mh.main_path}{c}" for c in _MANHWA_CATS
    ]

    all_tasks: list[tuple[str, Mode]] = [(u, mode_wt) for u in cats_wt] + [
        (u, mode_mh) for u in cats_mh
    ]

    print(f"  {C.CYAN}Cargando {len(all_tasks)} URLs en paralelo...{C.END}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_cat_url, task): task for task in all_tasks}
        done = 0
        for fut in as_completed(futures):
            done += 1
            items = fut.result()
            for it in items:
                key = f"{it['mode']}_{it['toon_id']}"
                if key not in seen:
                    seen.add(key)
                    all_series.append(it)
            sys.stdout.write(
                f"  {C.CYAN}{done}/{len(all_tasks)} URLs — "
                f"{len(all_series)} series{C.END}   \r"
            )
            sys.stdout.flush()

    # Ordenar: primero Webtoon, luego Manhwa, por título dentro de cada grupo
    all_series.sort(key=lambda s: (s["mode"], s["title"].lower()))
    print(
        f"\n  {C.GREEN}✔ {len(all_series)} series en total (Webtoon + Manhwa){C.END}   "
    )
    return all_series


# ── Limpieza de ruido ──────────────────────────────────────────────────────
_NOISE_RE = re.compile(
    r"^\d+\s*"
    r"|하루전|방금전|\d+일전|오늘"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\s{2,}"
)


def _clean_chap_title(raw: str) -> str:
    return _NOISE_RE.sub(" ", raw).strip()


# ── Ficha de serie ─────────────────────────────────────────────────────────
def parse_series_page(
    html: str, toon_id: str, enc_title: str, mode: Mode
) -> tuple[str, str, str, list[dict]]:
    soup = _soup(html)
    title = urllib.parse.unquote(enc_title)

    for sel in ["h1", ".toon-title", ".series-title", ".view-title", "#toon_title"]:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            title = node.get_text(strip=True)
            break

    autor = ""
    sinopsis = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):  # type: ignore[union-attr]
        content = str(meta["content"]).strip()  # type: ignore[index]
        m = re.search(r"작가\s+(.+?)\s+총편수\s+총\s*\d+화\s*(.*)", content, re.DOTALL)
        if m:
            autor = m.group(1).strip()
            sinopsis = m.group(2).strip()[:500]
        else:
            sinopsis = content[:500]

    if not autor:
        for sel in [".writer", ".author", ".toon-author", "[class*='author']"]:
            node = soup.select_one(sel)
            if node and node.get_text(strip=True):
                autor = node.get_text(strip=True)
                break

    if not sinopsis:
        for sel in [
            ".toon-descript",
            ".synopsis",
            ".series-desc",
            ".view-content",
            "#toon_desc",
            ".story",
        ]:
            node = soup.select_one(sel)
            if node and node.get_text(strip=True):
                sinopsis = node.get_text(strip=True)[:500]
                break

    chap_re = mode.chapter_href_re(toon_id)
    seen_nums: set[int] = set()
    chapters: list[dict] = []

    for a in soup.find_all("a", href=True):
        mm = chap_re.search(a["href"])
        if not mm:
            continue
        num = int(mm.group(1))
        if num in seen_nums:
            continue
        seen_nums.add(num)
        raw_text = a.get_text(" ", strip=True)
        chap_title = _clean_chap_title(raw_text) or f"Cap {num}"
        chapters.append({"num": num, "title": chap_title})

    for mm in chap_re.finditer(html):
        num = int(mm.group(1))
        if num not in seen_nums:
            seen_nums.add(num)
            chapters.append({"num": num, "title": f"Cap {num}"})

    chapters.sort(key=lambda c: c["num"], reverse=True)
    return title, autor, sinopsis, chapters


# ── Imágenes de un capítulo ────────────────────────────────────────────────
def extract_images(chapter_html: str) -> list[str]:
    # Estrategia 1: base64
    m64 = re.search(r"var\s+toon_img\s*=\s*['\"]([A-Za-z0-9+/=]+)['\"];", chapter_html)
    if m64:
        try:
            decoded = base64.b64decode(m64.group(1)).decode("utf-8", errors="replace")
            soup2 = _soup(decoded)
            urls = [
                img["src"]
                for img in soup2.find_all("img", src=True)
                if str(img["src"]).startswith("http")
                and not any(p in str(img["src"]) for p in _UI_PATHS)
            ]
            if urls:
                return list(dict.fromkeys(urls))
        except Exception as e:
            print(f"  {C.YELLOW}[base64 err] {e}{C.END}")

    # Estrategia 2: CDN en HTML crudo
    cdn_urls = [
        u
        for u in dict.fromkeys(CDN_RE.findall(chapter_html))
        if not any(p in u for p in _UI_PATHS)
    ]
    if cdn_urls:
        return cdn_urls

    # Estrategia 3: <img> en scope
    soup = _soup(chapter_html)
    scope = soup.select_one("#toon_img") or soup
    VALID_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
    candidates: list[str] = []
    for img in scope.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if (
            src.startswith("http")
            and not any(p in src for p in _UI_PATHS)
            and any(src.lower().endswith(ext) for ext in VALID_EXTS)
        ):
            candidates.append(src)

    if candidates:
        return list(dict.fromkeys(candidates))

    print(f"  {C.YELLOW}[!] 0 imágenes encontradas en este capítulo.{C.END}")
    return []


# ══════════════════════════════════════════════════════════════════════════════
#  DESCARGA
# ══════════════════════════════════════════════════════════════════════════════
def _ext_for(url: str) -> str:
    if HAS_PILLOW and USER_FORMAT != "original":
        return USER_FORMAT
    url_ext = os.path.splitext(url.split("?")[0])[-1].lower().lstrip(".")
    return url_ext if url_ext in ("jpg", "jpeg", "png", "webp", "gif") else "jpg"


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


def dl_worker(args: tuple[str, str, int]) -> bool:
    url, folder, idx = args
    if not url.startswith("http"):
        return False
    ext = _ext_for(url)
    path = os.path.join(folder, f"{idx + 1:03d}.{ext}")
    if os.path.exists(path):
        return True
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200 and r.content:
                save_image(r.content, path)
                return True
        except Exception:
            time.sleep(RETRY_DELAY * (attempt + 1))
    print(f"\n  {C.RED}[FAIL] {url}{C.END}")
    return False


def pack_chapter(src_folder: str, out_path: str, fmt: str) -> None:
    files = sorted(
        os.path.join(src_folder, f)
        for f in os.listdir(src_folder)
        if os.path.isfile(os.path.join(src_folder, f)) and not f.endswith(".json")
    )
    if not files:
        return
    if fmt == "pdf" and HAS_PILLOW and Image is not None:
        pages: list[Any] = []
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
#  DESCARGA DE GALERÍA
# ══════════════════════════════════════════════════════════════════════════════
def _safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


def download_gallery(toon_id: str, enc_title: str, mode: Mode) -> None:
    key = f"{mode.kind}_{toon_id}_{enc_title}"
    disp = urllib.parse.unquote(enc_title)

    print(f"\n{C.CYAN}[*] Cargando ficha · {mode} · '{disp}' ({toon_id})…{C.END}")

    data = METADATA_CACHE.get(key)
    if not data:
        html = fetch_html(mode.series_url(toon_id, enc_title))
        if not html:
            print(f"{C.RED}[!] No se pudo obtener la ficha.{C.END}")
            return
        title, autor, sinopsis, chapters = parse_series_page(
            html, toon_id, enc_title, mode
        )

        if not chapters:
            print(
                f"  {C.YELLOW}[!] No se detectaron capítulos, probando rango 1-500…{C.END}"
            )
            for n in range(1, 501):
                resp = SESSION.head(mode.chapter_url(toon_id, n, enc_title), timeout=5)
                if resp.status_code == 200:
                    chapters.append({"num": n, "title": f"Cap {n}"})
                elif chapters:
                    break
            chapters.sort(key=lambda c: c["num"], reverse=True)

        data = {
            "title": title,
            "autor": autor,
            "sinopsis": sinopsis,
            "chapters": chapters,
        }
        METADATA_CACHE[key] = data

    title = str(data["title"])
    autor_s = str(data.get("autor", ""))
    sinopsis_s = str(data.get("sinopsis", ""))
    all_chapters: list[dict] = list(data["chapters"])

    if not all_chapters:
        print(f"{C.RED}[!] 0 capítulos encontrados.{C.END}")
        return

    print(f"\n  {C.GREEN}{C.BOLD}{title}{C.END}")
    print(f"  Tipo    : {C.CYAN}{mode}{C.END}")
    print(f"  Autor   : {autor_s or C.YELLOW + 'N/A' + C.END}")
    print(
        f"  Sinopsis: {(sinopsis_s[:120] + '…') if len(sinopsis_s) > 120 else sinopsis_s or C.YELLOW + 'N/A' + C.END}"
    )
    print(f"  Caps    : {C.GREEN}{len(all_chapters)}{C.END}")

    PAGE = 20
    show_off = 0
    selection = ""

    while True:
        end_idx = min(show_off + PAGE, len(all_chapters))
        print(f"\n  {C.PURPLE}{'─' * 62}{C.END}")
        for i in range(show_off, end_idx):
            chap = all_chapters[i]
            print(f"  {C.BOLD}{i + 1:4d}.{C.END} {chap['title'][:55]}")
        print(f"  {C.PURPLE}{'─' * 62}{C.END}")
        nav = ""
        if end_idx < len(all_chapters):
            nav += f"  {C.CYAN}n{C.END}=siguiente  "
        if show_off > 0:
            nav += f"  {C.CYAN}p{C.END}=anterior"
        print(nav or "", end="")
        raw = input(
            f"\n\n  {C.YELLOW}Caps a bajar ('1', '3-5,9', 'all') ➜ {C.END}"
        ).strip()
        if raw.lower() == "n" and end_idx < len(all_chapters):
            show_off += PAGE
        elif raw.lower() == "p" and show_off > 0:
            show_off -= PAGE
        elif raw == "":
            continue
        else:
            selection = raw
            break

    selected: list[dict] = []
    if selection.lower() == "all":
        selected = list(all_chapters)
    else:
        req_nums = _parse_nums(selection)
        by_num = [c for c in all_chapters if c["num"] in req_nums]
        if by_num:
            selected = sorted(by_num, key=lambda c: c["num"], reverse=True)
        else:
            selected = [
                all_chapters[i] for i in _parse_positions(selection, len(all_chapters))
            ]

    if not selected:
        print(f"{C.RED}[!] Selección vacía.{C.END}")
        return

    print(f"\n  {C.BOLD}Capítulos seleccionados:{C.END}")
    for i, c in enumerate(selected, 1):
        print(f"    {i}. {c['title'][:60]}")

    confirm = (
        input(f"\n  {C.YELLOW}¿Confirmar descarga? [Enter=sí / n=cancelar] ➜ {C.END}")
        .strip()
        .lower()
    )
    if confirm == "n":
        return

    safe_title = _safe_name(title)
    out_folder = f"{safe_title} [{toon_id}]"
    os.makedirs(out_folder, exist_ok=True)

    with open(os.path.join(out_folder, "info.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "toon_id": toon_id,
                "encoded_title": enc_title,
                "title": title,
                "autor": autor_s,
                "sinopsis": sinopsis_s,
                "chapters": all_chapters,
                "url": mode.series_url(toon_id, enc_title),
                "mode": mode.kind,
            },
            f,
            indent=4,
            ensure_ascii=False,
        )

    ext_out = OUTPUT_TYPE.lower()
    ok = 0
    print(f"\n{C.CYAN}[*] Descargando {len(selected)} capítulo(s)…{C.END}\n")

    for i, chap in enumerate(selected, 1):
        num = chap["num"]
        safe_chap_title = _safe_name(chap["title"])
        chap_name = f"{num:04d}_{safe_chap_title}"
        label = f"[{i}/{len(selected)}] {chap['title'][:50]}"
        print(f"  {C.BOLD}{label}{C.END}", end=" ", flush=True)

        chap_html = fetch_html(mode.chapter_url(toon_id, num, enc_title))
        if not chap_html:
            print(f"{C.RED}× HTML no disponible{C.END}")
            continue

        imgs = extract_images(chap_html)
        if not imgs:
            print(f"{C.RED}× 0 imágenes{C.END}")
            continue

        print(f"→ {len(imgs)} imgs", flush=True)
        chap_dir = os.path.join(out_folder, chap_name)
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

        out_file = os.path.join(out_folder, f"{chap_name}.{ext_out}")
        pack_chapter(chap_dir, out_file, ext_out)
        if DELETE_TEMP:
            shutil.rmtree(chap_dir, ignore_errors=True)

        ok += 1
        print(f"    {C.GREEN}✓ guardado → {out_file}{C.END}")

    print(
        f"\n{C.GREEN}[+] Completado: {ok}/{len(selected)} caps  →  {out_folder}/{C.END}"
    )


# ══════════════════════════════════════════════════════════════════════════════
#  BÚSQUEDA (modo individual)
# ══════════════════════════════════════════════════════════════════════════════
def search_series(query: str, mode: Mode) -> list[dict[str, str]]:
    print(f"  {C.CYAN}[*] Cargando catálogo de {mode}…{C.END}")
    all_s = fetch_series_list(mode)
    if not all_s:
        print(f"  {C.YELLOW}[!] Catálogo vacío o no disponible.{C.END}")
        return []
    print(f"  {C.GREEN}[+] {len(all_s)} series en catálogo.{C.END}")
    if not query.strip():
        return all_s
    q = query.lower()
    return [
        s
        for s in all_s
        if q in s["title"].lower()
        or q in urllib.parse.unquote(s["encoded_title"]).lower()
    ]


# ══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO UNIFICADO (Webtoon + Manhwa)
# ══════════════════════════════════════════════════════════════════════════════
# Caché del catálogo completo para no volver a descargar en la misma sesión
_FULL_CATALOG_CACHE: list[dict[str, str]] = []


def menu_catalog() -> None:
    """
    Carga el catálogo completo (Webtoon + Manhwa) en paralelo y presenta
    una UI de navegación con filtro por texto y descarga por número.
    """
    global _FULL_CATALOG_CACHE

    header()
    print(f"  {C.PURPLE}{C.BOLD}Catálogo Completo — Webtoon + Manhwa{C.END}\n")

    if not _FULL_CATALOG_CACHE:
        _FULL_CATALOG_CACHE = fetch_full_catalog(workers=12)

    if not _FULL_CATALOG_CACHE:
        print(f"  {C.RED}No se pudieron cargar series.{C.END}")
        input(f"  {C.CYAN}Enter para volver…{C.END}")
        return

    all_series = _FULL_CATALOG_CACHE
    filtered = all_series[:]
    filter_text = ""
    page = 0

    while True:
        header()
        total_pages = max(1, (len(filtered) + MAX_RESULTS_PAGE - 1) // MAX_RESULTS_PAGE)
        page = max(0, min(page, total_pages - 1))
        start = page * MAX_RESULTS_PAGE
        end = min(start + MAX_RESULTS_PAGE, len(filtered))

        # Cabecera
        cache_note = f"  {C.DIM}(en caché — usa {C.CYAN}r{C.DIM} para recargar){C.END}"
        print(
            f"  {C.PURPLE}Catálogo{C.END}  {C.BLUE}│{C.END}"
            f"  {C.BOLD}{len(filtered)}{C.END}/{len(all_series)} series"
            + (
                f"  {C.BLUE}│{C.END}  filtro: {C.YELLOW}{filter_text}{C.END}"
                if filter_text
                else ""
            )
            + f"  {C.BLUE}│{C.END}  pág {C.BOLD}{page + 1}/{total_pages}{C.END}"
            + cache_note
        )
        print(f"  {C.PURPLE}{'─' * 62}{C.END}")

        for i, s in enumerate(filtered[start:end]):
            tipo_badge = (
                f"{C.CYAN}[WT]{C.END}"
                if s["mode"] == Mode.WEBTOON
                else f"{C.YELLOW}[MH]{C.END}"
            )
            print(
                f"  {C.BOLD}{start + i + 1:4d}.{C.END}  {tipo_badge}  {s['title'][:50]}"
            )

        print(f"  {C.PURPLE}{'─' * 62}{C.END}")
        print(
            f"\n  {C.CYAN}n{C.END}=sig  {C.CYAN}p{C.END}=ant"
            f"  {C.CYAN}f{C.END}=filtrar  {C.CYAN}r{C.END}=recargar"
            f"  {C.CYAN}q{C.END}=volver  {C.CYAN}[número]{C.END}=descargar"
        )
        cmd = input(f"\n  {C.YELLOW}➜ {C.END}").strip()

        if cmd.lower() == "q":
            return

        elif cmd.lower() == "f":
            ft = input(
                f"  {C.YELLOW}Filtrar por nombre (vacío = mostrar todo) ➜ {C.END}"
            ).strip()
            filter_text = ft
            filtered = (
                [s for s in all_series if ft.lower() in s["title"].lower()]
                if ft
                else all_series[:]
            )
            page = 0

        elif cmd.lower() == "r":
            _FULL_CATALOG_CACHE.clear()
            print(f"  {C.CYAN}Recargando…{C.END}")
            _FULL_CATALOG_CACHE[:] = fetch_full_catalog(workers=12)
            all_series = _FULL_CATALOG_CACHE
            filtered = (
                [s for s in all_series if filter_text.lower() in s["title"].lower()]
                if filter_text
                else all_series[:]
            )
            page = 0

        elif cmd.lower() == "n" and page < total_pages - 1:
            page += 1

        elif cmd.lower() == "p" and page > 0:
            page -= 1

        elif cmd:
            idxs = _parse_positions(cmd, len(filtered))
            if not idxs:
                print(f"  {C.RED}Entrada no válida.{C.END}")
                time.sleep(0.8)
                continue
            for idx in idxs:
                s = filtered[idx]
                mode = Mode(s["mode"])
                download_gallery(s["toon_id"], s["encoded_title"], mode)
            input(f"\n  {C.GREEN}Listo. Enter para continuar…{C.END}")


# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES DE PARSEO
# ══════════════════════════════════════════════════════════════════════════════
def _parse_nums(s: str) -> set[int]:
    s = s.replace(" ", "")
    nums: set[int] = set()
    for part in s.split(","):
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                nums.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        elif part.isdigit():
            nums.add(int(part))
    return nums


def _parse_positions(s: str, length: int) -> list[int]:
    idxs: set[int] = set()
    for n in _parse_nums(s):
        if 1 <= n <= length:
            idxs.add(n - 1)
    return sorted(idxs)


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════
def choose_mode() -> Mode:
    print(f"  {C.BOLD}Tipo de contenido:{C.END}")
    print(f"    {C.CYAN}1{C.END}. Webtoon  (ing / list / view)")
    print(f"    {C.CYAN}2{C.END}. Manhwa   (cm  / cl  / cv )")
    while True:
        op = input(f"  {C.YELLOW}Elige ➜ {C.END}").strip()
        if op == "1":
            return Mode(Mode.WEBTOON)
        if op == "2":
            return Mode(Mode.MANHWA)


def results_menu(results: list[dict[str, str]], query: str, mode: Mode) -> None:
    page = 0
    while True:
        header()
        start = page * MAX_RESULTS_PAGE
        end = min(start + MAX_RESULTS_PAGE, len(results))
        print(
            f"  {C.PURPLE}Resultados para '{query}' [{mode}]  "
            f"{start + 1}-{end} de {len(results)}{C.END}"
        )
        print(f"  {'━' * 54}")
        for i, r in enumerate(results[start:end]):
            print(
                f"  {C.BOLD}{start + i + 1:3d}.{C.END} "
                f"[{C.GREEN}{r['toon_id']}{C.END}] {r['title'][:48]}"
            )
        print(f"  {'━' * 54}")
        print(
            f"  {C.CYAN}n{C.END}=sig  {C.CYAN}p{C.END}=ant  "
            f"{C.CYAN}q{C.END}=volver  o número/rango para descargar"
        )
        sel = input(f"\n  {C.YELLOW}Acción ➜ {C.END}").strip().lower()
        if sel == "n" and end < len(results):
            page += 1
        elif sel == "p" and page > 0:
            page -= 1
        elif sel == "q":
            break
        elif sel:
            idxs = _parse_positions(sel, len(results))
            if not idxs:
                print(f"  {C.RED}Entrada no válida.{C.END}")
                time.sleep(1)
                continue
            for idx in idxs:
                r = results[idx]
                download_gallery(r["toon_id"], r["encoded_title"], mode)
            input(f"\n  {C.GREEN}Listo. Enter para continuar…{C.END}")
            break


def main() -> None:
    while True:
        header()
        print(f"  {C.PURPLE}{C.BOLD}Menú Principal{C.END}")
        print(
            f"  ├─ {C.BOLD}1.{C.END} Descargar por ID  "
            f"(ej: {C.DIM}10543 %B0%ED%BA%ED…{C.END})"
        )
        print(f"  ├─ {C.BOLD}2.{C.END} Buscar / explorar series")
        print(f"  └─ {C.BOLD}3.{C.END} Salir")
        print(
            f"\n  {C.PURPLE}Configuración:{C.END}  "
            f"salida={C.CYAN}{OUTPUT_TYPE.upper()}{C.END}  "
            f"imagen={C.CYAN}{USER_FORMAT.upper()}{C.END}"
        )

        op = input(f"\n  {C.YELLOW}Opción ➜ {C.END}").strip()

        if op == "1":
            mode = choose_mode()
            raw = input(
                f"  {C.CYAN}toon_id encoded_title (separados por espacio): {C.END}"
            ).strip()
            if " " not in raw:
                print(f"  {C.RED}Formato: toon_id encoded_title{C.END}")
                time.sleep(1)
                continue
            toon_id, enc_title = raw.split(" ", 1)
            download_gallery(toon_id.strip(), enc_title.strip(), mode)
            input(f"\n  {C.CYAN}Enter para continuar…{C.END}")

        elif op == "2":
            # Sub-menú: 0=catálogo completo, 1=Webtoon, 2=Manhwa
            print(f"\n  {C.BOLD}¿Qué quieres explorar?{C.END}")
            print(
                f"    {C.CYAN}0{C.END}. 📂 Catálogo completo  {C.DIM}(Webtoon + Manhwa){C.END}"
            )
            print(f"    {C.CYAN}1{C.END}. Webtoon")
            print(f"    {C.CYAN}2{C.END}. Manhwa")
            sub = input(f"  {C.YELLOW}Elige ➜ {C.END}").strip()

            if sub == "0":
                menu_catalog()
            elif sub in ("1", "2"):
                mode = Mode(Mode.WEBTOON if sub == "1" else Mode.MANHWA)
                query = input(
                    f"  {C.CYAN}Búsqueda (Enter para ver todo): {C.END}"
                ).strip()
                results = search_series(query, mode)
                if not results:
                    print(f"  {C.RED}Sin resultados.{C.END}")
                    time.sleep(2)
                else:
                    results_menu(results, query or "todos", mode)

        elif op == "3":
            print(f"\n  {C.GREEN}¡Hasta luego!{C.END}\n")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YELLOW}Interrumpido.{C.END}\n")
