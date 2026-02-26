"""
TOONKOR DOWNLOADER v1.5.0
Dependencias:  pip install scrapling requests pillow
"""

import base64
import json
import os
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import Any, Protocol, TYPE_CHECKING, cast

if TYPE_CHECKING:
    class ScraplingNode(Protocol):
        @property
        def text(self) -> str | None: ...
        @property
        def attrib(self) -> dict[str, str]: ...
        def css(self, sel: str) -> "ScraplingNode": ...
        @property
        def first(self) -> "ScraplingNode": ...
        @property
        def parent(self) -> "ScraplingNode": ...

import requests

if TYPE_CHECKING:
    def Selector(html: str, url: str = "") -> "ScraplingNode": ... # pyright: ignore[reportUnusedParameter]
else:
    from scrapling import Selector # type: ignore

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
        print(f"{UI.BLUE}╔══════════════════════════════════════╗")
        print(f"║ {UI.BOLD}TOONKOR DOWNLOADER v1.5.0{UI.END}{UI.BLUE}            ║")
        print(f"╚══════════════════════════════════════╝{UI.END}")


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
    CDN_HOSTS: list[str] = [
        "aws-cloud-no2.site",
        "aws-cloud-no1.site",
        "aws-cloud-no3.site",
    ]
    _UI_PATHS: tuple[str, ...] = ("/images/", "/bann/", "/img/", "/icons/", "/logo")

    def get_series_url(self, slug: str) -> str:
        return f"{BASE_URL}{slug}"

    def get_chapter_url(self, series_slug: str, chapter_num: int) -> str:
        return f"{BASE_URL}{series_slug}_{chapter_num}화.html"

    def parse_series_page(
        self, html: str, series_slug: str
    ) -> tuple[str, str, str, list[int]]:
        page = Selector(html, url=BASE_URL)
        title: str = series_slug
        for sel in ["h1", ".toon-title", ".series-title", ".view-title", "#toon_title"]:
            node = cast(object, page.css(sel).first)
            if node:
                t = getattr(node, "text", "")
                if t and str(t).strip():
                    title = str(t).strip()
                    break

        autor: str = ""
        sinopsis: str = ""

        meta = cast(object, page.css('meta[name="description"]').first)
        if meta:
            meta_attrib = cast(dict[str, str], getattr(meta, "attrib", {}))
            content: str = str(meta_attrib.get("content", "")).strip()
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
                node = cast(object, page.css(sel).first)
                if node:
                    a = getattr(node, "text", "")
                    if a and str(a).strip():
                        autor = str(a).strip()
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
                node = cast(object, page.css(sel).first)
                if node:
                    s = getattr(node, "text", "")
                    if s and str(s).strip():
                        sinopsis = str(s).strip()[:500]
                        break

        slug_esc: str = re.escape(series_slug)
        chapter_pattern: re.Pattern[str] = re.compile(
            rf"/{slug_esc}_(\d+)화\.html", re.IGNORECASE
        )
        nums: set[int] = set()

        css_results = cast(list[object], cast(object, page.css("a[href]")))
        for a in css_results:
            a_attrib = cast(dict[str, str], getattr(a, "attrib", {}))
            href: str = str(a_attrib.get("href", ""))
            m2 = chapter_pattern.search(href)
            if m2:
                nums.add(int(m2.group(1)))

        for m2 in chapter_pattern.finditer(html):
            nums.add(int(m2.group(1)))

        chapters: list[int] = sorted(list(nums), reverse=True)
        return title, autor, sinopsis, chapters

    def extract_images_from_chapter(self, chapter_html: str) -> list[str]:
        b64_match = re.search(r"var toon_img\s*=\s*'([^']+)';", chapter_html)
        if b64_match:
            try:
                b64_data = b64_match.group(1)
                decoded: str = base64.b64decode(b64_data).decode("utf-8")
                inner_page = Selector(decoded, url=BASE_URL)
                
                img_nodes = cast(list[object], cast(object, inner_page.css("img[src]")))
                urls: list[str] = []
                for img in img_nodes:
                    img_attrib = cast(dict[str, str], getattr(img, "attrib", {}))
                    urls.append(str(img_attrib.get("src", "")))

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
            r"https?://(?:"
            + "|".join(re.escape(h) for h in self.CDN_HOSTS)
            + r")"
            + r'/[^\s"\'<>]+\.(?:jpe?g|png|webp|gif)',
            re.IGNORECASE,
        )
        cdn_urls: list[str] = list(dict.fromkeys(cdn_re.findall(chapter_html)))
        if cdn_urls:
            return cdn_urls

        page = Selector(chapter_html, url=BASE_URL)
        toon_div = cast(object, page.css("#toon_img").first)
        scope = toon_div if toon_div else page

        # Dynamic css call since scope could be ScraplingNode or similar
        css_func = getattr(scope, "css", None)
        img_nodes_scope: list[object] = []
        if callable(css_func):
            img_nodes_scope = cast(list[object], css_func("img"))

        candidates: list[str] = []
        for img in img_nodes_scope:
            img_attrib = cast(dict[str, str], getattr(img, "attrib", {}))
            src = str(img_attrib.get("src") or img_attrib.get("data-src") or "")
            if (src.startswith("https://") and 
                not any(p in src for p in self._UI_PATHS) and 
                any(ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"])):
                candidates.append(src)

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


def fetch_series_list() -> list[dict[str, str]]:
    url: str = f"{BASE_URL}{WEBTOON_PAGE}"
    try:
        r = SESSION.get(url, timeout=10)
        if r.status_code != 200:
            print(f"{UI.RED}[!] HTTP {r.status_code}{UI.END}")
            return []

        page = Selector(r.text, url=url)
        series: list[dict[str, str]] = []
        seen: set[str] = set()

        css_results = cast(list[object], cast(object, page.css("a[href]")))
        for a in css_results:
            a_attrib = cast(dict[str, str], getattr(a, "attrib", {}))
            href: str = str(a_attrib.get("href", ""))
            slug: str = href.strip("/")

            if not slug or slug in seen or slug in _NAV_SLUGS:
                continue
            if slug.startswith(("http", "#", "javascript", "bbs", "img")):
                continue
            if "board.php" in href:
                continue

            seen.add(slug)
            text: str = str(getattr(a, "text", "") or "").strip()
            title: str = slug
            parent = cast(object, getattr(a, "parent", None))
            if parent:
                parent_css = getattr(parent, "css", None)
                if callable(parent_css):
                    h_results = parent_css("h2, h3, h4, strong, b")
                    h = cast(object, getattr(h_results, "first", None))
                    if h:
                        h_text = getattr(h, "text", "")
                        if h_text and str(h_text).strip():
                            title = str(h_text).strip()
            elif "더 읽기" not in text and text:
                title = text

            series.append({"slug": slug, "title": title})
        return series
    except Exception as e:
        print(f"{UI.RED}[!] fetch_series_list: {e}{UI.END}")
        return []


def search_query(query: str) -> list[dict[str, str]]:
    print(f"{UI.CYAN}[*] Buscando '{query}'...{UI.END}")
    all_s: list[dict[str, str]] = fetch_series_list()
    if not all_s:
        print(f"{UI.YELLOW}[!] Catálogo vacío. Usando slug directo.{UI.END}")
        return [{"slug": query, "title": query}]

    q = query.lower()
    return [s for s in all_s if q in s["title"].lower() or q in s["slug"].lower()]


def save_img(raw: bytes, path: str, fmt: str) -> None:
    if not has_pillow or fmt == "original" or Image is None:
        with open(path, "wb") as f:
            _ = f.write(raw)
        return
    try:
        img_obj = Image.open(BytesIO(raw))
        img = cast(object, img_obj)
        img_mode = str(getattr(img, "mode", ""))
        img_size = cast(tuple[int, int], getattr(img, "size", (0, 0)))
        if fmt.lower() in ("jpg", "jpeg") and img_mode in ("RGBA", "LA"):
            bg = Image.new(img_mode[:-1], img_size, (255, 255, 255))
            # Dynamic paste call
            paste_func = getattr(bg, "paste", None)
            if callable(paste_func):
                split_func = getattr(img, "split", None)
                if callable(split_func):
                    img_split = cast(list[object], split_func())
                    if img_split:
                        _ = paste_func(img, img_split[-1])
            img = bg.convert("RGB")
        
        save_func = getattr(img, "save", None)
        if callable(save_func):
            _ = save_func(path, quality=92)
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
            r = SESSION.get(url, timeout=(10, 15))
            if r.status_code == 200:
                save_img(r.content, path, USER_FORMAT)
                return True
        except Exception:
            time.sleep(attempt + 1)
    return False


def download_chapter(
    logic: ToonkorLogic, series_slug: str, chapter_num: int
) -> list[str]:
    url: str = logic.get_chapter_url(series_slug, chapter_num)
    try:
        r = SESSION.get(url, timeout=(10, 15))
        if r.status_code != 200:
            return []
        return logic.extract_images_from_chapter(str(r.text))
    except Exception:
        return []


def download_gallery(series_slug: str, logic: ToonkorLogic) -> None:
    print(f"\n{UI.CYAN}[*] Cargando serie '{series_slug}'...{UI.END}")
    try:
        data = cast("dict[str, object] | None", METADATA_CACHE.get(series_slug))
        if not data:
            r = SESSION.get(logic.get_series_url(series_slug), timeout=15)
            if r.status_code != 200:
                print(f"{UI.RED}[!] HTTP {r.status_code}{UI.END}")
                return
            title, autor, sinopsis, chapters = logic.parse_series_page(
                str(r.text), series_slug
            )
            if not chapters:
                chapters: list[int] = []
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

        raw_title: str = str(data.get("title", series_slug))
        autor_str: str = str(data.get("autor", ""))
        sinop_str: str = str(data.get("sinopsis", ""))
        all_chapters = cast("list[int]", data.get("chapters", []))

        if not all_chapters:
            print(f"{UI.RED}[!] 0 capítulos.{UI.END}")
            return

        print(f"\n{UI.GREEN}[+] {UI.BOLD}{raw_title}{UI.END}")
        print(f"    Autor   : {autor_str or UI.YELLOW + 'N/A' + UI.END}")
        print(f"    Sinopsis: {sinop_str[:100] or UI.YELLOW + 'N/A' + UI.END}")
        print(f"    Caps    : {len(all_chapters)}")

        PAGE = 20
        show_start = 0
        selection = ""
        while True:
            show_end = min(show_start + PAGE, len(all_chapters))
            print(f"\n {UI.PURPLE}{'─' * 48}{UI.END}")
            for idx in range(show_start, show_end):
                print(f"  {UI.BOLD}{idx + 1:4d}.{UI.END} Capítulo {all_chapters[idx]}")
            print(f" {UI.PURPLE}{'─' * 48}{UI.END}")

            nav = ""
            if show_end < len(all_chapters):
                nav += f" {UI.CYAN}n{UI.END}=más"
            if show_start > 0:
                nav += f"  {UI.CYAN}p{UI.END}=ant"
            nav += "  o escribe selección y Enter"
            print(nav)

            raw = input(f"\n{UI.YELLOW} Caps ('1', '3-5,9', 'all') ➜ {UI.END}").strip()
            if raw.lower() == "n" and show_end < len(all_chapters):
                show_start += PAGE
            elif raw.lower() == "p" and show_start > 0:
                show_start -= PAGE
            elif raw == "":
                continue
            else:
                selection = raw
                break

        chap_set: set[int] = set(all_chapters)
        selected: list[int] = []
        if selection.lower() == "all":
            selected = all_chapters
        else:
            requested: set[int] = parse_chapter_nums(selection)
            by_num: list[int] = [
                c for c in sorted(list(requested), reverse=True) if c in chap_set
            ]
            if by_num:
                selected = by_num
            else:
                selected = [
                    all_chapters[i] for i in parse_sel(selection, len(all_chapters))
                ]

        if not selected:
            print(f"{UI.RED}[!] Selección vacía.{UI.END}")
            return

        print("\n  Capítulos a descargar:")
        for i, chap in enumerate(selected):
            print(f"    {i + 1}. Capítulo {chap}")
        confirm = (
            input(f"\n{UI.YELLOW} ¿Confirmar? (Enter=sí / n=cancelar) ➜ {UI.END}")
            .strip()
            .lower()
        )
        if confirm == "n":
            return

        print(f"\n{UI.CYAN}[*] Descargando {len(selected)} capítulo(s)...{UI.END}")

        clean_title: str = re.sub(r'[\\/:*?"<>|]', "", raw_title).strip()
        folder: str = f"{clean_title} [{series_slug}]"
        os.makedirs(folder, exist_ok=True)

        with open(f"{folder}/info.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "slug": series_slug,
                    "title": raw_title,
                    "autor": autor_str,
                    "sinopsis": sinop_str,
                    "chapters": all_chapters,
                    "url": logic.get_series_url(series_slug),
                },
                f,
                indent=4,
                ensure_ascii=False,
            )

        ext_out: str = OUTPUT_TYPE.lower()

        for i, chap in enumerate(selected):
            print(
                f"  [{i + 1}/{len(selected)}] Capítulo {chap}...", end=" ", flush=True
            )
            imgs: list[str] = download_chapter(logic, series_slug, chap)
            if not imgs:
                print(f"{UI.RED}0 imgs — fallido{UI.END}")
                continue
            print()

            chap_f_name: str = f"{chap:03d} - 제{chap}화"
            chap_f_path: str = os.path.join(folder, chap_f_name)
            os.makedirs(chap_f_path, exist_ok=True)

            comp: int = 0
            with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
                futures = {
                    exe.submit(dl_worker, (u, chap_f_path, x)): x
                    for x, u in enumerate(imgs)
                }
                for _ in as_completed(futures):
                    comp += 1
                    perc: int = int(30 * comp // len(imgs))
                    _ = sys.stdout.write(
                        f"\r   [{UI.CYAN}{'█' * perc}{'-' * (30 - perc)}{UI.END}] {comp}/{len(imgs)}"
                    )
                    _ = sys.stdout.flush()
            print()

            out_f: str = os.path.join(folder, f"{chap_f_name}.{ext_out}")
            if ext_out == "pdf" and has_pillow and Image is not None:
                paths: list[str] = sorted(
                    os.path.join(chap_f_path, f)
                    for f in os.listdir(chap_f_path)
                    if not f.endswith(".json")
                )
                pages: list[object] = []
                for p in paths:
                    try:
                        img_to_add = cast(object, Image.open(p).convert("RGB"))
                        pages.append(img_to_add)
                    except Exception:
                        pass
                if pages:
                    save_func = getattr(pages[0], "save", None)
                    if callable(save_func):
                        _ = save_func(out_f, save_all=True, append_images=pages[1:])
            else:
                with zipfile.ZipFile(out_f, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in sorted(os.listdir(chap_f_path)):
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
                download_gallery(slug, logic)
                _ = input(f"\n{UI.CYAN}Enter...{UI.END}")
        elif op == "2":
            q: str = input(f"{UI.CYAN} [?] Búsqueda de serie: {UI.END}").strip()
            if not q:
                continue
            results: list[dict[str, str]] = search_query(q)
            if not results:
                print(f"{UI.RED} Sin resultados.{UI.END}")
                time.sleep(2)
                continue

            page: int = 0
            while True:
                UI.header()
                start: int = page * MAX_RESULTS_PAGE
                end: int = min(start + MAX_RESULTS_PAGE, len(results))
                print(f" {UI.PURPLE}Búsqueda: '{q}'{UI.END}")
                print(
                    f" {UI.PURPLE}Mostrando {start + 1}-{end} de {len(results)} resultados{UI.END}"
                )
                print(f" {'━' * 50}")

                for i, r in enumerate(results[start:end]):
                    print(
                        f" {UI.BOLD}{start + i + 1:3d}.{UI.END} [{UI.GREEN}{r['slug']}{UI.END}] {r['title'][:45]}"
                    )

                print(f" {'━' * 50}")
                print(
                    f" {UI.CYAN}Controles:{UI.END} {UI.BOLD}n{UI.END} (sig) | {UI.BOLD}p{UI.END} (ant) | {UI.BOLD}q{UI.END} (volver)"
                )
                print(
                    f" {UI.CYAN}Selección:{UI.END} Escribe números (ej: '1', '1-5', '1,10')"
                )

                sel: str = input(f"\n{UI.YELLOW} Acción > {UI.END}").lower().strip()

                if sel == "n" and end < len(results):
                    page += 1
                elif sel == "p" and page > 0:
                    page -= 1
                elif sel == "q":
                    break
                elif sel:
                    idxs: list[int] = parse_sel(sel, len(results))
                    if idxs:
                        for idx_sel in idxs:
                            slug_sel = results[idx_sel]["slug"]
                            download_gallery(slug_sel, logic)
                        _ = input(
                            f"\n{UI.GREEN}Cola terminada. Enter para continuar...{UI.END}"
                        )
                        break
                    else:
                        print(f"{UI.RED} Entrada no válida.{UI.END}")
                        time.sleep(1)
        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
