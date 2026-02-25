"""
DUMANWU DOWNLOADER v5.3
100% requests — Decryptor Inteligente (Fix Chapter 2)
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
from typing import Any, cast
from urllib.parse import quote

import requests
from scrapling import Selector

has_pillow: bool
try:
    from PIL import Image

    has_pillow = True
except ImportError:
    Image = None  # type: ignore
    has_pillow = False

# ─── CONFIGURACIÓN ─────────────────────────────────────────────────────────────
BASE_URL: str = "https://dumanwu.com"
OUTPUT_TYPE: str = "zip"
USER_FORMAT: str = "webp"  # 'original' | 'jpg' | 'png' | 'webp'
MAX_WORKERS_DL: int = 10
DELETE_TEMP: bool = True
MIN_IMAGE_SIZE_KB: int = 5

HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Referer": BASE_URL + "/",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# Semillas XOR en HEX (Extraídas de all2.js)
SEEDS_HEX: list[str] = [
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

_UI_PATHS: tuple[str, ...] = (
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
    CYAN: str = "\033[96m"
    GREEN: str = "\033[92m"
    YELLOW: str = "\033[93m"
    RED: str = "\033[91m"
    BOLD: str = "\033[1m"
    END: str = "\033[0m"

    PURPLE: str = "\033[95m"
    BLUE: str = "\033[94m"

    @staticmethod
    def header() -> None:
        _ = os.system("cls" if os.name == "nt" else "clear")
        print(f"{UI.BLUE}╔══════════════════════════════════════╗")
        print(f"║ {UI.BOLD}DUMANWU DOWNLOADER v5.3{UI.END}{UI.BLUE}            ║")
        print("║ Decryptor Inteligente (Cap 2 Fix)    ║")
        print(f"╚══════════════════════════════════════╝{UI.END}")


# ─── SESIÓN ────────────────────────────────────────────────────────────────────
SESSION: requests.Session = requests.Session()
SESSION.headers.update(HEADERS)


# ─── LÓGICA DE DESCIFRADO ──────────────────────────────────────────────────────
def _xor_decrypt(data: bytes, key_bytes: bytes) -> bytes:
    res = bytearray()
    for i in range(len(data)):
        res.append(data[i] ^ key_bytes[i % len(key_bytes)])
    return bytes(res)


def _any_to_int(token: str, b: str | int) -> int:
    chars: str = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    n: int = 0
    try:
        base: int = int(b)
        for ch in token:
            n = n * base + chars.index(ch)
    except (ValueError, IndexError):
        return -1
    return n


def _decode_packer(p: str, base: int, _count: int, k_str: str) -> str:
    keys: list[str] = k_str.split("|")

    def replace_token(m: Any) -> str:  # pyright: ignore[reportAny, reportExplicitAny]
        tok: str = m.group(0)  # pyright: ignore[reportAny]
        idx: int = _any_to_int(tok, base)
        if 0 <= idx < len(keys) and keys[idx]:
            return keys[idx]
        return tok

    return re.sub(r"\b[0-9A-Za-z]+\b", replace_token, p)


def _extract_packer_args(script: str) -> Any:  # pyright: ignore[reportAny, reportExplicitAny]
    try:
        # Busca el bloque de argumentos: ('p',a,c,'k'...)
        start_idx: int = script.rindex("}(") + 2
        args_part: str = script[start_idx:]
        # Captura strings o números
        matches: list[tuple[str, str]] = re.findall(r"'((?:[^'\\]|\\.)*)'|(\d+)", args_part)
        extracted: list[Any] = []  # pyright: ignore[reportExplicitAny]
        for s_val, n_val in matches:
            if n_val:
                extracted.append(int(n_val))
            else:
                extracted.append(s_val)
        # Los argumentos clave suelen ser los 4 primeros tras el }(
        if len(extracted) >= 4:
            return str(extracted[0]), int(extracted[1]), int(extracted[2]), str(extracted[3])  # pyright: ignore[reportAny]
    except (ValueError, IndexError):
        pass
    return None


def _decrypt_images(html: str) -> list[str]:
    scripts: list[str] = re.findall(
        r"<script[^>]*>(.*?)</script>", html, re.DOTALL | re.IGNORECASE
    )
    for script in scripts:
        if "eval(function(p,a,c,k,e,d)" in script:
            res: Any = _extract_packer_args(script)  # pyright: ignore[reportAny, reportExplicitAny]
            if not res:
                continue

            p, base, count, k = res  # pyright: ignore[reportAny]
            decoded: str = _decode_packer(p, base, count, k)  # pyright: ignore[reportAny]

            # Buscamos la variable con el string Base64 largo (puede ser comilla simple o doble)
            m_var = re.search(
                r"var\s+[a-zA-Z0-9_]+\s*=\s*['\"]([^'\"]{100,})['\"]", decoded
            )
            if not m_var:
                continue

            data_enc: str = m_var.group(1)
            try:
                pad: int = (4 - len(data_enc) % 4) % 4
                raw_data: bytes = base64.b64decode(data_enc + "=" * pad)
                for s_hex in SEEDS_HEX:
                    try:
                        key: bytes = bytes.fromhex(s_hex)
                        xor_res: bytes = _xor_decrypt(raw_data, key)
                        pad_xor: int = (4 - len(xor_res) % 4) % 4
                        final_json: str = base64.b64decode(xor_res + b"=" * pad_xor).decode(
                            "utf-8", errors="ignore"
                        )
                        if "http" in final_json:
                            try:
                                data: Any = json.loads(final_json)  # pyright: ignore[reportAny, reportExplicitAny]
                                if isinstance(data, list):
                                    extracted: list[str] = []
                                    for u in cast(list[Any], data):  # pyright: ignore[reportAny, reportExplicitAny]
                                        u_str: str = str(u)  # pyright: ignore[reportAny]
                                        if "http" in u_str:
                                            extracted.append(u_str)
                                    if extracted:
                                        return extracted
                            except Exception:
                                urls: list[str] = re.findall(
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
    def parse_series_page(self, slug: str) -> tuple[str, str, str, list[dict[str, Any]]]:  # pyright: ignore[reportExplicitAny]
        url: str = f"{BASE_URL}/{slug}/"
        r: requests.Response = SESSION.get(url, timeout=15)
        if r.status_code != 200:
            return slug, "", "", []
        sel: Selector = Selector(r.text, url=url)
        h1 = sel.css("h1").first
        title: str = (h1.text if h1 and h1.text else slug).strip()

        m_autor: Any = re.search(r"作者[：:]\s*([^\s<\n，,]+)", r.text)  # pyright: ignore[reportExplicitAny]
        _autor: str = m_autor.group(1) if m_autor else ""  # pyright: ignore[reportAny]

        _sinopsis: str = ""
        for p in sel.css("p"):
            t: Any = p.get_all_text()  # pyright: ignore[reportExplicitAny, reportUnknownMemberType]
            if t and isinstance(t, str):
                t_clean = t.strip()
                if len(t_clean) > 40 and "作者" not in t_clean and "更新" not in t_clean:
                    _sinopsis = t_clean[:600]
                    break

        print(
            f"  {UI.CYAN}[*] Obteniendo índice de capítulos...{UI.END}",
            end="",
            flush=True,
        )

        slug_esc: str = re.escape(slug)
        cap_re: Any = re.compile(rf"/{slug_esc}/([A-Za-z0-9]+)\.html", re.I)  # pyright: ignore[reportExplicitAny]

        caps: list[dict[str, Any]] = []  # pyright: ignore[reportExplicitAny]
        seen: set[str] = set()

        # 1. Obtener los capítulos iniciales del HTML
        for a in sel.css("a[href]"):
            attribs: dict[str, str] = cast(Any, a.attrib)  # pyright: ignore[reportAny, reportExplicitAny]
            href: str = attribs.get("href", "")
            m: re.Match[str] | None = cap_re.search(href)  # pyright: ignore[reportAny]
            if m:
                cap_slug: str = m.group(1)
                a_text: str = a.text or ""
                if (
                    cap_slug not in seen
                    and "阅读" not in a_text
                    and "start" not in a_text.lower()
                ):
                    seen.add(cap_slug)
                    caps.append(
                        {
                            "slug": cap_slug,
                            "title": a_text.strip() or cap_slug,
                            "url": f"{BASE_URL}{href}",
                            "html": None,
                        }
                    )

        # 2. Consultar la API interna de Dumanwu para obtener el resto de los capítulos al instante
        try:
            r2: requests.Response = SESSION.post(f"{BASE_URL}/morechapter", data={"id": slug}, timeout=10)
            if r2.status_code == 200:
                data: Any = r2.json()  # pyright: ignore[reportAny, reportExplicitAny]
                if isinstance(data, dict) and data.get("code") == "200" and "data" in data:  # pyright: ignore[reportUnknownMemberType]
                    data_list: list[Any] = cast(list[Any], data["data"])  # pyright: ignore[reportExplicitAny]
                    for item in data_list:  # pyright: ignore[reportAny]
                        if isinstance(item, dict):
                            d_item: dict[str, Any] = cast(dict[str, Any], item)  # pyright: ignore[reportExplicitAny]
                            cid: Any = d_item.get("chapterid")  # pyright: ignore[reportExplicitAny]
                            cname: Any = d_item.get("chaptername")  # pyright: ignore[reportExplicitAny]
                            if cid and str(cid) not in seen:  # pyright: ignore[reportAny]
                                cap_slug_v: str = str(cid)  # pyright: ignore[reportAny]
                                cap_title_v: str = str(cname) if cname else cap_slug_v  # pyright: ignore[reportAny]
                                seen.add(cap_slug_v)
                                caps.append(
                                    {
                                        "slug": cap_slug_v,
                                        "title": cap_title_v,
                                        "url": f"{BASE_URL}/{slug}/{cap_slug_v}.html",
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
        return title, _autor, _sinopsis, caps

    def extract_images(self, cap: dict[str, Any]) -> list[str]:  # pyright: ignore[reportExplicitAny]
        html: str | None = str(cap.get("html")) if cap.get("html") else None

        if not html or "eval(function(p,a,c,k,e,d)" not in html:
            html = None
            cap_url = str(cap.get("url", ""))  # pyright: ignore[reportAny]
            for attempt in range(3):
                try:
                    r: requests.Response = SESSION.get(cap_url, timeout=15)
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
        urls: list[str] = []
        seen_urls: set[str] = set()
        # Buscamos data-src, src, o data-original
        for pattern in [
            r'data-src="(https?://[^"]+)"',
            r'src="(https?://[^"]+)"',
            r'data-original="(https?://[^"]+)"',
        ]:
            for m in re.finditer(pattern, html):
                src: str = m.group(1)
                is_junk: bool = (
                    any(p in src.lower() for p in _UI_PATHS) or "scl3phc04j" in src
                )
                if src not in seen_urls and not is_junk:
                    seen_urls.add(src)
                    urls.append(src)
            if len(urls) > 5:  # Si encontramos bastantes con un patrón, paramos
                break

        return urls

    def search(self, query: str) -> list[dict[str, str]]:
        url: str = f"{BASE_URL}/search/?keywords={quote(query)}"
        try:
            r: requests.Response = SESSION.get(url, timeout=10)
            sel: Selector = Selector(r.text, url=url)
        except Exception:
            return []
        results: list[dict[str, str]] = []
        seen: set[str] = set()
        for a in sel.css("a[href]"):
            attribs: dict[str, str] = cast(Any, a.attrib)  # pyright: ignore[reportAny, reportExplicitAny]
            href: str = attribs.get("href", "")
            href_str: str = href.strip("/")
            title: str = (a.text or "").strip()
            if (
                re.fullmatch(r"[A-Za-z0-9]{5,12}", href_str)
                and href_str not in seen
                and title
                and len(title) > 1
                and not title.isdigit()
            ):
                seen.add(href_str)
                results.append({"slug": href_str, "title": title})
        return results


# ─── DESCARGA ──────────────────────────────────────────────────────────────────
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

    ext: str = USER_FORMAT if (has_pillow and USER_FORMAT != "original") else "jpg"
    url_ext: str = os.path.splitext(url.split("?")[0])[-1].lower().lstrip(".")
    if url_ext in ("jpg", "jpeg", "png", "webp", "gif"):
        ext = url_ext

    path: str = f"{folder}/{idx + 1:03d}.{ext}"
    if os.path.exists(path):
        return True

    for attempt in range(2):
        try:
            r: requests.Response = SESSION.get(url, timeout=(5, 10))
            if r.status_code == 200 and len(r.content) > MIN_IMAGE_SIZE_KB * 1024:
                save_img(r.content, path, USER_FORMAT)
                return True
        except Exception:
            time.sleep(attempt + 1)
    return False


def download_series(slug: str, logic: DumanwuLogic, selection: str) -> None:
    print(f"\n{UI.CYAN}[*] Cargando serie '{slug}'...{UI.END}")
    title, _, _, chapters = logic.parse_series_page(slug)
    if not chapters:
        print(f"{UI.RED}[!] 0 capítulos.{UI.END}")
        return

    print(f"\n{UI.GREEN}[+] {UI.BOLD}{title}{UI.END}")

    sel_indices: list[int] = []
    if selection.lower() == "all":
        sel_indices = list(range(len(chapters)))
    else:
        for part in selection.replace(" ", "").split(","):
            if "-" in part:
                try:
                    a_s, b_s = part.split("-")
                    a, b = int(a_s), int(b_s)
                    sel_indices.extend(range(a - 1, b))
                except Exception:
                    pass
            elif part.isdigit():
                sel_indices.append(int(part) - 1)

    to_dl: list[dict[str, Any]] = [chapters[i] for i in sel_indices if 0 <= i < len(chapters)]  # pyright: ignore[reportExplicitAny]
    if not to_dl:
        print(f"{UI.RED}[!] Selección vacía.{UI.END}")
        return

    clean_slug: str = re.sub(r"[\\/:*?\"<>|]", "", str(title)).strip()
    base_folder: str = f"{clean_slug} [{slug}]"
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)

    total_valid: int = 0
    for i, cap in enumerate(to_dl):
        cap_title: str = str(cap.get("title", "Capítulo"))  # pyright: ignore[reportAny]
        print(f"  [{i + 1}/{len(to_dl)}] {cap_title}...", end=" ", flush=True)
        imgs: list[str] = logic.extract_images(cap)
        if not imgs:
            print(f"{UI.RED}0 imgs{UI.END}")
            continue
        print()

        cap_slug: str = str(cap.get("slug", str(i)))  # pyright: ignore[reportAny]
        c_folder: str = os.path.join(base_folder, f"Cap_{i + 1:03d}_{cap_slug}")
        if not os.path.exists(c_folder):
            os.makedirs(c_folder)

        valid: int = 0
        comp: int = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as ex:
            futures = [
                ex.submit(dl_worker, (u, c_folder, x)) for x, u in enumerate(imgs)
            ]
            for fut in as_completed(futures):
                comp += 1
                if fut.result():
                    valid += 1
                perc: int = int(30 * comp // len(imgs))
                bar: str = "█" * perc + "-" * (30 - perc)
                _ = sys.stdout.write(f"\r   [{UI.CYAN}{bar}{UI.END}] {comp}/{len(imgs)}")
                _ = sys.stdout.flush()
        total_valid += valid
        print()

    if total_valid > 0:
        ext_out: str = OUTPUT_TYPE.lower()
        print(f"   [*] Generando {ext_out.upper()}s por capítulo...")
        for j, cap_zip in enumerate(to_dl):
            c_slug_v = cap_zip.get("slug", str(j))  # pyright: ignore[reportAny]
            c_folder_name: str = f"Cap_{j + 1:03d}_{c_slug_v}"
            c_folder_path: str = os.path.join(base_folder, c_folder_name)
            if not os.path.exists(c_folder_path):
                continue

            out_file: str = os.path.join(base_folder, f"{c_folder_name}.{ext_out}")

            if ext_out == "pdf" and has_pillow and Image is not None:
                paths: list[str] = sorted(
                    os.path.join(c_folder_path, f)
                    for f in os.listdir(c_folder_path)
                    if os.path.isfile(os.path.join(c_folder_path, f))
                )
                pages: list[Any] = []  # pyright: ignore[reportExplicitAny]
                for p in paths:
                    try:
                        pages.append(Image.open(p).convert("RGB"))
                    except Exception:
                        pass
                if pages:
                    _ = pages[0].save(out_file, save_all=True, append_images=pages[1:])  # pyright: ignore[reportAny]
            else:
                with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in os.listdir(c_folder_path):
                        full: str = os.path.join(c_folder_path, f)
                        if os.path.isfile(full):
                            _ = zf.write(full, f)

            if DELETE_TEMP:
                shutil.rmtree(c_folder_path)

        print(f"   {UI.GREEN}[OK] Empaquetado completado en: {base_folder}{UI.END}")


def main() -> None:
    logic: DumanwuLogic = DumanwuLogic()
    while True:
        UI.header()
        print(f" {UI.PURPLE}Menú Principal:{UI.END}")
        print(f" ├── {UI.BOLD}1.{UI.END} Descargar por Slug (ej: trbtGKl)")
        print(f" ├── {UI.BOLD}2.{UI.END} Buscar serie")
        print(f" └── {UI.BOLD}3.{UI.END} Salir")
        print(f"\n {UI.PURPLE}Configuración Actual:{UI.END}")
        print(f" ├── Salida: {UI.CYAN}{OUTPUT_TYPE.upper()}{UI.END}")
        print(f" └── Imagen: {UI.CYAN}{USER_FORMAT.upper()}{UI.END}")

        op: str = input(f"\n{UI.YELLOW} Selecciona una opción > {UI.END}").strip()
        if op == "1":
            slug_in: str = input(f"{UI.CYAN} [?] Slug: {UI.END}").strip()
            if not slug_in:
                continue
            sel_in: str = (
                input(f"{UI.CYAN} [?] Caps ('1', '3-5', 'all'): {UI.END}").strip()
                or "all"
            )
            download_series(slug_in, logic, sel_in)
            _ = input(f"\n{UI.CYAN} Enter para continuar...{UI.END}")
        elif op == "2":
            q: str = input(f"{UI.CYAN} [?] Búsqueda: {UI.END}").strip()
            if not q:
                continue
            results: list[dict[str, str]] = logic.search(q)
            if not results:
                print(f"{UI.RED} Sin resultados.{UI.END}")
                time.sleep(2)
                continue
            for i, r in enumerate(results[:20]):
                print(f" {i + 1:2d}. [{r['slug']}] {r['title']}")
            sel_q: str = input(f"\n{UI.YELLOW} Acción > {UI.END}").strip()
            if sel_q.isdigit() and int(sel_q) <= len(results):
                slug_res: str = results[int(sel_q) - 1]["slug"]
                caps_res: str = (
                    input(f"{UI.CYAN} [?] Caps para {slug_res}: {UI.END}").strip() or "all"
                )
                download_series(slug_res, logic, caps_res)
                _ = input("\n Enter...")
        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
