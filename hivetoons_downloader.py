"""
HIVETOONS DOWNLOADER v1.0.0
Dependencias:  pip install requests
"""

import os
import re
import sys
import json
import time
import base64
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
from io import BytesIO
import requests

try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    Image = None
    HAS_PILLOW = False

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════
OUTPUT_TYPE = "zip"  # 'zip' | 'cbz' | 'pdf'
USER_FORMAT = "original"  # 'original' | 'jpg' | 'png' | 'webp'
DELETE_TEMP = True
MAX_WORKERS = 5

class HivetoonsLogic:
    def __init__(self, output_dir="downloads"):
        self.output_dir = output_dir
        self.base_url = "https://hivetoons.org"
        self.api_url = "https://api.hivetoons.org"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': self.base_url + '/',
        })
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def extract_astro_props(self, page_html):
        """Extracts JSON properties from astro-island tags."""
        islands = re.findall(r'<astro-island[^>]*?props="([^"]*)"', page_html)
        props_list = []
        for p in islands:
            try:
                decoded = html.unescape(p)
                data = json.loads(decoded)
                props_list.append(data)
            except Exception:
                pass
        return props_list

    def get_all_series(self):
        """Fetches the list of all series from the site."""
        print("Obteniendo lista de series...")
        try:
            r = self.session.get(f"{self.base_url}/series", timeout=15)
            r.raise_for_status()
            
            # Extract slugs from HTML as fallback
            slugs = list(set(re.findall(r'href=["\']/series/([a-zA-Z0-9-]+)["\']', r.text)))
            
            # Try to get more info from astro props
            series_list = []
            props_list = self.extract_astro_props(r.text)
            
            for props in props_list:
                # The data structure depends on the specific astro island
                # We'll just look for title and slug patterns inside the strings
                pass 
                
            # For now, just return slugs
            # In a real scenario we could try to extract titles from HTML
            # e.g. <a href="/series/superhuman-era" title="Superhuman Era">
            results = []
            for slug in slugs:
                # find title in HTML for this slug
                title_match = re.search(fr'href=["\']/series/{slug}["\'][^>]*?title=["\']([^"\']+)["\']', r.text)
                if not title_match:
                    title_match = re.search(fr'href=["\']/series/{slug}["\'][^>]*>([^<]+)</a>', r.text)
                
                title = title_match.group(1).strip() if title_match else slug.replace('-', ' ').title()
                results.append({"slug": slug, "title": title})
            
            # Sort alphabetically by title
            results.sort(key=lambda x: x["title"])
            return results
        except Exception as e:
            print(f"[ERROR] No se pudo obtener la lista de series: {e}")
            return []

    def get_series_chapters(self, series_slug):
        """Fetches the chapters for a specific series."""
        print(f"Obteniendo información de la serie: {series_slug}")
        try:
            # 1. Get the series page to find the postId
            r = self.session.get(f"{self.base_url}/series/{series_slug}", timeout=15)
            r.raise_for_status()
            
            # Unescape HTML entities just in case
            import html as html_lib
            html_text = html_lib.unescape(r.text)
            
            # Look for postId in the HTML (e.g. postId":[0,36])
            post_id_match = re.search(r'postId["\']?\s*[:=]\s*\[?\d+,(\d+)', html_text)
            if not post_id_match:
                post_id_match = re.search(r'postId["\']?\s*[:=]\s*(\d+)', html_text)
                
            if not post_id_match:
                print("[ERROR] No se pudo encontrar el postId para la serie.")
                return []
                
            post_id = post_id_match.group(1)
            
            # 2. Get chapters list from API
            api_url = f"{self.api_url}/api/chapters?postId={post_id}&take=all"
            self.session.headers.update({"Accept": "application/json"})
            r_api = self.session.get(api_url, timeout=15)
            r_api.raise_for_status()
            
            data = r_api.json()
            if "post" in data and "chapters" in data["post"]:
                chapters = data["post"]["chapters"]
            elif "chapters" in data:
                chapters = data["chapters"]
            else:
                print("[ERROR] Estructura de respuesta de API desconocida.")
                return []
                
            # Return list of dicts with slug, number, title, price
            results = []
            for ch in chapters:
                results.append({
                    "id": ch.get("id"),
                    "slug": ch.get("slug"),
                    "number": ch.get("number"),
                    "title": ch.get("title", f"Chapter {ch.get('number')}"),
                    "price": ch.get("price", 0)
                })
            
            # Sort by number
            results.sort(key=lambda x: float(x["number"]) if str(x["number"]).replace('.','',1).isdigit() else 0)
            return results
        except Exception as e:
            print(f"[ERROR] No se pudieron obtener los capítulos: {e}")
            return []

    def get_chapter_images(self, series_slug, chapter_slug):
        """Fetches the image URLs for a specific chapter."""
        url = f"{self.base_url}/series/{series_slug}/{chapter_slug}"
        print(f"Obteniendo imágenes del capítulo de {url}")
        try:
            self.session.headers.update({"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9"})
            r = self.session.get(url, timeout=15)
            r.raise_for_status()
            
            # Extract image URLs from HTML
            # We look for storage.hivetoon.com/public/upload/... .webp/.jpg/.png
            img_urls = re.findall(r'https?://storage\.hivetoon\.com/public/upload/[^\s"\'<>\\{}]+?\.(?:jpg|jpeg|png|webp|gif)', r.text)
            
            # Filter and get unique maintaining order
            filtered_urls = []
            seen = set()
            exclude = ['/avatar/', '/theme/', '/featured/', '/icons/', '/logo']
            
            for u in img_urls:
                u_lower = u.lower()
                # Accept if it's inside a series folder and not in exclude list
                if u not in seen and '/series/' in u_lower and not any(x in u_lower for x in exclude):
                    filtered_urls.append(u)
                    seen.add(u)
            
            # Trust the order they appear in the HTML (which comes from the internal JSON array)
            return filtered_urls
        except Exception as e:
            print(f"[ERROR] Error obteniendo imágenes: {e}")
            return []

    def download_image(self, url, folder, filename):
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            return path
            
        try:
            r = self.session.get(url, stream=True, timeout=15)
            r.raise_for_status()
            
            raw = r.content
            # Convert if needed
            if USER_FORMAT != "original" and HAS_PILLOW and Image is not None:
                img = Image.open(BytesIO(raw))
                if img.mode in ("RGBA", "LA", "P") and USER_FORMAT in ("jpg", "jpeg"):
                    bg = Image.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                    img = bg
                else:
                    img = img.convert("RGB")
                    
                path = os.path.splitext(path)[0] + "." + USER_FORMAT
                img.save(path, format=USER_FORMAT.upper() if USER_FORMAT != "jpg" else "JPEG")
            else:
                with open(path, 'wb') as f:
                    f.write(raw)
                    
            return path
        except Exception as e:
            print(f"[ERROR] Error descargando {url}: {e}")
            return None

    def create_archive(self, folder, output_path):
        ext = OUTPUT_TYPE.lower()
        if ext == "pdf" and HAS_PILLOW and Image is not None:
            paths = sorted([os.path.join(folder, f) for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))])
            if not paths: return
            images = []
            for p in paths:
                try:
                    img = Image.open(p).convert("RGB")
                    images.append(img)
                except:
                    pass
            if images:
                pdf_path = os.path.splitext(output_path)[0] + ".pdf"
                images[0].save(pdf_path, save_all=True, append_images=images[1:])
        else:
            zip_ext = ext if ext in ("zip", "cbz") else "cbz"
            zip_path = os.path.splitext(output_path)[0] + "." + zip_ext
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(folder):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, folder)
                        zf.write(file_path, arcname)

