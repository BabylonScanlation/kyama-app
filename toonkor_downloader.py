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
from typing import TYPE_CHECKING, Any, Protocol, cast

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

    def Selector(html: str, url: str = "") -> "ScraplingNode": ...
else:
    from scrapling import Selector

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
OUTPUT_TYPE: str = "zip"
USER_FORMAT: str = "webp"
DELETE_TEMP: bool = True
MAX_WORKERS_DL: int = 20
MAX_RESULTS_PAGE: int = 20

METADATA_CACHE: dict[str, Any] = {}

headers: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": BASE_URL,
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(headers)
    try:
        _ = s.get(BASE_URL, timeout=10)
    except Exception:
        pass
    return s


SESSION: requests.Session = make_session()


# ─── AUTO-DETECCIÓN DE DOMINIO ────────────────────────────────────────────────
_REDIRECT_SOURCES: list[str] = [
    "https://xn--2h7b95c.net/",
    "https://xn--2h7b95c.kr/",
    "https://xn--2h7b95c.tech/",
]
_TKOR_DOMAIN_RE = re.compile(r"https?://(tkor\d+\.com)", re.IGNORECASE)


def resolve_tkor_domain() -> str | None:
    for redirect_url in _REDIRECT_SOURCES:
        try:
            r = SESSION.get(redirect_url, timeout=8, allow_redirects=True)
            m = _TKOR_DOMAIN_RE.search(r.text)
            if m:
                return f"https://{m.group(1)}/"
            m2 = _TKOR_DOMAIN_RE.search(r.url)
            if m2:
                return f"https://{m2.group(1)}/"
        except Exception:
            continue
    return None


def auto_update_domain() -> bool:
    global BASE_URL
    detected = resolve_tkor_domain()
    if not detected:
        return False
    if detected.rstrip("/") == BASE_URL.rstrip("/"):
        return False
    old = BASE_URL
    BASE_URL = detected
    SESSION.headers.update({"Referer": BASE_URL})
    print(
        f"  {UI.YELLOW}[!] Dominio: {old.rstrip('/')} → {BASE_URL.rstrip('/')}{UI.END}"
    )
    return True


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

        autor = ""
        sinopsis = ""
        meta = cast(object, page.css('meta[name="description"]').first)
        if meta:
            meta_attrib = cast(dict[str, str], getattr(meta, "attrib", {}))
            content = str(meta_attrib.get("content", "")).strip()
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

        slug_esc = re.escape(series_slug)
        chapter_pattern = re.compile(rf"/{slug_esc}_(\d+)화\.html", re.IGNORECASE)
        nums: set[int] = set()

        for a in cast(list[object], cast(object, page.css("a[href]"))):
            href = str(cast(dict[str, str], getattr(a, "attrib", {})).get("href", ""))
            m2 = chapter_pattern.search(href)
            if m2:
                nums.add(int(m2.group(1)))
        for m2 in chapter_pattern.finditer(html):
            nums.add(int(m2.group(1)))

        return title, autor, sinopsis, sorted(nums, reverse=True)

    def extract_images_from_chapter(self, chapter_html: str) -> list[str]:
        b64_match = re.search(r"var toon_img\s*=\s*'([^']+)';", chapter_html)
        if b64_match:
            try:
                decoded = base64.b64decode(b64_match.group(1)).decode("utf-8")
                inner = Selector(decoded, url=BASE_URL)
                imgs = cast(list[object], cast(object, inner.css("img[src]")))
                valid = [
                    str(cast(dict[str, str], getattr(img, "attrib", {})).get("src", ""))
                    for img in imgs
                ]
                valid = [
                    u
                    for u in valid
                    if u.startswith("http") and not any(p in u for p in self._UI_PATHS)
                ]
                if valid:
                    return valid
            except Exception as e:
                print(f"{UI.RED}[!] Base64 error: {e}{UI.END}")

        cdn_re = re.compile(
            r"https?://(?:" + "|".join(re.escape(h) for h in self.CDN_HOSTS) + r")"
            r'/[^\s"\'<>]+\.(?:jpe?g|png|webp|gif)',
            re.IGNORECASE,
        )
        cdn_urls = list(dict.fromkeys(cdn_re.findall(chapter_html)))
        if cdn_urls:
            return cdn_urls

        page = Selector(chapter_html, url=BASE_URL)
        toon_div = cast(object, page.css("#toon_img").first)
        scope = toon_div if toon_div else page
        css_func = getattr(scope, "css", None)
        img_nodes: list[object] = (
            cast(list[object], css_func("img")) if callable(css_func) else []
        )

        candidates: list[str] = []
        for img in img_nodes:
            attrib = cast(dict[str, str], getattr(img, "attrib", {}))
            src = str(attrib.get("src") or attrib.get("data-src") or "")
            if (
                src.startswith("https://")
                and not any(p in src for p in self._UI_PATHS)
                and any(
                    ext in src.lower() for ext in [".jpg", ".jpeg", ".png", ".webp"]
                )
            ):
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
                a, b = part.split("-")
                nums.update(range(int(a), int(b) + 1))
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
                a, b = part.split("-")
                indices.update(i for i in range(int(a) - 1, int(b)) if 0 <= i < max_len)
            elif part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < max_len:
                    indices.add(idx)
    except Exception:
        pass
    return sorted(indices)


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
# Prefijos de rutas de navegación que NO son series reales
_NAV_PREFIXES: tuple[str, ...] = (
    "웹툰/",
    "단행본/",
    "망가/",
    "포토툰/",
    "bbs/",
    "img/",
)


