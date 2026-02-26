import json
import os
import re
import shutil
import struct
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from typing import cast

import requests

# --- SOPORTE PARA PILLOW ---
has_pillow: bool
try:
    from PIL import Image

    has_pillow = True
except ImportError:
    Image = None  # type: ignore
    has_pillow = False


# --- ESTÉTICA DE CONSOLA ---
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
        print(f"║ {UI.BOLD}HITOMI DOWNLOADER v1.2.5{UI.END}{UI.BLUE}             ║")
        print(f"╚══════════════════════════════════════╝{UI.END}")


# ==========================================
#      CONFIGURACIÓN (Edita aquí)
# ==========================================
OUTPUT_TYPE: str = "zip"  # 'zip', 'cbz', 'pdf'
USER_FORMAT: str = "webp"  # 'original', 'jpg', 'png', 'webp'
DELETE_TEMP: bool = True
MAX_WORKERS_DL: int = 20
MAX_RESULTS_PAGE: int = 20
# ==========================================

METADATA_CACHE: dict[str, object] = {}
headers: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://hitomi.la/",
}


class HitomiLogic:
    m_default: int
    m_map: dict[int, int]
    b_val: str

    def __init__(self) -> None:
        self.m_default = 0
        self.m_map = {}
        self.b_val = ""
        self.load_gg()

    def load_gg(self) -> None:
        try:
            body: str = requests.get(
                "https://ltn.gold-usergeneratedcontent.net/gg.js", timeout=10
            ).text

            m_o: re.Match[str] | None = re.search(r"var o = (\d)", body)
            self.m_default = int(m_o.group(1)) if m_o else 0

            o_match: re.Match[str] | None = re.search(r"o = (\d); break;", body)
            o_val: int = int(o_match.group(1)) if o_match else self.m_default
            for c in re.findall(r"case (\d+):", body):  # pyright: ignore[reportAny]
                self.m_map[int(c)] = o_val  # pyright: ignore[reportAny]

            m_b: re.Match[str] | None = re.search(r"b: '(.+)'", body)
            self.b_val = m_b.group(1) if m_b else ""

            if self.b_val and not self.b_val.endswith("/"):
                self.b_val += "/"
        except Exception:
            print(f"{UI.RED}[!] Error cargando gg.js. URLs podrían fallar.{UI.END}")

    def get_url(self, h: str, ext: str) -> str:
        # Lógica de common.rs: reordenar hash para el subdominio
        g: int = int(h[-1] + h[-3:-1], 16) if h else 0
        m: int = self.m_map.get(g, self.m_default)
        sub: str = f"{'a' if ext == 'avif' else 'w'}{1 + m}"
        return f"https://{sub}.gold-usergeneratedcontent.net/{self.b_val}{g}/{h}.{ext}"


# --- FUNCIONES DE APOYO ---


def parse_sel(selection_str: str, max_len: int) -> list[int]:
    """Parsea entradas como '1, 3-5, 10' y devuelve índices reales"""
    selection_str = selection_str.lower().replace(" ", "")
    if selection_str == "all":
        return list(range(max_len))
    indices: set[int] = set()
    try:
        for part in selection_str.split(","):
            if "-" in part:
                start_s, end_s = part.split("-")
                start, end = int(start_s), int(end_s)
                for i in range(start - 1, end):
                    if 0 <= i < max_len:
                        indices.add(i)
            elif part.isdigit():
                idx: int = int(part) - 1
                if 0 <= idx < max_len:
                    indices.add(idx)
    except Exception:
        pass
    return sorted(list(indices))


