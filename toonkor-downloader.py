"""
TOONKOR DOWNLOADER v1.5.0
Dependencias:  pip install scrapling requests pillow
"""

import base64
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import requests

# ── Scrapling (reemplaza BeautifulSoup completamente) ──────────────────────────
# Instalar: pip install scrapling
# Scrapling usa lxml internamente → 3-10x más rápido que html.parser de BS4
# API:
#   page  = Selector(html, url=base_url)  ← parsea el HTML
#   elems = page.css("selector")         ← lista (vacía si no hay nada)
#   elem  = page.css("selector").first   ← primer elemento o None
#   elem.text                            ← texto interno
#   elem.attrib["href"]                  ← atributo (KeyError si no existe)
#   elem.attrib.get("href", "")          ← atributo con default seguro
# ─────────────────────────────────────────────────────────────────────────────
from scrapling import Selector

try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


# ─── UI ───────────────────────────────────────────────────────────────────────
class UI:
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    END = "\033[0m"

    @staticmethod
    def header():
        os.system("cls" if os.name == "nt" else "clear")
        print(f"{UI.BLUE}╔══════════════════════════════╗")
        print(f"║ {UI.BOLD}TOONKOR DOWNLOADER v1.5.0{UI.END}{UI.BLUE} ║")
        print(f"╚══════════════════════════════╝{UI.END}")


# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────
BASE_URL = "https://tkor098.com/"
WEBTOON_PAGE = "웹툰"
OUTPUT_TYPE = "zip"  # 'zip' | 'cbz' | 'pdf'
USER_FORMAT = "webp"  # 'original' | 'jpg' | 'png' | 'webp'
DELETE_TEMP = True
MAX_WORKERS_DL = 20
MAX_RESULTS_PAGE = 20
# ─────────────────────────────────────────────────────────────────────────────

METADATA_CACHE = {}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": BASE_URL,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",  # sin 'br' → evita error Brotli en requests
}


def make_session():
    """Sesión con cookies iniciales del home."""
    s = requests.Session()
    s.headers.update(headers)
    try:
        s.get(BASE_URL, timeout=10)
    except Exception:
        pass
    return s


SESSION = make_session()


