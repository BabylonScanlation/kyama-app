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
    DIM: str = "\033[2m"
    END: str = "\033[0m"

    @staticmethod
    def header() -> None:
        _ = os.system("cls" if os.name == "nt" else "clear")
        print(f"{UI.BLUE}╔══════════════════════════════════════╗")
        print(f"║ {UI.BOLD}HITOMI DOWNLOADER v1.3.0{UI.END}{UI.BLUE}             ║")
        print(f"╚══════════════════════════════════════╝{UI.END}")


# ==========================================
#      CONFIGURACIÓN (Edita aquí)
# ==========================================
OUTPUT_TYPE: str = "zip"  # 'zip', 'cbz', 'pdf'
USER_FORMAT: str = "webp"  # 'original', 'jpg', 'png', 'webp'
DELETE_TEMP: bool = True
MAX_WORKERS_DL: int = 50
MAX_RESULTS_PAGE: int = 20
# ==========================================

METADATA_CACHE: dict[str, object] = {}
headers: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://hitomi.la/",
}

SESSION = requests.Session()
adapter = requests.adapters.HTTPAdapter(
    pool_connections=MAX_WORKERS_DL, pool_maxsize=MAX_WORKERS_DL
)
SESSION.mount("http://", adapter)
SESSION.mount("https://", adapter)
SESSION.headers.update(headers)

