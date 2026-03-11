"""
18MH DOWNLOADER v1.3
Fuente: 18mh.org

Instalación:
    pip install requests pillow beautifulsoup4 lxml

Uso:
    python 18mh_downloader.py
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
from urllib.parse import quote, urljoin

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
SITE_URL = "https://18mh.org"
OUTPUT_TYPE = "zip"  # 'zip' | 'cbz' | 'pdf'
USER_FORMAT = "webp"  # 'original' | 'jpg' | 'png' | 'webp'
DELETE_TEMP = True
MAX_WORKERS = 8
TIMEOUT = (15, 45)
RETRY_DELAY = 2.0
DEBUG = False


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
    print(f"║  {C.BOLD}18MH DOWNLOADER v1.3{C.END}{C.BLUE}                    ║")
    print(f"║  {C.DIM}18mh.org{C.END}{C.BLUE}                                ║")
    print(f"╚══════════════════════════════════════════╝{C.END}\n")


def bar(done: int, total: int, width: int = 32) -> str:
    pct = done / max(total, 1)
    fill = int(width * pct)
    return f"[{C.CYAN}{'█' * fill}{C.DIM}{'─' * (width - fill)}{C.END}] {done}/{total}"


def dbg(msg: str) -> None:
    if DEBUG:
        print(f"  {C.DIM}[dbg] {msg}{C.END}")


# ══════════════════════════════════════════════════════════════════════════════
#  SESIÓN HTTP
# ══════════════════════════════════════════════════════════════════════════════
_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": f"{SITE_URL}/",
}

SESSION = requests.Session()
SESSION.headers.update(_BASE_HEADERS)


def _get_raw(url: str, referer: str = "", retries: int = 3) -> Optional[bytes]:
    hdrs = {"Referer": referer} if referer else {}
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=TIMEOUT, headers=hdrs)
            if r.status_code == 200 and r.content:
                return r.content
            if r.status_code in (403, 404):
                return None
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    return None


def fetch_html(url: str, referer: str = "") -> Optional[str]:
    raw = _get_raw(url, referer)
    return raw.decode("utf-8", errors="replace") if raw else None


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ══════════════════════════════════════════════════════════════════════════════
#  PARSEO DE TARJETAS
# ══════════════════════════════════════════════════════════════════════════════
def _parse_cards(html: str) -> list:
    """Extrae las series, evadiendo las recomendaciones del fondo."""
    soup = _soup(html)

    # 1. Destruir la sección de "Recomendados" para limpiar la página
    for heading in soup.find_all(["h2", "h3"]):
        text = heading.get_text(strip=True)
        if any(kw in text for kw in ["您可能喜歡", "猜你喜歡", "推荐", "推薦"]):
            parent = heading.parent
            # Si el header está en un contenedor div (lo habitual), borramos todo el div
            if parent and parent.name == "div":
                parent.decompose()
            else:
                heading.decompose()

    results = []
    seen = set()

    # 2. Extraer los links limpios
    for a in soup.find_all("a", href=re.compile(r"/manga/([^/?#]+)/?$")):
        href = a.get("href", "").rstrip("/")
        slug = href.split("/")[-1]

        if slug in seen or slug == "get":
            continue

        title = slug
        h3 = a.find(["h3", "h4", "p", "span"])
        img = a.find("img")

        if h3 and h3.get_text(strip=True):
            title = h3.get_text(strip=True)
        elif img and img.get("alt"):
            title = img.get("alt")

        seen.add(slug)
        results.append({"slug": slug, "title": title})

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  METADATOS Y CAPÍTULOS
# ══════════════════════════════════════════════════════════════════════════════
def parse_series_meta(slug: str) -> Optional[dict]:
    url = f"{SITE_URL}/manga/{slug}"
    html = fetch_html(url)
    if not html:
        return None

    soup = _soup(html)
    title_tag = soup.find("h1")
    title = title_tag.get_text(strip=True) if title_tag else slug
    title = re.sub(r"\s*(完結|連載中|连载中|完结)\s*$", "", title).strip()

    mid = None
    mid_match = re.search(r'data-mid="(\d+)"', html)
    if mid_match:
        mid = mid_match.group(1)

    status = "完結" if "完結" in html else "連載中"

    summary = ""
    for p in soup.find_all("p"):
        t = p.get_text(strip=True)
        if len(t) > 20 and not re.search(r"(Copyright|18歲|警告)", t):
            summary = t
            break

    return {
        "slug": slug,
        "title": title,
        "mid": mid,
        "status": status,
        "summary": summary,
        "url": url,
    }


def get_chapter_list(mid: str) -> list:
    if not mid:
        return []
    api_url = f"{SITE_URL}/manga/get?mid={mid}&mode=all"
    html = fetch_html(api_url)
    if not html:
        return []

    soup = _soup(html)
    chapters = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(strip=True)
        if not title or "javascript" in href or title in ["排序", "最新章節"]:
            continue
        chapters.append({"title": title, "url": urljoin(SITE_URL, href)})

    if chapters:
        chapters.reverse()
    return chapters


# ══════════════════════════════════════════════════════════════════════════════
#  EXTRACCIÓN Y DESCARGA DE IMÁGENES
# ══════════════════════════════════════════════════════════════════════════════
_EXCLUDE_IMG = ("/logo", "/icon", "/ads", "ad/", "cover/", "avatar", ".gif")


def _valid_img(u: str) -> bool:
    return bool(u and u.startswith("http") and not any(x in u for x in _EXCLUDE_IMG))


def extract_chapter_images(chap_url: str) -> list:
    html = fetch_html(chap_url)
    if not html:
        return []
    soup = _soup(html)
    candidates = []

    for img in soup.find_all("img"):
        for attr in ("data-src", "data-original", "data-lazy-src", "src"):
            u = img.get(attr, "")
            if u and not u.startswith("data:") and _valid_img(u):
                if not u.startswith("http"):
                    u = urljoin(SITE_URL, u)
                candidates.append(u)
                break

    if not candidates:
        for u in re.findall(
            r'(https?://[^\s"\'<>]+\.(?:jpe?g|png|webp)(?:\?[^\s"\'<>]*)?)', html, re.I
        ):
            if _valid_img(u):
                candidates.append(u)

    seen = set()
    return [u for u in candidates if not (u in seen or seen.add(u))]


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
    if not url:
        return False
    ext = _ext_for(url)
    path = os.path.join(folder, f"{idx + 1:03d}.{ext}")
    if os.path.exists(path):
        return True
    raw = _get_raw(url, referer=SITE_URL)
    if raw:
        save_image(raw, path)
        return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  EMPAQUETADO
# ══════════════════════════════════════════════════════════════════════════════
def pack_folder(src: str, out: str, fmt: str) -> None:
    files = sorted(
        os.path.join(src, f)
        for f in os.listdir(src)
        if os.path.isfile(os.path.join(src, f))
    )
    if not files:
        return
    if fmt == "pdf" and HAS_PILLOW and Image is not None:
        pages = []
        for p in files:
            try:
                pages.append(Image.open(p).convert("RGB"))
            except:
                pass
        if pages:
            pages[0].save(out, save_all=True, append_images=pages[1:])
    else:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, os.path.basename(f))


# ══════════════════════════════════════════════════════════════════════════════
#  INTERFAZ Y MENÚS
# ══════════════════════════════════════════════════════════════════════════════
def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


def _parse_positions(s: str, length: int) -> list:
    idxs = set()
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
    meta = parse_series_meta(slug)

    if not meta or not meta.get("mid"):
        print(f"  {C.RED}[!] No se pudo cargar la metadata o el ID del manga.{C.END}")
        return

    title = meta["title"]
    mid = meta["mid"]

    print(f"  {C.DIM}Obteniendo lista de capítulos (mid={mid})…{C.END}", end="\r")
    chapters = get_chapter_list(mid)

    print(f"  {' ' * 55}", end="\r")
    print(f"\n  {C.BOLD}{C.GREEN}{title}{C.END}")
    print(f"  Estado : {meta['status']}")
    if meta["summary"]:
        s = meta["summary"]
        print(f"  Sinopsis: {(s[:100] + '…') if len(s) > 100 else s}")
    print(f"  Caps   : {C.GREEN}{len(chapters)}{C.END}")

    if not chapters:
        print(f"\n  {C.YELLOW}[!] 0 capítulos detectados.{C.END}")
        return

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
            return
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
        return

    out_folder = _safe(title)
    os.makedirs(out_folder, exist_ok=True)
    ext_out = OUTPUT_TYPE.lower()
    ok = 0

    print(f"\n{C.CYAN}[*] Descargando {len(selected)} cap(s)…{C.END}\n")

    for i, chap in enumerate(selected, 1):
        lbl = f"[{i}/{len(selected)}] {chap['title'][:50]}"
        print(f"  {C.BOLD}{lbl}{C.END}", end=" ", flush=True)

        imgs = extract_chapter_images(chap["url"])
        safe_t = _safe(chap["title"])
        out_f = os.path.join(out_folder, f"{i:04d} - {safe_t}.{ext_out}")

        if imgs:
            print(f"\n    → {len(imgs)} págs", flush=True)
            tmp = os.path.join(out_folder, f"_tmp_{i}")
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
            print(f"    {C.GREEN}✓ → {os.path.basename(out_f)}{C.END}")
        else:
            print(f"\n    {C.RED}× Sin imágenes encontradas{C.END}")

    print(f"\n{C.GREEN}[+] {ok}/{len(selected)} completados  → {out_folder}/{C.END}")


# ── Menú Paginado Inteligente ──
def paginated_menu(base_path: str, label: str) -> None:
    current_page = 1
    cache = {}

    while True:
        if current_page not in cache:
            print(f"\n  {C.CYAN}[*] Cargando página {current_page}…{C.END}")
            urls_to_try = [
                f"{SITE_URL}{base_path}?page={current_page}",
                f"{SITE_URL}{base_path}/{current_page}",
                f"{SITE_URL}{base_path}/page/{current_page}",
            ]

            if current_page == 1:
                urls_to_try = [f"{SITE_URL}{base_path}"]

            batch = []
            for url in urls_to_try:
                if not url:
                    continue
                html = fetch_html(url)
                if html:
                    cand = _parse_cards(html)
                    # Prevención bucle infinito si el server devuelve pag 1
                    if current_page > 1 and 1 in cache and cand:
                        if [c["slug"] for c in cand] == [c["slug"] for c in cache[1]]:
                            continue
                    if cand:
                        batch = cand
                        break

            cache[current_page] = batch

        items = cache[current_page]

        header()
        print(f"  {C.PURPLE}'{label}'  (Página {current_page}){C.END}")
        print(f"  {'━' * 58}")
        if not items:
            print(f"  {C.RED}No hay más resultados en esta página.{C.END}")
        else:
            for i, r in enumerate(items):
                print(f"  {C.BOLD}{i + 1:2d}.{C.END} {r['title'][:52]}")
                print(f"       {C.DIM}{r['slug']}{C.END}")
        print(f"  {'━' * 58}")

        nav = []
        if len(items) >= 10:
            nav.append(f"{C.CYAN}n{C.END}=sig")
        if current_page > 1:
            nav.append(f"{C.CYAN}p{C.END}=ant")
        nav.append(f"{C.CYAN}q{C.END}=volver")
        print("  " + "  ".join(nav) + "  o número")

        sel = input(f"\n  {C.YELLOW}➜ {C.END}").strip().lower()
        if sel == "n" and len(items) >= 10:
            current_page += 1
        elif sel == "p" and current_page > 1:
            current_page -= 1
        elif sel == "q":
            break
        elif sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(items):
                show_and_download(items[idx]["slug"])
                input(f"\n  {C.CYAN}Enter para continuar…{C.END}")


# ── Opción 1: Buscar/URL ──
def option1() -> None:
    raw = input(f"  {C.CYAN}URL, nombre o slug ➜ {C.END}").strip()
    if not raw:
        return

    m = re.search(r"18mh\.org/manga/([^/?#]+)", raw)
    if m:
        show_and_download(m.group(1))
        return

    if (
        " " not in raw
        and not raw.startswith("http")
        and re.match(r"^[a-zA-Z0-9\-]+$", raw)
    ):
        test = parse_series_meta(raw)
        if test and test.get("mid"):
            show_and_download(raw)
            return

    path = f"/s/{quote(raw)}"
    paginated_menu(path, f"Búsqueda: {raw}")


# ── Opción 2: Catálogo ──
_CAT_OPTS = {
    "0": ("/manga", "Todas las Series"),
    "1": ("/hots", "人氣推薦 (Recomendadas)"),
    "2": ("/dayup", "熱門更新 (Populares)"),
    "3": ("/newss", "最新上架 (Recientes)"),
    "4": ("/manga-genre/hanman", "韓漫 (Manhwa)"),
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

        path, lbl = _CAT_OPTS[op]
        paginated_menu(path, lbl)


# ── Menú Principal ──
def main() -> None:
    print(f"{C.DIM}Iniciando sesión en {SITE_URL}…{C.END}", end=" ", flush=True)
    _get_raw(SITE_URL)
    print(f"✓{C.END}")

    while True:
        header()
        print(f"  {C.PURPLE}{C.BOLD}Menú Principal{C.END}")
        print(f"  ├─ {C.BOLD}1.{C.END} Buscar / URL / Slug")
        print(f"  │     {C.DIM}↳ Ej: yiqixiangyongba o el nombre de la serie{C.END}")
        print(
            f"  ├─ {C.BOLD}2.{C.END} Catálogo {C.DIM}(Populares / Recientes / Manhwa){C.END}"
        )
        print(f"  └─ {C.BOLD}3.{C.END} Salir")
        print(f"\n  {C.DIM}Config: {OUTPUT_TYPE.upper()}  {USER_FORMAT.upper()}{C.END}")

        op = input(f"\n  {C.YELLOW}Opción ➜ {C.END}").strip()
        if op == "1":
            option1()
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
