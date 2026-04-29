"""
pigmh.com Scraper v3.0
======================
Cifrado: AES-128-CBC — IV = primeros 16 bytes del ciphertext base64
Requiere: pip install requests beautifulsoup4 pycryptodome

Menu:
  1. Descargar serie o capitulo  (URL, slug)
  2. Buscar serie por nombre
  3. Listar / explorar catalogo
  4. Salir
"""
from __future__ import annotations

import base64, io, json, os, re, shutil, sys, time, zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ─── CONFIG ──────────────────────────────────────────────────
BASE_URL    = "https://www.pigmh.com"
OUTPUT_DIR  = "pigmh_descargas"
AES_KEY     = b"5V&RoR%Jf@pJPydF"
MAX_WORKERS = 20
DELETE_TEMP = True
MIN_IMG_KB  = 3

HEADERS = {
    "User-Agent"     : "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

# ─── COLORES ─────────────────────────────────────────────────
class C:
    G = "\033[92m"; Y = "\033[93m"; R = "\033[91m"
    B = "\033[94m"; M = "\033[95m"; E = "\033[0m"; BO = "\033[1m"

def ok(s):  return f"{C.G}[OK]{C.E} {s}"
def err(s): return f"{C.R}[!]{C.E}  {s}"
def inf(s): return f"{C.B}[*]{C.E} {s}"

def header():
    os.system("cls" if os.name == "nt" else "clear")
    print(f"{C.M}  +==========================================+")
    print(f"  |   pigmh.com Scraper v3.0               |")
    print(f"  |   AES-128-CBC  |  puro requests        |")
    print(f"  +==========================================+{C.E}\n")

# ─── SESION ──────────────────────────────────────────────────
SESSION = requests.Session()
_adp = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
SESSION.mount("http://", _adp); SESSION.mount("https://", _adp)
SESSION.headers.update(HEADERS)

# ─── DESCIFRADO ──────────────────────────────────────────────
def decrypt_params(params_b64: str) -> dict:
    """
    Replica cms.min.js:
      raw = base64decode(params)
      IV  = raw[:16]
      CT  = raw[16:]
      JSON.parse( AES-CBC-decrypt(CT, KEY, IV) )
    """
    raw    = base64.b64decode(params_b64)   # ya viene en multiplo de 3; sin padding extra
    iv, ct = raw[:16], raw[16:]
    dec    = unpad(AES.new(AES_KEY, AES.MODE_CBC, iv).decrypt(ct), 16)
    return json.loads(dec.decode("utf-8"))

# ─── IMAGENES DE UN CAPITULO ─────────────────────────────────
def get_chapter_images(slug: str) -> tuple[str, list[str]]:
    """Devuelve (titulo, [url_completa, ...])."""
    url = f"{BASE_URL}/chapter/{slug}"
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=15,
                            headers={**HEADERS, "Referer": BASE_URL + "/"})
            if r.status_code != 200:
                time.sleep(attempt + 1); continue

            m = re.search(r"params\s*=\s*'([A-Za-z0-9+/=]+)'", r.text)
            if not m:
                time.sleep(1.5); continue

            data   = decrypt_params(m.group(1))
            title  = data.get("chapter_title", slug)
            paths  = data.get("chapter_images", [])
            hosts  = data.get("images_hosts", [])
            b64mode = data.get("images_base64", False)

            if not paths or not hosts:
                return title, []

            host = hosts[0]
            urls: list[str] = []
            for p in paths:
                if str(p).startswith("http"):
                    urls.append(str(p))
                elif b64mode:
                    encoded = base64.b64encode(str(p).encode()).decode()
                    urls.append(f"{host}/{encoded}")
                else:
                    sep = "" if str(p).startswith("/") else "/"
                    urls.append(f"{host}{sep}{p}")
            return title, urls

        except Exception as e:
            print(err(f"  intento {attempt+1}: {e}"))
            time.sleep(attempt + 1)

    return slug, []