# ─── LÓGICA PRINCIPAL ─────────────────────────────────────────────────────────
class ToonkorLogic:
    CDN_HOSTS = ["aws-cloud-no2.site", "aws-cloud-no1.site", "aws-cloud-no3.site"]
    _UI_PATHS = ("/images/", "/bann/", "/img/", "/icons/", "/logo")

    def get_series_url(self, slug: str) -> str:
        return f"{BASE_URL}{slug}"

    def get_chapter_url(self, series_slug: str, chapter_num: int) -> str:
        return f"{BASE_URL}{series_slug}_{chapter_num}화.html"

    # ── parse_series_page ────────────────────────────────────────────────────
    def parse_series_page(self, html: str, series_slug: str):
        """
        Extrae título, autor, sinopsis y lista de capítulos.
        100% Scrapling — sin BeautifulSoup.
        """
        page = Selector(html, url=BASE_URL)

        # ── Título ──────────────────────────────────────────────────────────
        title = series_slug
        for sel in ["h1", ".toon-title", ".series-title", ".view-title", "#toon_title"]:
            node = page.css(sel).first
            if node and node.text.strip():
                title = node.text.strip()
                break

        # ── Autor + Sinopsis ─────────────────────────────────────────────────
        # El meta description tiene formato:
        #   "<título> 작가 <autor> 총편수 총 <N>화 <sinopsis>"
        # FIX: re.search en lugar de re.match  (el título precede a "작가")
        autor = ""
        sinopsis = ""

        meta = page.css('meta[name="description"]').first
        if meta:
            content = meta.attrib.get("content", "").strip()
            m = re.search(
                r"작가\s+(.+?)\s+총편수\s+총\s+\d+화\s*(.*)", content, re.DOTALL
            )
            if m:
                autor = m.group(1).strip()
                sinopsis = m.group(2).strip()[:500]
            else:
                sinopsis = content[:500]

        # Fallback autor via CSS
        if not autor:
            for sel in [".writer", ".author", ".toon-author", "[class*='author']"]:
                node = page.css(sel).first
                if node and node.text.strip():
                    autor = node.text.strip()
                    break

        # Fallback sinopsis via CSS
        if not sinopsis:
            for sel in [
                ".toon-descript",
                ".synopsis",
                ".series-desc",
                ".view-content",
                "#toon_desc",
                ".story",
            ]:
                node = page.css(sel).first
                if node and node.text.strip():
                    sinopsis = node.text.strip()[:500]
                    break

        # ── Capítulos ────────────────────────────────────────────────────────
        slug_esc = re.escape(series_slug)
        chapter_pattern = re.compile(rf"/{slug_esc}_(\d+)화\.html", re.IGNORECASE)
        nums = set()

        # Scrapling itera todos los <a href>
        for a in page.css("a[href]"):
            href = a.attrib.get("href", "")
            m2 = chapter_pattern.search(href)
            if m2:
                nums.add(int(m2.group(1)))

        # También en el HTML crudo (puede haber URLs en JS inline)
        for m2 in chapter_pattern.finditer(html):
            nums.add(int(m2.group(1)))

        chapters = sorted(nums, reverse=True)  # más reciente primero
        return title, autor, sinopsis, chapters

    # ── extract_images_from_chapter ──────────────────────────────────────────
    def extract_images_from_chapter(self, chapter_html: str) -> list:
        """
        3 estrategias en orden de prioridad:
          1. Base64 decode de var toon_img (método nativo del sitio).
          2. CDN regex en HTML crudo (fallback).
          3. Scrapling sobre #toon_img (fallback final).
        """

        # ── 1. Base64 ────────────────────────────────────────────────────────
        b64 = re.search(r"var toon_img\s*=\s*'([^']+)';", chapter_html)
        if b64:
            try:
                decoded = base64.b64decode(b64.group(1)).decode("utf-8")
                inner_page = Selector(decoded, url=BASE_URL)
                urls = [img.attrib.get("src", "") for img in inner_page.css("img[src]")]
                valid = [
                    u
                    for u in urls
                    if u.startswith("http") and not any(p in u for p in self._UI_PATHS)
                ]
                if valid:
                    return valid
            except Exception as e:
                print(f"{UI.RED}[!] Base64 error: {e}{UI.END}")

        # ── 2. CDN regex ─────────────────────────────────────────────────────
        cdn_re = re.compile(
            r"https?://(?:" + "|".join(re.escape(h) for h in self.CDN_HOSTS) + r")"
            r'/[^\s"\'<>]+\.(?:jpe?g|png|webp|gif)',
            re.IGNORECASE,
        )
        cdn_urls = list(dict.fromkeys(cdn_re.findall(chapter_html)))
        if cdn_urls:
            return cdn_urls

        # ── 3. Scrapling / #toon_img ─────────────────────────────────────────
        page = Selector(chapter_html, url=BASE_URL)
        toon_div = page.css("#toon_img").first
        scope = toon_div if toon_div else page

        candidates = [
            src
            for img in scope.css("img")
            for src in [img.attrib.get("src") or img.attrib.get("data-src") or ""]
            if src.startswith("https://")
            and not any(p in src for p in self._UI_PATHS)
            and any(ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"])
        ]
        if candidates:
            return candidates

        print(f"{UI.YELLOW}[!] Sin imágenes. ¿El sitio requiere JS completo?{UI.END}")
        return []


# ─── UTILIDADES ───────────────────────────────────────────────────────────────
def parse_chapter_nums(s: str) -> set:
    """'1-5,10,20' → {1,2,3,4,5,10,20}"""
    s = s.lower().replace(" ", "")
    nums = set()
    try:
        for part in s.split(","):
            if "-" in part:
                a, b = map(int, part.split("-"))
                nums.update(range(a, b + 1))
            elif part.isdigit():
                nums.add(int(part))
    except Exception:
        pass
    return nums


def parse_sel(s: str, max_len: int) -> list:
    """'1-3,5' → [0,1,2,4]  (índices de lista 0-based)"""
    s = s.lower().replace(" ", "")
    if s == "all":
        return list(range(max_len))
    indices = set()
    try:
        for part in s.split(","):
            if "-" in part:
                a, b = map(int, part.split("-"))
                indices.update(i for i in range(a - 1, b) if 0 <= i < max_len)
            elif part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < max_len:
                    indices.add(idx)
    except Exception:
        pass
    return sorted(indices)


# ─── CATÁLOGO DE SERIES ───────────────────────────────────────────────────────
# Slugs de nav / secciones que no son series
_NAV_SLUGS = {
    "웹툰",
    "애니",
    "주소안내",
    "단행본",
    "망가",
    "포토툰",
    "코사이트",
    "토토보증업체",
    "notice",
    "bbs",
}


def fetch_series_list() -> list:
    """Obtiene el catálogo. 100% Scrapling."""
    url = f"{BASE_URL}{WEBTOON_PAGE}"
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            print(f"{UI.RED}[!] HTTP {r.status_code}{UI.END}")
            return []

        page = Selector(r.text, url=url)
        series = []
        seen = set()

        for a in page.css("a[href]"):
            href = a.attrib.get("href", "")
            slug = href.strip("/")

            if not slug:
                continue
            if slug in seen or slug in _NAV_SLUGS:
                continue
            if slug.startswith(("http", "#", "javascript", "bbs", "img")):
                continue
            if "board.php" in href:
                continue

            seen.add(slug)
            text = a.text.strip()

            # Intentar título del contenedor padre
            title = slug
            parent = a.parent
            if parent:
                h = parent.css("h2, h3, h4, strong, b").first
                if h and h.text.strip():
                    title = h.text.strip()
            elif "더 읽기" not in text and text:
                title = text

            series.append({"slug": slug, "title": title})

        return series

    except Exception as e:
        print(f"{UI.RED}[!] fetch_series_list: {e}{UI.END}")
        return []


def search_query(query: str) -> list:
    print(f"{UI.CYAN}[*] Buscando '{query}'...{UI.END}")
    all_s = fetch_series_list()
    if not all_s:
        print(f"{UI.YELLOW}[!] Catálogo vacío. Usando slug directo.{UI.END}")
        return [query]
    q = query.lower()
    return [
        s["slug"] for s in all_s if q in s["title"].lower() or q in s["slug"].lower()
    ]


# ─── DESCARGA ─────────────────────────────────────────────────────────────────
def save_img(raw: bytes, path: str, fmt: str):
    if not HAS_PILLOW or fmt == "original":
        with open(path, "wb") as f:
            f.write(raw)
        return
    try:
        img = Image.open(BytesIO(raw))
        if fmt.lower() in ("jpg", "jpeg") and img.mode in ("RGBA", "LA"):
            bg = Image.new(img.mode[:-1], img.size, (255, 255, 255))
            bg.paste(img, img.split()[-1])
            img = bg.convert("RGB")
        img.save(path, quality=92)
    except Exception:
        with open(path, "wb") as f:
            f.write(raw)


def dl_worker(args):
    url, folder, idx = args
    if not url.startswith("https://"):
        return False

    ext = USER_FORMAT if (HAS_PILLOW and USER_FORMAT != "original") else "jpg"
    url_ext = os.path.splitext(url.split("?")[0])[-1].lower().lstrip(".")
    if url_ext in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = url_ext

    path = f"{folder}/{idx + 1:03d}.{ext}"
    if os.path.exists(path):
        return True

    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=(10, 15))
            if r.status_code == 200:
                save_img(r.content, path, USER_FORMAT)
                return True
        except Exception:
            time.sleep(attempt + 1)

    print(f"\n{UI.RED}[!] Falló: {url}{UI.END}")
    return False