def cls():
    os.system('cls' if os.name == 'nt' else 'clear')

def main():
    downloader = HivetoonsLogic()
    
    while True:
        print("\n=== HIVETOONS DOWNLOADER ===")
        print("1. Buscar serie (por nombre o link)")
        print("2. Listar TODAS las series del sitio")
        print("3. Salir")
        
        opcion = input("Selecciona una opción: ").strip()
        
        if opcion == "3":
            print("Saliendo...")
            break
            
        elif opcion == "2":
            cls()
            series_list = downloader.get_all_series()
            if not series_list:
                continue
                
            print(f"\nSe encontraron {len(series_list)} series:")
            for i, s in enumerate(series_list):
                print(f"{i+1}. {s['title']} ({s['slug']})")
                
            sel = input("\nIngresa el número de la serie para descargar (o Enter para volver): ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(series_list):
                serie = series_list[int(sel)-1]
                handle_serie(downloader, serie['slug'], serie['title'])
                
        elif opcion == "1":
            q = input("\nIngresa el nombre o el link de la serie: ").strip()
            if not q:
                continue
                
            # If it's a link
            if 'hivetoons.org/series/' in q:
                slug = q.split('hivetoons.org/series/')[-1].split('/')[0]
                handle_serie(downloader, slug, slug.replace('-', ' ').title())
            else:
                # Search by name locally
                print("Buscando...")
                all_series = downloader.get_all_series()
                matches = [s for s in all_series if q.lower() in s['title'].lower() or q.lower() in s['slug'].lower()]
                
                if not matches:
                    print("No se encontraron resultados.")
                    continue
                    
                print(f"\nResultados para '{q}':")
                for i, s in enumerate(matches):
                    print(f"{i+1}. {s['title']} ({s['slug']})")
                    
                sel = input("\nIngresa el número de la serie para descargar (o Enter para volver): ").strip()
                if sel.isdigit() and 1 <= int(sel) <= len(matches):
                    serie = matches[int(sel)-1]
                    handle_serie(downloader, serie['slug'], serie['title'])

def handle_serie(downloader, slug, title):
    cls()
    print(f"=== SERIE: {title} ===")
    chapters = downloader.get_series_chapters(slug)
    
    if not chapters:
        print("No se encontraron capítulos o hubo un error.")
        return
        
    print(f"\nSe encontraron {len(chapters)} capítulos.")
    print("Capítulos disponibles:")
    for ch in chapters:
        cost_str = f" (Costo: {ch['price']} coins)" if ch['price'] > 0 else " (Gratis)"
        print(f" - {ch['number']}: {ch['title']}{cost_str}")
        
    op = input("\n Caps ('1', '3-5', 'all') ➜ ").strip()
    
    to_download = []
    if not op:
        return
    elif op.lower() in ["todos", "all"]:
        to_download = chapters
    elif '-' in op:
        try:
            start, end = map(float, op.split('-'))
            to_download = [c for c in chapters if start <= float(c['number']) <= end]
        except:
            print("Rango inválido.")
    else:
        # User might have typed chapter number directly or a comma-separated list
        if ',' in op:
            nums = [n.strip() for n in op.split(',')]
            to_download = [c for c in chapters if str(c['number']) in nums]
        else:
            to_download = [c for c in chapters if str(c['number']) == op]
        
    if not to_download:
        print("No hay capítulos seleccionados para descargar.")
        return
        
    # Check for paid chapters
    paid_chapters = [c for c in to_download if c['price'] > 0]
    if paid_chapters:
        print(f"\n¡ADVERTENCIA! {len(paid_chapters)} capítulos seleccionados son de PAGA (Coins).")
        print("El downloader intentará descargarlos, pero probablemente fallen o solo descarguen imágenes gratuitas de preview si no hay sesión activa.")
        resp = input("¿Deseas continuar de todas formas? (s/n): ").strip().lower()
        if resp != 's':
            return
            
    print(f"\nComenzando descarga de {len(to_download)} capítulos...")
    
    serie_dir = os.path.join(downloader.output_dir, slug)
    if not os.path.exists(serie_dir):
        os.makedirs(serie_dir)
        
    for ch in to_download:
        print(f"\n>> Procesando Capítulo {ch['number']} ({ch['slug']})...")
        time.sleep(1.5)  # Evitar rate limits / 404 del servidor
        urls = downloader.get_chapter_images(slug, ch['slug'])
        
        if not urls:
            print(f"[!] No se encontraron imágenes para el capítulo {ch['number']}.")
            continue
            
        print(f"Descargando {len(urls)} imágenes...")
        
        ch_dir = os.path.join(serie_dir, ch['slug'])
        if not os.path.exists(ch_dir):
            os.makedirs(ch_dir)
            
        def dl_task(i, url):
            ext = url.split('.')[-1]
            if len(ext) > 5: ext = "jpg"
            filename = f"{i:03d}.{ext}"
            return downloader.download_image(url, ch_dir, filename)
            
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(dl_task, i, url) for i, url in enumerate(urls, 1)]
            for future in as_completed(futures):
                pass
                
        # Empaquetar
        archive_name = f"{slug} - {ch['number']}.{OUTPUT_TYPE}"
        archive_path = os.path.join(serie_dir, archive_name)
        print(f"Creando {archive_name}...")
        downloader.create_archive(ch_dir, archive_path)
        
        # Opcional: borrar carpeta temporal
        if DELETE_TEMP:
            import shutil
            shutil.rmtree(ch_dir)
        
    print("\n¡Descargas completadas!")

if __name__ == "__main__":
    main()
