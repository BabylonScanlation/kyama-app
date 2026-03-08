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

# ── Soporte Pillow ─────────────────────────────────────────────────────────────
try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    Image = None  # type: ignore
    HAS_PILLOW = False


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN  (edita aquí)
# ══════════════════════════════════════════════════════════════════════════════
BASE_URL = "https://wfwf448.com/"
OUTPUT_TYPE = "cbz"  # 'zip' | 'cbz' | 'pdf'
USER_FORMAT = "original"  # 'original' | 'jpg' | 'png' | 'webp'
DELETE_TEMP = True
MAX_WORKERS_DL = 16
MAX_RESULTS_PAGE = 20
REQUEST_TIMEOUT = (12, 20)
RETRY_DELAY = 1.5  # segundos entre reintentos


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
        s.get(BASE_URL, timeout=10)  # obtener cookies iniciales
    except Exception:
        pass
    return s


SESSION: requests.Session = make_session()
METADATA_CACHE: dict[str, Any] = {}


# ══════════════════════════════════════════════════════════════════════════════
#  MODOS: Webtoon vs Manhwa
# ══════════════════════════════════════════════════════════════════════════════
class Mode:
    """Encapsula las rutas URL según el tipo de contenido."""

    WEBTOON = "webtoon"
    MANHWA = "manhwa"

    def __init__(self, kind: str) -> None:
        assert kind in (self.WEBTOON, self.MANHWA)
        self.kind = kind

    # ── páginas principales ────────────────────────────────────────────────
    @property
    def main_path(self) -> str:
        return "ing" if self.kind == self.WEBTOON else "cm"

    # ── URL ficha de serie ─────────────────────────────────────────────────
    def series_url(self, toon_id: str, enc_title: str) -> str:
        path = "list" if self.kind == self.WEBTOON else "cl"
        safe = urllib.parse.quote(enc_title, safe="%+")
        return f"{BASE_URL}{path}?toon={toon_id}&title={safe}"

    # ── URL de un capítulo ─────────────────────────────────────────────────
    def chapter_url(self, toon_id: str, num: int, enc_title: str) -> str:
        path = "view" if self.kind == self.WEBTOON else "cv"
        safe = urllib.parse.quote(enc_title, safe="%+")
        return f"{BASE_URL}{path}?toon={toon_id}&num={num}&title={safe}{num}%C8%AD"

    # ── Patrón para capítulos en el HTML de la ficha ───────────────────────
    def chapter_href_re(self, toon_id: str) -> re.Pattern[str]:
        path = "view" if self.kind == self.WEBTOON else "cv"
        return re.compile(
            rf"{path}\?toon={toon_id}&num=(\d+)&title=",
            re.IGNORECASE,
        )

    def __str__(self) -> str:
        return "Webtoon" if self.kind == self.WEBTOON else "Manhwa"


# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPING
# ══════════════════════════════════════════════════════════════════════════════
_UI_PATHS = ("/images/", "/bann/", "/img/", "/icons/", "/logo", "/thumb")