# ─── CATÁLOGO COMPLETO (paralelo) ─────────────────────────────────────────────
_CATALOG_SECTIONS: list[tuple[str, str]] = [
    ("웹툰", "웹툰"),
    ("웹툰?fil=인기", "웹툰 인기"),
    ("웹툰?fil=최신", "웹툰 최신"),
    ("웹툰?fil=제목", "웹툰 제목"),
    ("웹툰?fil=성인", "웹툰 성인"),
    ("웹툰/완결", "웹툰 완결"),
    ("웹툰/완결?fil=최신", "웹툰 완결 최신"),
    ("웹툰/완결?fil=인기", "웹툰 완결 인기"),
    ("웹툰/완결?fil=성인", "웹툰 완결 성인"),
    ("단행본", "단행본"),
    ("단행본?fil=인기", "단행본 인기"),
    ("단행본?fil=최신", "단행본 최신"),
    ("단행본?fil=성인", "단행본 성인"),
    ("망가", "망가"),
    ("망가?fil=인기", "망가 인기"),
    ("망가?fil=최신", "망가 최신"),
    ("망가?fil=성인", "망가 성인"),
    ("포토툰", "포토툰"),
    ("포토툰?fil=인기", "포토툰 인기"),
    ("포토툰?fil=최신", "포토툰 최신"),
]


def _fetch_section(args: tuple[str, str]) -> tuple[str, list[dict[str, str]]]:
    path, label = args
    url = BASE_URL + path
    try:
        r = SESSION.get(url, timeout=12)
        if r.status_code != 200:
            return label, []
        page = Selector(r.text, url=url)
        items: list[dict[str, str]] = []
        for a in cast(list[object], cast(object, page.css("a[href]"))):
            attrib = cast(dict[str, str], getattr(a, "attrib", {}))
            href = str(attrib.get("href", ""))
            slug = href.strip("/")
            if not slug or slug in _NAV_SLUGS:
                continue
            if slug.startswith(("http", "#", "javascript", "bbs", "img")):
                continue
            # Filtrar rutas de navegación (contienen '/' → sub-página, no serie)
            if "/" in slug:
                continue
            if any(c in slug for c in ("?", "board.php", "search.php")):
                continue
            text = str(getattr(a, "text", "") or "").strip()
            title = slug
            parent = cast(object, getattr(a, "parent", None))
            if parent:
                fn = getattr(parent, "css", None)
                if callable(fn):
                    h = cast(
                        object, getattr(fn("h2, h3, h4, strong, b"), "first", None)
                    )
                    if h:
                        ht = getattr(h, "text", "")
                        if ht and str(ht).strip():
                            title = str(ht).strip()
            if title == slug and text and "더 읽기" not in text:
                title = text
            items.append({"slug": slug, "title": title})
        return label, items
    except Exception:
        return label, []


