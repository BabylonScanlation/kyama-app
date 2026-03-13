"""
DUMANWU DOWNLOADER v5.4
100% requests — Decryptor Inteligente
Fixes: orden de capítulos, Scrapling universal, semillas XOR automáticas
"""

from __future__ import annotations

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
from typing import TYPE_CHECKING, Protocol, TypedDict, cast, runtime_checkable

if TYPE_CHECKING:

    class ScraplingNode(Protocol):
        @property
        def text(self) -> str: ...
        @property
        def attrib(self) -> dict[str, str]: ...
        def css(self, sel: str) -> list[ScraplingNode]: ...
        def css_first(self, sel: str) -> ScraplingNode | None: ...

    class AdaptorType(Protocol):
        def __call__(self, html: str, url: str = ...) -> ScraplingNode: ...


import requests


# ─── INTERFACES DE PARSER ─────────────────────────────────────────────────────
class ChapterDict(TypedDict):
    slug: str
    title: str
    url: str
    html: str | None


@runtime_checkable
class ElementProtocol(Protocol):
    @property
    def text(self) -> str: ...
    @property
    def attrib(self) -> dict[str, str]: ...
    def css(self, sel: str) -> list[ElementProtocol]: ...
    def css_first(self, sel: str) -> ElementProtocol | None: ...


# ─── SCRAPLING UNIVERSAL ───────────────────────────────────────────────────────
try:
    try:
        import scrapling as _scrapling1  # type: ignore

        _Adaptor = cast("AdaptorType", getattr(_scrapling1, "Selector"))
    except ImportError:
        try:
            import scrapling.parser as _scrapling2  # type: ignore

            _Adaptor = cast("AdaptorType", getattr(_scrapling2, "Adaptor"))
        except ImportError:
            import scrapling as _scrapling3  # type: ignore

            _Adaptor = cast("AdaptorType", getattr(_scrapling3, "Adaptor"))

    class ScraplingElem:
        def __init__(self, node: object) -> None:
            self._n: object = node

        @property
        def text(self) -> str:
            if self._n is None:
                return ""
            try:
                t = getattr(self._n, "text", "")
                return str(t).strip() if t is not None else ""
            except Exception:
                return ""

        @property
        def attrib(self) -> dict[str, str]:
            try:
                a = getattr(self._n, "attrib", {})
                if isinstance(a, dict):
                    a_dict = cast("dict[object, object]", a)
                    return {str(k): str(v) for k, v in a_dict.items()}
                return {}
            except Exception:
                return {}

        def css(self, sel: str) -> list[ElementProtocol]:
            try:
                css_func = getattr(self._n, "css", None)
                if callable(css_func):
                    nodes = cast("list[object]", css_func(sel))
                    return [ScraplingElem(e) for e in nodes]
                return []
            except Exception:
                return []

        def css_first(self, sel: str) -> ElementProtocol | None:
            try:
                css_first_func = getattr(self._n, "css_first", None)
                if callable(css_first_func):
                    r = cast("object | None", css_first_func(sel))
                    return ScraplingElem(r) if r is not None else None
                css_func = getattr(self._n, "css", None)
                if callable(css_func):
                    results = cast("list[object]", css_func(sel))
                    if results:
                        return ScraplingElem(results[0])
                return None
            except Exception:
                return None

    class ScraplingSelector:
        def __init__(self, html: str, url: str = "") -> None:
            self._a: object = _Adaptor(html, url=url)

        def css(self, sel: str) -> list[ElementProtocol]:
            try:
                css_func = getattr(self._a, "css", None)
                if callable(css_func):
                    nodes = cast("list[object]", css_func(sel))
                    return [ScraplingElem(e) for e in nodes]
                return []
            except Exception:
                return []

        def css_first(self, sel: str) -> ElementProtocol | None:
            try:
                css_first_func = getattr(self._a, "css_first", None)
                if callable(css_first_func):
                    r = cast("object | None", css_first_func(sel))
                    return ScraplingElem(r) if r is not None else None
                return None
            except Exception:
                return None

    Selector = ScraplingSelector
    parser_name = "scrapling"