def fetch_nozomi_ids(term: str) -> set[int]:
    """Busca IDs en los archivos .nozomi de Hitomi"""
    term = term.replace("_", " ").strip()
    if ":" in term:
        ns, v = term.split(":", 1)
        if ns in ["female", "male"]:
            url: str = (
                f"https://ltn.gold-usergeneratedcontent.net/n/tag/{term}-all.nozomi"
            )
        elif ns == "language":
            url = f"https://ltn.gold-usergeneratedcontent.net/n/index-{v}.nozomi"
        else:
            url = f"https://ltn.gold-usergeneratedcontent.net/n/{ns}/{v}-all.nozomi"
    else:
        url = f"https://ltn.gold-usergeneratedcontent.net/n/tag/{term}-all.nozomi"

    try:
        r: requests.Response = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return set()
        return {
            struct.unpack(">i", r.content[i * 4 : (i + 1) * 4])[0]
            for i in range(len(r.content) // 4)
        }
    except Exception:
        return set()


def search_query(query: str) -> list[int]:
    """Lógica de búsqueda por intersección de search.rs"""
    print(f"{UI.CYAN}🔎 Analizando términos...{UI.END}")
    parts: list[str] = query.split()
    if not parts:
        return []

    # Empezamos con el primer término
    results: set[int] = fetch_nozomi_ids(parts[0])
    # Intersección con los siguientes (AND)
    for p in parts[1:]:
        if not results:
            break
        results.intersection_update(fetch_nozomi_ids(p))

    return sorted(list(results), reverse=True)


# --- DESCARGA Y EMPAQUETADO ---


def save_img(raw: bytes, path: str, fmt: str) -> None:
    if not has_pillow or fmt == "original" or Image is None:
        with open(path, "wb") as f:
            _ = f.write(raw)
        return
    try:
        img = cast(object, Image.open(BytesIO(raw)))
        img_mode = cast(str, getattr(img, "mode"))
        if fmt.lower() in ["jpg", "jpeg"] and img_mode in ("RGBA", "LA"):
            img_size = cast(tuple[int, int], getattr(img, "size"))
            bg = cast(object, Image.new(img_mode[:-1], img_size, (255, 255, 255)))
            img_split = cast(list[object], getattr(img, "split")())
            _ = getattr(bg, "paste")(img, img_split[-1])  # pyright: ignore[reportAny]
            img = getattr(bg, "convert")("RGB")  # pyright: ignore[reportAny]
        _ = getattr(img, "save")(path, quality=92)  # pyright: ignore[reportAny]
    except Exception:
        with open(path, "wb") as f:
            _ = f.write(raw)


def dl_worker(args: tuple[HitomiLogic, dict[str, object], str, int]) -> bool:
    logic, img, folder, idx = args
    h: str = str(img.get("hash", ""))
    ext: str = "avif" if img.get("hasavif") else "webp"
    url: str = logic.get_url(h, ext)
    final_ext: str = USER_FORMAT if (has_pillow and USER_FORMAT != "original") else ext
    path: str = f"{folder}/{idx + 1:03d}.{final_ext}"

    if os.path.exists(path):
        return True
    for _ in range(3):
        try:
            r: requests.Response = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                save_img(r.content, path, USER_FORMAT)
                return True
        except Exception:
            time.sleep(1)
    return False


def download_gallery(gid: int, logic: HitomiLogic) -> None:
    try:
        # Usar caché si ya cargamos la info en el listado
        data: object = METADATA_CACHE.get(str(gid))
        if not data:
            r: requests.Response = requests.get(
                f"https://ltn.gold-usergeneratedcontent.net/galleries/{gid}.js",
                headers=headers,
            )
            data = json.loads(r.text.split("var galleryinfo = ")[1])  # pyright: ignore[reportAny]

        if not data:
            return

        d_dict = cast(dict[str, object], data)

        raw_title: str = str(d_dict.get("title", str(gid)))
        clean_title: str = re.sub(r'[\\/:*?"<>|]', "", raw_title).strip()
        folder: str = f"{clean_title} [{gid}]"
        if not os.path.exists(folder):
            os.makedirs(folder)

        # info.json simplificado (Babylon style)
        artists_list: list[str] = []
        raw_artists: object = d_dict.get("artists")
        if isinstance(raw_artists, list):
            for a_item in cast(list[object], raw_artists):
                if isinstance(a_item, dict):
                    a_dict: dict[str, object] = cast(dict[str, object], a_item)
                    a_val: object = a_dict.get("artist", "")
                    artists_list.append(str(a_val))

        tags_list: list[str] = []
        raw_tags: object = d_dict.get("tags")
        if isinstance(raw_tags, list):
            for t_item in cast(list[object], raw_tags):
                if isinstance(t_item, dict):
                    t_dict: dict[str, object] = cast(dict[str, object], t_item)
                    t_val: object = t_dict.get("tag", "")
                    tags_list.append(str(t_val))

        info: dict[str, object] = {
            "id": gid,
            "title": raw_title,
            "artists": artists_list,
            "tags": tags_list,
            "url": f"https://hitomi.la/galleries/{gid}.html",
        }
        with open(f"{folder}/info.json", "w", encoding="utf-8") as f:
            json.dump(info, f, indent=4, ensure_ascii=False)

        print(f"\n{UI.GREEN}⬇  Iniciando: {UI.BOLD}{raw_title[:60]}...{UI.END}")
        raw_files = d_dict.get("files", [])
        files: list[dict[str, object]] = []
        if isinstance(raw_files, list):
            for f_item in cast(list[object], raw_files):
                if isinstance(f_item, dict):
                    files.append(cast(dict[str, object], f_item))

        comp: int = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
            futures = [
                exe.submit(dl_worker, (logic, f_dic, folder, i))
                for i, f_dic in enumerate(files)
            ]
            for _ in as_completed(futures):
                comp += 1
                if len(files) > 0:
                    perc: int = int(30 * comp // len(files))
                    bar: str = "█" * perc + "-" * (30 - perc)
                    _ = sys.stdout.write(
                        f"\r   [{UI.CYAN}{bar}{UI.END}] {comp}/{len(files)}"
                    )
                    _ = sys.stdout.flush()

        # Empaquetado
        ext_out: str = OUTPUT_TYPE.lower()
        out_file: str = f"{folder}.{ext_out}"
        print(f"\n   📦 Generando {ext_out.upper()}...")

        if ext_out == "pdf" and has_pillow and Image is not None:
            imgs: list[str] = sorted(
                [
                    os.path.join(folder, f_n)
                    for f_n in os.listdir(folder)
                    if not f_n.endswith(".json")
                ]
            )
            if imgs:
                img_first = cast(object, Image.open(imgs[0]))
                pages: list[object] = [getattr(img_first, "convert")("RGB")]
                for i_n in imgs[1:]:
                    img_next = cast(object, Image.open(i_n))
                    converted = cast(object, getattr(img_next, "convert")("RGB"))
                    pages.append(converted)
                save_func = cast(object, getattr(pages[0], "save"))
                if callable(save_func):
                    _ = save_func(out_file, save_all=True, append_images=pages[1:])
        else:
            with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
                for f_n in os.listdir(folder):
                    _ = zf.write(os.path.join(folder, f_n), f_n)

        if DELETE_TEMP:
            shutil.rmtree(folder)
        print(f"   {UI.GREEN}✔  Completado: {out_file}{UI.END}")
    except Exception as e:
        print(f"\n{UI.RED}[!] Error en {gid}: {e}{UI.END}")


# --- MENÚS ESCALONADOS ---


def main() -> None:
    logic: HitomiLogic = HitomiLogic()
    while True:
        UI.header()
        print(f" {UI.PURPLE}Menú Principal:{UI.END}")
        print(f" ├── {UI.BOLD}1.{UI.END} Descargar por ID única")
        print(f" ├── {UI.BOLD}2.{UI.END} Buscar Series (Múltiples Tags/Artistas)")
        print(f" └── {UI.BOLD}3.{UI.END} Salir")
        print(f"\n {UI.PURPLE}Configuración Actual:{UI.END}")
        print(f" ├── Salida: {UI.CYAN}{OUTPUT_TYPE.upper()}{UI.END}")
        print(f" └── Imagen: {UI.CYAN}{USER_FORMAT.upper()}{UI.END}")

        op: str = input(f"\n{UI.YELLOW} Selecciona una opción ➜ {UI.END}")

        if op == "1":
            gid_s: str = input(f"{UI.CYAN} ✍  ID de la galería: {UI.END}")
            if gid_s.isdigit():
                download_gallery(int(gid_s), logic)
                _ = input(f"\n{UI.CYAN}Presiona Enter para volver al menú...{UI.END}")

        elif op == "2":
            q: str = input(
                f"{UI.CYAN} ✍  Búsqueda (ej: language:spanish female:mind_control): {UI.END}"
            )
            if not q.strip():
                continue

            ids: list[int] = search_query(q)
            if not ids:
                print(f"{UI.RED} No se encontró ninguna coincidencia.{UI.END}")
                time.sleep(2)
                continue

            page: int = 0
            while True:
                UI.header()
                start: int = page * MAX_RESULTS_PAGE
                end: int = min(start + MAX_RESULTS_PAGE, len(ids))
                print(f" {UI.PURPLE}Búsqueda: '{q}'{UI.END}")
                print(
                    f" {UI.PURPLE}Mostrando {start + 1}-{end} de {len(ids)} resultados{UI.END}"
                )
                print(f" {'━' * 50}")

                # Carga masiva de metadata (batch)
                batch: list[int] = ids[start:end]
                to_load: list[int] = [i for i in batch if str(i) not in METADATA_CACHE]
                if to_load:
                    _ = sys.stdout.write(f" {UI.YELLOW}⚡ Cargando títulos...{UI.END}")
                    _ = sys.stdout.flush()

                    def load(gi: int) -> None:
                        try:
                            r_l: requests.Response = requests.get(
                                f"https://ltn.gold-usergeneratedcontent.net/galleries/{gi}.js",
                                timeout=5,
                            )
                            METADATA_CACHE[str(gi)] = json.loads(
                                r_l.text.split("var galleryinfo = ")[1]
                            )
                        except Exception:
                            pass

                    with ThreadPoolExecutor(max_workers=20) as exe:
                        _ = list(exe.map(load, to_load))
                    _ = sys.stdout.write(
                        "\r" + " " * 30 + "\r"
                    )  # Limpiar texto cargando

                for i, gid_b in enumerate(batch):
                    m_obj: object = METADATA_CACHE.get(str(gid_b), {})
                    if isinstance(m_obj, dict):
                        m_dict = cast(dict[str, object], m_obj)
                        title: str = str(m_dict.get("title", "Sin título"))[:55]
                        print(
                            f" {UI.BOLD}{start + i + 1:3d}.{UI.END} [{UI.GREEN}{gid_b}{UI.END}] {title}"
                        )

                print(f" {'━' * 50}")
                print(
                    f" {UI.CYAN}Controles:{UI.END} {UI.BOLD}n{UI.END} (sig) | {UI.BOLD}p{UI.END} (ant) | {UI.BOLD}q{UI.END} (volver)"
                )
                print(
                    f" {UI.CYAN}Selección:{UI.END} Escribe números (ej: '1', '1-5', '1,10,21')"
                )

                sel: str = input(f"\n{UI.YELLOW} Acción ➜ {UI.END}").lower().strip()

                if sel == "n" and end < len(ids):
                    page += 1
                elif sel == "p" and page > 0:
                    page -= 1
                elif sel == "q":
                    break
                elif sel:
                    # Usar la función parse_sel reparada
                    idxs: list[int] = parse_sel(sel, len(ids))
                    if idxs:
                        print("\n  Galerías a descargar:")
                        for x in idxs:
                            m_obj_sel: object = METADATA_CACHE.get(str(ids[x]), {})
                            title_sel: str = (
                                str(
                                    cast(dict[str, object], m_obj_sel).get("title", "")
                                )[:50]
                                if isinstance(m_obj_sel, dict)
                                else ""
                            )
                            print(f"    - [{ids[x]}] {title_sel}")

                        confirm = (
                            input(
                                f"\n{UI.YELLOW} ¿Confirmar? (Enter=sí / n=cancelar) ➜ {UI.END}"
                            )
                            .strip()
                            .lower()
                        )
                        if confirm != "n":
                            for idx_sel in idxs:
                                download_gallery(ids[idx_sel], logic)
                            _ = input(
                                f"\n{UI.GREEN}Cola terminada. Enter para continuar...{UI.END}"
                            )
                    else:
                        print(f"{UI.RED} Entrada no válida.{UI.END}")
                        time.sleep(1)
                else:
                    break
        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