def load_full_catalog(workers: int = 8) -> list[dict[str, str]]:
    all_items: list[dict[str, str]] = []
    seen: set[str] = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_section, t): t for t in _CATALOG_SECTIONS}
        done = 0
        for fut in as_completed(futures):
            done += 1
            label, items = fut.result()
            added = 0
            for it in items:
                if it["slug"] not in seen:
                    seen.add(it["slug"])
                    all_items.append(it)
                    added += 1
            sys.stdout.write(
                f"  {UI.CYAN}[{done}/{len(_CATALOG_SECTIONS)}] {label} +{added} — {len(all_items)} total{UI.END}   \r"
            )
            sys.stdout.flush()
    print(f"  {UI.GREEN}✔ {len(all_items)} series cargadas{UI.END}   ")
    return all_items


# ─── BÚSQUEDA GLOBAL (/bbs/search.php) ────────────────────────────────────────
def search_global(query: str) -> list[dict[str, str]]:
    import urllib.parse as _up

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    page_n = 1
    consecutive_empty = 0
    slug_pat = re.compile(
        r"<a[^>]+href=[\"']/([A-Za-z0-9\uAC00-\uD7A3_%\-]+)[\"'][^>]*>\s*([^<]{2,80})\s*</a>"
    )

    sys.stdout.write(f"  {UI.CYAN}Buscando '{query}'...{UI.END}   \r")
    sys.stdout.flush()

    while page_n <= 50:
        params = {"sfl": "wr_subject||wr_content", "stx": query, "page": str(page_n)}
        url = BASE_URL + "bbs/search.php?" + _up.urlencode(params)
        try:
            r = SESSION.get(url, timeout=12)
            if r.status_code != 200:
                break
            html = r.text
            if any(
                s in html
                for s in ["검색된 자료가 없습니다", "결과가 없습니다", "No results"]
            ):
                break
            added = 0
            for m in slug_pat.finditer(html):
                slug = m.group(1).strip("/")
                title = m.group(2).strip()
                if slug in _NAV_SLUGS or not title or len(slug) < 2:
                    continue
                if any(c in slug for c in (".", "?", " ")):
                    continue
                if slug not in seen:
                    seen.add(slug)
                    results.append({"slug": slug, "title": title})
                    added += 1
            if added == 0:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    break
            else:
                consecutive_empty = 0
            sys.stdout.write(
                f"  {UI.CYAN}'{query}' — {len(results)} resultados (pág {page_n}){UI.END}   \r"
            )
            sys.stdout.flush()
            page_n += 1
        except Exception:
            break

    sys.stdout.write(" " * 70 + "\r")
    sys.stdout.flush()
    return results


# ─── DESCARGA ─────────────────────────────────────────────────────────────────
def save_img(raw: bytes, path: str, fmt: str) -> None:
    if not has_pillow or fmt == "original" or Image is None:
        with open(path, "wb") as f:
            _ = f.write(raw)
        return
    try:
        img = cast(object, Image.open(BytesIO(raw)))
        img_mode = str(getattr(img, "mode", ""))
        img_size = cast(tuple[int, int], getattr(img, "size", (0, 0)))
        if fmt.lower() in ("jpg", "jpeg") and img_mode in ("RGBA", "LA"):
            bg = Image.new(img_mode[:-1], img_size, (255, 255, 255))
            paste_fn = getattr(bg, "paste", None)
            split_fn = getattr(img, "split", None)
            if callable(paste_fn) and callable(split_fn):
                sp = cast(list[object], split_fn())
                if sp:
                    _ = paste_fn(img, sp[-1])
            img = bg.convert("RGB")
        save_fn = getattr(img, "save", None)
        if callable(save_fn):
            _ = save_fn(path, quality=92)
    except Exception:
        with open(path, "wb") as f:
            _ = f.write(raw)


