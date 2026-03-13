"""
╔══════════════════════════════════════════╗
║  PICACOMIC DOWNLOADER  v1.1.0            ║
║  picacomic.com  /  哔咔漫画               ║
╚══════════════════════════════════════════╝

Dependencias:
    pip install requests pillow

Uso:
    python picacomic_downloader.py
"""

import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from getpass import getpass
from io import BytesIO

try:
    from curl_cffi import requests
    from curl_cffi.requests import Session as CurlSession

    _USE_CURL = True
except ImportError:
    import requests

    CurlSession = None
    _USE_CURL = False

try:
    from PIL import Image

    has_pillow = True
except ImportError:
    Image = None
    has_pillow = False


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
OUTPUT_TYPE = "cbz"
USER_FORMAT = "original"
DELETE_TEMP = True
MAX_WORKERS = 40
REQUEST_DELAY = 0.3
IMAGE_QUALITY = "original"
AUTO_USER = "lucaaaa09"
AUTO_PASS = "Aa0!Bb2?Cc4_"
MANUAL_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJfaWQiOiI2OWFmYWM1MGZiMzgxZGM2ZjU5OWI1ZDYiLCJlbWFpbCI6Imx1Y2FhYWEwOSIsInJvbGUiOiJtZW1iZXIiLCJuYW1lIjoiTHVjYXMgR29sZHN0ZWluIiwidmVyc2lvbiI6IjIuMi4xLjIuMy4zIiwiYnVpbGRWZXJzaW9uIjoiNDQiLCJwbGF0Zm9ybSI6ImFuZHJvaWQiLCJpYXQiOjE3NzMxMjQ4MDQsImV4cCI6MTc3MzcyOTYwNH0.XHxBVgHxzhwnuRhLgABtlsmmVIx4NLY4WcALOBbW7F0"

BASE_URL = "https://picaapi.picacomic.com"
API_KEY = "C69BAF41DA5ABD1FFEDC6D2FEA56B"
SECRET = r"~d}$Q7$eIni=V)9\RK/P.RM4;9[7|@/CA}b~OW!3?EV`:<>M7pddUBL5n|0/*Cn"
APP_VER = "2.2.1.2.3.3"
BUILD_VER = "44"

SORT_OPTS = {
    "ua": "Por defecto",
    "dd": "Más nuevos",
    "da": "Más viejos",
    "ld": "Más likes",
    "vd": "Más vistos",
}

_token: str = ""

if _USE_CURL:
    _sess = CurlSession()
else:
    _sess = requests.Session()
    _sess.headers.update({"User-Agent": "okhttp/3.8.1"})


class C:
    PU = "\033[95m"
    CY = "\033[96m"
    BL = "\033[94m"
    GR = "\033[92m"
    YE = "\033[93m"
    RE = "\033[91m"
    BO = "\033[1m"
    DIM = "\033[2m"
    EN = "\033[0m"


def header(subtitle: str = ""):
    os.system("cls" if os.name == "nt" else "clear")
    print(f"{C.BL}╔══════════════════════════════╗{C.EN}")
    print(
        f"{C.BL}║{C.EN}  {C.BO}{C.PU}哔咔漫画  DOWNLOADER{C.EN}  {C.BL}v1.1{C.EN}  {C.BL}║{C.EN}"
    )
    print(f"{C.BL}╚══════════════════════════════╝{C.EN}")
    print()


def _bar(done: int, total: int, width: int = 30) -> str:
    pct = done / max(total, 1)
    fill = int(width * pct)
    return f"[{C.CY}{'█' * fill}{C.DIM}{'─' * (width - fill)}{C.EN}] {done}/{total}"


