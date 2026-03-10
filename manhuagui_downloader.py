"""
╔══════════════════════════════════════════╗
║  MANHUAGUI DOWNLOADER  v1.0.0            ║
║  manhuagui.com  /  飒漫乐画               ║
╚══════════════════════════════════════════╝

Dependencias:
    pip install requests beautifulsoup4 lxml pillow

Uso:
    python manhuagui_downloader.py
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
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

try:
    from PIL import Image

    has_pillow = True
except ImportError:
    Image = None
    has_pillow = False


class UnpackingError(Exception):
    pass


def detect_packer(source):
    """Detects whether `source` is P.A.C.K.E.R. coded."""
    # Relajado para escaped y espacios variables
    return (
        re.search(
            r"(eval|window\['eval'\])\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,",
            source,
            re.IGNORECASE,
        )
        is not None
    )


def unpack_packer(source: str) -> str:
    source = source.replace('window["\\x65\\x76\\x61\\x6c"]', "eval")
    # Optimización: reemplazamos '(.*?)' por algo más estricto que evite el DOTALL costoso si la cadena crece
    # Usamos (?:\\\\'|[^'])* para coincidir con cualquier cosa que no sea una comilla simple o sea una comilla simple escapada.
    match = re.search(
        r"}\s*\(\s*'((?:\\'|[^'])*)'\s*,\s*(\d+|\[\])\s*,\s*(\d+)\s*,\s*'((?:\\'|[^'])*)'[^,]*?,\s*0\s*,\s*\{\}\s*\)\)",
        source,
        re.IGNORECASE,
    )
    if match:
        a = list(match.groups())
        if a[1] == "[]":
            a[1] = 62

        payload = a[0]
        radix = int(a[1])
        count = int(a[2])
        symtab_str = a[3]

        # ¡EL TRUCO DE MANHUAGUI! Si parece Base64, descomprímelo primero
        if "|" not in symtab_str and len(symtab_str) > 20:
            decomp = lzstring_decompress_base64(symtab_str)
            if decomp:
                symtab_str = decomp

        symtab = symtab_str.split("|")

        if count != len(symtab):
            raise UnpackingError(
                f"Malformed p.a.c.k.e.r. symtab. Expected {count}, got {len(symtab)}"
            )

        unbase = Unbaser(radix)

        def lookup(match_obj):
            word = match_obj.group(0)
            try:
                val = unbase(word)
                if val < len(symtab) and symtab[val]:
                    return symtab[val]
            except Exception:
                pass
            return word

        payload = payload.replace("\\\\", "\\").replace("\\'", "'")
        # Usamos regex \b[0-9a-zA-Z]+\b para no romper los reemplazos Base62
        source = re.sub(r"\b[0-9a-zA-Z]+\b", lookup, payload)

        return source
    raise UnpackingError("Could not make sense of p.a.c.k.e.r data.")


class Unbaser:
    ALPHABET = {
        62: "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
        95: " !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~",
    }

    def __init__(self, base):
        self.base = base
        if 2 <= base <= 36:
            self.unbase = lambda s: int(s, base)
        else:
            # Si usa una base rara (ej. 58), usamos el alfabeto de 62 caracteres que es compatible
            alphabet = self.ALPHABET.get(base, self.ALPHABET[62])
            self.dictionary = {c: i for i, c in enumerate(alphabet)}
            self.unbase = self._dictunbaser

    # ESTO ES LO QUE FALTABA: Permite usar la clase como función
    def __call__(self, string):
        return self.unbase(string)

    def _dictunbaser(self, string):
        ret = 0
        for index, cipher in enumerate(string[::-1]):
            ret += (self.base**index) * self.dictionary.get(cipher, 0)
        return ret


# ==========================================
#   CONFIGURACIÓN (editá aquí)
# ==========================================
OUTPUT_TYPE = "zip"  # 'zip' | 'cbz' | 'pdf'
USER_FORMAT = "webp"  # 'original' | 'jpg' | 'png' | 'webp'
DELETE_TEMP = True
MAX_WORKERS = 8
REQUEST_DELAY = 0.5  # segundos entre peticiones
# ==========================================

BASE = "https://www.manhuagui.com"
IMG_HOST = "https://i.hamreus.com"  # CDN principal de imágenes

SESS = requests.Session()
SESS.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": BASE + "/",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    }
)

_CACHE: dict[str, dict] = {}


# ── colores ─────────────────────────────────────────────────────────────────
class C:
    PU = "\033[95m"
    CY = "\033[96m"
    BL = "\033[94m"
    GR = "\033[92m"
    YE = "\033[93m"
    RE = "\033[91m"
    BO = "\033[1m"
    EN = "\033[0m"


def header():
    os.system("cls" if os.name == "nt" else "clear")
    print(f"{C.BL}╔══════════════════════════════════════════╗")
    print(f"║ {C.BO}MANHUAGUI DOWNLOADER v1.0.0{C.EN}{C.BL}               ║")
    print(f"║ {C.CY}manhuagui.com{C.EN}{C.BL}                             ║")
    print(f"╚══════════════════════════════════════════╝{C.EN}")


# ══════════════════════════════════════════════════════════════════════════════
#  LZSTRING  —  pure-Python port of lz-string.js
#  Fuente: https://github.com/pieroxy/lz-string
# ══════════════════════════════════════════════════════════════════════════════
_B64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_B64_MAP = {ch: i for i, ch in enumerate(_B64_CHARS)}


def lzstring_decompress_base64(compressed: str) -> str:
    """
    LZString.decompressFromBase64 — pure-Python port verificado.
    lz-string.js v1.4.4  (https://github.com/pieroxy/lz-string)
    """
    if not compressed:
        return ""
    safe = lambda i: _B64_MAP.get(compressed[i], 0) if i < len(compressed) else 0

    dv = safe(0)
    dp = 32
    di = 1
    result: list[str] = []
    dictionary: list = list(range(3))
    enlargeIn = 4
    dictSize = 4
    numBits = 3

    def rb(maxpower: int) -> int:
        nonlocal dv, dp, di
        bits = 0
        p = 1
        while p != maxpower:
            resb = dv & dp
            dp >>= 1
            if dp == 0:
                dp = 32
                dv = safe(di)
                di += 1
            bits |= (1 if resb > 0 else 0) * p
            p <<= 1
        return bits

    nxt = rb(4)
    if nxt == 0:
        c = chr(rb(256))
    elif nxt == 1:
        c = chr(rb(65536))
    else:
        return ""
    dictionary.append(c)
    w = c
    result.append(c)

    while True:
        if di > len(compressed):
            return ""
        c = rb(1 << numBits)

        if c == 0:
            dictionary.append(chr(rb(256)))
            c = dictSize
            dictSize += 1
            enlargeIn -= 1
        elif c == 1:
            dictionary.append(chr(rb(65536)))
            c = dictSize
            dictSize += 1
            enlargeIn -= 1
        elif c == 2:
            return "".join(result)

        if enlargeIn == 0:
            enlargeIn = 1 << numBits
            numBits += 1

        entry = (
            dictionary[c]
            if c < len(dictionary)
            else w + w[0]
            if c == dictSize
            else None
        )
        if entry is None:
            return "".join(result)

        result.append(entry)
        dictionary.append(w + entry[0])
        dictSize += 1
        enlargeIn -= 1

        if enlargeIn == 0:
            enlargeIn = 1 << numBits
            numBits += 1
        w = entry


# ══════════════════════════════════════════════════════════════════════════════
#  EXTRACTOR DE BASE64  (manhuagui chapter pages)
#
#  El script del capítulo está obfuscado con Dean Edwards P.A.C.K.E.R.:
#    eval(function(p,a,c,k,e,d){...}('payload', 62, N, 'k0|k1|...|BASE64|...'))
#  La cadena base64 larga es el JSON comprimido con LZString.
#  Estrategia: extraer las keys del packer → buscar la más larga y válida.
#  Fallback: regex directo sobre el HTML.
# ══════════════════════════════════════════════════════════════════════════════
def _extract_b64_from_script(source: str) -> str:
    m0 = re.search(r'atob\(["\']([A-Za-z0-9+/=]{300,})["\']\)', source)
    if m0:
        return m0.group(1)
    # Intento 0: direct decompressFromBase64('b64')
    m0 = re.search(
        r'decompressFromBase64\s*\(\s*["\']([A-Za-z0-9+/=]{300,})["\']\s*\)', source
    )
    if m0:
        return m0.group(1)
    # Intento 1: keys del packer (Corregido para evitar Catastrophic Backtracking)
    m = re.search(r"'([^']{100,})'\s*\.split\(['\"]?\|['\"]?\)", source)
    if m:
        keys = m.group(1).split("|")
        b64 = [k for k in keys if len(k) > 100 and re.fullmatch(r"[A-Za-z0-9+/=]+", k)]
        if b64:
            return max(b64, key=len)
    # Intento 2: regex directo
    m2 = re.search(r"[A-Za-z0-9+/=]{300,}", source)
    if m2:
        return m2.group(0)
    # Intento 3: cInfo var
    m3 = re.search(r"cInfo\s*=\s*['\"]([A-Za-z0-9+/=]{300,})['\"];", source)
    if m3:
        return m3.group(1)
    # Intento 4: return 'b64';
    m4 = re.search(r"return\s*['\"]?([A-Za-z0-9+/=]{300,})['\"]?;", source)
    if m4:
        return m4.group(1)
    return ""


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════════════
def _get(url: str, is_img=False, retries=3) -> bytes | None:
    for attempt in range(retries):
        try:
            r = SESS.get(url, timeout=20 if is_img else 12)
            if r.status_code == 200:
                return r.content
            if r.status_code in (403, 404):
                return None
        except Exception:
            pass
        time.sleep(1 + attempt)
    return None


def _soup(url: str) -> BeautifulSoup | None:
    time.sleep(REQUEST_DELAY)
    raw = _get(url)
    if raw is None:
        return None
    return BeautifulSoup(raw.decode("utf-8", errors="replace"), "lxml")


# ══════════════════════════════════════════════════════════════════════════════
#  FILTROS / CATEGORÍAS
#  El sitio usa /list/{slug}/ para cada filtro.
# ══════════════════════════════════════════════════════════════════════════════
REGIONS = {
    "全部": "",
    "日本": "japan",
    "韩国": "korea",
    "内地": "china",
    "港台": "hongkong",
    "欧美": "europe",
    "其它": "other",
}
GENRES = {
    "全部": "",
    "热血": "rexue",
    "冒险": "maoxian",
    "魔幻": "mohuan",
    "搞笑": "gaoxiao",
    "萌系": "mengxi",
    "爱情": "aiqing",
    "科幻": "kehuan",
    "魔法": "mofa",
    "格斗": "gedou",
    "武侠": "wuxia",
    "战争": "zhanzheng",
    "竞技": "jingji",
    "校园": "xiaoyuan",
    "生活": "shenghuo",
    "励志": "lizhi",
    "历史": "lishi",
    "耽美": "danmei",
    "百合": "baihe",
    "后宫": "hougong",
    "治愈": "zhiyu",
    "恐怖": "kongbu",
    "推理": "tuili",
    "悬疑": "xuanyi",
    "四格": "sige",
    "职场": "zhichang",
    "侦探": "zhentan",
    "社会": "shehui",
    "伪娘": "weiniang",
    "腐女": "funv",
    "宅男": "zhainan",
}
AUDIENCE = {
    "全部": "",
    "少女": "shaonv",
    "少年": "shaonian",
    "青年": "qingnian",
    "儿童": "ertong",
    "通用": "tongyong",
}
STATUS = {"全部": "", "连载": "lianzai", "完结": "wanjie"}


def _build_list_url(region="", genre="", audience="", status="", page=1) -> str:
    """Construye la URL de /list/ con los filtros combinados."""
    parts = [p for p in [region, genre, audience, status] if p]
    slug = "_".join(parts) if parts else ""

    # Formato de Manhuagui:
    # Pág 1: /list/ o /list/japan/
    # Pág 2+: /list/index_p2.html o /list/japan/index_p2.html
    base_path = f"/list/{slug}/" if slug else "/list/"

    if page == 1:
        return f"{BASE}{base_path}"
    else:
        return f"{BASE}{base_path}index_p{page}.html"


# ══════════════════════════════════════════════════════════════════════════════
#  LISTADO DE SERIES  /list/
# ══════════════════════════════════════════════════════════════════════════════
def browse_page(
    page=1, region="", genre="", audience="", status=""
) -> tuple[list[dict], int]:
    url = _build_list_url(region, genre, audience, status, page)
    soup = _soup(url)
    if soup is None:
        return [], 0

    series: list[dict] = []
    seen: set[str] = set()

    # Usamos los mismos selectores robustos de la búsqueda
    items = soup.select(
        "#contList li, div.book-result li, ul.book-list li, div.book-list li"
    )

    for li in items:
        a = li.find("a", href=re.compile(r"/comic/\d+/"))
        if not a:
            continue
        m = re.search(r"/comic/(\d+)/", a["href"])
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue
        seen.add(cid)

        title = a.get("title") or a.get_text(strip=True)
        # Último capítulo (el <span> pequeño suele tenerlo)
        last = ""
        sp = li.find("span", class_="tt")
        if sp:
            last = sp.get_text(strip=True)

        series.append({"id": int(cid), "title": title[:60], "last": last})

    # Paginación: buscar el mayor número de página con formato _p
    total = page
    for a in soup.select("a[href*='_p']"):
        m = re.search(r"_p(\d+)\.html", a["href"])
        if m:
            total = max(total, int(m.group(1)))

    m2 = re.search(r"共\s*(\d+)\s*页", soup.get_text())
    if m2:
        total = max(total, int(m2.group(1)))

    return series, total


# ══════════════════════════════════════════════════════════════════════════════
#  BÚSQUEDA  /s/query.html
# ══════════════════════════════════════════════════════════════════════════════
def search(query: str, page=1) -> tuple[list[dict], int]:
    # Corregir la URL para respetar el formato de Manhuagui (_p2.html)
    if page == 1:
        url = f"{BASE}/s/{quote(query)}.html"
    else:
        url = f"{BASE}/s/{quote(query)}_p{page}.html"

    soup = _soup(url)
    if soup is None:
        return [], 0

    items = soup.select("#contList li, div.book-result li, ul.book-list li")
    results: list[dict] = []
    seen: set[str] = set()

    for li in items:
        a = li.find("a", href=re.compile(r"/comic/\d+/"))
        if not a:
            continue
        m = re.search(r"/comic/(\d+)/", a["href"])
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue
        seen.add(cid)
        title = a.get("title") or a.get_text(strip=True)
        results.append({"id": int(cid), "title": title[:60], "last": ""})

    # Detectar el total de páginas en la búsqueda
    total = page
    for a in soup.select("a[href*='_p']"):
        m = re.search(r"_p(\d+)\.html", a["href"])
        if m:
            total = max(total, int(m.group(1)))

    m2 = re.search(r"共\s*(\d+)\s*页", soup.get_text())
    if m2:
        total = max(total, int(m2.group(1)))

    return results, total


# ══════════════════════════════════════════════════════════════════════════════
#  INFO DE SERIE  /comic/ID/
#  La lista de capítulos está en #__VIEWSTATE → LZString.decompressFromBase64
# ══════════════════════════════════════════════════════════════════════════════
def get_comic(comic_id: int) -> dict:
    key = str(comic_id)
    if key in _CACHE:
        return _CACHE[key]

    soup = _soup(f"{BASE}/comic/{comic_id}/")
    if soup is None:
        return {}

    # Título
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        tt = soup.find("title")
        if tt:
            title = re.sub(r"漫画|在线看|看漫画.*$", "", tt.string or "").strip()

    # Descripción corta
    desc = ""
    intro = soup.select_one("div#intro-all, div.intro-all, p.intro")
    if intro:
        desc = intro.get_text(" ", strip=True)[:200]

    # ── Capítulos ──────────────────────────────────────────────────────────
    # El sitio esconde la lista en un <input id="__VIEWSTATE"> comprimido con LZString
    chapters: list[dict] = []

    vs_tag = soup.find("input", id="__VIEWSTATE") or soup.find(
        "input", attrs={"id": "__VIEWSTATE"}
    )
    if vs_tag and vs_tag.get("value"):
        vs_html = lzstring_decompress_base64(vs_tag["value"])
        if vs_html:
            vs_soup = BeautifulSoup(vs_html, "lxml")
            chapters = _parse_chapters(vs_soup, comic_id)

    # Fallback: capítulos en el HTML directo (si el sitio los incluye parcialmente)
    if not chapters:
        chapters = _parse_chapters(soup, comic_id)

    result = {
        "id": comic_id,
        "title": title or f"Comic {comic_id}",
        "desc": desc,
        "chapters": chapters,
    }
    if chapters:
        _CACHE[key] = result
    return result


def _parse_chapters(soup: BeautifulSoup, comic_id: int) -> list[dict]:
    chapters: list[dict] = []
    seen: set[str] = set()

    # El HTML descomprimido tiene <h4> para grupos (单话, 单行本, etc.)
    # y <ul class="chapter-list"> dentro de cada grupo
    for section in soup.select(".chapter-list, ul.chapter-list"):
        for a in section.find_all(
            "a", href=re.compile(rf"/comic/{comic_id}/\d+\.html")
        ):
            m = re.search(rf"/comic/{comic_id}/(\d+)\.html", a["href"])
            if not m:
                continue
            chid = m.group(1)
            if chid in seen:
                continue
            seen.add(chid)
            title = a.get("title") or a.get_text(strip=True)
            # Número de páginas (el <span> tiny dentro del <a>)
            pages = ""
            sp = a.find("span")
            if sp:
                pages = sp.get_text(strip=True)
            chapters.append(
                {
                    "id": int(chid),
                    "title": title or f"Cap {len(chapters) + 1}",
                    "pages": pages,
                    "url": f"{BASE}/comic/{comic_id}/{chid}.html",
                }
            )

    # Los capítulos vienen de más nuevo a más viejo; invertimos para orden cronológico
    chapters.reverse()
    return chapters


# ══════════════════════════════════════════════════════════════════════════════
#  IMÁGENES DE UN CAPÍTULO  /comic/ID/CHID.html
#  El script del capítulo está empaquetado con P.A.C.K.E.R.
#  Dentro hay una llamada a LZString.decompressFromBase64("...") con el JSON.
# ══════════════════════════════════════════════════════════════════════════════
def get_images(comic_id: int, chapter_id: int) -> list[str]:
    url = f"{BASE}/comic/{comic_id}/{chapter_id}.html"
    raw = _get(url)
    if raw is None:
        return []

    html = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    scripts = soup.find_all("script")

    packed_block = None
    for script in scripts:
        if script.string:
            content = script.string.strip()
            content = content.replace('window["\\x65\\x76\\x61\\x6c"]', "eval")
            if detect_packer(content):
                packed_block = content
                break

    if not packed_block:
        return []

    unpacked = ""
    try:
        unpacked = unpack_packer(packed_block)
    except Exception:
        return []

    # Variables a extraer
    files = []
    path = ""
    e_val = ""
    m_val = ""

    # Estrategia 1: Intentar extraer el bloque JSON puro
    m_json = re.search(r'(\{.*"files".*\})', unpacked, re.DOTALL | re.IGNORECASE)
    if m_json:
        try:
            data = json.loads(m_json.group(1))
            files = data.get("files", [])
            path = data.get("path", "")
            sl = data.get("sl", {})
            e_val = sl.get("e", "")
            m_val = sl.get("m", "")
        except Exception:
            pass

    # Estrategia 2 (Fallback): Extraer directamente con Regex si el JSON falló
    if not files:
        m_files = re.search(r'"files"\s*:\s*\[(.*?)\]', unpacked, re.IGNORECASE)
        if m_files:
            f_str = m_files.group(1)
            # Limpiar comillas y espacios de cada nombre de archivo
            if f_str.strip():
                files = [f.strip("\"' ") for f in f_str.split(",")]

        m_path = re.search(r'"path"\s*:\s*"([^"]+)"', unpacked, re.IGNORECASE)
        if m_path:
            path = m_path.group(1)

        m_e = re.search(r'"e"\s*:\s*(\d+|"[^"]+")', unpacked, re.IGNORECASE)
        if m_e:
            e_val = m_e.group(1).replace('"', "")

        m_m = re.search(r'"m"\s*:\s*"([^"]+)"', unpacked, re.IGNORECASE)
        if m_m:
            m_val = m_m.group(1)

    if not files:
        return []

    # Construir las URLs finales
    urls = []
    for fname in files:
        # Ignorar archivos vacíos por si la regex capturó comas extra
        if not fname:
            continue
        img_url = f"{IMG_HOST}{path}{fname}"
        if e_val or m_val:
            img_url += f"?e={e_val}&m={m_val}"
        urls.append(img_url)

    return urls


# ══════════════════════════════════════════════════════════════════════════════
#  DESCARGA Y EMPAQUETADO
# ══════════════════════════════════════════════════════════════════════════════
def _ext(url: str) -> str:
    for e in ("webp", "jpg", "jpeg", "png"):
        if f".{e}" in url.lower():
            return e
    return "jpg"


def _save(raw: bytes, path: str):
    if not has_pillow or USER_FORMAT == "original" or Image is None:
        with open(path, "wb") as f:
            f.write(raw)
        return
    try:
        img = Image.open(BytesIO(raw))
        fmt = USER_FORMAT.lower()
        if fmt in ("jpg", "jpeg") and img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA") if img.mode == "P" else img
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        kw = (
            {"quality": 92, "optimize": True}
            if fmt in ("jpg", "jpeg")
            else {"quality": 90}
            if fmt == "webp"
            else {}
        )
        img.save(path, **kw)
    except Exception:
        with open(path, "wb") as f:
            f.write(raw)


def _dl_one(args: tuple) -> bool:
    url, folder, idx, referer_url = args
    src_ext = _ext(url)
    out_ext = USER_FORMAT if (has_pillow and USER_FORMAT != "original") else src_ext
    if out_ext == "original":
        out_ext = src_ext
    path = os.path.join(folder, f"{idx:03d}.{out_ext}")
    if os.path.exists(path):
        return True

    # El CDN de manhuagui necesita el Referer del capítulo
    headers = {"Referer": referer_url, "User-Agent": SESS.headers["User-Agent"]}
    for attempt in range(3):
        try:
            r = SESS.get(url, timeout=20, headers=headers)
            if r.status_code == 200:
                _save(r.content, path)
                return True
        except Exception:
            pass
        time.sleep(1 + attempt)
    return False


def download_chapter(comic: dict, chapter: dict) -> str | None:
    def clean(s: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', "", s).strip()

    series = clean(comic.get("title", "Comic"))[:50]
    chname = clean(chapter.get("title", "Cap"))[:40]
    cid = comic["id"]
    chid = chapter["id"]

    # Directorio padre de la serie
    series_folder = f"{series} [{cid}]"
    os.makedirs(series_folder, exist_ok=True)

    # Directorio temporal para el capítulo
    folder = os.path.join(series_folder, f"{chname} [{chid}]")

    os.makedirs(folder, exist_ok=True)
    print(f"\n  {C.GR}⬇  {series[:35]} / {chname}{C.EN}")

    imgs = get_images(cid, chid)
    if not imgs:
        print(f"  {C.RE}✗  Sin imágenes para capítulo {chid}{C.EN}")
        shutil.rmtree(folder, ignore_errors=True)
        return None

    # Generar la URL del capítulo para el Referer
    chapter_url = f"{BASE}/comic/{cid}/{chid}.html"

    comp = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = [
            pool.submit(_dl_one, (url, folder, i, chapter_url))
            for i, url in enumerate(imgs)
        ]
        for _ in as_completed(futs):
            comp += 1
            pct = int(30 * comp / len(imgs))
            bar = "█" * pct + "─" * (30 - pct)
            sys.stdout.write(f"\r   [{C.CY}{bar}{C.EN}] {comp}/{len(imgs)}")
            sys.stdout.flush()
    print()

    ext_out = "cbz" if OUTPUT_TYPE == "cbz" else OUTPUT_TYPE.lower()
    out_file = os.path.join(series_folder, f"{series} - {chname} [{chid}].{ext_out}")
    print(f"   📦 Empaquetando {ext_out.upper()}...")

    if ext_out == "pdf" and has_pillow and Image is not None:
        files = sorted(f for f in os.listdir(folder))
        pages = [Image.open(os.path.join(folder, f)).convert("RGB") for f in files]
        if pages:
            pages[0].save(out_file, save_all=True, append_images=pages[1:])
    else:
        with zipfile.ZipFile(out_file, "w", zipfile.ZIP_STORED) as zf:
            for fn in sorted(os.listdir(folder)):
                zf.write(os.path.join(folder, fn), fn)

    if DELETE_TEMP:
        shutil.rmtree(folder, ignore_errors=True)

    print(f"   {C.GR}✔  {out_file}{C.EN}")
    return out_file


# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES DE MENÚ
# ══════════════════════════════════════════════════════════════════════════════
def parse_sel(s: str, n: int) -> list[int]:
    s = s.lower().replace(" ", "")
    if s == "all":
        return list(range(n))
    idxs: set[int] = set()
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                for i in range(int(a) - 1, int(b)):
                    if 0 <= i < n:
                        idxs.add(i)
        elif part.isdigit():
            i = int(part) - 1
            if 0 <= i < n:
                idxs.add(i)
    return sorted(idxs)


def choose_chapters(comic_id: int):
    print(f"\n  {C.YE}⚡ Cargando serie {comic_id}...{C.EN}")
    info = get_comic(comic_id)
    chapters = info.get("chapters", [])

    print(f"\n  {C.GR}{C.BO}{info.get('title', '')}{C.EN}")
    if info.get("desc"):
        print(f"  {C.CY}{info['desc'][:120]}...{C.EN}")
    print(f"  {C.PU}{len(chapters)} capítulos{C.EN}\n")

    if not chapters:
        print(f"  {C.RE}Sin capítulos disponibles.{C.EN}")
        input("\n  Enter para volver...")
        return

    for i, ch in enumerate(chapters):
        pages = f" ({ch['pages']})" if ch.get("pages") else ""
        print(f"  {i + 1:>4}. {ch['title'][:55]}{pages}")

    print()
    sel = input(f"{C.YE} Capítulos (1 | 1-5 | 1,3,5 | all) ➜ {C.EN}").strip()
    idxs = parse_sel(sel, len(chapters))
    if not idxs:
        print(f"  {C.RE}Selección vacía.{C.EN}")
        return

    for idx in idxs:
        download_chapter(info, chapters[idx])

    input(f"\n{C.GR} Listo. Enter para volver...{C.EN}")


def _pick(label: str, options: dict) -> str:
    """Muestra un menú de opciones y devuelve el slug elegido."""
    keys = list(options.keys())
    print(f"\n  {C.PU}{label}:{C.EN}")
    for i, k in enumerate(keys):
        print(f"  {i + 1:>2}. {k}", end="   ")
        if (i + 1) % 6 == 0:
            print()
    print()
    sel = input(f"  {C.YE}Número (Enter = todo) ➜ {C.EN}").strip()
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(keys):
            return options[keys[idx]]
    return ""  # "全部" → slug vacío


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 1: DESCARGAR
# ══════════════════════════════════════════════════════════════════════════════
def menu_download():
    header()
    print(f" {C.PU}── Descargar ──────────────────────────────{C.EN}")
    print(f" Opciones:")
    print(f"   • URL de serie  → https://www.manhuagui.com/comic/47412/")
    print(f"   • ID numérico   → 47412")
    print(f"   • URL capítulo  → https://www.manhuagui.com/comic/47412/757191.html")
    print(f"   • Texto         → nombre de la serie (realiza búsqueda)\n")

    val = input(f"{C.YE} URL, ID o nombre ➜ {C.EN}").strip()
    if not val:
        return

    # URL de capítulo directo
    m = re.search(r"/comic/(\d+)/(\d+)\.html", val)
    if m:
        cid, chid = int(m.group(1)), int(m.group(2))
        comic = {"id": cid, "title": f"Comic {cid}"}
        chapter = {"id": chid, "title": f"Cap {chid}", "pages": ""}
        download_chapter(comic, chapter)
        input(f"\n{C.GR} Listo. Enter para volver...{C.EN}")
        return

    # URL o ID de serie
    m = re.search(r"/comic/(\d+)/", val) or (
        re.fullmatch(r"\d+", val) and type("", (object,), {"group": lambda s, n: val})()
    )
    if re.search(r"/comic/(\d+)/", val):
        choose_chapters(int(re.search(r"/comic/(\d+)/", val).group(1)))
        return
    if val.isdigit():
        choose_chapters(int(val))
        return

    # Búsqueda por texto interactiva
    page = 1
    total_pgs = 1

    while True:
        header()
        print(f"\n  {C.YE}🔎 Buscando '{val}' (Página {page})...{C.EN}")
        results, total_pgs = search(val, page)

        if not results:
            print(f"  {C.RE}Sin resultados en esta página.{C.EN}")
            time.sleep(2)
            return

        print()
        for i, s in enumerate(results):
            print(f"  {i + 1:>3}. [{C.GR}{s['id']}{C.EN}] {s['title']}")

        # Mostrar controles de paginación si hay más de una página
        if total_pgs > 1:
            print(
                f"\n {C.CY}n{C.EN} siguiente  {C.CY}p{C.EN} anterior  {C.CY}q{C.EN} cancelar  —  número para descargar"
            )
            sel = (
                input(f"\n{C.YE} Acción (pág {page}/{total_pgs}) ➜ {C.EN}")
                .strip()
                .lower()
            )
        else:
            sel = (
                input(f"\n{C.YE} Número de la serie (q para salir) ➜ {C.EN}")
                .strip()
                .lower()
            )

        if sel == "q":
            return
        elif sel == "n" and page < total_pgs:
            page += 1
        elif sel == "p" and page > 1:
            page -= 1
        elif sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(results):
                choose_chapters(results[idx]["id"])
                return
            else:
                print(f"  {C.RE}Número fuera de rango.{C.EN}")
                time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 2: EXPLORAR CATÁLOGO CON FILTROS
# ══════════════════════════════════════════════════════════════════════════════
def menu_browse():
    header()
    print(f" {C.PU}── Explorar catálogo ──────────────────────{C.EN}\n")
    print(" Configurá filtros (Enter para saltear y ver todos)\n")

    region = _pick("Región", REGIONS)
    genre = _pick("Género", GENRES)
    audience = _pick("Público", AUDIENCE)
    status = _pick("Estado", STATUS)

    page = 1
    total_pgs = 999
    cache_pg: dict[int, list[dict]] = {}

    while True:
        header()
        filter_str = (
            " | ".join(
                filter(
                    None,
                    [
                        next((k for k, v in REGIONS.items() if v == region and v), ""),
                        next((k for k, v in GENRES.items() if v == genre and v), ""),
                        next(
                            (k for k, v in AUDIENCE.items() if v == audience and v), ""
                        ),
                        next((k for k, v in STATUS.items() if v == status and v), ""),
                    ],
                )
            )
            or "全部"
        )
        print(f" {C.PU}── [{filter_str}]  Página {page}/{total_pgs} ──{C.EN}\n")

        if page not in cache_pg:
            print(f"  {C.YE}⚡ Cargando...{C.EN}", end="\r")
            series, total_pgs = browse_page(page, region, genre, audience, status)
            cache_pg[page] = series
        else:
            series = cache_pg[page]

        header()
        print(f" {C.PU}── [{filter_str}]  Página {page}/{total_pgs} ──{C.EN}\n")

        if not series:
            print(f"  {C.RE}Sin resultados.{C.EN}")
        else:
            for i, s in enumerate(series):
                last = f"  {C.CY}{s['last'][:25]}{C.EN}" if s.get("last") else ""
                print(f"  {i + 1:>3}. [{C.GR}{s['id']}{C.EN}] {s['title'][:55]}{last}")

        print(
            f"\n {C.CY}n{C.EN} siguiente  {C.CY}p{C.EN} anterior  "
            f"{C.CY}f{C.EN} cambiar filtros  {C.CY}q{C.EN} volver  "
            f"— número para descargar"
        )

        cmd = input(f"\n{C.YE} Acción ➜ {C.EN}").strip().lower()

        if cmd == "q":
            break
        elif cmd == "n" and page < total_pgs:
            page += 1
        elif cmd == "p" and page > 1:
            page -= 1
        elif cmd == "f":
            # Cambiar filtros sin salir del explorador
            region = _pick("Región", REGIONS)
            genre = _pick("Género", GENRES)
            audience = _pick("Público", AUDIENCE)
            status = _pick("Estado", STATUS)
            page = 1
            cache_pg.clear()
        elif cmd.isdigit():
            idx = int(cmd) - 1
            if series and 0 <= idx < len(series):
                choose_chapters(series[idx]["id"])
            else:
                print(f"  {C.RE}Número fuera de rango.{C.EN}")
                time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def main():
    while True:
        header()
        print(f" {C.PU}Menú Principal{C.EN}\n")
        print(f" {C.BO}1.{C.EN} Descargar  (URL, ID o nombre de serie)")
        print(f" {C.BO}2.{C.EN} Explorar   catálogo con filtros")
        print(f" {C.BO}3.{C.EN} Salir\n")
        print(
            f" {C.PU}Config:{C.EN}"
            f" salida={C.CY}{OUTPUT_TYPE.upper()}{C.EN}"
            f"  imagen={C.CY}{USER_FORMAT.upper()}{C.EN}"
            f"  workers={C.CY}{MAX_WORKERS}{C.EN}"
        )

        op = input(f"\n{C.YE} Opción ➜ {C.EN}").strip()
        if op == "1":
            menu_download()
        elif op == "2":
            menu_browse()
        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YE}Interrumpido.{C.EN}")