def download_chapter(logic: ToonkorLogic, series_slug: str, chapter_num: int) -> list:
    url = logic.get_chapter_url(series_slug, chapter_num)
    try:
        r = SESSION.get(url, timeout=(10, 15))
        if r.status_code != 200:
            print(f"\n{UI.YELLOW}[!] Cap {chapter_num} → HTTP {r.status_code}{UI.END}")
            return []
        return logic.extract_images_from_chapter(r.text)
    except Exception as e:
        print(f"\n{UI.RED}[!] Error cap {chapter_num}: {e}{UI.END}")
        return []


def download_gallery(
    series_slug: str, logic: ToonkorLogic, chapters_to_dl: str = "all"
):
    try:
        # ── Metadata ──────────────────────────────────────────────────────────
        data = METADATA_CACHE.get(series_slug)
        if not data:
            print(f"{UI.CYAN}[*] Cargando metadata '{series_slug}'...{UI.END}")
            r = SESSION.get(logic.get_series_url(series_slug), timeout=15)
            if r.status_code != 200:
                print(f"{UI.RED}[!] HTTP {r.status_code}{UI.END}")
                return

            title, autor, sinopsis, chapters = logic.parse_series_page(
                r.text, series_slug
            )

            if not chapters:
                print(f"{UI.YELLOW}[!] Sin capítulos detectados. HEAD 1-30...{UI.END}")
                chapters = []
                for n in range(1, 31):
                    resp = SESSION.head(
                        logic.get_chapter_url(series_slug, n), timeout=5
                    )
                    if resp.status_code == 200:
                        chapters.append(n)
                    elif chapters:
                        break

            data = {
                "title": title,
                "autor": autor,
                "sinopsis": sinopsis,
                "chapters": chapters,
            }
            METADATA_CACHE[series_slug] = data

        # ── Mostrar info ───────────────────────────────────────────────────────
        raw_title = data.get("title", series_slug)
        clean_title = "".join(
            c for c in raw_title if c.isalnum() or c in " -_[]"
        ).strip()
        folder = f"{clean_title} [{series_slug}]"
        os.makedirs(folder, exist_ok=True)

        print(f"\n{UI.GREEN}[+] {UI.BOLD}{raw_title}{UI.END}")
        print(f"   Autor  : {data['autor'] or UI.YELLOW + 'No encontrado' + UI.END}")
        print(
            f"   Sinops : {(data['sinopsis'] or UI.YELLOW + 'No encontrada' + UI.END)[:120]}"
        )
        print(f"   Caps   : {len(data['chapters'])} detectados")

        with open(f"{folder}/info.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "slug": series_slug,
                    "title": raw_title,
                    "autor": data["autor"],
                    "sinopsis": data["sinopsis"],
                    "chapters": data["chapters"],
                    "url": logic.get_series_url(series_slug),
                },
                f,
                indent=4,
                ensure_ascii=False,
            )

        all_chapters = data["chapters"]
        if not all_chapters:
            print(f"\n{UI.RED}[!] Sin capítulos. ¿Slug correcto?{UI.END}")
            return

        # ── Selección ──────────────────────────────────────────────────────────
        if chapters_to_dl == "all":
            selected = all_chapters
        else:
            chap_set = set(all_chapters)
            requested = parse_chapter_nums(chapters_to_dl)
            by_num = [c for c in sorted(requested, reverse=True) if c in chap_set]
            if by_num:
                selected = by_num
            else:
                print(f"{UI.YELLOW}[!] Sin match por número; usando índices.{UI.END}")
                selected = [
                    all_chapters[i]
                    for i in parse_sel(chapters_to_dl, len(all_chapters))
                ]

        print(f"\n{UI.GREEN}[v] {len(selected)} capítulo(s){UI.END}")

        # ── Descarga paralela ──────────────────────────────────────────────────
        all_images = []
        for chap in selected:
            print(f"  {UI.CYAN}[-] Cap {chap}...{UI.END}", end=" ", flush=True)
            imgs = download_chapter(logic, series_slug, chap)
            chap_folder = os.path.join(folder, f"chapter_{chap:04d}")
            os.makedirs(chap_folder, exist_ok=True)
            all_images.extend((url, chap_folder, i) for i, url in enumerate(imgs))
            print(f"{len(imgs)} imgs")

        if not all_images:
            print(f"\n{UI.RED}[!] Sin imágenes.{UI.END}")
            return

        print()  # Line break before global progress bar
        comp = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
            futures = [exe.submit(dl_worker, a) for a in all_images]
            for _ in as_completed(futures):
                comp += 1
                perc = int(30 * comp // len(all_images))
                sys.stdout.write(
                    f"\r   [{UI.CYAN}{'█' * perc}{'-' * (30 - perc)}{UI.END}] {comp}/{len(all_images)}"
                )
                sys.stdout.flush()
        print()

        # ── Empaquetado ────────────────────────────────────────────────────────
        ext_out = OUTPUT_TYPE.lower()
        print(f"   [*] Generando {ext_out.upper()}s por capítulo...")

        for chap in selected:
            chap_folder_name = f"chapter_{chap:04d}"
            chap_folder_path = os.path.join(folder, chap_folder_name)
            if not os.path.exists(chap_folder_path):
                continue

            out_file = os.path.join(folder, f"{chap_folder_name}.{ext_out}")

            if ext_out == "pdf" and HAS_PILLOW:
                paths = sorted(
                    os.path.join(chap_folder_path, f)
                    for f in os.listdir(chap_folder_path)
                    if not f.endswith(".json")
                )
                pages = []
                for p in paths:
                    try:
                        pages.append(Image.open(p).convert("RGB"))
                    except Exception:
                        pass
                if pages:
                    pages[0].save(out_file, save_all=True, append_images=pages[1:])
            else:
                with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in os.listdir(chap_folder_path):
                        full = os.path.join(chap_folder_path, f)
                        if os.path.isfile(full):
                            zf.write(full, f)

            if DELETE_TEMP:
                import shutil

                shutil.rmtree(chap_folder_path)

        print(f"   {UI.GREEN}[OK] Empaquetado completado en: {folder}{UI.END}")

    except Exception as e:
        print(f"\n{UI.RED}[!] {e}{UI.END}")
        import traceback

        traceback.print_exc()


# ─── MENÚ ─────────────────────────────────────────────────────────────────────
def main():
    logic = ToonkorLogic()
    while True:
        UI.header()
        print(f" {UI.PURPLE}Menú Principal:{UI.END}")
        print(f" ├── {UI.BOLD}1.{UI.END} Descargar por Slug")
        print(f" ├── {UI.BOLD}2.{UI.END} Buscar Series")
        print(f" └── {UI.BOLD}3.{UI.END} Salir")
        print(f"\n {UI.PURPLE}Configuración Actual:{UI.END}")
        print(f" ├── Salida: {UI.CYAN}{OUTPUT_TYPE.upper()}{UI.END}")
        print(f" └── Imagen: {UI.CYAN}{USER_FORMAT.upper()}{UI.END}")

        op = input(f"\n{UI.YELLOW} Selecciona una opción > {UI.END}").strip()

        if op == "1":
            slug = input(f"{UI.CYAN} [?] Slug: {UI.END}").strip()
            if slug:
                caps = (
                    input(f"{UI.CYAN} Capítulos ('1-5,10' o 'all'): {UI.END}").strip()
                    or "all"
                )
                download_gallery(slug, logic, caps)
                input(f"\n{UI.CYAN}Enter...{UI.END}")

        elif op == "2":
            q = input(f"{UI.CYAN} [?] Búsqueda: {UI.END}").strip()
            if not q:
                continue
            slugs = search_query(q)
            if not slugs:
                print(f"{UI.RED} Sin resultados.{UI.END}")
                time.sleep(2)
                continue
            page = 0
            while True:
                UI.header()
                start = page * MAX_RESULTS_PAGE
                end = min(start + MAX_RESULTS_PAGE, len(slugs))
                print(f" {UI.PURPLE}'{q}' → {len(slugs)} resultados{UI.END}")
                print(f" {'━' * 54}")
                for i, s in enumerate(slugs[start:end]):
                    title = METADATA_CACHE.get(s, {}).get("title", s)[:50]
                    print(
                        f" {UI.BOLD}{start + i + 1:3d}.{UI.END} [{UI.GREEN}{s}{UI.END}] {title}"
                    )
                print(f" {'━' * 54}")
                print(
                    f" {UI.CYAN}n{UI.END}=sig  {UI.CYAN}p{UI.END}=ant  {UI.CYAN}q{UI.END}=volver"
                )
                sel = input(f"\n{UI.YELLOW} Acción > {UI.END}").lower().strip()
                if sel == "n" and end < len(slugs):
                    page += 1
                elif sel == "p" and page > 0:
                    page -= 1
                elif sel == "q":
                    break
                elif sel:
                    for i in parse_sel(sel, len(slugs)):
                        caps = (
                            input(f"{UI.CYAN} [?] Caps '{slugs[i]}': {UI.END}").strip()
                            or "all"
                        )
                        download_gallery(slugs[i], logic, caps)
                    input(f"\n{UI.GREEN}Listo. Enter...{UI.END}")

        elif op == "3":
            print(f"{UI.BLUE} ¡Hasta pronto!{UI.END}")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{UI.RED} Ctrl+C{UI.END}")