# ══════════════════════════════════════════════════════════════════════════════
#  FIRMA
# ══════════════════════════════════════════════════════════════════════════════
def _sign(path: str, ts: str, nonce: str, method: str) -> str:
    clean = path.lstrip("/")
    raw = (clean + ts + nonce + method + API_KEY).lower()
    return hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _build_headers(path: str, method: str) -> dict:
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    h = {
        "api-key": API_KEY,
        "accept": "application/vnd.picacomic.com.v1+json",
        "app-channel": "2",
        "app-version": APP_VER,
        "app-uuid": "defaultUuid",
        "app-platform": "android",
        "app-build-version": BUILD_VER,
        "time": ts,
        "nonce": nonce,
        "signature": _sign(path, ts, nonce, method),
        "image-quality": IMAGE_QUALITY,
        "Content-Type": "application/json; charset=UTF-8",
        "User-Agent": "okhttp/3.8.1",
    }
    if _token:
        h["authorization"] = _token
    return h


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP helpers
# ══════════════════════════════════════════════════════════════════════════════
def _api_get(path: str, params: dict | None = None, retries=3) -> dict | None:
    time.sleep(REQUEST_DELAY)
    url = BASE_URL + "/" + path.lstrip("/")
    for attempt in range(retries):
        try:
            if params:
                from urllib.parse import urlencode

                sign_path = path.lstrip("/") + "?" + urlencode(params)
            else:
                sign_path = path
            r = _sess.get(
                url, params=params, headers=_build_headers(sign_path, "GET"), timeout=15
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 401, 403, 404):
                try:
                    err = r.json()
                    print(
                        f"\n  {C.RE}API error {r.status_code}: {err.get('message', '')}{C.EN}"
                    )
                except Exception:
                    print(f"\n  {C.RE}HTTP {r.status_code}{C.EN}")
                return None
        except Exception as e:
            if attempt == retries - 1:
                print(f"\n  {C.RE}Request error: {e}{C.EN}")
        time.sleep(1 + attempt)
    return None


def _api_post(path: str, body: dict, retries=3) -> dict | None:
    time.sleep(REQUEST_DELAY)
    url = BASE_URL + "/" + path.lstrip("/")
    for attempt in range(retries):
        try:
            r = _sess.post(
                url,
                json=body,
                headers=_build_headers(path.lstrip("/"), "POST"),
                timeout=15,
            )
            try:
                data = r.json()
            except Exception:
                data = None
            if isinstance(data, dict):
                data["_resp_headers"] = dict(r.headers)
                data["_resp_status"] = r.status_code
            if r.status_code == 200:
                return data
            if r.status_code in (400, 401, 403, 404):
                msg = data.get("message", "") if data else ""
                print(
                    f"\n  {C.RE}API error {r.status_code}: {msg or r.text[:120]}{C.EN}"
                )
                return data
        except Exception as e:
            if attempt == retries - 1:
                print(f"\n  {C.RE}Request error: {e}{C.EN}")
        time.sleep(1 + attempt)
    return None


def _dl_image(url: str, path: str, referer: str = BASE_URL) -> bool:
    headers = {"Referer": referer, "User-Agent": "okhttp/3.8.1"}
    for attempt in range(3):
        try:
            r = _sess.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                _save_img(r.content, path)
                return True
        except Exception:
            pass
        time.sleep(1 + attempt)
    return False


def _save_img(raw: bytes, path: str):
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


# ══════════════════════════════════════════════════════════════════════════════
#  AUTENTICACIÓN
# ══════════════════════════════════════════════════════════════════════════════
def login(email: str, password: str) -> bool:
    global _token
    resp = _api_post("/auth/sign-in", {"email": email, "password": password})
    if resp is None:
        return False
    token = (
        (resp.get("data") or {}).get("token")
        or resp.get("token")
        or ((resp.get("data") or {}).get("user") or {}).get("token")
        or ""
    )
    if not token:
        hdrs = resp.get("_resp_headers") or {}
        token = (
            hdrs.get("authorization")
            or hdrs.get("Authorization")
            or hdrs.get("token")
            or hdrs.get("Token")
            or ""
        )
    if token:
        _token = token.removeprefix("Bearer ").strip()
        return True
    code = resp.get("code", 0)
    msg = resp.get("message", "")
    print(f"\n  {C.YE}⚠  Login OK (code={code}, msg={msg}) pero sin token.{C.EN}")
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  URL DE IMAGEN
# ══════════════════════════════════════════════════════════════════════════════
def _img_url(media: dict) -> str:
    server = media.get("fileServer", "").rstrip("/")
    path = media.get("path", "")
    if path.startswith("http"):
        return path
    if "tobeimg" in path or path.startswith("tobeimg"):
        return f"{server}/{path}"
    return f"{server}/static/{path}"