# Idiomas disponibles para el catálogo
LANGUAGES = {
    "all": "Todos",
    "japanese": "Japonés",
    "english": "Inglés",
    "chinese": "Chino",
    "korean": "Coreano",
    "spanish": "Español",
    "french": "Francés",
    "german": "Alemán",
    "italian": "Italiano",
    "russian": "Ruso",
    "thai": "Tailandés",
    "indonesian": "Indonesio",
    "vietnamese": "Vietnamita",
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
            body: str = SESSION.get(
                "https://ltn.gold-usergeneratedcontent.net/gg.js", timeout=10
            ).text

            m_o = re.search(r"var o = (\d)", body)
            self.m_default = int(m_o.group(1)) if m_o else 0

            o_match = re.search(r"o = (\d); break;", body)
            o_val: int = int(o_match.group(1)) if o_match else self.m_default
            for c in re.findall(r"case (\d+):", body):
                self.m_map[int(c)] = o_val

            m_b = re.search(r"b: '(.+)'", body)
            self.b_val = m_b.group(1) if m_b else ""
            if self.b_val and not self.b_val.endswith("/"):
                self.b_val += "/"
        except Exception:
            print(f"{UI.RED}[!] Error cargando gg.js.{UI.END}")

    def get_url(self, h: str, ext: str) -> str:
        g: int = int(h[-1] + h[-3:-1], 16) if h else 0
        m: int = self.m_map.get(g, self.m_default)
        sub: str = f"{'a' if ext == 'avif' else 'w'}{1 + m}"
        return f"https://{sub}.gold-usergeneratedcontent.net/{self.b_val}{g}/{h}.{ext}"


# ══════════════════════════════════════════════════════════════════════════════
#  SORT OPTIONS
# ══════════════════════════════════════════════════════════════════════════════
# Cada entrada: (label, [términos extra que se añaden a la query])
SORT_OPTIONS: list[tuple[str, list[str]]] = [
    ("Fecha añadida (default)", []),
    ("Fecha publicación", ["orderby:datepublished"]),
    ("Popular: Hoy", ["orderby:popular", "orderbykey:today"]),
    ("Popular: Semana", ["orderby:popular", "orderbykey:week"]),
    ("Popular: Mes", ["orderby:popular", "orderbykey:month"]),
    ("Popular: Año", ["orderby:popular", "orderbykey:year"]),
    ("Aleatorio", ["orderbydirection:random"]),
]


# ══════════════════════════════════════════════════════════════════════════════
#  NOZOMI — fetch de IDs
# ══════════════════════════════════════════════════════════════════════════════
def _nozomi_ids_from_url(url: str) -> list[int]:
    """Descarga un .nozomi y devuelve lista ordenada de IDs (big-endian int32)."""
    try:
        r = SESSION.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return []
        data = r.content
        return [
            struct.unpack(">i", data[i * 4 : (i + 1) * 4])[0]
            for i in range(len(data) // 4)
        ]
    except Exception:
        return []


def _term_to_url(term: str) -> str:
    """Convierte un término de búsqueda/sort en su URL .nozomi."""
    base = "https://ltn.gold-usergeneratedcontent.net"
    term = term.replace("_", " ").strip()
    if ":" in term:
        ns, v = term.split(":", 1)
        if ns in ["female", "male"]:
            return f"{base}/n/tag/{term}-all.nozomi"
        elif ns == "language":
            return f"{base}/n/index-{v}.nozomi"
        else:
            # orderby, orderbykey, orderbydirection, artist, group, etc.
            return f"{base}/n/{ns}/{v}-all.nozomi"
    else:
        return f"{base}/n/tag/{term}-all.nozomi"


def _fetch_ids_set(term: str) -> set[int]:
    """IDs de un término como conjunto (para intersección de contenido)."""
    try:
        r = SESSION.get(_term_to_url(term), headers=headers, timeout=15)
        if r.status_code != 200:
            return set()
        return {
            struct.unpack(">i", r.content[i * 4 : (i + 1) * 4])[0]
            for i in range(len(r.content) // 4)
        }
    except Exception:
        return set()


def _fetch_ids_ordered(term: str) -> list[int]:
    """IDs de un término como lista ordenada (para aplicar sort)."""
    return _nozomi_ids_from_url(_term_to_url(term))


def _apply_sort(base_ids: list[int], sort_terms: list[str]) -> list[int]:
    """
    Aplica ordenamiento a una lista de IDs.
    - Descarga el/los nozomi de sort (ya vienen en orden correcto).
    - Filtra para quedarse solo con los IDs que están en base_ids.
    - Los IDs de base_ids que no aparezcan en el sort se agregan al final.
    """
    if not sort_terms:
        return base_ids

    base_set = set(base_ids)

    # Si hay varios términos de sort (ej: orderby + orderbykey), intersectarlos
    # usando el primero como lista ordenada y los demás como filtros
    sort_ordered = _fetch_ids_ordered(sort_terms[0])
    if len(sort_terms) > 1:
        for extra in sort_terms[1:]:
            extra_set = _fetch_ids_set(extra)
            sort_ordered = [i for i in sort_ordered if i in extra_set]

    # Preservar orden del sort, filtrado por base_ids
    seen: set[int] = set()
    result: list[int] = []
    for gid in sort_ordered:
        if gid in base_set:
            result.append(gid)
            seen.add(gid)
    # IDs que no aparecieron en el sort (raros) al final
    for gid in base_ids:
        if gid not in seen:
            result.append(gid)
    return result


def fetch_catalog_ids(
    language: str = "all", sort_terms: list[str] | None = None
) -> list[int]:
    """
    Catálogo completo por idioma, con sort opcional.
    URL base: index-{language}.nozomi  (fallback: n/index-{language}.nozomi)
    """
    base = "https://ltn.gold-usergeneratedcontent.net"
    url = f"{base}/index-{language}.nozomi"
    ids = _nozomi_ids_from_url(url)
    if not ids:
        ids = _nozomi_ids_from_url(f"{base}/n/index-{language}.nozomi")
    if ids and sort_terms:
        ids = _apply_sort(ids, sort_terms)
    return ids


def search_query(query: str, sort_terms: list[str] | None = None) -> list[int]:
    """
    Búsqueda por intersección de términos de contenido.
    sort_terms: lista de términos orderby/orderbykey/orderbydirection.
    Sin sort → orden por ID desc (= fecha añadida, más nuevos primero).
    """
    print(f"{UI.CYAN}🔎 Analizando términos…{UI.END}")
    parts = query.split()
    if not parts:
        return []

    content_ids: set[int] = _fetch_ids_set(parts[0])
    for p in parts[1:]:
        if not content_ids:
            break
        content_ids.intersection_update(_fetch_ids_set(p))

    if not content_ids:
        return []

    if sort_terms:
        return _apply_sort(list(content_ids), sort_terms)
    else:
        return sorted(content_ids, reverse=True)  # fecha añadida desc


# ══════════════════════════════════════════════════════════════════════════════
#  METADATA — carga por batch
# ══════════════════════════════════════════════════════════════════════════════
def _load_meta(gid: int) -> None:
    if str(gid) in METADATA_CACHE:
        return
    try:
        r = SESSION.get(
            f"https://ltn.gold-usergeneratedcontent.net/galleries/{gid}.js",
            timeout=5,
        )
        METADATA_CACHE[str(gid)] = json.loads(r.text.split("var galleryinfo = ")[1])
    except Exception:
        pass


def load_meta_batch(gids: list[int]) -> None:
    to_load = [g for g in gids if str(g) not in METADATA_CACHE]
    if not to_load:
        return
    sys.stdout.write(f" {UI.YELLOW}⚡ Cargando títulos…{UI.END}")
    sys.stdout.flush()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
        list(exe.map(_load_meta, to_load))
    sys.stdout.write("\r" + " " * 35 + "\r")


def _title(gid: int) -> str:
    m = METADATA_CACHE.get(str(gid), {})
    if isinstance(m, dict):
        return str(cast(dict, m).get("title", "Sin título"))[:55]
    return "Sin título"


# ══════════════════════════════════════════════════════════════════════════════
#  DESCARGA
# ══════════════════════════════════════════════════════════════════════════════
def save_img(raw: bytes, path: str, fmt: str) -> None:
    if not has_pillow or fmt == "original" or Image is None:
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


def dl_worker(args: tuple) -> bool:
    logic, img, folder, idx = args
    h: str = str(img.get("hash", ""))
    ext: str = "avif" if img.get("hasavif") else "webp"
    url: str = logic.get_url(h, ext)
    final_ext = USER_FORMAT if (has_pillow and USER_FORMAT != "original") else ext
    path = f"{folder}/{idx + 1:03d}.{final_ext}"
    if os.path.exists(path):
        return True
    for _ in range(3):
        try:
            r = SESSION.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                save_img(r.content, path, USER_FORMAT)
                return True
        except Exception:
            time.sleep(1)
    return False


def download_gallery(gid: int, logic: HitomiLogic) -> None:
    try:
        data = METADATA_CACHE.get(str(gid))
        if not data:
            r = SESSION.get(
                f"https://ltn.gold-usergeneratedcontent.net/galleries/{gid}.js",
                headers=headers,
            )
            data = json.loads(r.text.split("var galleryinfo = ")[1])
        if not data:
            return

        d = cast(dict, data)
        raw_title = str(d.get("title", str(gid)))
        clean_title = re.sub(r'[\\/:*?"<>|]', "", raw_title).strip()
        folder = f"{clean_title} [{gid}]"
        os.makedirs(folder, exist_ok=True)

        print(f"\n{UI.GREEN}⬇  {UI.BOLD}{raw_title[:60]}…{UI.END}")
        files = [f for f in d.get("files", []) if isinstance(f, dict)]

        comp = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
            futs = [
                exe.submit(dl_worker, (logic, f, folder, i))
                for i, f in enumerate(files)
            ]
            for _ in as_completed(futs):
                comp += 1
                pct = int(30 * comp // max(len(files), 1))
                bar = "█" * pct + "─" * (30 - pct)
                sys.stdout.write(f"\r   [{UI.CYAN}{bar}{UI.END}] {comp}/{len(files)}")
                sys.stdout.flush()

        ext_out = OUTPUT_TYPE.lower()
        out_file = f"{folder}.{ext_out}"
        print(f"\n   📦 Generando {ext_out.upper()}…")

        if ext_out == "pdf" and has_pillow and Image is not None:
            imgs = sorted(
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if not f.endswith(".json")
            )
            if imgs:
                pages = [Image.open(i).convert("RGB") for i in imgs]
                pages[0].save(out_file, save_all=True, append_images=pages[1:])
        else:
            with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
                for fn in os.listdir(folder):
                    zf.write(os.path.join(folder, fn), fn)

        if DELETE_TEMP:
            shutil.rmtree(folder)
        print(f"   {UI.GREEN}✔  {out_file}{UI.END}")
    except Exception as e:
        print(f"\n{UI.RED}[!] Error en {gid}: {e}{UI.END}")


# ══════════════════════════════════════════════════════════════════════════════
#  MENÚ DE RESULTADOS — compartido entre búsqueda y catálogo
#  Soporta toggle 't' para paginar/ver todo, multiselect
# ══════════════════════════════════════════════════════════════════════════════
def results_browser(
    ids: list[int], label: str, logic: HitomiLogic, paginated: bool = True
) -> None:
    page = 0


def results_browser(
    ids: list[int], label: str, logic: HitomiLogic, paginated: bool = True
) -> None:
    page = 0
    DISPLAY_PAGE = MAX_RESULTS_PAGE  # cuántos mostrar por página
    LOAD_BATCH = MAX_WORKERS_DL  # cuántos fetchear en paralelo a la vez

    while True:
        UI.header()

        if paginated:
            start = page * CHUNK
            end = min(start + CHUNK, len(ids))
            load_meta_batch(ids[start:end])

            print(f" {UI.PURPLE}{label}{UI.END}")
            print(
                f" {UI.DIM}Mostrando {start + 1}–{end} de {len(ids):,} resultados{UI.END}"
            )
            print(f" {'━' * 52}")
            for i, gid in enumerate(ids[start:end]):
                print(
                    f" {UI.BOLD}{start + i + 1:>5}.{UI.END} [{UI.GREEN}{gid}{UI.END}] {_title(gid)}"
                )
            print(f" {'━' * 52}")

        else:
            to_load = [gid for gid in ids if str(gid) not in METADATA_CACHE]
            total = len(to_load)
            if to_load:
                done = 0
                with ThreadPoolExecutor(max_workers=MAX_WORKERS_DL) as exe:
                    futs = {exe.submit(_load_meta, gid): gid for gid in to_load}
                    for _ in as_completed(futs):
                        done += 1
                        sys.stdout.write(
                            f"\r {UI.CYAN}⚡ Cargando metadata… {done}/{total}{UI.END}   "
                        )
                        sys.stdout.flush()
                print()

            UI.header()
            print(f" {UI.PURPLE}{label}{UI.END}")
            print(f" {UI.DIM}{len(ids):,} resultados{UI.END}")
            print(f" {'━' * 52}")
            for i, gid in enumerate(ids):
                print(
                    f" {UI.BOLD}{i + 1:>5}.{UI.END} [{UI.GREEN}{gid}{UI.END}] {_title(gid)}"
                )
            print(f" {'━' * 52}")

        nav = []
        if paginated and end < len(ids):
            nav.append(f"{UI.BOLD}n{UI.END}=sig")
        if paginated and page > 0:
            nav.append(f"{UI.BOLD}p{UI.END}=ant")
        nav.append(f"{UI.BOLD}t{UI.END}=toggle pag")
        nav.append(f"{UI.BOLD}q{UI.END}=volver")
        print(f" {UI.CYAN}Nav:{UI.END} {'  '.join(nav)}")
        print(f" {UI.CYAN}Sel:{UI.END} número  ·  1-5  ·  1,3,7  ·  all")

        sel = input(f"\n{UI.YELLOW} Acción ➜ {UI.END}").strip().lower()

        if sel == "q":
            return
        elif sel == "n" and paginated and end < len(ids):
            page += 1
        elif sel == "p" and paginated and page > 0:
            page -= 1
        elif sel == "t":
            paginated = not paginated
            page = 0
        elif sel:
            idxs = parse_sel(sel, len(ids))
            if not idxs:
                print(f"{UI.RED} Entrada no válida.{UI.END}")
                time.sleep(1)
                continue

            # Cargar metadata solo de los seleccionados
            load_meta_batch([ids[x] for x in idxs])

            print(f"\n  {UI.PURPLE}Galerías seleccionadas:{UI.END}")
            for x in idxs:
                print(f"   - [{ids[x]}] {_title(ids[x])}")

            confirm = (
                input(f"\n{UI.YELLOW} ¿Confirmar? (Enter=sí / n=cancelar) ➜ {UI.END}")
                .strip()
                .lower()
            )
            if confirm != "n":
                for x in idxs:
                    download_gallery(ids[x], logic)
                input(f"\n{UI.GREEN} Cola terminada. Enter para continuar…{UI.END}")


# ══════════════════════════════════════════════════════════════════════════════
#  UTILIDADES
# ══════════════════════════════════════════════════════════════════════════════
def parse_sel(selection_str: str, max_len: int) -> list[int]:
    selection_str = selection_str.lower().replace(" ", "")
    if selection_str == "all":
        return list(range(max_len))
    indices: set[int] = set()
    try:
        for part in selection_str.split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                for i in range(int(a) - 1, int(b)):
                    if 0 <= i < max_len:
                        indices.add(i)
            elif part.isdigit():
                i = int(part) - 1
                if 0 <= i < max_len:
                    indices.add(i)
    except Exception:
        pass
    return sorted(indices)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════
def _pick_sort() -> tuple[str, list[str]]:
    """Muestra el selector de ordenamiento y devuelve (label, sort_terms)."""
    print(f"\n {UI.PURPLE}Ordenar por:{UI.END}")
    for i, (label, _) in enumerate(SORT_OPTIONS):
        print(f"  {UI.BOLD}{i + 1}.{UI.END} {label}")
    sel = input(f"\n{UI.YELLOW} Número (Enter = default) ➜ {UI.END}").strip()
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(SORT_OPTIONS):
            return SORT_OPTIONS[idx]
    return SORT_OPTIONS[0]


def _catalog_submenu(logic: HitomiLogic) -> None:
    """Subopción del menú de búsqueda: explorar catálogo por idioma."""
    UI.header()
    print(f" {UI.PURPLE}📚  Catálogo completo{UI.END}\n")
    print(f" {UI.DIM}'all' puede tardar varios segundos (archivo grande).{UI.END}\n")

    lang_keys = list(LANGUAGES.keys())
    for i, (k, v) in enumerate(LANGUAGES.items()):
        print(f"  {UI.BOLD}{i + 1:>2}.{UI.END} {v}  {UI.DIM}({k}){UI.END}")

    sel = input(f"\n{UI.YELLOW} Idioma (Enter = Todos) ➜ {UI.END}").strip()
    lang = "all"
    if sel.isdigit():
        idx = int(sel) - 1
        if 0 <= idx < len(lang_keys):
            lang = lang_keys[idx]

    sort_label, sort_terms = _pick_sort()
    lang_label = LANGUAGES.get(lang, lang)

    print(f"\n  {UI.CYAN}⚡ Descargando índice [{lang_label}] · {sort_label}…{UI.END}")
    ids = fetch_catalog_ids(lang, sort_terms=sort_terms or None)

    if not ids:
        print(f"  {UI.RED}✗  No se pudo cargar el índice.{UI.END}")
        input(f"\n  {UI.CYAN}Enter para volver…{UI.END}")
        return

    print(f"  {UI.GREEN}✔  {len(ids):,} galerías{UI.END}")
    pag_raw = (
        input(f"  {UI.CYAN}¿Paginado? [Enter=sí / n=todo] ➜ {UI.END}").strip().lower()
    )
    paginated = pag_raw != "n"
    results_browser(
        ids,
        f"Catálogo · {lang_label} · {sort_label} · {len(ids):,}",
        logic,
        paginated=paginated,
    )


def menu_search_or_catalog(logic: HitomiLogic) -> None:
    UI.header()
    print(
        f" {UI.DIM}ID numérico para descargar directo, o términos para buscar.{UI.END}\n"
    )
    q = input(
        f"{UI.CYAN} ✍  ID / búsqueda (ej: language:spanish female:mind_control): {UI.END}"
    ).strip()
    if not q:
        return

    # Si es un ID numérico → descarga directa
    if q.isdigit():
        download_gallery(int(q), logic)
        input(f"\n{UI.CYAN} Enter para volver…{UI.END}")
        return

    sort_label, sort_terms = _pick_sort()
    ids = search_query(q, sort_terms=sort_terms or None)
    if not ids:
        print(f"{UI.RED} Sin coincidencias.{UI.END}")
        time.sleep(2)
        return
    results_browser(ids, f"'{q}' · {sort_label} · {len(ids):,} resultados", logic)


def main() -> None:
    logic = HitomiLogic()
    while True:
        UI.header()
        print(f" {UI.PURPLE}Menú Principal:{UI.END}")
        print(
            f" ├── {UI.BOLD}1.{UI.END} Buscar / descargar  {UI.DIM}(por ID o tags){UI.END}"
        )
        print(
            f" ├── {UI.BOLD}2.{UI.END} {UI.GREEN}📚 Catálogo completo{UI.END}  {UI.DIM}(idioma · orden · paginación){UI.END}"
        )
        print(f" └── {UI.BOLD}3.{UI.END} Salir")
        print(
            f"\n {UI.PURPLE}Config:{UI.END} salida={UI.CYAN}{OUTPUT_TYPE.upper()}{UI.END}  imagen={UI.CYAN}{USER_FORMAT.upper()}{UI.END}  workers={UI.CYAN}{MAX_WORKERS_DL}{UI.END}"
        )

        op = input(f"\n{UI.YELLOW} Opción ➜ {UI.END}").strip()

        if op == "1":
            menu_search_or_catalog(logic)

        elif op == "2":
            _catalog_submenu(logic)

        elif op == "3":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{UI.YELLOW}Interrumpido.{UI.END}")