def dl_worker(args: tuple[str, str, int]) -> bool:
    url, folder, idx = args
    if not url.startswith("https://"):
        return False
    ext = USER_FORMAT if (has_pillow and USER_FORMAT != "original") else "jpg"
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
    return False


def download_chapter(
    logic: ToonkorLogic, series_slug: str, chapter_num: int
) -> list[str]:
    url = logic.get_chapter_url(series_slug, chapter_num)
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

        raw_title = str(data.get("title", series_slug))
        autor_str = str(data.get("autor", ""))
        sinop_str = str(data.get("sinopsis", ""))
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

        chap_set = set(all_chapters)
        selected: list[int] = []
        if selection.lower() == "all":
            selected = all_chapters
        else:
            requested = parse_chapter_nums(selection)
            by_num = [c for c in sorted(requested, reverse=True) if c in chap_set]
            selected = (
                by_num
                if by_num
                else [all_chapters[i] for i in parse_sel(selection, len(all_chapters))]
            )

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
        clean_title = re.sub(r'[\\/:*?"<>|]', "", raw_title).strip()
        folder = f"{clean_title} [{series_slug}]"
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

        ext_out = OUTPUT_TYPE.lower()
        for i, chap in enumerate(selected):
            print(
                f"  [{i + 1}/{len(selected)}] Capítulo {chap}...", end=" ", flush=True
            )
            imgs = download_chapter(logic, series_slug, chap)
            if not imgs:
                print(f"{UI.RED}0 imgs — fallido{UI.END}")
                continue
            print()
            chap_name = f"{chap:03d} - 제{chap}화"
            chap_path = os.path.join(folder, chap_name)
            os.makedirs(chap_path, exist_ok=True)
            comp = 0
            with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
                futures = {
                    exe.submit(dl_worker, (u, chap_path, x)): x
                    for x, u in enumerate(imgs)
                }
                for _ in as_completed(futures):
                    comp += 1
                    perc = int(30 * comp // len(imgs))
                    _ = sys.stdout.write(
                        f"\r   [{UI.CYAN}{'█' * perc}{'-' * (30 - perc)}{UI.END}] {comp}/{len(imgs)}"
                    )
                    _ = sys.stdout.flush()
            print()
            out_f = os.path.join(folder, f"{chap_name}.{ext_out}")
            if ext_out == "pdf" and has_pillow and Image is not None:
                paths_list = sorted(
                    os.path.join(chap_path, f)
                    for f in os.listdir(chap_path)
                    if not f.endswith(".json")
                )
                pages_list: list[object] = []
                for p in paths_list:
                    try:
                        pages_list.append(cast(object, Image.open(p).convert("RGB")))
                    except Exception:
                        pass
                if pages_list:
                    fn = getattr(pages_list[0], "save", None)
                    if callable(fn):
                        _ = fn(out_f, save_all=True, append_images=pages_list[1:])
            else:
                with zipfile.ZipFile(out_f, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in sorted(os.listdir(chap_path)):
                        full = os.path.join(chap_path, f)
                        if os.path.isfile(full):
                            zf.write(full, f)
            if DELETE_TEMP:
                shutil.rmtree(chap_path)
    except Exception:
        pass


# ─── MENÚ ─────────────────────────────────────────────────────────────────────
def _show_results_menu(
    results: list[dict[str, str]],
    label: str,
    logic: ToonkorLogic,
    paginated: bool = True,
) -> None:
    page = 0
    while True:
        UI.header()
        if paginated:
            start = page * MAX_RESULTS_PAGE
            end = min(start + MAX_RESULTS_PAGE, len(results))
            chunk = results[start:end]
        else:
            start = 0
            end = len(results)
            chunk = results

        print(f" {UI.PURPLE}{label}{UI.END}")
        print(f" {UI.PURPLE}Mostrando {start + 1}-{end} de {len(results)}{UI.END}")
        print(f" {'━' * 50}")
        for i, r in enumerate(chunk):
            print(
                f" {UI.BOLD}{start + i + 1:3d}.{UI.END} [{UI.GREEN}{r['slug']}{UI.END}] {r['title'][:45]}"
            )
        print(f" {'━' * 50}")
        if paginated:
            nav = f" {UI.CYAN}n{UI.END} sig | {UI.CYAN}p{UI.END} ant | {UI.CYAN}t{UI.END} todo sin pag | {UI.CYAN}q{UI.END} volver | número p/descargar"
        else:
            nav = f" {UI.CYAN}t{UI.END} volver a paginado | {UI.CYAN}q{UI.END} volver | número p/descargar"
        print(nav)
        sel = input(f"\n{UI.YELLOW} Acción > {UI.END}").lower().strip()
        if sel == "n" and paginated and end < len(results):
            page += 1
        elif sel == "p" and paginated and page > 0:
            page -= 1
        elif sel == "t":
            paginated = not paginated
            page = 0
        elif sel == "q":
            break
        elif sel:
            idxs = parse_sel(sel, len(results))
            if idxs:
                for idx in idxs:
                    download_gallery(results[idx]["slug"], logic)
                _ = input(f"\n{UI.GREEN}Cola terminada. Enter...{UI.END}")
                break
            else:
                print(f"{UI.RED} Entrada no válida.{UI.END}")
                time.sleep(1)


def main() -> None:
    logic: ToonkorLogic = ToonkorLogic()

    sys.stdout.write(f"{UI.CYAN}[*] Detectando dominio activo...{UI.END}   \r")
    sys.stdout.flush()
    if not auto_update_domain():
        print(f"{UI.GREEN}[OK] Dominio: {BASE_URL}{' ' * 20}{UI.END}")

    while True:
        UI.header()
        print(f" {UI.PURPLE}Menú Principal:{UI.END}")
        print(f" ├── {UI.BOLD}1.{UI.END} Descargar por nombre o slug")
        print(f" ├── {UI.BOLD}2.{UI.END} 📂  Ver todas las series")
        print(f" └── {UI.BOLD}3.{UI.END} Salir")
        print(
            f"\n {UI.PURPLE}Config:{UI.END}  dominio={UI.CYAN}{BASE_URL.rstrip('/')}{UI.END}  salida={UI.CYAN}{OUTPUT_TYPE.upper()}{UI.END}  img={UI.CYAN}{USER_FORMAT.upper()}{UI.END}"
        )

        op = input(f"\n{UI.YELLOW} Selecciona una opción > {UI.END}").strip()

        if op == "1":
            query_or_slug = input(f"{UI.CYAN} [?] Nombre o slug: {UI.END}").strip()
            if not query_or_slug:
                continue
            hits = search_global(query_or_slug)
            if len(hits) == 1:
                download_gallery(hits[0]["slug"], logic)
                _ = input(f"\n{UI.CYAN}Enter...{UI.END}")
            elif hits:
                _show_results_menu(
                    hits, f"'{query_or_slug}' — {len(hits)} resultados", logic
                )
            else:
                print(
                    f"  {UI.YELLOW}Sin resultados en búsqueda, intentando slug directo...{UI.END}"
                )
                download_gallery(query_or_slug, logic)
                _ = input(f"\n{UI.CYAN}Enter...{UI.END}")

        elif op == "2":
            all_series = load_full_catalog()
            if not all_series:
                print(f"{UI.RED} No se pudieron cargar las series.{UI.END}")
                time.sleep(2)
                continue
            ft = input(
                f"  {UI.CYAN}Filtrar por nombre (Enter = mostrar todo): {UI.END}"
            ).strip()
            shown = (
                [s for s in all_series if ft.lower() in s["title"].lower()]
                if ft
                else all_series
            )
            if not shown:
                print(f"{UI.RED} Sin resultados para '{ft}'.{UI.END}")
                time.sleep(2)
                continue
            modo = (
                input(
                    f"  {UI.CYAN}Mostrar con paginación? (Enter=sí / n=todo de una vez): {UI.END}"
                )
                .strip()
                .lower()
            )
            _show_results_menu(
                shown,
                f"Catálogo — {len(shown)}/{len(all_series)} series",
                logic,
                paginated=(modo != "n"),
            )

        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