# ══════════════════════════════════════════════════════════════════════════════
#  API — CATEGORÍAS
# ══════════════════════════════════════════════════════════════════════════════
def get_categories() -> list[dict]:
    resp = _api_get("/categories")
    if not resp or resp.get("code") != 200:
        return []
    cats = (resp.get("data") or {}).get("categories", [])
    return [c for c in cats if c.get("isWeb") is not True]


# ══════════════════════════════════════════════════════════════════════════════
#  API — COMICS POR CATEGORÍA (una página)
# ══════════════════════════════════════════════════════════════════════════════
def get_comics_by_category(category: str, page=1, sort="dd") -> tuple[list[dict], int]:
    resp = _api_get("/comics", params={"c": category, "page": page, "s": sort})
    if not resp or resp.get("code") != 200:
        return [], 0
    outer = resp.get("data") or {}
    data = outer.get("comics") or {}
    docs = data.get("docs", [])
    pages = data.get("pages", 1)
    return [_parse_comic_stub(d) for d in docs], pages


# ══════════════════════════════════════════════════════════════════════════════
#  CATÁLOGO COMPLETO — todas las categorías en paralelo
# ══════════════════════════════════════════════════════════════════════════════
def _fetch_global_page(page: int, sort: str) -> tuple[int, list[dict], int]:
    """GET /comics?page=N&s=sort — catálogo global sin filtro de categoría."""
    resp = _api_get("/comics", params={"page": page, "s": sort})
    if not resp or resp.get("code") != 200:
        return page, [], 0
    outer = (resp.get("data") or {}).get("comics") or {}
    docs = outer.get("docs", [])
    total = outer.get("pages", 1)
    return page, [_parse_comic_stub(d) for d in docs], total


def fetch_full_catalog(
    sort: str = "dd", workers: int = 20, page_limit: int | None = None
) -> list[dict]:
    """
    Catálogo global: GET /comics?page=N&s=sort
    135,748 series · 6,788 páginas · sin deduplicación necesaria.
    Con workers=20 → ~340 batches en paralelo (~3-5 min completo).
    page_limit: si se especifica, carga solo las primeras N páginas.
    """
    # Paso 1: pág 1 para saber el total de páginas
    print(f"  {C.YE}Consultando catálogo global…{C.EN}")
    _, first_comics, total_pages = _fetch_global_page(1, sort)
    if not first_comics:
        print(f"  {C.RE}No se pudo acceder al catálogo global.{C.EN}")
        return []

    if page_limit:
        total_pages = min(total_pages, page_limit)
    print(f"  {C.GR}{total_pages} páginas · ~{total_pages * 20} series{C.EN}")

    all_comics: list[dict] = list(first_comics)
    remaining = list(range(2, total_pages + 1))
    done = 1

    # Paso 2: resto en paralelo, en bloques para no saturar
    BATCH = workers * 5  # lanzar de a 100 páginas simultáneas
    for batch_start in range(0, len(remaining), BATCH):
        batch = remaining[batch_start : batch_start + BATCH]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_fetch_global_page, pg, sort): pg for pg in batch}
            for fut in as_completed(futs):
                _, comics, _ = fut.result()
                all_comics.extend(comics)
                done += 1
                pct = int(40 * done / total_pages)
                bar = f"{'█' * pct}{'─' * (40 - pct)}"
                sys.stdout.write(
                    f"\r  {C.CY}[{bar}]{C.EN} {done}/{total_pages} págs  "
                    f"{C.GR}{len(all_comics)} series{C.EN}   "
                )
                sys.stdout.flush()

    print(f"\n  {C.GR}✔ {len(all_comics)} series cargadas{C.EN}                    ")
    return all_comics