# ─── INFO DE SERIE ───────────────────────────────────────────
def get_series_info(slug: str) -> tuple[str, list[dict]]:
    url = f"{BASE_URL}/comic/{slug}"
    r   = SESSION.get(url, timeout=15, headers=HEADERS)
    if r.status_code != 200:
        return slug, []

    soup  = BeautifulSoup(r.text, "html.parser")
    h1    = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else slug

    chapters: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=re.compile(r"/chapter/[A-Za-z0-9]+")):
        cslug = a["href"].split("/chapter/")[1].rstrip("/").split("?")[0]
        if cslug in seen: continue
        seen.add(cslug)
        chapters.append({"slug": cslug, "title": a.get_text(strip=True) or cslug})

    def sort_key(c: dict) -> float:
        m = re.search(r"(\d+(?:\.\d+)?)", c["title"])
        return float(m.group(1)) if m else 0.0

    chapters.sort(key=sort_key)
    return title, chapters

# ─── BUSQUEDA ────────────────────────────────────────────────
def search(query: str) -> list[dict]:
    try:
        r = SESSION.get(f"{BASE_URL}/search", params={"q": query}, timeout=12,
                        headers={**HEADERS, "Referer": BASE_URL + "/"})
        if r.status_code != 200: return []
        soup = BeautifulSoup(r.text, "html.parser")
        results: list[dict] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=re.compile(r"/comic/[A-Za-z0-9]+")):
            slug = a["href"].split("/comic/")[1].rstrip("/").split("?")[0]
            if slug in seen: continue
            seen.add(slug)
            name = (a.get("title") or a.get_text(strip=True) or "").strip()
            if len(name) > 1:
                results.append({"slug": slug, "title": name})
        return results
    except Exception:
        return []

# ─── CATALOGO ────────────────────────────────────────────────
def load_catalog() -> list[dict]:
    catalog: list[dict] = []
    seen: set[str] = set()
    for page in ["/ranking", "/update", "/category", "/"]:
        try:
            r = SESSION.get(BASE_URL + page, timeout=15, headers=HEADERS)
            if r.status_code != 200: continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=re.compile(r"/comic/[A-Za-z0-9]+")):
                slug = a["href"].split("/comic/")[1].rstrip("/").split("?")[0]
                if slug in seen: continue
                seen.add(slug)
                name = re.sub(r"\s+", " ",
                              (a.get("title") or a.get_text(strip=True) or "").strip())
                if len(name) >= 2:
                    catalog.append({"slug": slug, "title": name})
        except Exception:
            pass
        if len(catalog) >= 300: break
    return catalog