except (ImportError, Exception):
    from bs4 import BeautifulSoup as _BS4  # type: ignore

    class BS4Elem:
        def __init__(self, node: object) -> None:
            self._n: object = node

        @property
        def text(self) -> str:
            if self._n is None:
                return ""
            try:
                get_text = getattr(self._n, "get_text", None)
                if callable(get_text):
                    return str(get_text()).strip()
                return ""
            except Exception:
                return ""

        @property
        def attrib(self) -> dict[str, str]:
            if self._n is None:
                return {}
            try:
                attrs = getattr(self._n, "attrs", {})
                if isinstance(attrs, dict):
                    attrs_dict = cast("dict[object, object]", attrs)
                    return {str(k): str(v) for k, v in attrs_dict.items()}
                return {}
            except Exception:
                return {}

        def css(self, sel: str) -> list[ElementProtocol]:
            if self._n is None:
                return []
            try:
                select = getattr(self._n, "select", None)
                if callable(select):
                    nodes = cast("list[object]", select(sel))
                    return [BS4Elem(e) for e in nodes]
                return []
            except Exception:
                return []

        def css_first(self, sel: str) -> ElementProtocol | None:
            if self._n is None:
                return None
            try:
                select_one = getattr(self._n, "select_one", None)
                if callable(select_one):
                    res = cast("object | None", select_one(sel))
                    return BS4Elem(res) if res else None
                return None
            except Exception:
                return None

    class BS4Selector:
        def __init__(self, html: str, url: str = "") -> None:
            self._a: object = _BS4(html, "html.parser")

        def css(self, sel: str) -> list[ElementProtocol]:
            try:
                select = getattr(self._a, "select", None)
                if callable(select):
                    nodes = cast("list[object]", select(sel))
                    return [BS4Elem(e) for e in nodes]
                return []
            except Exception:
                return []

        def css_first(self, sel: str) -> ElementProtocol | None:
            try:
                select_one = getattr(self._a, "select_one", None)
                if callable(select_one):
                    res = cast("object | None", select_one(sel))
                    return BS4Elem(res) if res else None
                return None
            except Exception:
                return None

    Selector = BS4Selector
    parser_name = "bs4"

try:
    from PIL import Image

    has_pillow = True
except ImportError:
    Image = None
    has_pillow = False

# ─── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
BASE_URL = "https://dumanwu.com"
OUTPUT_TYPE = "zip"
USER_FORMAT = "webp"
MAX_WORKERS_DL = 50
DELETE_TEMP = True
MIN_IMAGE_SIZE_KB = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": BASE_URL + "/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

_UI_PATHS = (
    "/static/",
    "load.gif",
    "logo.png",
    "prev.png",
    "next.png",
    "nulls.png",
    "user.png",
    "favicon.ico",
)

_seeds_cache: list[bytes] = []


# ─── UI ───────────────────────────────────────────────────────────────────────
class UI:
    CYAN: str = "\033[96m"
    GREEN: str = "\033[92m"
    YELLOW: str = "\033[93m"
    RED: str = "\033[91m"
    BOLD: str = "\033[1m"
    PURPLE: str = "\033[95m"
    BLUE: str = "\033[94m"
    END: str = "\033[0m"

    @staticmethod
    def header() -> None:
        _ = os.system("cls" if os.name == "nt" else "clear")
        seed_status = (
            f"{UI.GREEN}✔ {len(_seeds_cache)} semillas{UI.END}"
            if _seeds_cache
            else f"{UI.YELLOW}⚠ semillas hardcoded{UI.END}"
        )
        print(f"{UI.BLUE}╔══════════════════════════════════════╗")
        print(f"║ {UI.BOLD}DUMANWU DOWNLOADER v5.4{UI.END}{UI.BLUE}             ║")
        print("║ 100% requests — Decryptor Inteligente  ║")
        print(f"╚══════════════════════════════════════╝{UI.END}")
        print(f" Parser: {UI.CYAN}{parser_name}{UI.END}  Seeds: {seed_status}")


SESSION = requests.Session()
adapter = requests.adapters.HTTPAdapter(
    pool_connections=MAX_WORKERS_DL, pool_maxsize=MAX_WORKERS_DL
)
SESSION.mount("http://", adapter)
SESSION.mount("https://", adapter)
SESSION.headers.update(HEADERS)


# ─── SEMILLAS XOR ─────────────────────────────────────────────────────────────
_SEEDS_FALLBACK_HEX = [
    "736d6b6879323538",
    "736d6b6439356676",
    "6d64343936393532",
    "63646373647771",
    "7662667361323536",
    "b28470300000",
    "6364353663766461",
    "386b69686e7439",
    "70d297b80000",
    "356b6f36706c6879",
]


def _load_seeds() -> list[bytes]:
    global _seeds_cache
    js_urls = []
    try:
        r = SESSION.get(f"{BASE_URL}/", timeout=8)
        matches = re.findall(r'src="(/static/js/all2\.js[^"]*)"', r.text)
        if matches:
            js_urls = [BASE_URL + matches[0]]
        else:
            js_urls = [
                f"{BASE_URL}/static/js/all2.js?v=2.3",
                f"{BASE_URL}/static/js/all2.js",
            ]
    except Exception:
        js_urls = [f"{BASE_URL}/static/js/all2.js?v=2.3"]

    for js_url in js_urls:
        try:
            r = SESSION.get(js_url, timeout=10, headers={**HEADERS, "Accept": "*/*"})
            if r.status_code != 200 or len(r.content) < 100:
                continue
            js_text = r.text
            m = re.search(
                r'\[\s*"([0-9a-fA-F]{6,})"(?:\s*,\s*"([0-9a-fA-F]{6,})")+\s*\]', js_text
            )
            if m:
                all_hex = cast(
                    "list[str]", re.findall(r'"([0-9a-fA-F]{8,})"', m.group(0))
                )
                if len(all_hex) >= 3:
                    extracted: list[bytes] = []
                    for h in all_hex:
                        try:
                            extracted.append(bytes.fromhex(h))
                        except Exception:
                            pass
                    if extracted:
                        _seeds_cache = extracted
                        return extracted
            all_hex2 = cast(
                "list[str]", re.findall(r'["\']([0-9a-fA-F]{8,})["\']', js_text)
            )
            if len(all_hex2) >= 5:
                extracted2: list[bytes] = []
                for h in all_hex2:
                    try:
                        extracted2.append(bytes.fromhex(h))
                    except Exception:
                        pass
                if len(extracted2) >= 5:
                    _seeds_cache = extracted2
                    return extracted2
        except Exception:
            continue

    seeds: list[bytes] = []
    for h in _SEEDS_FALLBACK_HEX:
        try:
            seeds.append(bytes.fromhex(h))
        except Exception:
            pass
    _seeds_cache = seeds
    return seeds


