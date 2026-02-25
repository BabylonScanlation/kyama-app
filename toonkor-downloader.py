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
from typing import Any
import shutil

import requests
from scrapling import Selector

# Support for PILLOW
has_pillow: bool
try:
    from PIL import Image

    has_pillow = True
except ImportError:
    Image = None  # type: ignore
    has_pillow = False


# ─── UI ───────────────────────────────────────────────────────────────────────
class UI:
    PURPLE: str = "\033[95m"
    CYAN: str = "\033[96m"
    BLUE: str = "\033[94m"
    GREEN: str = "\033[92m"
    YELLOW: str = "\033[93m"
    RED: str = "\033[91m"
    BOLD: str = "\033[1m"
    END: str = "\033[0m"

    @staticmethod
    def header() -> None:
        _ = os.system("cls" if os.name == "nt" else "clear")
        print(f"{UI.BLUE}╔══════════════════════════════╗")
        print(f"║ {UI.BOLD}TOONKOR DOWNLOADER v1.5.0{UI.END}{UI.BLUE} ║")
        print(f"╚══════════════════════════════╝{UI.END}")


# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────
BASE_URL: str = "https://tkor098.com/"
WEBTOON_PAGE: str = "웹툰"
OUTPUT_TYPE: str = "zip"  # 'zip' | 'cbz' | 'pdf'
USER_FORMAT: str = "webp"  # 'original' | 'jpg' | 'png' | 'webp'
DELETE_TEMP: bool = True
MAX_WORKERS_DL: int = 20
MAX_RESULTS_PAGE: int = 20
# ─────────────────────────────────────────────────────────────────────────────

METADATA_CACHE: dict[str, Any] = {}  # pyright: ignore[reportExplicitAny]

headers: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": BASE_URL,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


def make_session() -> requests.Session:
    """Sesión con cookies iniciales del home."""
    s = requests.Session()
    s.headers.update(headers)
    try:
        _ = s.get(BASE_URL, timeout=10)
    except Exception:
        pass
    return s


SESSION: requests.Session = make_session()