# ─── DESCARGA ────────────────────────────────────────────────
def safe_name(s: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", s).strip() or "untitled"

def _dl_one(args: tuple[str, str, int]) -> bool:
    url, folder, idx = args
    ext  = url.split("?")[0].rsplit(".", 1)[-1].lower()
    ext  = ext if ext in ("jpg","jpeg","png","webp","gif","avif") else "jpg"
    dest = Path(folder) / f"{idx+1:03d}.{ext}"
    if dest.exists() and dest.stat().st_size > MIN_IMG_KB * 1024:
        return True
    dl_h = {**HEADERS, "Referer": BASE_URL+"/", "Accept": "image/avif,image/webp,*/*"}
    for attempt in range(3):
        try:
            r = SESSION.get(url, headers=dl_h, timeout=(6, 20), stream=True)
            if r.status_code == 200:
                data = r.content
                if len(data) > MIN_IMG_KB * 1024:
                    dest.write_bytes(data)
                    return True
        except Exception:
            pass
        time.sleep(attempt + 1)
    return False

def download_chapter(cap_slug: str, num: int, total: int, base_dir: str) -> bool:
    cap_title, imgs = get_chapter_images(cap_slug)
    if not imgs:
        time.sleep(5)
        cap_title, imgs = get_chapter_images(cap_slug)
    if not imgs:
        print(f"\r  [{num}/{total}] {err(f'{cap_title} — sin imagenes')}")
        return False

    cap_dir = os.path.join(base_dir, f"{num:03d} - {safe_name(cap_title)}")
    os.makedirs(cap_dir, exist_ok=True)
    ok_c = fail_c = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_dl_one, (u, cap_dir, i)): i for i, u in enumerate(imgs)}
        for fut in as_completed(futs):
            ok_c += 1 if fut.result() else 0
            fail_c += 0 if fut.result() else 1
            done = ok_c + fail_c
            pct  = int(30 * done // len(imgs))
            sys.stdout.write(
                f"\r  [{num}/{total}] {safe_name(cap_title)[:28]:28s} "
                f"[{C.G}{'#'*pct}{C.E}{'-'*(30-pct)}] {done}/{len(imgs)}"
            )
            sys.stdout.flush()
    print()

    zip_path = os.path.join(base_dir, f"{num:03d} - {safe_name(cap_title)}.cbz")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(os.listdir(cap_dir)):
            fp = os.path.join(cap_dir, f)
            if os.path.isfile(fp): zf.write(fp, f)
    if DELETE_TEMP:
        shutil.rmtree(cap_dir, ignore_errors=True)
    if fail_c:
        print(f"     {C.Y}Advertencia:{C.E} {fail_c} imagenes fallaron")
    return ok_c > 0

# ─── SELECCION ───────────────────────────────────────────────
PAGE = 20

def parse_selection(raw: str, total: int) -> list[int]:
    raw = raw.strip().lower().replace(" ", "")
    if raw == "all": return list(range(total))
    idxs: set[int] = set()
    for part in raw.split(","):
        try:
            if "-" in part:
                a, b = map(int, part.split("-", 1))
                idxs.update(i for i in range(a-1, b) if 0 <= i < total)
            elif part.isdigit():
                i = int(part) - 1
                if 0 <= i < total: idxs.add(i)
        except Exception:
            pass
    return sorted(idxs)

def slug_from_input(raw: str) -> tuple[str, str]:
    raw = raw.strip().rstrip("/")
    if "/chapter/" in raw: return "chapter", raw.split("/chapter/")[-1].split("?")[0]
    if "/comic/"   in raw: return "comic",   raw.split("/comic/")[-1].split("?")[0]
    return "comic", raw

# ─── FLUJO DESCARGAR ─────────────────────────────────────────
def flujo_descargar(entrada: str = "") -> None:
    if not entrada:
        print(f"\n  Ingresa URL o slug:")
        print(f"    Serie  : https://www.pigmh.com/comic/P4beVYdopg  (o solo P4beVYdopg)")
        print(f"    Capitulo: https://www.pigmh.com/chapter/bEM6lUndOv")
        print()
        entrada = input(f"  {C.Y}-> {C.E}").strip()
        if not entrada: return

    tipo, slug = slug_from_input(entrada)

    if tipo == "chapter":
        cap_title, imgs = get_chapter_images(slug)
        if not imgs:
            print(err("No se obtuvieron imagenes.")); return
        base_dir = os.path.join(OUTPUT_DIR, safe_name(cap_title))
        os.makedirs(base_dir, exist_ok=True)
        download_chapter(slug, 1, 1, base_dir)
        print(ok(f"Guardado en {base_dir}/"))
        return

    print(inf("Cargando info de la serie..."))
    series_title, chapters = get_series_info(slug)
    if not chapters:
        print(err("No se encontraron capitulos.")); return

    print(f"\n  {C.BO}{series_title}{C.E}  [{slug}]  —  {len(chapters)} capitulos\n")

    offset, sel = 0, ""
    while True:
        end = min(offset + PAGE, len(chapters))
        print(f"  {C.M}{'='*52}{C.E}")
        for i in range(offset, end):
            print(f"  {C.BO}{i+1:4d}.{C.E} {chapters[i]['title']}")
        print(f"  {C.M}{'='*52}{C.E}")
        nav = []
        if end < len(chapters): nav.append(f"{C.B}n{C.E}=sig")
        if offset > 0:          nav.append(f"{C.B}p{C.E}=ant")
        nav.append(f"Ej: {C.Y}1{C.E} / {C.Y}3-7{C.E} / {C.Y}all{C.E}")
        print("  " + "  ".join(nav))
        raw = input(f"\n  {C.Y}Capitulos -> {C.E}").strip()
        if   raw.lower() == "n" and end < len(chapters): offset += PAGE
        elif raw.lower() == "p" and offset > 0:          offset -= PAGE
        elif raw == "": continue
        else: sel = raw; break

    sel_idx = parse_selection(sel, len(chapters))
    to_dl   = [chapters[i] for i in sel_idx]
    if not to_dl:
        print(err("Seleccion vacia.")); return

    print(f"\n  {len(to_dl)} capitulos seleccionados.")
    if input(f"  {C.Y}Confirmar? (Enter=si / n=no) -> {C.E}").strip().lower() == "n":
        return

    base_dir = os.path.join(OUTPUT_DIR, f"{safe_name(series_title)} [{slug}]")
    os.makedirs(base_dir, exist_ok=True)
    print(f"\n{inf(f'Descargando {len(to_dl)} capitulo(s)...')}\n")

    failed: list[tuple[int, dict]] = []
    for i, cap in enumerate(to_dl):
        if not download_chapter(cap["slug"], i+1, len(to_dl), base_dir):
            failed.append((i+1, cap))
        time.sleep(0.3)

    if failed:
        print(f"\n{C.Y}Reintentando {len(failed)} capitulo(s)...{C.E}")
        time.sleep(8)
        still: list[str] = []
        for num, cap in failed:
            if not download_chapter(cap["slug"], num, len(to_dl), base_dir):
                still.append(cap["title"])
        if still:
            print(err("No se descargaron:")); [print(f"    - {t}") for t in still]

    print(f"\n{ok(f'Completado -> {base_dir}/')}")

# ─── FLUJO LISTAR ────────────────────────────────────────────
CAT_PAGE = 20

def flujo_listar() -> None:
    print(inf("Cargando catalogo..."))
    catalog = load_catalog()
    if not catalog:
        print(err("No se pudo cargar el catalogo.")); return

    filtered, filter_text, page = catalog[:], "", 0
    while True:
        header()
        total_pg = max(1, (len(filtered) + CAT_PAGE - 1) // CAT_PAGE)
        page     = max(0, min(page, total_pg - 1))
        start    = page * CAT_PAGE
        end      = min(start + CAT_PAGE, len(filtered))

        info = (f"  {C.M}Catalogo{C.E}  {C.BO}{len(filtered)}{C.E} series"
                f"  pag {C.BO}{page+1}/{total_pg}{C.E}")
        if filter_text: info += f"  filtro: {C.Y}{filter_text}{C.E}"
        print(info + "\n")

        print(f"  {C.M}{'='*60}{C.E}")
        for i, it in enumerate(filtered[start:end]):
            print(f"  {C.BO}{start+i+1:4d}.{C.E}  {it['title'][:48]:50s} {C.B}{it['slug']}{C.E}")
        print(f"  {C.M}{'='*60}{C.E}")
        print(f"\n  {C.B}n{C.E}=sig  {C.B}p{C.E}=ant  {C.B}f{C.E}=filtrar  "
              f"{C.B}q{C.E}=volver  {C.Y}[numero]{C.E}=descargar")
        cmd = input(f"\n  {C.Y}-> {C.E}").strip()

        if   cmd.lower() == "q": return
        elif cmd.lower() == "n" and page < total_pg - 1: page += 1
        elif cmd.lower() == "p" and page > 0:            page -= 1
        elif cmd.lower() == "f":
            ft = input(f"  {C.Y}Filtrar (vacio=todos) -> {C.E}").strip()
            filter_text = ft
            filtered = [it for it in catalog if ft.lower() in it["title"].lower()] if ft else catalog[:]
            page = 0
        elif cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(filtered):
                flujo_descargar(f"/comic/{filtered[idx]['slug']}")
                input(f"\n  {C.B}Enter para continuar...{C.E}")

# ─── MAIN ────────────────────────────────────────────────────
def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    while True:
        header()
        print(f"  {C.BO}1.{C.E} Descargar serie o capitulo")
        print(f"  {C.BO}2.{C.E} Buscar serie por nombre")
        print(f"  {C.BO}3.{C.E} Listar / explorar catalogo")
        print(f"  {C.BO}4.{C.E} Salir\n")
        op = input(f"  {C.Y}-> {C.E}").strip()

        if op == "1":
            flujo_descargar()
            input(f"\n  {C.B}Enter para continuar...{C.E}")
        elif op == "2":
            q = input(f"  {C.Y}Buscar: {C.E}").strip()
            if not q: continue
            print(inf("Buscando..."))
            results = search(q)
            if not results:
                print(err("Sin resultados.")); time.sleep(2); continue
            for i, r in enumerate(results[:20]):
                print(f"  {C.BO}{i+1:3d}.{C.E} {r['title'][:52]:54s} {C.B}{r['slug']}{C.E}")
            sel = input(f"\n  {C.Y}Numero (Enter=volver): {C.E}").strip()
            if sel.isdigit():
                idx = int(sel) - 1
                if 0 <= idx < len(results):
                    flujo_descargar(f"/comic/{results[idx]['slug']}")
            input(f"\n  {C.B}Enter para continuar...{C.E}")
        elif op == "3":
            flujo_listar()
        elif op == "4":
            print(f"\n  {C.G}Hasta luego!{C.E}\n"); break

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{err('Interrumpido.')}")