# CDNs conocidos para imágenes
CDN_RE = re.compile(
    r"https?://[a-z0-9\-]+\.(?:site|com|net|kr)/[^\s\"'<>]+"
    r"\.(?:jpe?g|png|webp|gif)",
    re.IGNORECASE,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


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


# ── Categorías completas por modo ──────────────────────────────────────────
_WEBTOON_CATS = [
    # días (type2=1..7) + recientes + nuevas + cada 10 días
    *[f"?o=n&type1=day&type2={i}" for i in range(1, 8)],
    "?o=n&type1=day&type2=10",
    "?o=n&type1=day&type2=recent",
    "?o=n&type1=day&type2=new",
]

_MANHWA_CATS = [
    # periodicidad
    *[f"?o=n&type1=complete&type2={i}" for i in [10, 11, 12, 13, 14, 15, 16, 20]],
    "?o=n&type1=complete&type2=recent",
]


def fetch_series_list(mode: Mode) -> list[dict[str, str]]:
    """Raspa el catálogo completo combinando todas las categorías del sitio."""
    path_kw = "list" if mode.kind == Mode.WEBTOON else "cl"
    pat = re.compile(rf"/{path_kw}\?toon=(\d+)&title=([^&\s\"']+)")

    cats = _WEBTOON_CATS if mode.kind == Mode.WEBTOON else _MANHWA_CATS
    main_path = mode.main_path

    series: list[dict[str, str]] = []
    seen: set[str] = set()

    # Página principal primero, luego todas las categorías
    all_urls = [f"{BASE_URL}{main_path}"] + [
        f"{BASE_URL}{main_path}{cat}" for cat in cats
    ]

    for url in all_urls:
        html = fetch_html(url)
        if not html:
            continue

        soup = _soup(html)
        nuevas = 0

        for a in soup.find_all("a", href=True):
            m = pat.search(a["href"])
            if not m:
                continue
            toon_id, enc_title = m.group(1), m.group(2)
            if toon_id in seen:
                continue
            seen.add(toon_id)
            nuevas += 1

            text = a.get_text(" ", strip=True)
            title = urllib.parse.unquote(enc_title)
            if text and "더 읽기" not in text:
                title = text.split("/")[0].strip() or title

            series.append(
                {
                    "toon_id": toon_id,
                    "encoded_title": enc_title,
                    "title": title,
                    "mode": mode.kind,
                }
            )

        label = url.split(main_path)[-1] or "/principal"
        print(f"  {C.DIM}{label:<35} +{nuevas:>4}  total: {len(series)}{C.END}")
        time.sleep(0.2)

    return series


# ── Limpieza de ruido en títulos de capítulos ─────────────────────────────
_NOISE_RE = re.compile(
    r"^\d+\s*"  # número inicial "94 "
    r"|하루전|방금전|\d+일전|오늘"  # badges de tiempo
    r"|\d{4}-\d{2}-\d{2}"  # fecha "2026-03-07"
    r"|\s{2,}"  # espacios múltiples
)


def _clean_chap_title(raw: str) -> str:
    t = _NOISE_RE.sub(" ", raw).strip()
    return t


# ── Ficha de serie (título, autor, sinopsis, lista de capítulos) ────────────
def parse_series_page(
    html: str,
    toon_id: str,
    enc_title: str,
    mode: Mode,
) -> tuple[str, str, str, list[dict]]:
    soup = _soup(html)
    title = urllib.parse.unquote(enc_title)

    # Título
    for sel in ["h1", ".toon-title", ".series-title", ".view-title", "#toon_title"]:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            title = node.get_text(strip=True)
            break

    # Autor / sinopsis desde <meta name="description">
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

    # Capítulos → ahora guardamos num + título
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

    # También buscar en HTML crudo (pero aquí no tendremos títulos bonitos)
    for mm in chap_re.finditer(html):
        num = int(mm.group(1))
        if num not in seen_nums:
            seen_nums.add(num)
            chapters.append({"num": num, "title": f"Cap {num}"})

    chapters.sort(key=lambda c: c["num"], reverse=True)
    return title, autor, sinopsis, chapters


# ── Imágenes dentro de un capítulo ─────────────────────────────────────────
def extract_images(chapter_html: str) -> list[str]:
    """
    Estrategia 1: var toon_img = '<base64>';  → decodificar y extraer <img src>
    Estrategia 2: URLs CDN directas en el HTML
    Estrategia 3: <img> dentro de #toon_img o cualquier <img> con extensión válida
    """
    # ── Estrategia 1: base64 ──────────────────────────────────────────────
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

    # ── Estrategia 2: CDN en HTML crudo ──────────────────────────────────
    cdn_urls = [
        u
        for u in dict.fromkeys(CDN_RE.findall(chapter_html))
        if not any(p in u for p in _UI_PATHS)
    ]
    if cdn_urls:
        return cdn_urls

    # ── Estrategia 3: <img> en scope #toon_img o global ──────────────────
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
#  DESCARGA DE IMÁGENES
# ══════════════════════════════════════════════════════════════════════════════
def _ext_for(url: str) -> str:
    """Determina la extensión de salida."""
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
#  FLUJO PRINCIPAL: DESCARGA DE GALERÍA
# ══════════════════════════════════════════════════════════════════════════════
def _safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


def download_gallery(toon_id: str, enc_title: str, mode: Mode) -> None:
    key = f"{mode.kind}_{toon_id}_{enc_title}"
    disp = urllib.parse.unquote(enc_title)

    print(f"\n{C.CYAN}[*] Cargando ficha · {mode} · '{disp}' ({toon_id})…{C.END}")

    # ── Obtener metadatos ──────────────────────────────────────────────────
    data = METADATA_CACHE.get(key)
    if not data:
        html = fetch_html(mode.series_url(toon_id, enc_title))
        if not html:
            print(f"{C.RED}[!] No se pudo obtener la ficha.{C.END}")
            return
        title, autor, sinopsis, chapters = parse_series_page(
            html, toon_id, enc_title, mode
        )

        # Fallback: probar capítulos 1-500 si la página no los lista
        if not chapters:
            print(
                f"  {C.YELLOW}[!] No se detectaron capítulos en la ficha, "
                f"probando rango 1-500…{C.END}"
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

    # ── Mostrar ficha ──────────────────────────────────────────────────────
    print(f"\n  {C.GREEN}{C.BOLD}{title}{C.END}")
    print(f"  Tipo    : {C.CYAN}{mode}{C.END}")
    print(f"  Autor   : {autor_s or C.YELLOW + 'N/A' + C.END}")
    print(
        f"  Sinopsis: {(sinopsis_s[:120] + '…') if len(sinopsis_s) > 120 else sinopsis_s or C.YELLOW + 'N/A' + C.END}"
    )
    print(f"  Caps    : {C.GREEN}{len(all_chapters)}{C.END}")

    # ── Selección de capítulos ────────────────────────────────────────────
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

    # Resolver selección → lista de diccionarios de capítulo
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

    # ── Preparar carpeta ───────────────────────────────────────────────────
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

    # ── Descargar capítulos ────────────────────────────────────────────────
    ext_out = OUTPUT_TYPE.lower()
    ok = 0

    print(f"\n{C.CYAN}[*] Descargando {len(selected)} capítulo(s)…{C.END}\n")

    for i, chap in enumerate(selected, 1):
        num = chap["num"]
        safe_chap_title = _safe_name(chap["title"])
        # Nombre del archivo: "0094_Título del Capítulo"
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

        # Carpeta temporal para las imágenes del capítulo
        chap_dir = os.path.join(out_folder, chap_name)
        os.makedirs(chap_dir, exist_ok=True)

        # Descarga paralela
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

        # Empaquetar
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
#  BÚSQUEDA
# ══════════════════════════════════════════════════════════════════════════════
def search_series(query: str, mode: Mode) -> list[dict[str, str]]:
    print(f"  {C.CYAN}[*] Cargando catálogo de {mode}…{C.END}")
    all_s = fetch_series_list(mode)
    if not all_s:
        print(f"  {C.YELLOW}[!] Catálogo vacío o no disponible.{C.END}")
        return []

    print(f"  {C.GREEN}[+] {len(all_s)} series en catálogo.{C.END}")

    # Sin query → devolver todo
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
#  UTILIDADES DE PARSEO
# ══════════════════════════════════════════════════════════════════════════════
def _parse_nums(s: str) -> set[int]:
    """'3,5-7,10' → {3,5,6,7,10}"""
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
    """Igual pero los números son posiciones 1-N → índices 0-based."""
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


def results_menu(
    results: list[dict[str, str]],
    query: str,
    mode: Mode,
) -> None:
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
        print(f"  ├─ {C.BOLD}2.{C.END} Buscar serie")
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
            mode = choose_mode()
            query = input(f"  {C.CYAN}Búsqueda (Enter para ver todo): {C.END}").strip()
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