# ─── DECODIFICADOR ────────────────────────────────────────────────────────────
_B62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _b62_int(token: str, base: int = 62) -> int:
    n = 0
    chars = _B62[:base] if base <= 62 else _B62
    try:
        for ch in token:
            n = n * base + chars.index(ch)
    except ValueError:
        return -1
    return n


def _decode_packer(p: str, base: int, k_str: str) -> str:
    keys = k_str.split("|")

    def replace(m: re.Match[str]) -> str:
        idx = _b62_int(m.group(0), base)
        return keys[idx] if 0 <= idx < len(keys) and keys[idx] else m.group(0)

    return re.sub(r"\b[0-9A-Za-z]+\b", replace, p)


def _extract_packer_args(script: str) -> tuple[str, int, int, str] | None:
    try:
        start = script.rindex("}(") + 2
        args = script[start:]
        parts = cast(
            "list[tuple[str, str]]", re.findall(r"'((?:[^'\\]|\\.)*)'|(\d+)", args)
        )
        vals: list[int | str] = [int(n) if n else s for s, n in parts]
        if len(vals) >= 4:
            return str(vals[0]), int(vals[1]), int(vals[2]), str(vals[3])
    except (ValueError, IndexError):
        pass
    return None


def _xor_decrypt(data: bytes, key: bytes) -> bytes:
    return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))


def _decrypt_images(html: str) -> list[str]:
    seeds = _seeds_cache or _load_seeds()
    scripts = cast(
        "list[str]",
        re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE),
    )
    for script in scripts:
        if "eval(function(p,a,c,k,e,d)" not in script:
            continue
        args = _extract_packer_args(script)
        if not args:
            continue
        p, base, _count, k = args
        decoded = _decode_packer(p, base, k)
        m = re.search(
            r"""var\s+\w+\s*=\s*['"]([A-Za-z0-9+/]{40,}={0,2})['"]""", decoded
        )
        if not m:
            continue
        match_val = m.group(1)
        try:
            pad = (4 - len(match_val) % 4) % 4
            raw = base64.b64decode(match_val + "=" * pad)
        except Exception:
            continue
        for seed in seeds:
            try:
                xored = _xor_decrypt(raw, seed)
                pad2 = (4 - len(xored) % 4) % 4
                final = base64.b64decode(xored + b"=="[:pad2]).decode(
                    "utf-8", errors="ignore"
                )
                if "http" not in final:
                    continue
                try:
                    data_json = cast(object, json.loads(final))
                    if isinstance(data_json, list):
                        data_list = cast("list[object]", data_json)
                        urls = [str(u) for u in data_list if "http" in str(u)]
                        if urls:
                            return urls
                except (json.JSONDecodeError, ValueError):
                    pass
                raw_urls = cast(
                    "list[str]", re.findall(r"https?://[^\s\"',\[\]]+", final)
                )
                urls2 = [
                    u
                    for u in raw_urls
                    if any(e in u.lower() for e in [".jpg", ".jpeg", ".png", ".webp"])
                    or any(cdn in u for cdn in ["ecombdimg", "shimolife", "tplv"])
                ]
                if urls2:
                    return urls2
            except Exception:
                continue
    return []


# ─── CAPÍTULOS ────────────────────────────────────────────────────────────────
def _cap_sort_key(cap: ChapterDict) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", cap.get("title", ""))
    return float(m.group(1)) if m else 0.0


# ─── CATÁLOGO ────────────────────────────────────────────────────────────────
class CatalogItem(TypedDict):
    slug: str
    title: str
    latest: str


# Categorías del sitio
_DW_SORTS = {
    1: "冒险",
    2: "热血",
    3: "都市",
    4: "玄幻",
    5: "悬疑",
    6: "耽美",
    7: "恋爱",
    8: "生活",
    9: "搞笑",
    10: "穿越",
    11: "修真",
    12: "后宫",
    13: "女主",
    14: "古风",
    15: "连载",
    16: "完结",
}
_DW_RANKS = {
    1: "精品榜",
    2: "人气榜",
    3: "推荐榜",
    4: "黑马榜",
    5: "最近更新",
    6: "新漫画",
}

_SYSTEM_SLUGS = {
    "static",
    "s",
    "list",
    "tag",
    "type",
    "update",
    "rank",
    "new",
    "morechapter",
    "sort",
    "user",
    "track",
    "sortmore",
    "rankmore",
}


