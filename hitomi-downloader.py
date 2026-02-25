import json
import os
import re
import struct
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import requests

# --- SOPORTE PARA PILLOW ---
HAS_PILLOW: bool
try:
    from PIL import Image

    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


# --- ESTÉTICA DE CONSOLA ---
class UI:
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    END = "\033[0m"

    @staticmethod
    def header():
        os.system("cls" if os.name == "nt" else "clear")
        print(f"{UI.BLUE}╔══════════════════════════════╗")
        print(f"║   {UI.BOLD}HITOMI DOWNLOADER v1.2.5{UI.END}{UI.BLUE}   ║")
        print(f"╚══════════════════════════════╝{UI.END}")


# ==========================================
#      CONFIGURACIÓN (Edita aquí)
# ==========================================
OUTPUT_TYPE = "zip"  # 'zip', 'cbz', 'pdf'
USER_FORMAT = "webp"  # 'original', 'jpg', 'png', 'webp'
DELETE_TEMP = True
MAX_WORKERS_DL = 20
MAX_RESULTS_PAGE = 20
# ==========================================

METADATA_CACHE = {}
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://hitomi.la/",
}


class HitomiLogic:
    def __init__(self):
        self.m_default = 0
        self.m_map = {}
        self.b_val = ""
        self.load_gg()

    def load_gg(self):
        try:
            body = requests.get(
                "https://ltn.gold-usergeneratedcontent.net/gg.js", timeout=10
            ).text

            m_o = re.search(r"var o = (\d)", body)
            self.m_default = int(m_o.group(1)) if m_o else 0

            o_match = re.search(r"o = (\d); break;", body)
            o_val = int(o_match.group(1)) if o_match else self.m_default
            for c in re.findall(r"case (\d+):", body):
                self.m_map[int(c)] = o_val

            m_b = re.search(r"b: '(.+)'", body)
            self.b_val = m_b.group(1) if m_b else ""

            if self.b_val and not self.b_val.endswith("/"):
                self.b_val += "/"
        except Exception:
            print(f"{UI.RED}[!] Error cargando gg.js. URLs podrían fallar.{UI.END}")

    def get_url(self, h: str, ext: str):
        # Lógica de common.rs: reordenar hash para el subdominio
        g = int(h[-1] + h[-3:-1], 16) if h else 0
        m = self.m_map.get(g, self.m_default)
        sub = f"{'a' if ext == 'avif' else 'w'}{1 + m}"
        return f"https://{sub}.gold-usergeneratedcontent.net/{self.b_val}{g}/{h}.{ext}"  # type: ignore


# --- FUNCIONES DE APOYO ---


def parse_sel(selection_str, max_len):
    """Parsea entradas como '1, 3-5, 10' y devuelve índices reales"""
    selection_str = selection_str.lower().replace(" ", "")
    if selection_str == "all":
        return list(range(max_len))
    indices = set()
    try:
        for part in selection_str.split(","):
            if "-" in part:
                start, end = map(int, part.split("-"))
                for i in range(start - 1, end):
                    if 0 <= i < max_len:
                        indices.add(i)
            elif part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < max_len:
                    indices.add(idx)
    except Exception:
        pass
    return sorted(list(indices))