# ─── LÓGICA PRINCIPAL ─────────────────────────────────────────────────────────
class ToonkorLogic:
    CDN_HOSTS: list[str] = ["aws-cloud-no2.site", "aws-cloud-no1.site", "aws-cloud-no3.site"]
    _UI_PATHS: tuple[str, ...] = ("/images/", "/bann/", "/img/", "/icons/", "/logo")

    def get_series_url(self, slug: str) -> str:
        return f"{BASE_URL}{slug}"

    def get_chapter_url(self, series_slug: str, chapter_num: int) -> str:
        return f"{BASE_URL}{series_slug}_{chapter_num}화.html"

    def parse_series_page(self, html: str, series_slug: str) -> tuple[str, str, str, list[int]]:
        page: Any = Selector(html, url=BASE_URL)  # pyright: ignore[reportExplicitAny]
        title: str = series_slug
        for sel in ["h1", ".toon-title", ".series-title", ".view-title", "#toon_title"]:
            node: Any = page.css(sel).first  # pyright: ignore[reportAny, reportExplicitAny]
            if node and node.text and node.text.strip():  # pyright: ignore[reportAny]
                title = node.text.strip()  # pyright: ignore[reportAny]
                break

        autor: str = ""
        sinopsis: str = ""

        meta: Any = page.css('meta[name="description"]').first  # pyright: ignore[reportAny, reportExplicitAny]
        if meta:
            content: str = str(meta.attrib.get("content", "")).strip()  # pyright: ignore[reportAny]
            m_meta = re.search(
                r"작가\s+(.+?)\s+총편수\s+총\s+\d+화\s*(.*)", content, re.DOTALL
            )
            if m_meta:
                autor = m_meta.group(1).strip()
                sinopsis = m_meta.group(2).strip()[:500]
            else:
                sinopsis = content[:500]

        if not autor:
            for sel in [".writer", ".author", ".toon-author", "[class*='author']"]:
                node = page.css(sel).first  # pyright: ignore[reportAny]
                if node and node.text and node.text.strip():  # pyright: ignore[reportAny]
                    autor = node.text.strip()  # pyright: ignore[reportAny]
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
                node = page.css(sel).first  # pyright: ignore[reportAny]
                if node and node.text and node.text.strip():  # pyright: ignore[reportAny]
                    sinopsis = node.text.strip()[:500]  # pyright: ignore[reportAny]
                    break

        slug_esc: str = re.escape(series_slug)
        chapter_pattern: re.Pattern[str] = re.compile(rf"/{slug_esc}_(\d+)화\.html", re.IGNORECASE)
        nums: set[int] = set()

        for a in page.css("a[href]"):  # pyright: ignore[reportAny]
            href: str = str(a.attrib.get("href", ""))  # pyright: ignore[reportAny]
            m2 = chapter_pattern.search(href)
            if m2:
                nums.add(int(m2.group(1)))

        for m2 in chapter_pattern.finditer(html):
            nums.add(int(m2.group(1)))

        chapters: list[int] = sorted(list(nums), reverse=True)
        return title, autor, sinopsis, chapters

    def extract_images_from_chapter(self, chapter_html: str) -> list[str]:
        b64: Any = re.search(r"var toon_img\s*=\s*'([^']+)';", chapter_html)  # pyright: ignore[reportExplicitAny]
        if b64:
            try:
                decoded: str = base64.b64decode(b64.group(1)).decode("utf-8")  # pyright: ignore[reportAny]
                inner_page: Any = Selector(decoded, url=BASE_URL)  # pyright: ignore[reportExplicitAny]
                urls: list[str] = [str(img.attrib.get("src", "")) for img in inner_page.css("img[src]")]  # pyright: ignore[reportAny]
                valid: list[str] = [
                    u
                    for u in urls
                    if u.startswith("http") and not any(p in u for p in self._UI_PATHS)
                ]
                if valid:
                    return valid
            except Exception as e:
                print(f"{UI.RED}[!] Base64 error: {e}{UI.END}")

        cdn_re: re.Pattern[str] = re.compile(
            r"https?://(?:" + "|".join(re.escape(h) for h in self.CDN_HOSTS) + r")"
            + r'/[^\s"\'<>]+\.(?:jpe?g|png|webp|gif)',
            re.IGNORECASE,
        )
        cdn_urls: list[str] = list(dict.fromkeys(cdn_re.findall(chapter_html)))
        if cdn_urls:
            return cdn_urls

        page: Any = Selector(chapter_html, url=BASE_URL)  # pyright: ignore[reportExplicitAny]
        toon_div: Any = page.css("#toon_img").first  # pyright: ignore[reportAny, reportExplicitAny]
        scope: Any = toon_div if toon_div else page  # pyright: ignore[reportAny, reportExplicitAny]

        candidates: list[str] = [
            src
            for img in scope.css("img")  # pyright: ignore[reportAny]
            for src in [str(img.attrib.get("src") or img.attrib.get("data-src") or "")]  # pyright: ignore[reportAny]
            if src.startswith("https://")
            and not any(p in src for p in self._UI_PATHS)
            and any(ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"])
        ]
        if candidates:
            return candidates

        print(f"{UI.YELLOW}[!] Sin imágenes. ¿El sitio requiere JS completo?{UI.END}")
        return []


# ─── UTILIDADES ───────────────────────────────────────────────────────────────
def parse_chapter_nums(s: str) -> set[int]:
    s = s.lower().replace(" ", "")
    nums: set[int] = set()
    try:
        for part in s.split(","):
            if "-" in part:
                a_s, b_s = part.split("-")
                a, b = int(a_s), int(b_s)
                nums.update(range(a, b + 1))
            elif part.isdigit():
                nums.add(int(part))
    except Exception:
        pass
    return nums


def parse_sel(s: str, max_len: int) -> list[int]:
    s = s.lower().replace(" ", "")
    if s == "all":
        return list(range(max_len))
    indices: set[int] = set()
    try:
        for part in s.split(","):
            if "-" in part:
                a_s, b_s = part.split("-")
                a, b = int(a_s), int(b_s)
                indices.update(i for i in range(a - 1, b) if 0 <= i < max_len)
            elif part.isdigit():
                idx: int = int(part) - 1
                if 0 <= idx < max_len:
                    indices.add(idx)
    except Exception:
        pass
    return sorted(list(indices))


_NAV_SLUGS: set[str] = {
    "웹툰", "애니", "주소안내", "단행본", "망가", "포토툰", "코사이트", "토토보증업체", "notice", "bbs",
}


def fetch_series_list() -> list[dict[str, str]]:
    url: str = f"{BASE_URL}{WEBTOON_PAGE}"
    try:
        r: Any = SESSION.get(url, timeout=10)  # pyright: ignore[reportExplicitAny]
        if r.status_code != 200:  # pyright: ignore[reportAny]
            print(f"{UI.RED}[!] HTTP {r.status_code}{UI.END}")  # pyright: ignore[reportAny]
            return []

        page: Any = Selector(r.text, url=url)  # pyright: ignore[reportAny, reportExplicitAny]
        series: list[dict[str, str]] = []
        seen: set[str] = set()

        for a in page.css("a[href]"):  # pyright: ignore[reportAny]
            href: str = str(a.attrib.get("href", ""))  # pyright: ignore[reportAny]
            slug: str = href.strip("/")

            if not slug or slug in seen or slug in _NAV_SLUGS:
                continue
            if slug.startswith(("http", "#", "javascript", "bbs", "img")):
                continue
            if "board.php" in href:
                continue

            seen.add(slug)
            text: str = str(a.text or "").strip()  # pyright: ignore[reportAny]
            title: str = slug
            parent: Any = a.parent  # pyright: ignore[reportAny, reportExplicitAny]
            if parent:
                h: Any = parent.css("h2, h3, h4, strong, b").first  # pyright: ignore[reportAny, reportExplicitAny]
                if h and h.text and h.text.strip():  # pyright: ignore[reportAny]
                    title = h.text.strip()  # pyright: ignore[reportAny]
            elif "더 읽기" not in text and text:
                title = text

            series.append({"slug": slug, "title": title})
        return series
    except Exception as e:
        print(f"{UI.RED}[!] fetch_series_list: {e}{UI.END}")
        return []


def search_query(query: str) -> list[str]:
    print(f"{UI.CYAN}[*] Buscando '{query}'...{UI.END}")
    all_s: list[dict[str, str]] = fetch_series_list()
    if not all_s:
        print(f"{UI.YELLOW}[!] Catálogo vacío. Usando slug directo.{UI.END}")
        return [query]
    q: str = query.lower()
    return [
        s["slug"] for s in all_s if q in s["title"].lower() or q in s["slug"].lower()
    ]


def save_img(raw: bytes, path: str, fmt: str) -> None:
    if not has_pillow or fmt == "original" or Image is None:
        with open(path, "wb") as f:
            _ = f.write(raw)
        return
    try:
        img: Any = Image.open(BytesIO(raw))  # pyright: ignore[reportExplicitAny]
        if fmt.lower() in ("jpg", "jpeg") and img.mode in ("RGBA", "LA"):  # pyright: ignore[reportAny]
            bg: Any = Image.new(img.mode[:-1], img.size, (255, 255, 255))  # pyright: ignore[reportAny, reportExplicitAny]
            _ = bg.paste(img, img.split()[-1])  # pyright: ignore[reportAny]
            img = bg.convert("RGB")  # pyright: ignore[reportAny]
        _ = img.save(path, quality=92)  # pyright: ignore[reportAny]
    except Exception:
        with open(path, "wb") as f:
            _ = f.write(raw)


def dl_worker(args: tuple[str, str, int]) -> bool:
    url, folder, idx = args
    if not url.startswith("https://"):
        return False

    ext: str = USER_FORMAT if (has_pillow and USER_FORMAT != "original") else "jpg"
    url_ext: str = os.path.splitext(url.split("?")[0])[-1].lower().lstrip(".")
    if url_ext in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = url_ext

    path: str = f"{folder}/{idx + 1:03d}.{ext}"
    if os.path.exists(path):
        return True

    for attempt in range(3):
        try:
            r: Any = SESSION.get(url, timeout=(10, 15))  # pyright: ignore[reportExplicitAny]
            if r.status_code == 200:  # pyright: ignore[reportAny]
                save_img(r.content, path, USER_FORMAT)  # pyright: ignore[reportAny]
                return True
        except Exception:
            time.sleep(attempt + 1)
    return False


def download_chapter(logic: ToonkorLogic, series_slug: str, chapter_num: int) -> list[str]:
    url: str = logic.get_chapter_url(series_slug, chapter_num)
    try:
        r: Any = SESSION.get(url, timeout=(10, 15))  # pyright: ignore[reportExplicitAny]
        if r.status_code != 200:  # pyright: ignore[reportAny]
            return []
        return logic.extract_images_from_chapter(r.text)  # pyright: ignore[reportAny]
    except Exception:
        return []


def download_gallery(series_slug: str, logic: ToonkorLogic, chapters_to_dl: str = "all") -> None:
    try:
        data: Any = METADATA_CACHE.get(series_slug)  # pyright: ignore[reportExplicitAny]
        if not data:
            r: Any = SESSION.get(logic.get_series_url(series_slug), timeout=15)  # pyright: ignore[reportExplicitAny]
            if r.status_code != 200:  # pyright: ignore[reportAny]
                return
            title, autor, sinopsis, chapters = logic.parse_series_page(r.text, series_slug)  # pyright: ignore[reportAny]
            if not chapters:
                chapters = []
                for n in range(1, 31):
                    resp: Any = SESSION.head(logic.get_chapter_url(series_slug, n), timeout=5)  # pyright: ignore[reportExplicitAny]
                    if resp.status_code == 200:  # pyright: ignore[reportAny]
                        chapters.append(n)  # pyright: ignore[reportUnknownMemberType]
                    elif chapters:
                        break
            data = {"title": title, "autor": autor, "sinopsis": sinopsis, "chapters": chapters}
            METADATA_CACHE[series_slug] = data

        raw_title: str = str(data.get("title", series_slug))  # pyright: ignore[reportAny]
        clean_title: str = "".join(c for c in raw_title if c.isalnum() or c in " -_[]").strip()
        folder: str = f"{clean_title} [{series_slug}]"
        os.makedirs(folder, exist_ok=True)

        with open(f"{folder}/info.json", "w", encoding="utf-8") as f:
            json.dump({
                "slug": series_slug, "title": raw_title, "autor": data["autor"],
                "sinopsis": data["sinopsis"], "chapters": data["chapters"],
                "url": logic.get_series_url(series_slug)
            }, f, indent=4, ensure_ascii=False)

        all_chapters: list[int] = data["chapters"]  # pyright: ignore[reportAny]
        if not all_chapters:
            return

        if chapters_to_dl == "all":
            selected: list[int] = all_chapters
        else:
            chap_set: set[int] = set(all_chapters)
            requested: set[int] = parse_chapter_nums(chapters_to_dl)
            by_num: list[int] = [c for c in sorted(list(requested), reverse=True) if c in chap_set]
            if by_num:
                selected = by_num
            else:
                selected = [all_chapters[i] for i in parse_sel(chapters_to_dl, len(all_chapters))]

        all_images: list[tuple[str, str, int]] = []
        for chap in selected:
            imgs: list[str] = download_chapter(logic, series_slug, chap)
            chap_folder: str = os.path.join(folder, f"chapter_{chap:04d}")
            os.makedirs(chap_folder, exist_ok=True)
            all_images.extend((u, chap_folder, x) for x, u in enumerate(imgs))

        if not all_images:
            return

        comp: int = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
            futures: Any = [exe.submit(dl_worker, a) for a in all_images]  # pyright: ignore[reportExplicitAny]
            for _ in as_completed(futures):  # pyright: ignore[reportAny]
                comp += 1
                perc: int = int(30 * comp // len(all_images))
                _ = sys.stdout.write(f"\r   [{UI.CYAN}{'█' * perc}{'-' * (30 - perc)}{UI.END}] {comp}/{len(all_images)}")
                _ = sys.stdout.flush()
        print()

        ext_out: str = OUTPUT_TYPE.lower()
        for chap in selected:
            chap_f_name: str = f"chapter_{chap:04d}"
            chap_f_path: str = os.path.join(folder, chap_f_name)
            if not os.path.exists(chap_f_path):
                continue
            out_f: str = os.path.join(folder, f"{chap_f_name}.{ext_out}")
            if ext_out == "pdf" and has_pillow and Image is not None:
                paths: list[str] = sorted(os.path.join(chap_f_path, f) for f in os.listdir(chap_f_path) if not f.endswith(".json"))
                pages: list[Any] = []  # pyright: ignore[reportExplicitAny]
                for p in paths:
                    try:
                        pages.append(Image.open(p).convert("RGB"))
                    except Exception:
                        pass
                if pages:
                    _ = pages[0].save(out_f, save_all=True, append_images=pages[1:])  # pyright: ignore[reportAny]
            else:
                with zipfile.ZipFile(out_f, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in os.listdir(chap_f_path):
                        full: str = os.path.join(chap_f_path, f)
                        if os.path.isfile(full):
                            _ = zf.write(full, f)
            if DELETE_TEMP:
                shutil.rmtree(chap_f_path)
    except Exception:
        pass


def main() -> None:
    logic: ToonkorLogic = ToonkorLogic()
    while True:
        UI.header()
        print(f" {UI.PURPLE}Menú Principal:{UI.END}")
        print(f" ├── {UI.BOLD}1.{UI.END} Descargar por Slug")
        print(f" ├── {UI.BOLD}2.{UI.END} Buscar Series")
        print(f" └── {UI.BOLD}3.{UI.END} Salir")
        print(f"\n {UI.PURPLE}Configuración Actual:{UI.END}")
        print(f" ├── Salida: {UI.CYAN}{OUTPUT_TYPE.upper()}{UI.END}")
        print(f" └── Imagen: {UI.CYAN}{USER_FORMAT.upper()}{UI.END}")

        op: str = input(f"\n{UI.YELLOW} Selecciona una opción > {UI.END}").strip()
        if op == "1":
            slug: str = input(f"{UI.CYAN} [?] Slug: {UI.END}").strip()
            if slug:
                caps: str = input(f"{UI.CYAN} Capítulos ('1-5,10' o 'all'): {UI.END}").strip() or "all"
                download_gallery(slug, logic, caps)
                _ = input(f"\n{UI.CYAN}Enter...{UI.END}")
        elif op == "2":
            q: str = input(f"{UI.CYAN} [?] Búsqueda: {UI.END}").strip()
            if not q:
                continue
            slugs: list[str] = search_query(q)
            if not slugs:
                print(f"{UI.RED} Sin resultados.{UI.END}")
                time.sleep(2)
                continue
            for i, s in enumerate(slugs[:20]):
                m: Any = METADATA_CACHE.get(s, {})  # pyright: ignore[reportAny, reportExplicitAny]
                title: str = str(m.get("title", s))  # pyright: ignore[reportAny]
                print(f" {i + 1:2d}. [{s}] {title}")
            sel: str = input(f"\n{UI.YELLOW} Acción > {UI.END}").strip()
            if sel.isdigit() and int(sel) <= len(slugs):
                slug_sel: str = slugs[int(sel) - 1]
                caps_sel: str = input(f"{UI.CYAN} [?] Caps para {slug_sel}: {UI.END}").strip() or "all"
                download_gallery(slug_sel, logic, caps_sel)
                _ = input("\n Enter...")
        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