def _parse_series_html(html: str) -> list[CatalogItem]:
    """
    Extrae series del HTML crudo de /sort/N o /rank/N.
    Busca pares (slug, h2-título) en bloques <a href=".../SLUG/">...<h2>TÍTULO</h2>.
    """
    items: list[CatalogItem] = []
    seen: set[str] = set()

    # Estrategia 1: bloque <a href="/SLUG/">...<h2>título</h2>
    for m in re.finditer(
        r'<a\s[^>]*href="(?:https?://dumanwu\.com)?/([A-Za-z0-9]{5,10})/"[^>]*>'
        r"([\s\S]{0,400}?)</a>",
        html,
    ):
        slug = m.group(1)
        inner = m.group(2)
        if slug in _SYSTEM_SLUGS or slug in seen:
            continue
        # Buscar h2 dentro del bloque
        h2 = re.search(r"<h2[^>]*>([^<]{1,100})</h2>", inner)
        if not h2:
            continue
        title = re.sub(r"<[^>]+>", "", h2.group(1)).strip()
        if not title:
            continue
        seen.add(slug)
        items.append({"slug": slug, "title": title, "latest": ""})

    # Estrategia 2: si no hay h2, extraer slugs únicos alfanuméricos de 7 chars
    if not items:
        for m in re.finditer(
            r'href="(?:https?://dumanwu\.com)?/([A-Za-z0-9]{7})/"', html
        ):
            slug = m.group(1)
            if slug in _SYSTEM_SLUGS or slug in seen:
                continue
            seen.add(slug)
            items.append({"slug": slug, "title": slug, "latest": ""})

    return items