def fetch_nozomi_ids(term):
    """Busca IDs en los archivos .nozomi de Hitomi"""
    term = term.replace("_", " ").strip()
    if ":" in term:
        ns, v = term.split(":", 1)
        if ns in ["female", "male"]:
            url = f"https://ltn.gold-usergeneratedcontent.net/n/tag/{term}-all.nozomi"
        elif ns == "language":
            url = f"https://ltn.gold-usergeneratedcontent.net/n/index-{v}.nozomi"
        else:
            url = f"https://ltn.gold-usergeneratedcontent.net/n/{ns}/{v}-all.nozomi"
    else:
        url = f"https://ltn.gold-usergeneratedcontent.net/n/tag/{term}-all.nozomi"

    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return set()
        return {
            struct.unpack(">i", r.content[i * 4 : (i + 1) * 4])[0]
            for i in range(len(r.content) // 4)
        }
    except Exception:
        return set()


def search_query(query):
    """Lógica de búsqueda por intersección de search.rs"""
    print(f"{UI.CYAN}🔎 Analizando términos...{UI.END}")
    parts = query.split()
    if not parts:
        return []

    # Empezamos con el primer término
    results = fetch_nozomi_ids(parts[0])
    # Intersección con los siguientes (AND)
    for p in parts[1:]:
        if not results:
            break
        results.intersection_update(fetch_nozomi_ids(p))

    return sorted(list(results), reverse=True)


# --- DESCARGA Y EMPAQUETADO ---


def save_img(raw, path, fmt):
    if not HAS_PILLOW or fmt == "original":
        with open(path, "wb") as f:
            f.write(raw)
        return
    try:
        img = Image.open(BytesIO(raw))
        if fmt.lower() in ["jpg", "jpeg"] and img.mode in ("RGBA", "LA"):
            bg = Image.new(img.mode[:-1], img.size, (255, 255, 255))
            bg.paste(img, img.split()[-1])
            img = bg.convert("RGB")
        img.save(path, quality=92)
    except Exception:
        with open(path, "wb") as f:
            f.write(raw)


def dl_worker(args):
    logic, img, folder, idx = args
    h = img["hash"]
    ext = "avif" if img.get("hasavif") else "webp"
    url = logic.get_url(h, ext)
    final_ext = USER_FORMAT if (HAS_PILLOW and USER_FORMAT != "original") else ext
    path = f"{folder}/{idx + 1:03d}.{final_ext}"

    if os.path.exists(path):
        return True
    for _ in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                save_img(r.content, path, USER_FORMAT)
                return True
        except Exception:
            time.sleep(1)
    return False


def download_gallery(gid, logic):
    try:
        # Usar caché si ya cargamos la info en el listado
        data = METADATA_CACHE.get(int(gid))
        if not data:
            r = requests.get(
                f"https://ltn.gold-usergeneratedcontent.net/galleries/{gid}.js",
                headers=headers,
            )
            data = json.loads(r.text.split("var galleryinfo = ")[1])

        raw_title = data.get("title", str(gid))
        clean_title = "".join(
            [c for c in raw_title if c.isalnum() or c in " -_"]
        ).strip()
        folder = f"{clean_title} [{gid}]"
        if not os.path.exists(folder):
            os.makedirs(folder)

        # info.json simplificado (Babylon style)
        info = {
            "id": gid,
            "title": raw_title,
            "artists": [a["artist"] for a in (data.get("artists") or [])],
            "tags": [t["tag"] for t in (data.get("tags") or [])],
            "url": f"https://hitomi.la/galleries/{gid}.html",
        }
        with open(f"{folder}/info.json", "w", encoding="utf-8") as f:
            json.dump(info, f, indent=4, ensure_ascii=False)

        print(f"\n{UI.GREEN}⬇  Iniciando: {UI.BOLD}{raw_title[:60]}...{UI.END}")
        files = data["files"]
        comp = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
            futures = [
                exe.submit(dl_worker, (logic, f, folder, i))
                for i, f in enumerate(files)
            ]
            for _ in as_completed(futures):
                comp += 1
                perc = int(30 * comp // len(files))
                bar = "█" * perc + "-" * (30 - perc)
                sys.stdout.write(f"\r   [{UI.CYAN}{bar}{UI.END}] {comp}/{len(files)}")
                sys.stdout.flush()

        # Empaquetado
        ext_out = OUTPUT_TYPE.lower()
        out_file = f"{folder}.{ext_out}"
        print(f"\n   📦 Generando {ext_out.upper()}...")

        if ext_out == "pdf" and HAS_PILLOW:
            imgs = sorted(
                [
                    os.path.join(folder, f)
                    for f in os.listdir(folder)
                    if not f.endswith(".json")
                ]
            )
            pages = [Image.open(imgs[0]).convert("RGB")]
            for i in imgs[1:]:
                pages.append(Image.open(i).convert("RGB"))
            pages[0].save(out_file, save_all=True, append_images=pages[1:])
        else:
            with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in os.listdir(folder):
                    zf.write(os.path.join(folder, f), f)

        if DELETE_TEMP:
            import shutil

            shutil.rmtree(folder)
        print(f"   {UI.GREEN}✔  Completado: {out_file}{UI.END}")
    except Exception as e:
        print(f"\n{UI.RED}[!] Error en {gid}: {e}{UI.END}")


# --- MENÚS ESCALONADOS ---


def main():
    logic = HitomiLogic()
    while True:
        UI.header()
        print(f" {UI.PURPLE}Menú Principal:{UI.END}")
        print(f" ├── {UI.BOLD}1.{UI.END} Descargar por ID única")
        print(f" ├── {UI.BOLD}2.{UI.END} Buscar Series (Múltiples Tags/Artistas)")
        print(f" └── {UI.BOLD}3.{UI.END} Salir")
        print(f"\n {UI.PURPLE}Configuración Actual:{UI.END}")
        print(f" ├── Salida: {UI.CYAN}{OUTPUT_TYPE.upper()}{UI.END}")
        print(f" └── Imagen: {UI.CYAN}{USER_FORMAT.upper()}{UI.END}")

        op = input(f"\n{UI.YELLOW} Selecciona una opción ➜ {UI.END}")

        if op == "1":
            gid = input(f"{UI.CYAN} ✍  ID de la galería: {UI.END}")
            if gid.isdigit():
                download_gallery(gid, logic)
                input(f"\n{UI.CYAN}Presiona Enter para volver al menú...{UI.END}")

        elif op == "2":
            q = input(
                f"{UI.CYAN} ✍  Búsqueda (ej: language:spanish female:mind_control): {UI.END}"
            )
            if not q.strip():
                continue

            ids = search_query(q)
            if not ids:
                print(f"{UI.RED} No se encontró ninguna coincidencia.{UI.END}")
                time.sleep(2)
                continue

            page = 0
            while True:
                UI.header()
                start = page * MAX_RESULTS_PAGE
                end = min(start + MAX_RESULTS_PAGE, len(ids))
                print(f" {UI.PURPLE}Búsqueda: '{q}'{UI.END}")
                print(
                    f" {UI.PURPLE}Mostrando {start + 1}-{end} de {len(ids)} resultados{UI.END}"
                )
                print(f" {'━' * 54}")

                # Carga masiva de metadata (batch)
                batch = ids[start:end]
                to_load = [i for i in batch if i not in METADATA_CACHE]
                if to_load:
                    sys.stdout.write(f" {UI.YELLOW}⚡ Cargando títulos...{UI.END}")
                    sys.stdout.flush()

                    def load(gi):
                        try:
                            r = requests.get(
                                f"https://ltn.gold-usergeneratedcontent.net/galleries/{gi}.js",
                                timeout=5,
                            )
                            METADATA_CACHE[gi] = json.loads(
                                r.text.split("var galleryinfo = ")[1]
                            )
                        except Exception:
                            pass

                    with ThreadPoolExecutor(max_workers=20) as exe:
                        exe.map(load, to_load)
                    sys.stdout.write("\r" + " " * 30 + "\r")  # Limpiar texto cargando

                for i, gid in enumerate(batch):
                    m = METADATA_CACHE.get(gid, {})
                    title = m.get("title", "Sin título")[:55]
                    print(
                        f" {UI.BOLD}{start + i + 1:3d}.{UI.END} [{UI.GREEN}{gid}{UI.END}] {title}"
                    )

                print(f" {'━' * 54}")
                print(
                    f" {UI.CYAN}Controles:{UI.END} {UI.BOLD}n{UI.END} (sig) | {UI.BOLD}p{UI.END} (ant) | {UI.BOLD}q{UI.END} (volver)"
                )
                print(
                    f" {UI.CYAN}Selección:{UI.END} Escribe números (ej: '1', '1-5', '1,10,21')"
                )

                sel = input(f"\n{UI.YELLOW} Acción ➜ {UI.END}").lower().strip()

                if sel == "n" and end < len(ids):
                    page += 1
                elif sel == "p" and page > 0:
                    page -= 1
                elif sel == "q":
                    break
                elif sel:
                    # Usar la función parse_sel reparada
                    idxs = parse_sel(sel, len(ids))
                    if idxs:
                        for i in idxs:
                            download_gallery(ids[i], logic)
                        input(
                            f"\n{UI.GREEN}Cola terminada. Enter para continuar...{UI.END}"
                        )
                    else:
                        print(f"{UI.RED} Entrada no válida.{UI.END}")
                        time.sleep(1)

        elif op == "3":
            print(f"{UI.BLUE} ¡Hasta pronto!{UI.END}")
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{UI.RED} Saliendo...{UI.END}")
