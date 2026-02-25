"""
DUMANWU DOWNLOADER v5.3
100% requests — Decryptor Inteligente (Fix Chapter 2)

Sistema de extracción:
  1. Extrae y descifra el script 'packer' con soporte para argumentos variables.
  2. Descifra los datos usando 10 semillas XOR maestras (HEX).
  3. Prioriza el contenido del packer sobre el HTML para evitar basura.
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
from urllib.parse import quote

import requests
from scrapling import Selector

HAS_PILLOW: bool
try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

# ─── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
BASE_URL = "https://dumanwu.com"
OUTPUT_TYPE = "zip"
USER_FORMAT = "webp"  # 'original' | 'jpg' | 'png' | 'webp'
MAX_WORKERS_DL = 10
DELETE_TEMP = True
MIN_IMAGE_SIZE_KB = 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Referer": BASE_URL + "/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Semillas XOR en HEX (Extraídas de all2.js)
SEEDS_HEX = [
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


# ─── UI ───────────────────────────────────────────────────────────────────────
class UI:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    END = "\033[0m"

    PURPLE = "\033[95m"
    BLUE = "\033[94m"

    @staticmethod
    def header():
        os.system("cls" if os.name == "nt" else "clear")
        print(f"{UI.BLUE}╔══════════════════════════════════════╗")
        print(f"║ {UI.BOLD}DUMANWU DOWNLOADER v5.3{UI.END}{UI.BLUE}            ║")
        print("║ Decryptor Inteligente (Cap 2 Fix)    ║")
        print(f"╚══════════════════════════════════════╝{UI.END}")


# ─── SESIÓN ────────────────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ─── LÓGICA DE DESCIFRADO ──────────────────────────────────────────────────────
def _xor_decrypt(data, key_bytes):
    res = bytearray()
    for i in range(len(data)):
        res.append(data[i] ^ key_bytes[i % len(key_bytes)])
    return bytes(res)


def _any_to_int(token, b):
    chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    n = 0
    try:
        base = int(b)
        for ch in token:
            n = n * base + chars.index(ch)
    except (ValueError, IndexError):
        return -1
    return n


def _decode_packer(p, base, count, k_str):
    keys = k_str.split("|")

    def replace_token(m):
        tok = m.group(0)
        idx = _any_to_int(tok, base)
        if 0 <= idx < len(keys) and keys[idx]:
            return keys[idx]
        return tok

    return re.sub(r"\b[0-9A-Za-z]+\b", replace_token, p)


def _extract_packer_args(script):
    try:
        # Busca el bloque de argumentos: ('p',a,c,'k'...)
        start_idx = script.rindex("}(") + 2
        args_part = script[start_idx:]
        # Captura strings o números
        matches = re.findall(r"'((?:[^'\\]|\\.)*)'|(\d+)", args_part)
        extracted = []
        for s_val, n_val in matches:
            if n_val:
                extracted.append(int(n_val))
            else:
                extracted.append(s_val)
        # Los argumentos clave suelen ser los 4 primeros tras el }(
        if len(extracted) >= 4:
            return extracted[0], extracted[1], extracted[2], extracted[3]
    except (ValueError, IndexError):
        pass
    return None


def _decrypt_images(html):
    scripts = re.findall(
        r"<script[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE
    )
    for script in scripts:
        if "eval(function(p,a,c,k,e,d)" in script:
            res = _extract_packer_args(script)
            if not res:
                continue

            p, base, count, k = res
            decoded = _decode_packer(p, base, count, k)

            # Buscamos la variable con el string Base64 largo (puede ser comilla simple o doble)
            m_var = re.search(
                r"var\s+[a-zA-Z0-9_]+\s*=\s*['\"]([^'\"]{100,})['\"]", decoded
            )
            if not m_var:
                continue

            data_enc = m_var.group(1)
            try:
                pad = (4 - len(data_enc) % 4) % 4
                raw_data = base64.b64decode(data_enc + "=" * pad)
                for s_hex in SEEDS_HEX:
                    try:
                        key = bytes.fromhex(s_hex)
                        xor_res = _xor_decrypt(raw_data, key)
                        pad_xor = (4 - len(xor_res) % 4) % 4
                        final_json = base64.b64decode(xor_res + b"=" * pad_xor).decode(
                            "utf-8", errors="ignore"
                        )
                        if "http" in final_json:
                            try:
                                data = json.loads(final_json)
                                if isinstance(data, list):
                                    extracted = [
                                        str(u) for u in data if "http" in str(u)
                                    ]
                                    if extracted:
                                        return extracted
                            except Exception:
                                urls = re.findall(
                                    r"https?://[^\s\"',\[\]]+", final_json
                                )
                                if urls:
                                    return urls
                    except Exception:
                        continue
            except Exception:
                continue
    return []


# ─── LÓGICA PRINCIPAL ─────────────────────────────────────────────────────────
class DumanwuLogic:
    def parse_series_page(self, slug: str):
        url = f"{BASE_URL}/{slug}/"
        r = SESSION.get(url, timeout=15)
        if r.status_code != 200:
            return slug, "", "", []
        sel = Selector(r.text, url=url)
        h1 = sel.css("h1").first
        title = (h1.text if h1 and h1.text else slug).strip()

        m_autor = re.search(r"作者[：:]\s*([^\s<\n，,]+)", r.text)
        autor = m_autor.group(1) if m_autor else ""

        sinopsis = ""
        for p in sel.css("p"):
            t = p.get_all_text().strip()
            if len(t) > 40 and "作者" not in t and "更新" not in t:
                sinopsis = t[:600]
                break

        print(
            f"  {UI.CYAN}[*] Obteniendo índice de capítulos...{UI.END}",
            end="",
            flush=True,
        )

        slug_esc = re.escape(slug)
        cap_re = re.compile(rf"/{slug_esc}/([A-Za-z0-9]+)\.html", re.I)

        caps = []
        seen = set()

        # 1. Obtener los capítulos iniciales del HTML
        for a in sel.css("a[href]"):
            href = a.attrib.get("href", "")
            m = cap_re.search(href)
            if m:
                cap_slug = m.group(1)
                if (
                    cap_slug not in seen
                    and "阅读" not in a.text
                    and "start" not in a.text.lower()
                ):
                    seen.add(cap_slug)
                    caps.append(
                        {
                            "slug": cap_slug,
                            "title": a.text.strip() or cap_slug,
                            "url": f"{BASE_URL}{href}",
                            "html": None,
                        }
                    )

        # 2. Consultar la API interna de Dumanwu para obtener el resto de los capítulos al instante
        try:
            r2 = SESSION.post(f"{BASE_URL}/morechapter", data={"id": slug}, timeout=10)
            if r2.status_code == 200:
                data = r2.json()
                if data.get("code") == "200" and "data" in data:
                    for item in data["data"]:
                        cap_slug = item.get("chapterid")
                        if cap_slug and cap_slug not in seen:
                            seen.add(cap_slug)
                            caps.append(
                                {
                                    "slug": cap_slug,
                                    "title": item.get("chaptername", cap_slug),
                                    "url": f"{BASE_URL}/{slug}/{cap_slug}.html",
                                    "html": None,
                                }
                            )
        except Exception:
            pass

        # Invertir para que queden en orden cronológico real (del cap 1 al último)
        caps.reverse()

        print(
            f"\r  {UI.GREEN}[OK] {len(caps)} capítulos descubiertos al instante.{' ' * 10}{UI.END}"
        )
        return title, autor, sinopsis, caps

    def extract_images(self, cap: dict) -> list:
        html = cap.get("html")

        if not html or "eval(function(p,a,c,k,e,d)" not in html:
            html = None
            for attempt in range(3):
                try:
                    r = SESSION.get(cap["url"], timeout=15)
                    if r.status_code == 200 and "eval(function(p,a,c,k,e,d)" in r.text:
                        html = r.text
                        break
                    else:
                        time.sleep(attempt + 1)
                except Exception:
                    time.sleep(attempt + 1)

        if not html:
            return []

        # 1. Intentar DESCIFRAR el packer (Contenido Real del Capítulo)
        packer_urls: list[str] = _decrypt_images(html)
        if packer_urls:
            # Filtrar portadas genéricas de otras series
            real_content = [
                u
                for u in packer_urls
                if "scl3phc04j" not in u and "/static/" not in u.lower()
            ]
            if real_content:
                return real_content

        # 2. Fallback al HTML (regex directo de data-src o src)
        urls, seen = [], set()
        # Buscamos data-src, src, o data-original
        for pattern in [
            r'data-src="(https?://[^"]+)"',
            r'src="(https?://[^"]+)"',
            r'data-original="(https?://[^"]+)"',
        ]:
            for m in re.finditer(pattern, html):
                src = m.group(1)
                is_junk = (
                    any(p in src.lower() for p in _UI_PATHS) or "scl3phc04j" in src
                )
                if src not in seen and not is_junk:
                    seen.add(src)
                    urls.append(src)
            if len(urls) > 5:  # Si encontramos bastantes con un patrón, paramos
                break

        return urls

    def search(self, query: str) -> list:
        url = f"{BASE_URL}/search/?keywords={quote(query)}"
        try:
            r = SESSION.get(url, timeout=10)
            sel = Selector(r.text, url=url)
        except Exception:
            return []
        results, seen = [], set()
        for a in sel.css("a[href]"):
            href = a.attrib.get("href", "").strip("/")
            title = a.text.strip()
            if (
                re.fullmatch(r"[A-Za-z0-9]{5,12}", href)
                and href not in seen
                and title
                and len(title) > 1
                and not title.isdigit()
            ):
                seen.add(href)
                results.append({"slug": href, "title": title})
        return results


# ─── DESCARGA ──────────────────────────────────────────────────────────────────
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

    ext = USER_FORMAT if (HAS_PILLOW and USER_FORMAT != "original") else "jpg"
    url_ext = os.path.splitext(url.split("?")[0])[-1].lower().lstrip(".")
    if url_ext in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = url_ext

    path = f"{folder}/{idx + 1:03d}.{ext}"
    if os.path.exists(path):
        return True

    for attempt in range(2):
        try:
            r = SESSION.get(url, timeout=(5, 10))
            if r.status_code == 200 and len(r.content) > MIN_IMAGE_SIZE_KB * 1024:
                save_img(r.content, path, USER_FORMAT)
                return True
        except Exception:
            time.sleep(attempt + 1)
    return False


def download_series(slug: str, logic: DumanwuLogic, selection: str):
    print(f"\n{UI.CYAN}[*] Cargando serie '{slug}'...{UI.END}")
    title, autor, sinopsis, chapters = logic.parse_series_page(slug)
    if not chapters:
        print(f"{UI.RED}[!] 0 capítulos.{UI.END}")
        return

    print(f"\n{UI.GREEN}[+] {UI.BOLD}{title}{UI.END}")

    sel_indices = []
    if selection.lower() == "all":
        sel_indices = list(range(len(chapters)))
    else:
        for part in selection.replace(" ", "").split(","):
            if "-" in part:
                try:
                    a, b = map(int, part.split("-"))
                    sel_indices.extend(range(a - 1, b))
                except Exception:
                    pass
            elif part.isdigit():
                sel_indices.append(int(part) - 1)

    to_dl = [chapters[i] for i in sel_indices if 0 <= i < len(chapters)]
    if not to_dl:
        print(f"{UI.RED}[!] Selección vacía.{UI.END}")
        return

    clean_slug = re.sub(r"[\\/:*?\"<>|]", "", title).strip()
    base_folder = f"{clean_slug} [{slug}]"
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)

    total_valid = 0
    for i, cap in enumerate(to_dl):
        cap_title = cap.get("title", "Capítulo")
        print(f"  [{i + 1}/{len(to_dl)}] {cap_title}...", end=" ", flush=True)  # type: ignore
        imgs = logic.extract_images(cap)
        if not imgs:
            print(f"{UI.RED}0 imgs{UI.END}")
            continue
        print()

        cap_slug = cap.get("slug", str(i))
        c_folder = os.path.join(base_folder, f"Cap_{i + 1:03d}_{cap_slug}")  # type: ignore
        if not os.path.exists(c_folder):
            os.makedirs(c_folder)

        valid = 0
        comp = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as ex:
            futures = [
                ex.submit(dl_worker, (u, c_folder, x)) for x, u in enumerate(imgs)
            ]
            for fut in as_completed(futures):
                comp += 1
                if fut.result():
                    valid += 1
                perc = int(30 * comp // len(imgs))
                bar = "█" * perc + "-" * (30 - perc)
                sys.stdout.write(f"\r   [{UI.CYAN}{bar}{UI.END}] {comp}/{len(imgs)}")
                sys.stdout.flush()
        total_valid += valid
        print()

    if total_valid > 0:
        ext_out = OUTPUT_TYPE.lower()
        print(f"   [*] Generando {ext_out.upper()}s por capítulo...")
        for j, cap_zip in enumerate(to_dl):
            c_folder_name = f"Cap_{j + 1:03d}_{cap_zip['slug']}"
            c_folder_path = os.path.join(base_folder, c_folder_name)
            if not os.path.exists(c_folder_path):
                continue

            out_file = os.path.join(base_folder, f"{c_folder_name}.{ext_out}")

            if ext_out == "pdf" and HAS_PILLOW:
                paths = sorted(
                    os.path.join(c_folder_path, f)
                    for f in os.listdir(c_folder_path)
                    if os.path.isfile(os.path.join(c_folder_path, f))
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
                    for f in os.listdir(c_folder_path):
                        full = os.path.join(c_folder_path, f)
                        if os.path.isfile(full):
                            zf.write(full, f)

            if DELETE_TEMP:
                shutil.rmtree(c_folder_path)

        print(f"   {UI.GREEN}[OK] Empaquetado completado en: {base_folder}{UI.END}")
    # ─── MAIN ─────────────────────────────────────────────────────────────────────


def main():
    logic = DumanwuLogic()
    while True:
        UI.header()
        print(f" {UI.PURPLE}Menú Principal:{UI.END}")
        print(f" ├── {UI.BOLD}1.{UI.END} Descargar por Slug (ej: trbtGKl)")
        print(f" ├── {UI.BOLD}2.{UI.END} Buscar serie")
        print(f" └── {UI.BOLD}3.{UI.END} Salir")
        print(f"\n {UI.PURPLE}Configuración Actual:{UI.END}")
        print(f" ├── Salida: {UI.CYAN}{OUTPUT_TYPE.upper()}{UI.END}")
        print(f" └── Imagen: {UI.CYAN}{USER_FORMAT.upper()}{UI.END}")

        op = input(f"\n{UI.YELLOW} Selecciona una opción > {UI.END}").strip()
        if op == "1":
            slug = input(f"{UI.CYAN} [?] Slug: {UI.END}").strip()
            if not slug:
                continue
            sel = (
                input(f"{UI.CYAN} [?] Caps ('1', '3-5', 'all'): {UI.END}").strip()
                or "all"
            )
            download_series(slug, logic, sel)
            input(f"\n{UI.CYAN} Enter para continuar...{UI.END}")
        elif op == "2":
            q = input(f"{UI.CYAN} [?] Búsqueda: {UI.END}").strip()
            if not q:
                continue
            results = logic.search(q)
            if not results:
                print(f"{UI.RED} Sin resultados.{UI.END}")
                time.sleep(2)
                continue
            for i, r in enumerate(results[:20]):
                print(f" {i + 1:2d}. [{r['slug']}] {r['title']}")
            sel = input(f"\n{UI.YELLOW} Acción > {UI.END}").strip()
            if sel.isdigit() and int(sel) <= len(results):
                slug = results[int(sel) - 1]["slug"]
                caps = (
                    input(f"{UI.CYAN} [?] Caps para {slug}: {UI.END}").strip() or "all"
                )
                download_series(slug, logic, caps)
                input("\n Enter...")
        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