def _sortmore(type_id: int, page: int) -> list[CatalogItem]:
    """
    POST /sortmore  — endpoint AJAX del botón 'cargar más' en /sort/N.
    Devuelve lista de series o [] si no hay más / endpoint no existe.
    """
    try:
        r = SESSION.post(
            f"{BASE_URL}/sortmore",
            data={"type": type_id, "page": page},
            headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        # Puede responder JSON o HTML fragmentado
        ct = r.headers.get("Content-Type", "")
        if "json" in ct:
            try:
                data = cast("dict[str, object]", r.json())
                if str(data.get("code", "")) == "200" and isinstance(
                    data.get("data"), list
                ):
                    rows = cast("list[dict[str, object]]", data["data"])
                    return [
                        {
                            "slug": str(row.get("id", "")),
                            "title": str(row.get("name", "")),
                            "latest": "",
                        }
                        for row in rows
                        if row.get("id")
                    ]
            except Exception:
                pass
        # HTML fragmentado (lo más común)
        if len(r.content) < 50:
            return []
        return _parse_series_html(r.text)
    except Exception:
        return []


def _load_sort(sort_id: int, sort_name: str) -> list[CatalogItem]:
    """
    Carga TODAS las series de /sort/{sort_id} usando:
    1. GET /sort/{sort_id}  (primera página, 20 items)
    2. POST /sortmore {type, page}  (páginas adicionales hasta vacío)
    """
    all_items: list[CatalogItem] = []
    seen: set[str] = set()

    # Página inicial
    try:
        r = SESSION.get(f"{BASE_URL}/sort/{sort_id}", timeout=15, headers=HEADERS)
        if r.status_code == 200:
            for it in _parse_series_html(r.text):
                if it["slug"] not in seen:
                    seen.add(it["slug"])
                    all_items.append(it)
    except Exception:
        pass

    # Cargar más vía AJAX
    page = 2
    consecutive_empty = 0
    while page <= 500:
        more = _sortmore(sort_id, page)
        if not more:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                break
            page += 1
            continue
        consecutive_empty = 0
        added = 0
        for it in more:
            if it["slug"] not in seen:
                seen.add(it["slug"])
                all_items.append(it)
                added += 1
        if added == 0:
            break  # ya no hay nuevas
        page += 1
        time.sleep(0.1)

    return all_items


def load_full_catalog(workers: int = 8) -> list[CatalogItem]:
    """
    Carga TODAS las series iterando /sort/1..16 con paginación AJAX.
    """
    all_items: list[CatalogItem] = []
    seen: set[str] = set()

    for sort_id, sort_name in _DW_SORTS.items():
        sys.stdout.write(
            f"  {UI.CYAN}[{sort_id}/{len(_DW_SORTS)}] {sort_name}...{UI.END}   \r"
        )
        sys.stdout.flush()

        items = _load_sort(sort_id, sort_name)
        added = 0
        for it in items:
            if it["slug"] not in seen:
                seen.add(it["slug"])
                all_items.append(it)
                added += 1

        sys.stdout.write(
            f"  {UI.CYAN}[{sort_id}/{len(_DW_SORTS)}] {sort_name}"
            f" — {added} nuevas — {len(all_items)} total{UI.END}   \r"
        )
        sys.stdout.flush()
        time.sleep(0.2)

    print(f"\n  {UI.GREEN}✔ {len(all_items)} series cargadas{UI.END}   ")
    return all_items


class DumanwuLogic:
    def parse_series_page(self, slug: str) -> tuple[str, str, str, list[ChapterDict]]:
        url = f"{BASE_URL}/{slug}/"
        r = SESSION.get(url, timeout=15)
        if r.status_code != 200:
            print(f"{UI.RED}[!] HTTP {r.status_code}{UI.END}")
            return slug, "", "", []

        sel = Selector(r.text, url=url)
        h1 = sel.css_first("h1")
        title = (
            (h1.text if h1 and h1.text else "")
            or (re.search(r"<h1[^>]*>([^<]+)</h1>", r.text) or ["", ""])[1].strip()
            or slug
        )

        m_autor = re.search(r"作者[：:]\s*([^\s<\n，,&]+)", r.text)
        autor = m_autor.group(1).strip() if m_autor else ""

        sinopsis = ""
        intro_p = sel.css_first("p.introduction")
        if intro_p and intro_p.text:
            sinopsis = intro_p.text.strip()[:600]
        else:
            for p in sel.css("p"):
                t = p.text
                if len(t) > 40 and "作者" not in t:
                    sinopsis = t[:600]
                    break

        print(f"  {UI.CYAN}[*] Obteniendo capítulos...{UI.END}", end="", flush=True)

        slug_esc = re.escape(slug)
        caps: list[ChapterDict] = []
        seen: set[str] = set()

        for m2 in re.finditer(
            rf'href="(/{slug_esc}/([A-Za-z0-9]+)\.html)"[^>]*>([^<]*)</a>', r.text
        ):
            href_full = m2.group(1)
            cap_slug = m2.group(2)
            a_text = m2.group(3).strip()
            if (
                cap_slug not in seen
                and "阅读" not in a_text
                and "start" not in a_text.lower()
            ):
                seen.add(cap_slug)
                caps.append(
                    {
                        "slug": cap_slug,
                        "title": a_text or cap_slug,
                        "url": f"{BASE_URL}{href_full}",
                        "html": None,
                    }
                )

        try:
            r2 = SESSION.post(f"{BASE_URL}/morechapter", data={"id": slug}, timeout=10)
            if r2.status_code == 200:
                data = cast("dict[str, object]", r2.json())
                if str(data.get("code", "")) == "200" and "data" in data:
                    data_items = cast("list[object]", data["data"])
                    for item in data_items:
                        if not isinstance(item, dict):
                            continue
                        item_dict = cast("dict[str, object]", item)
                        cid = item_dict.get("chapterid")
                        cname = item_dict.get("chaptername", "")
                        if cid and str(cid) not in seen:
                            seen.add(str(cid))
                            caps.append(
                                {
                                    "slug": str(cid),
                                    "title": str(cname) if cname else str(cid),
                                    "url": f"{BASE_URL}/{slug}/{cid}.html",
                                    "html": None,
                                }
                            )
        except Exception:
            pass

        caps.sort(key=_cap_sort_key)
        print(f"\r  {UI.GREEN}[OK] {len(caps)} capítulos.{' ' * 20}{UI.END}")
        return title, autor, sinopsis, caps

    def extract_images(self, cap: ChapterDict) -> list[str]:
        html = cap.get("html")
        if not html or "eval(function(p,a,c,k,e,d)" not in str(html):
            cap_url = cap["url"]
            series_slug = cap_url.split("/")[-2]
            referer = f"{BASE_URL}/{series_slug}/"
            html = None
            for attempt in range(3):
                try:
                    r = SESSION.get(
                        cap_url, timeout=15, headers={**HEADERS, "Referer": referer}
                    )
                    if r.status_code == 200:
                        html = r.text
                        if "eval(function(p,a,c,k,e,d)" in html:
                            break
                        time.sleep(1.5)
                    else:
                        time.sleep(attempt + 1)
                except Exception:
                    time.sleep(attempt + 1)

        if not html:
            print(f"\n{UI.RED}[!] No se pudo obtener el HTML del capítulo.{UI.END}")
            return []

        urls = _decrypt_images(html)
        if urls:
            content_urls = [
                u
                for u in urls
                if "scl3phc04j" not in u and not any(p in u.lower() for p in _UI_PATHS)
            ]
            if content_urls:
                return content_urls

        fallback: list[str] = []
        seen_fb: set[str] = set()
        for pattern in [
            r'data-src="(https?://[^"]+)"',
            r'data-original="(https?://[^"]+)"',
        ]:
            for m in re.finditer(pattern, html):
                src = m.group(1)
                if (
                    src not in seen_fb
                    and not any(p in src.lower() for p in _UI_PATHS)
                    and "scl3phc04j" not in src
                ):
                    seen_fb.add(src)
                    fallback.append(src)
        return fallback

    def search(self, query: str) -> list[dict[str, str]]:
        url = f"{BASE_URL}/s"
        try:
            r = SESSION.post(
                url,
                data={"k": query},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=10,
            )
            if r.status_code == 200:
                data = cast("dict[str, object]", r.json())
                if str(data.get("code")) == "200" and isinstance(
                    data.get("data"), list
                ):
                    data_items = cast("list[dict[str, object]]", data["data"])
                    return [
                        {"slug": str(item.get("id")), "title": str(item.get("name"))}
                        for item in data_items
                        if item.get("id") and item.get("name")
                    ]
        except Exception:
            pass
        return []


# ─── DESCARGA ─────────────────────────────────────────────────────────────────
def save_img(raw: bytes, path: str, fmt: str) -> None:
    if not has_pillow or str(fmt) == "original" or Image is None:
        with open(path, "wb") as f:
            _ = f.write(raw)
        return
    try:
        img = Image.open(BytesIO(raw))
        if fmt.lower() in ("jpg", "jpeg") and img.mode in ("RGBA", "LA"):
            bg = Image.new(img.mode[:-1], img.size, (255, 255, 255))
            bg.paste(img, img.split()[-1])  # pyright: ignore[reportArgumentType]
            img = bg.convert("RGB")
        img.save(path, quality=92)
    except Exception:
        with open(path, "wb") as f:
            _ = f.write(raw)


def dl_worker(args: tuple[str, str, int]) -> bool:
    url, folder, idx = args
    ext = USER_FORMAT if (has_pillow and str(USER_FORMAT) != "original") else "jpg"
    url_ext = os.path.splitext(url.split("?")[0])[-1].lower().lstrip(".")
    if url_ext in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = url_ext
    path = f"{folder}/{idx + 1:03d}.{ext}"
    if os.path.exists(path):
        return True
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=(5, 15))
            if r.status_code == 200 and len(r.content) > MIN_IMAGE_SIZE_KB * 1024:
                save_img(r.content, path, USER_FORMAT)
                return True
        except Exception:
            time.sleep(attempt + 1)
    return False