# ══════════════════════════════════════════════════════════════════════════════
#  API — BÚSQUEDA
# ══════════════════════════════════════════════════════════════════════════════
def search(
    keyword: str, page=1, sort="dd", categories: list | None = None
) -> tuple[list[dict], int]:
    body: dict = {"keyword": keyword, "sort": sort, "page": page}
    if categories:
        body["categories"] = categories
    resp = _api_post("/comics/advanced-search?page=" + str(page), body)
    if not resp or resp.get("code") != 200:
        return [], 0
    outer = resp.get("data") or {}
    data = outer.get("comics") or {}
    docs = data.get("docs", [])
    pages = data.get("pages", 1)
    return [_parse_comic_stub(d) for d in docs], pages


def _parse_comic_stub(d: dict) -> dict:
    return {
        "id": d.get("_id", ""),
        "title": d.get("title", "")[:70],
        "author": d.get("author", ""),
        "pages": d.get("pagesCount", 0),
        "eps": d.get("epsCount", 1),
        "finished": d.get("finished", False),
        "likes": d.get("likesCount", 0),
        "categories": d.get("categories", []),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  API — INFO / CAPÍTULOS / PÁGINAS
# ══════════════════════════════════════════════════════════════════════════════
def get_comic_info(comic_id: str) -> dict:
    resp = _api_get(f"/comics/{comic_id}")
    if not resp or resp.get("code") != 200:
        return {}
    c = (resp.get("data") or {}).get("comic") or {}
    if not c:
        return {}
    return {
        "id": c.get("_id", comic_id),
        "title": c.get("title", ""),
        "author": c.get("author", ""),
        "desc": c.get("description", "")[:200],
        "pages": c.get("pagesCount", 0),
        "eps": c.get("epsCount", 1),
        "finished": c.get("finished", False),
        "likes": c.get("likesCount", 0),
        "categories": c.get("categories", []),
        "tags": c.get("tags", []),
    }


def get_episodes(comic_id: str) -> list[dict]:
    eps = []
    page = 1
    while True:
        resp = _api_get(f"/comics/{comic_id}/eps", params={"page": page})
        if not resp or resp.get("code") != 200:
            break
        data = ((resp.get("data") or {}).get("eps")) or {}
        docs = data.get("docs", [])
        eps.extend(docs)
        if page >= data.get("pages", 1):
            break
        page += 1
    eps.sort(key=lambda e: e.get("order", 0))
    return [
        {
            "order": e.get("order", i + 1),
            "title": e.get("title", f"Cap {i + 1}"),
            "id": e.get("_id", ""),
        }
        for i, e in enumerate(eps)
    ]


def get_pages(comic_id: str, ep_order: int) -> list[dict]:
    pages = []
    page = 1
    while True:
        resp = _api_get(
            f"/comics/{comic_id}/order/{ep_order}/pages", params={"page": page}
        )
        if not resp or resp.get("code") != 200:
            break
        data = ((resp.get("data") or {}).get("pages")) or {}
        docs = data.get("docs", [])
        pages.extend(docs)
        if page >= data.get("pages", 1):
            break
        page += 1
    return pages


# ══════════════════════════════════════════════════════════════════════════════
#  DESCARGA DE UN CAPÍTULO
# ══════════════════════════════════════════════════════════════════════════════
def _clean(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


def _ext_from_name(name: str, fallback="jpg") -> str:
    m = re.search(r"\.(\w+)$", name)
    return m.group(1).lower() if m else fallback


def download_chapter(comic: dict, ep: dict) -> str | None:
    series = _clean(comic.get("title", "Comic"))[:50]
    chname = _clean(ep.get("title", "Cap"))[:40]
    order = ep.get("order", 1)
    folder = f"{series} - {chname} [ord{order}]"
    os.makedirs(folder, exist_ok=True)
    print(f"\n  {C.GR}⬇  {series[:35]} / {chname}{C.EN}")

    pages = get_pages(comic["id"], order)
    if not pages:
        print(f"  {C.RE}✗  Sin páginas para '{chname}'{C.EN}")
        shutil.rmtree(folder, ignore_errors=True)
        return None

    tasks = []
    for i, pg in enumerate(pages):
        media = pg.get("media", {})
        url = _img_url(media)
        orig = media.get("originalName", f"{i + 1:03d}.jpg")
        src_ext = _ext_from_name(orig)
        out_ext = USER_FORMAT if (has_pillow and USER_FORMAT != "original") else src_ext
        if out_ext == "original":
            out_ext = src_ext
        tasks.append((url, os.path.join(folder, f"{i + 1:03d}.{out_ext}")))

    comp = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {
            pool.submit(_dl_image, url, dest): i for i, (url, dest) in enumerate(tasks)
        }
        for fut in as_completed(futs):
            comp += 1
            sys.stdout.write(f"\r   {_bar(comp, len(tasks))}")
            sys.stdout.flush()
    print()

    ext_out = "cbz" if OUTPUT_TYPE == "cbz" else OUTPUT_TYPE.lower()
    out_file = f"{folder}.{ext_out}"
    print(f"   📦 Empaquetando {ext_out.upper()}…")

    if ext_out == "pdf" and has_pillow and Image is not None:
        files = sorted(os.listdir(folder))
        imgs = []
        for fn in files:
            try:
                imgs.append(Image.open(os.path.join(folder, fn)).convert("RGB"))
            except Exception:
                pass
        if imgs:
            imgs[0].save(out_file, save_all=True, append_images=imgs[1:])
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


def _print_comics_list(comics: list[dict], offset: int = 0):
    for i, c in enumerate(comics):
        fin = f"{C.GR}✔{C.EN}" if c.get("finished") else f"{C.YE}…{C.EN}"
        eps = f"{C.BL}{c['eps']:>2}ep{C.EN}"
        num = f"{C.BO}{offset + i + 1:>4}.{C.EN}"
        titl = c["title"][:46]
        cats = f"{C.PU}{','.join(c.get('categories', []))[:20]}{C.EN}"
        print(f"  {num} {fin} {eps}  {titl:<46}  {cats}")


def show_comic_and_choose_eps(comic_id: str):
    header("⚡ Cargando…")
    info = get_comic_info(comic_id)
    if not info:
        print(f"  {C.RE}No se pudo cargar el cómic.{C.EN}")
        input("\n  Enter para volver…")
        return

    header(info["title"][:38])
    print(f"  {C.BO}{C.GR}{info['title']}{C.EN}")
    if info.get("author"):
        print(f"  {C.CY}✏  {info['author']}{C.EN}")
    cats = "  ".join(info.get("categories", []))
    status = (
        f"{C.GR}Completo{C.EN}" if info.get("finished") else f"{C.YE}En curso{C.EN}"
    )
    print(f"  {C.PU}{cats}{C.EN}")
    print(
        f"  {status}  {C.BL}│{C.EN}  {info['pages']} págs  {C.BL}│{C.EN}  {C.RE}❤ {info['likes']}{C.EN}"
    )
    if info.get("desc"):
        print(f"\n  {info['desc'][:120]}")
    print()

    print(f"  {C.YE}Cargando capítulos…{C.EN}")
    eps = get_episodes(comic_id)
    if not eps:
        print(f"  {C.RE}Sin capítulos disponibles.{C.EN}")
        input("\n  Enter para volver…")
        return

    print(f"  {C.PU}{len(eps)} capítulos{C.EN}\n")
    for i, ep in enumerate(eps):
        print(f"  {C.BO}{i + 1:>3}.{C.EN}  {ep['title'][:60]}")

    print()
    sel = input(f"  {C.YE}Capítulos  1 · 1-5 · 1,3,5 · all  ➜ {C.EN}").strip()
    idxs = parse_sel(sel, len(eps))
    if not idxs:
        print(f"  {C.RE}Selección vacía.{C.EN}")
        return

    for idx in idxs:
        download_chapter(info, eps[idx])

    input(f"\n{C.GR} Listo. Enter para volver…{C.EN}")


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ DE RESULTADOS  (paginación + toggle 't' + multiselect)
# ══════════════════════════════════════════════════════════════════════════════
def results_menu(comics: list[dict], label: str, paginated: bool = True):
    PAGE = 20
    page = 0
    while True:
        header()
        if paginated:
            start = page * PAGE
            end = min(start + PAGE, len(comics))
        else:
            start, end = 0, len(comics)

        print(f"  {C.PU}'{label}'  ({start + 1}–{end} / {len(comics)}){C.EN}")
        print(f"  {'━' * 60}")
        _print_comics_list(comics[start:end], offset=start)
        print(f"  {'━' * 60}")

        nav = []
        if paginated and end < len(comics):
            nav.append(f"{C.CY}n{C.EN}=sig")
        if paginated and page > 0:
            nav.append(f"{C.CY}p{C.EN}=ant")
        nav.append(f"{C.CY}t{C.EN}=toggle pag")
        nav.append(f"{C.CY}q{C.EN}=volver")
        print("  " + "  ".join(nav) + "  o número(s)")

        sel = input(f"\n  {C.YE}➜ {C.EN}").strip().lower()

        if sel == "n" and paginated and end < len(comics):
            page += 1
        elif sel == "p" and paginated and page > 0:
            page -= 1
        elif sel == "t":
            paginated = not paginated
            page = 0
        elif sel == "q":
            return
        elif sel.isdigit():
            idx = int(sel) - 1
            if 0 <= idx < len(comics):
                show_comic_and_choose_eps(comics[idx]["id"])
                input(f"\n  {C.CY}Enter para continuar…{C.EN}")
        elif "," in sel or (sel and "-" in sel and not sel.startswith("-")):
            for idx in parse_sel(sel, len(comics)):
                show_comic_and_choose_eps(comics[idx]["id"])
            input(f"\n  {C.CY}Enter para continuar…{C.EN}")


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 1: DESCARGAR / BUSCAR
# ══════════════════════════════════════════════════════════════════════════════
def menu_download():
    header("🔍  Buscar / Descargar")
    print(f"  {C.CY}ID{C.EN} (24 chars hex)  o  {C.CY}nombre{C.EN} del cómic\n")
    val = input(f"  {C.YE}➜ {C.EN}").strip()
    if not val:
        return

    if re.fullmatch(r"[0-9a-f]{24}", val, re.IGNORECASE):
        show_comic_and_choose_eps(val)
        return

    page = 1
    total_pgs = 1
    sort = "dd"

    while True:
        header()
        print(
            f"  {C.YE}🔎  {val}{C.EN}  {C.BL}│{C.EN}  pág {C.BO}{page}{C.EN}/{total_pgs}  {C.BL}│{C.EN}  {SORT_OPTS[sort]}\n"
        )
        results, total_pgs = search(val, page=page, sort=sort)

        if not results:
            print(f"  {C.RE}Sin resultados.{C.EN}")
            time.sleep(2)
            return

        _print_comics_list(results)

        print(
            f"\n  {C.BL}[n]{C.EN} sig  {C.BL}[p]{C.EN} ant  {C.BL}[s]{C.EN} orden  {C.BL}[q]{C.EN} volver  número=descargar"
        )
        cmd = input(f"\n  {C.YE}➜ {C.EN}").strip().lower()

        if cmd == "q":
            return
        elif cmd == "n" and page < total_pgs:
            page += 1
        elif cmd == "p" and page > 1:
            page -= 1
        elif cmd == "s":
            sort_keys = list(SORT_OPTS.keys())
            print()
            for i, (k, v) in enumerate(SORT_OPTS.items()):
                print(f"  {i + 1}. {v} ({k})")
            sc = input("  Número ➜ ").strip()
            if sc.isdigit():
                idx = int(sc) - 1
                if 0 <= idx < len(sort_keys):
                    sort = sort_keys[idx]
            page = 1
        elif cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(results):
                show_comic_and_choose_eps(results[idx]["id"])
                return
            else:
                print(f"  {C.RE}Número fuera de rango.{C.EN}")
                time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 2: EXPLORAR POR CATEGORÍA (una categoría a la vez)
# ══════════════════════════════════════════════════════════════════════════════
def menu_browse():
    header("📂  Categorías")
    print(f"  {C.YE}Cargando…{C.EN}")
    cats = get_categories()
    if not cats:
        print(f"  {C.RE}No se pudieron cargar las categorías.{C.EN}")
        input("\n  Enter para volver…")
        return

    header("📂  Categorías")
    cols = 3
    for i, c in enumerate(cats):
        entry = f"  {C.BO}{i + 1:>2}.{C.EN} {C.CY}{c.get('title', '')}{C.EN}"
        print(entry, end="")
        if (i + 1) % cols == 0:
            print()
    print("\n")

    sel = input(f"  {C.YE}Número ➜ {C.EN}").strip()
    if not sel.isdigit():
        return
    idx = int(sel) - 1
    if not (0 <= idx < len(cats)):
        return

    cat = cats[idx]["title"]
    sort = "dd"
    page = 1
    total_pgs = 999
    cache: dict[int, list[dict]] = {}

    while True:
        if page not in cache:
            header(f"{cat}")
            print(f"  {C.YE}Cargando pág {page}…{C.EN}")
            comics, total_pgs = get_comics_by_category(cat, page=page, sort=sort)
            cache[page] = comics
        else:
            comics = cache[page]

        header(f"{cat[:30]}")
        print(
            f"  {C.PU}{cat}{C.EN}  {C.BL}│{C.EN}  {SORT_OPTS[sort]}  {C.BL}│{C.EN}  pág {C.BO}{page}{C.EN}/{total_pgs}\n"
        )

        if not comics:
            print(f"  {C.RE}Sin resultados.{C.EN}")
        else:
            _print_comics_list(comics)

        print(
            f"\n  {C.BL}[n]{C.EN} sig  {C.BL}[p]{C.EN} ant  {C.BL}[s]{C.EN} orden  {C.BL}[q]{C.EN} volver  número=descargar"
        )
        cmd = input(f"\n  {C.YE}➜ {C.EN}").strip().lower()

        if cmd == "q":
            break
        elif cmd == "n" and page < total_pgs:
            page += 1
        elif cmd == "p" and page > 1:
            page -= 1
        elif cmd == "s":
            sort_keys = list(SORT_OPTS.keys())
            print()
            for i, (k, v) in enumerate(SORT_OPTS.items()):
                print(f"  {i + 1}. {v}")
            sc = input("  Número ➜ ").strip()
            if sc.isdigit():
                ii = int(sc) - 1
                if 0 <= ii < len(sort_keys):
                    sort = sort_keys[ii]
            page = 1
            cache.clear()
        elif cmd.isdigit():
            ii = int(cmd) - 1
            if comics and 0 <= ii < len(comics):
                show_comic_and_choose_eps(comics[ii]["id"])
            else:
                print(f"  {C.RE}Número fuera de rango.{C.EN}")
                time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ 3: CATÁLOGO COMPLETO — endpoint global /comics (135k series)
# ══════════════════════════════════════════════════════════════════════════════
def menu_catalog():
    header("📚  Catálogo completo")

    print(f"  {C.DIM}Catálogo global: ~135,748 series · 6,788 páginas{C.EN}")
    print(f"  {C.DIM}Con workers=20 tarda ~3-5 min en cargarse todo.{C.EN}\n")

    # Elegir ordenamiento
    print(f"  {C.BO}Ordenar por:{C.EN}")
    sort_keys = list(SORT_OPTS.keys())
    for i, (k, v) in enumerate(SORT_OPTS.items()):
        print(f"  {C.BO}{i + 1}.{C.EN} {v}")
    sc = input(f"\n  {C.YE}Número (Enter = Más nuevos) ➜ {C.EN}").strip()
    sort = "dd"
    if sc.isdigit():
        idx = int(sc) - 1
        if 0 <= idx < len(sort_keys):
            sort = sort_keys[idx]

    # Opción de carga parcial
    print(f"\n  {C.BO}¿Cuántas páginas cargar?{C.EN}")
    print(
        f"  {C.DIM}Enter = todas (~3-5 min)  |  número = solo N páginas (ej: 100 = 2000 series){C.EN}"
    )
    lim_raw = input(f"  {C.YE}➜ {C.EN}").strip()
    page_limit = int(lim_raw) if lim_raw.isdigit() else None

    print(f"\n  {C.CY}⚡ Cargando catálogo [{SORT_OPTS[sort]}]…{C.EN}")
    all_comics = fetch_full_catalog(sort=sort, page_limit=page_limit)

    if not all_comics:
        print(f"  {C.RE}Sin resultados.{C.EN}")
        input(f"\n  {C.CY}Enter para volver…{C.EN}")
        return

    # Filtro en memoria
    filt = (
        input(f"\n  {C.CY}Filtrar por nombre/categoría/autor (Enter=ver todos): {C.EN}")
        .strip()
        .lower()
    )
    if filt:
        all_comics = [
            c
            for c in all_comics
            if filt in c["title"].lower()
            or any(filt in cat.lower() for cat in c.get("categories", []))
            or filt in c.get("author", "").lower()
        ]
    if not all_comics:
        print(f"  {C.RE}Sin resultados para '{filt}'.{C.EN}")
        time.sleep(2)
        return

    pag_raw = (
        input(f"  {C.CY}¿Mostrar paginado? [Enter=sí / n=todo] ➜ {C.EN}")
        .strip()
        .lower()
    )
    paginated = pag_raw != "n"
    results_menu(all_comics, f"Catálogo · {SORT_OPTS[sort]}", paginated=paginated)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def _do_login():
    global _token
    header()
    print(f" {C.PU}── Iniciar Sesión ─────────────────────────{C.EN}\n")
    email = input(f"  {C.YE}Email/usuario ➜ {C.EN}").strip()
    password = getpass(f"  {C.YE}Password ➜ {C.EN}")
    print(f"\n  {C.YE}⚡ Iniciando sesión…{C.EN}")
    if login(email, password):
        print(f"  {C.GR}✔  Sesión iniciada correctamente.{C.EN}")
        time.sleep(1)
        return True
    else:
        print(f"  {C.RE}✗  Credenciales inválidas o error de red.{C.EN}")
        input("\n  Enter para volver…")
        return False


def main():
    global _token

    if not _USE_CURL:
        print(f"\n  {C.YE}⚠  curl_cffi no instalado — el login puede fallar.{C.EN}")
        print(f"  {C.CY}Instalalo con: pip install curl_cffi{C.EN}\n")
        time.sleep(2)

    if MANUAL_TOKEN and not _token:
        _token = MANUAL_TOKEN
        header()
        print(f"\n  {C.GR}✔  Token cargado. Bienvenido.{C.EN}")
        time.sleep(0.6)
    elif AUTO_USER and AUTO_PASS and not _token:
        header()
        print(
            f"\n  {C.YE}⚡ Iniciando sesión como {C.BO}{AUTO_USER}{C.EN}{C.YE}…{C.EN}"
        )
        if login(AUTO_USER, AUTO_PASS):
            print(f"  {C.GR}✔  Sesión iniciada.{C.EN}")
            time.sleep(0.8)
        else:
            print(f"  {C.RE}✗  Auto-login falló.{C.EN}")
            time.sleep(1)

    while not _token:
        header()
        print(f" {C.PU}Menú Principal{C.EN}\n")
        print(f" {C.BO}1.{C.EN} Iniciar sesión")
        print(f" {C.BO}2.{C.EN} Salir\n")
        op = input(f"{C.YE} Opción ➜ {C.EN}").strip()
        if op == "1":
            _do_login()
        elif op == "2":
            return

    while True:
        header()
        fmt_out = OUTPUT_TYPE.upper()
        fmt_img = USER_FORMAT.upper()
        print(f" {C.BO}1.{C.EN}  Buscar / descargar por ID o nombre")
        print(
            f" {C.BO}2.{C.EN}  {C.GR}📚 Catálogo completo{C.EN}  {C.DIM}(~135k series, carga paralela){C.EN}"
        )
        print(f" {C.BO}3.{C.EN}  Salir")
        print(f"\n  {C.BL}{'─' * 42}{C.EN}")
        print(
            f"  {C.PU}salida{C.EN} {C.CY}{fmt_out}{C.EN}   "
            f"{C.PU}imagen{C.EN} {C.CY}{fmt_img}{C.EN}   "
            f"{C.PU}workers{C.EN} {C.CY}{MAX_WORKERS}{C.EN}   "
            f"{C.PU}calidad{C.EN} {C.CY}{IMAGE_QUALITY}{C.EN}"
        )

        op = input(f"\n{C.YE}  ➜ {C.EN}").strip()
        if op == "1":
            menu_download()
        elif op == "2":
            menu_catalog()
        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YE}Interrumpido.{C.EN}")