def parse_selection(s: str, total: int) -> list[int]:
    s = s.strip().lower().replace(" ", "")
    if s == "all":
        return list(range(total))
    indices: set[int] = set()
    for part in s.split(","):
        try:
            if "-" in part:
                a, b = map(int, part.split("-"))
                indices.update(i for i in range(a - 1, b) if 0 <= i < total)
            elif part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < total:
                    indices.add(idx)
        except Exception:
            pass
    return sorted(indices)


def download_series(slug: str, logic: DumanwuLogic):
    print(f"\n{UI.CYAN}[*] Cargando serie '{slug}'...{UI.END}")
    title, autor, sinopsis, chapters = logic.parse_series_page(slug)
    if not chapters:
        print(f"{UI.RED}[!] 0 capítulos. ¿Slug correcto?{UI.END}")
        return

    print(f"\n{UI.GREEN}[+] {UI.BOLD}{title}{UI.END}")
    print(f"    Autor   : {autor or UI.YELLOW + 'N/A' + UI.END}")
    print(f"    Sinopsis: {sinopsis[:100] or UI.YELLOW + 'N/A' + UI.END}")
    print(f"    Caps    : {len(chapters)}")

    PAGE = 20
    show_start = 0
    selection = ""
    while True:
        show_end = min(show_start + PAGE, len(chapters))
        print(f"\n {UI.PURPLE}{'─' * 48}{UI.END}")
        for idx in range(show_start, show_end):
            print(f"  {UI.BOLD}{idx + 1:4d}.{UI.END} {chapters[idx]['title']}")
        print(f" {UI.PURPLE}{'─' * 48}{UI.END}")
        nav = ""
        if show_end < len(chapters):
            nav += f" {UI.CYAN}n{UI.END}=más"
        if show_start > 0:
            nav += f"  {UI.CYAN}p{UI.END}=ant"
        nav += "  o escribe selección y Enter"
        print(nav)
        raw = input(f"\n{UI.YELLOW} Caps ('1', '3-5,9', 'all') ➜ {UI.END}").strip()
        if raw.lower() == "n" and show_end < len(chapters):
            show_start += PAGE
        elif raw.lower() == "p" and show_start > 0:
            show_start -= PAGE
        elif raw == "":
            continue
        else:
            selection = raw
            break

    sel_idx = parse_selection(selection, len(chapters))
    to_dl: list[ChapterDict] = [chapters[i] for i in sel_idx]
    if not to_dl:
        print(f"{UI.RED}[!] Selección vacía.{UI.END}")
        return

    print("\n  Capítulos a descargar:")
    for i, cap in enumerate(to_dl):
        print(f"    {i + 1}. {cap['title']}")
    confirm = (
        input(f"\n{UI.YELLOW} ¿Confirmar? (Enter=sí / n=cancelar) ➜ {UI.END}")
        .strip()
        .lower()
    )
    if confirm == "n":
        return

    print(f"\n{UI.CYAN}[*] Descargando {len(to_dl)} capítulo(s)...{UI.END}")
    clean = re.sub(r'[\\/:*?"<>|]', "", title).strip()
    base_folder = f"{clean} [{slug}]"
    os.makedirs(base_folder, exist_ok=True)

    total_valid = 0
    failed: list[tuple[int, ChapterDict]] = []

    for i, cap in enumerate(to_dl):
        print(f"  [{i + 1}/{len(to_dl)}] {cap['title']}...", end=" ", flush=True)
        imgs = logic.extract_images(cap)
        if not imgs:
            print(f"{UI.RED}0 imgs — reintentando en 5s...{UI.END}")
            time.sleep(5)
            imgs = logic.extract_images(cap)
        if not imgs:
            print(f"  {UI.RED}✗ fallido{UI.END}")
            failed.append((i, cap))
            continue
        print()
        clean_title = re.sub(r'[\\/:*?"<>|]', "", cap["title"]).strip()
        c_folder = os.path.join(base_folder, f"{i + 1:03d} - {clean_title}")
        os.makedirs(c_folder, exist_ok=True)
        comp, valid = 0, 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as ex:
            futures = {
                ex.submit(dl_worker, (u, c_folder, x)): x for x, u in enumerate(imgs)
            }
            for fut in as_completed(futures):
                comp += 1
                if fut.result():
                    valid += 1
                perc = int(30 * comp // len(imgs))
                _ = sys.stdout.write(
                    f"\r   [{UI.CYAN}{'█' * perc}{'-' * (30 - perc)}{UI.END}] {comp}/{len(imgs)}"
                )
                _ = sys.stdout.flush()
        total_valid += valid
        print()
        out = os.path.join(base_folder, f"{i + 1:03d} - {clean_title}.{OUTPUT_TYPE}")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(os.listdir(c_folder)):
                full = os.path.join(c_folder, f)
                if os.path.isfile(full):
                    zf.write(full, f)
        if DELETE_TEMP:
            shutil.rmtree(c_folder)

    if failed:
        print(
            f"\n{UI.YELLOW}[*] Reintentando {len(failed)} cap(s) fallido(s)...{UI.END}"
        )
        time.sleep(10)
        still_failed: list[str] = []
        for i, cap in failed:
            print(f"  [retry] {cap['title']}...", end=" ", flush=True)
            imgs = logic.extract_images(cap)
            if not imgs:
                print(f"{UI.RED}✗ sin imágenes{UI.END}")
                still_failed.append(cap["title"])
                continue
            print()
            clean_title = re.sub(r'[\\/:*?"<>|]', "", cap["title"]).strip()
            c_folder = os.path.join(base_folder, f"{i + 1:03d} - {clean_title}")
            os.makedirs(c_folder, exist_ok=True)
            comp, valid = 0, 0
            with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as ex:
                futures = {
                    ex.submit(dl_worker, (u, c_folder, x)): x
                    for x, u in enumerate(imgs)
                }
                for fut in as_completed(futures):
                    comp += 1
                    if fut.result():
                        valid += 1
                    perc = int(30 * comp // len(imgs))
                    _ = sys.stdout.write(
                        f"\r   [{UI.CYAN}{'█' * perc}{'-' * (30 - perc)}{UI.END}] {comp}/{len(imgs)}"
                    )
                    _ = sys.stdout.flush()
            total_valid += valid
            print()
            out = os.path.join(
                base_folder, f"{i + 1:03d} - {clean_title}.{OUTPUT_TYPE}"
            )
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in sorted(os.listdir(c_folder)):
                    full = os.path.join(c_folder, f)
                    if os.path.isfile(full):
                        zf.write(full, f)
            if DELETE_TEMP:
                shutil.rmtree(c_folder)
        if still_failed:
            print(f"\n{UI.RED}[!] No se pudieron descargar:{UI.END}")
            for t in still_failed:
                print(f"    • {t}")

    if total_valid == 0:
        print(f"\n{UI.RED}[!] No se descargó ninguna imagen.{UI.END}")
        return
    print(f"\n  {UI.GREEN}[OK] Completado → {base_folder}/{UI.END}")


# ─── MENÚ CATÁLOGO ────────────────────────────────────────────────────────────
MAX_RESULTS_PAGE = 20


def menu_catalog(logic: DumanwuLogic) -> None:
    """
    Carga TODAS las series del sitio en paralelo y permite
    filtrar por nombre y descargar directamente por número.
    """
    UI.header()
    print(f"\n {UI.CYAN}Cargando catálogo completo del sitio...{UI.END}\n")

    all_items = load_full_catalog(workers=8)

    if not all_items:
        # Fallback: si no se detectó URL de catálogo, ofrecer búsqueda directa
        print(f"\n {UI.YELLOW}No se pudo cargar el catálogo automáticamente.{UI.END}")
        print(
            f" Usa la opción {UI.BOLD}2. Buscar serie{UI.END} para encontrar series.\n"
        )
        _ = input(f"{UI.CYAN} Enter para volver...{UI.END}")
        return

    filtered = all_items[:]
    filter_text = ""
    page = 0

    while True:
        UI.header()
        total_pages = max(1, (len(filtered) + MAX_RESULTS_PAGE - 1) // MAX_RESULTS_PAGE)
        page = max(0, min(page, total_pages - 1))
        start = page * MAX_RESULTS_PAGE
        end = min(start + MAX_RESULTS_PAGE, len(filtered))

        header_line = (
            f" {UI.PURPLE}Catálogo{UI.END}  {UI.BLUE}│{UI.END}"
            f"  {UI.BOLD}{len(filtered)}{UI.END} series"
        )
        if filter_text:
            header_line += (
                f"  {UI.BLUE}│{UI.END}  filtro: {UI.YELLOW}{filter_text}{UI.END}"
            )
        header_line += (
            f"  {UI.BLUE}│{UI.END}  pág {UI.BOLD}{page + 1}/{total_pages}{UI.END}"
        )
        print(f"\n{header_line}\n")

        print(f" {UI.PURPLE}{'─' * 54}{UI.END}")
        for i, item in enumerate(filtered[start:end]):
            latest = (
                f"  {UI.BLUE}│{UI.END} {UI.CYAN}{item['latest'][:20]}{UI.END}"
                if item.get("latest")
                else ""
            )
            print(
                f"  {UI.BOLD}{start + i + 1:4d}.{UI.END}  {item['title'][:45]}{latest}"
            )
        print(f" {UI.PURPLE}{'─' * 54}{UI.END}")

        print(
            f"\n  {UI.CYAN}n{UI.END}=sig  {UI.CYAN}p{UI.END}=ant"
            f"  {UI.CYAN}f{UI.END}=filtrar  {UI.CYAN}q{UI.END}=volver"
            f"  {UI.CYAN}[número]{UI.END}=descargar"
        )
        cmd = input(f"\n{UI.YELLOW} ➜ {UI.END}").strip()

        if cmd.lower() == "q":
            return

        elif cmd.lower() == "f":
            ft = input(
                f"  {UI.YELLOW}Filtrar por nombre (vacío = mostrar todos) ➜ {UI.END}"
            ).strip()
            filter_text = ft
            filtered = (
                [it for it in all_items if ft.lower() in it["title"].lower()]
                if ft
                else all_items[:]
            )
            page = 0

        elif cmd.lower() == "n" and page < total_pages - 1:
            page += 1

        elif cmd.lower() == "p" and page > 0:
            page -= 1

        elif cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(filtered):
                download_series(filtered[idx]["slug"], logic)
                _ = input(f"\n{UI.CYAN} Enter para continuar...{UI.END}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    print(
        f"{UI.CYAN}[*] Cargando semillas XOR desde all2.js...{UI.END}",
        end="",
        flush=True,
    )
    seeds = _load_seeds()
    src = "all2.js" if len(seeds) != len(_SEEDS_FALLBACK_HEX) else "hardcoded"
    print(f"\r{UI.GREEN}[OK] {len(seeds)} semillas cargadas ({src}).{' ' * 20}{UI.END}")

    logic = DumanwuLogic()

    while True:
        UI.header()
        print(f"\n {UI.PURPLE}Menú:{UI.END}")
        print(f" ├── {UI.BOLD}1.{UI.END} Descargar por Slug  (ej: trbtGKl)")
        print(f" ├── {UI.BOLD}2.{UI.END} Buscar serie")
        print(f" ├── {UI.BOLD}3.{UI.END} 📂  Explorar catálogo completo")
        print(f" ├── {UI.BOLD}4.{UI.END} Recargar semillas XOR")
        print(f" └── {UI.BOLD}5.{UI.END} Salir")
        print(
            f"\n Salida: {UI.CYAN}{OUTPUT_TYPE.upper()}{UI.END}  Imagen: {UI.CYAN}{USER_FORMAT.upper()}{UI.END}"
        )

        op = input(f"\n{UI.YELLOW} ➜ {UI.END}").strip()

        if op == "1":
            slug = input(f"{UI.CYAN} Slug: {UI.END}").strip()
            if not slug:
                continue
            download_series(slug, logic)
            _ = input(f"\n{UI.CYAN} Enter para continuar...{UI.END}")

        elif op == "2":
            q = input(f"{UI.CYAN} Búsqueda de serie: {UI.END}").strip()
            if not q:
                continue
            print(f"{UI.CYAN} 🔎 Buscando en el catálogo...{UI.END}")
            results = logic.search(q)
            if not results:
                print(f"{UI.RED} Sin resultados.{UI.END}")
                time.sleep(2)
                continue
            page = 0
            while True:
                UI.header()
                start = page * MAX_RESULTS_PAGE
                end = min(start + MAX_RESULTS_PAGE, len(results))
                print(f" '{q}' → {len(results)} resultado(s)")
                print(f" {'━' * 50}")
                for i, r in enumerate(results[start:end]):
                    print(
                        f" {UI.BOLD}{start + i + 1:3d}.{UI.END} [{UI.GREEN}{r['slug']}{UI.END}] {r['title'][:45]}"
                    )
                print(f" {'━' * 50}")
                print(
                    f" {UI.CYAN}n{UI.END}=sig  {UI.CYAN}p{UI.END}=ant  {UI.CYAN}q{UI.END}=volver  o número"
                )
                sel = input(f"\n{UI.YELLOW} ➜ {UI.END}").lower().strip()
                if sel == "n" and end < len(results):
                    page += 1
                elif sel == "p" and page > 0:
                    page -= 1
                elif sel == "q":
                    break
                elif sel:
                    for i in parse_selection(sel, len(results)):
                        download_series(results[i]["slug"], logic)
                    _ = input(f"\n{UI.GREEN} Listo. Enter...{UI.END}")
                    break

        elif op == "3":
            menu_catalog(logic)

        elif op == "4":
            _seeds_cache.clear()
            _ = _load_seeds()
            print(f"{UI.GREEN}[OK] {len(_seeds_cache)} semillas recargadas.{UI.END}")
            time.sleep(1.5)

        elif op == "5":
            print(f"{UI.CYAN} ¡Hasta pronto!{UI.END}")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{UI.RED} Ctrl+C{UI.END}")
