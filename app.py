import os
import io
import sys
import threading
import time
import shutil
import re
import requests
import warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*pkg_resources is deprecated.*")
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory


from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ADD these imports for the new driver
from seleniumwire import webdriver as webdriver_wire
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service as ChromeService

from merge import (
    scrape_ehentai,
    scrape_nhentai,
    scrape_rule34,
    search_ehentai_urls_by_tags,
    search_nhentai_codes_by_tags
)
from imhen import scrape_imhentai

app = Flask(__name__)
DOWNLOAD_DIRECTORY = os.path.abspath(os.path.dirname(__file__))

# Add 'is_cancelled' to the job_status dictionary
job_status = { 'is_running': False, 'output': '', 'progress': 0, 'outcome': None, 'completed_galleries': [], 'error_message': '', 'is_cancelled': False }


def setup_driver():
    """Configures and returns a robust, headless Selenium WebDriver using Selenium Manager."""
    print("🚀 Setting up Selenium WebDriver...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])

    brave_path_windows = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    if sys.platform == "win32" and os.path.exists(brave_path_windows):
        print("Brave Browser detected. Setting binary location.")
        chrome_options.binary_location = brave_path_windows
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        print("✅ WebDriver setup successful.")
        return driver
    except Exception as e:
        print(f"❌ WebDriver setup failed: {e}")
        raise

# ADD the new driver setup function for Selenium-Wire
def setup_selenium_wire_driver():
    """Configures and returns a headless Selenium-Wire WebDriver."""
    print("🚀 Setting up Selenium-Wire WebDriver...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--log-level=3")
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver_wire.Chrome(service=service, options=chrome_options)
    print("✅ Selenium-Wire WebDriver setup successful.")
    return driver

# --- START BATO.TO SCRAPER SECTION ---
MAX_THREADS_BATO = 16
HEADERS_BATO = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"}

def safe_folder_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " _-").strip() or "Gallery"

def download_image_bato(args):
    idx, img_url, output_dir, session = args
    try:
        r = session.get(img_url, headers=HEADERS_BATO, stream=True, timeout=20)
        r.raise_for_status()
        ext = 'webp'
        filepath = os.path.join(output_dir, f"{idx:04d}.{ext}")
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(1024):
                f.write(chunk)
        print(f"[{idx}] ✅ Saved {filepath}")
        return True
    except Exception as e:
        print(f"[{idx}] ❌ Failed {img_url}: {e}")
        return False

def _parse_chapter_selection(selection_str, total_chapters):
    if not selection_str or selection_str.strip() == "":
        return set(range(1, total_chapters + 1))

    selected_chapters = set()
    parts = selection_str.split(',')
    for part in parts:
        part = part.strip()
        if not part: continue
        try:
            if '-' in part:
                start, end = map(int, part.split('-'))
                if 1 <= start <= end <= total_chapters:
                    for i in range(start, end + 1): selected_chapters.add(i)
                else: print(f"⚠️ Invalid range '{part}'.")
            else:
                chapter_num = int(part)
                if 1 <= chapter_num <= total_chapters: selected_chapters.add(chapter_num)
                else: print(f"⚠️ Invalid chapter number '{part}'.")
        except ValueError:
            print(f"⚠️ Could not parse '{part}'.")
    return sorted(list(selected_chapters))

def scrape_bato(series_url, chapter_selection_str="", status_dict=None, driver_setup_func=None):
    if not driver_setup_func:
        raise ValueError("A valid driver setup function must be provided.")

    SITE_FOLDER = "Bato"
    driver = driver_setup_func()
    
    try:
        # --- Series-level setup ---
        series_slug = series_url.strip('/').split('/')[-1]
        series_folder_name = safe_folder_name(series_slug).title()
        series_path = os.path.join(SITE_FOLDER, series_folder_name)
        os.makedirs(series_path, exist_ok=True)
        print(f"Saving series '{series_folder_name}' into folder: '{series_path}'")
        if status_dict: status_dict['progress'] = 2

        # --- Find all available chapters ---
        driver.get(series_url)
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        all_chapter_links = sorted(list(set([urljoin(series_url, link['href']) for link in soup.find_all('a', href=True) if re.search(r'/chapter', link['href'].lower())])))
        if not all_chapter_links: return None
        
        # --- Filter chapters based on user selection ---
        total_found = len(all_chapter_links)
        print(f"✅ Found {total_found} total chapters.")
        selected_numbers = _parse_chapter_selection(chapter_selection_str, total_found)
        if not selected_numbers: return None
        
        chapters_to_download = [all_chapter_links[num - 1] for num in selected_numbers]
        print(f"➡️ Will download {len(chapters_to_download)} selected chapter(s).")
        if status_dict: status_dict['progress'] = 5

        # --- Loop through and download each selected chapter ---
        completed_chapters = [] 
        total_chapters_to_download = len(chapters_to_download)
        for i, chapter_url in enumerate(chapters_to_download):
            # MODIFICATION: Check for cancellation signal between chapters
            if status_dict and status_dict.get('is_cancelled'):
                print("LOG: [BATO] Cancellation detected. Stopping further chapter downloads.")
                break

            chapter_slug = chapter_url.strip('/').split('/')[-1]
            chapter_name = safe_folder_name(f"Chapter-{selected_numbers[i]}-{chapter_slug}")
            chapter_folder_path = os.path.join(series_path, chapter_name)
            os.makedirs(chapter_folder_path, exist_ok=True)
            
            driver.get(chapter_url)
            time.sleep(3)
            
            image_tags = BeautifulSoup(driver.page_source, 'html.parser').find_all('img', class_='page-img')
            if not image_tags: continue

            tasks = [(idx + 1, img.get('src').strip(), chapter_folder_path, requests.Session()) for idx, img in enumerate(image_tags) if img.get('src')]
            
            successful_downloads = 0
            with ThreadPoolExecutor(max_workers=MAX_THREADS_BATO) as executor:
                # MODIFICATION: Changed to a for loop to check cancellation flag
                futures = []
                for task in tasks:
                    if status_dict and status_dict.get('is_cancelled'):
                        print(f"LOG: [BATO] Cancellation detected. No more images will be downloaded for this chapter.")
                        break
                    futures.append(executor.submit(download_image_bato, task))
                
                for future in as_completed(futures):
                    if future.result(): successful_downloads += 1
                    if status_dict and total_chapters_to_download > 0:
                        chapter_progress = (len(futures) / len(tasks)) * (95 / total_chapters_to_download)
                        overall_progress = (i / total_chapters_to_download) * 95
                        status_dict['progress'] = int(5 + overall_progress + chapter_progress)
            
            if successful_downloads > 0:
                completed_chapters.append({
                    'name': f"{series_folder_name} - {chapter_name}",
                    'path': chapter_folder_path.replace('\\', '/')
                })
        
        return completed_chapters
    finally:
        driver.quit()

# --- END BATO.TO SCRAPER SECTION ---

class LiveLogger:
    def __init__(self, job_status_dict): self.buffer = io.StringIO(); self.job_status = job_status_dict
    def write(self, message): self.buffer.write(message); self.job_status['output'] = self.buffer.getvalue()
    def flush(self): pass

def scraper_worker(site, mode, **kwargs):
    global job_status
    # MODIFICATION: Added 'is_cancelled' to the worker's reset dictionary
    job_status.update({'is_running': True, 'output': '', 'progress': 0, 'outcome': 'in_progress', 'completed_galleries': [], 'error_message': '', 'is_cancelled': False})
    live_logger = LiveLogger(job_status); old_stdout = sys.stdout; sys.stdout = live_logger
    try:
        if site == 'rule34':
            result_data = scrape_rule34(kwargs.get('tags', []), status_dict=job_status, driver_setup_func=setup_driver)
            if result_data:
                job_status['completed_galleries'].append(result_data)
        else:
            gallery_list = []
            if mode == 'tags':
                tags, limit = kwargs.get('tags', []), kwargs.get('limit', 1)
                if site == 'ehentai': gallery_list = search_ehentai_urls_by_tags(tags, limit, driver_setup_func=setup_driver)
                elif site == 'nhentai': gallery_list = search_nhentai_codes_by_tags(tags, limit)
            elif mode == 'direct':
                gallery_list = kwargs.get('urls_or_codes', [])
            
            for item in gallery_list:
                # MODIFICATION: Check for cancellation between galleries
                if job_status.get('is_cancelled'):
                    print("LOG: [WORKER] Job cancelled. Halting further gallery processing.")
                    break

                result_data = None
                processed_item = item
                if site == 'nhentai' and '/g/' in item:
                    try: processed_item = [part for part in item.split('/') if part.isdigit()][-1]
                    except IndexError: continue
                
                if site == 'bato':
                    result_data = scrape_bato(processed_item, chapter_selection_str=kwargs.get('chapter_selection', ''), status_dict=job_status, driver_setup_func=setup_driver)
                elif site == 'ehentai': result_data = scrape_ehentai(processed_item, status_dict=job_status)
                elif site == 'nhentai': result_data = scrape_nhentai(processed_item, status_dict=job_status)
                # ADD THE NEW CONDITION for imhentai
                elif site == 'imhentai':
                    result_data = scrape_imhentai(processed_item, status_dict=job_status, driver_setup_func=setup_selenium_wire_driver)


                if result_data:
                    if isinstance(result_data, list):
                        job_status['completed_galleries'].extend(result_data)
                    else:
                        job_status['completed_galleries'].append(result_data)
        
        job_status['outcome'] = 'success' if job_status['completed_galleries'] else 'failure'
    except Exception as e:
        job_status['outcome'] = 'failure'
        job_status['error_message'] = str(e)
    finally:
        sys.stdout = old_stdout
        job_status['is_running'] = False
        job_status['progress'] = 100
        print("LOG: [WORKER] Scraper worker thread finished.")

@app.route('/')
def index():
    return render_template('index.html', cache_buster=int(time.time()))

# --- NEW: Helper function for natural sorting ---
def natural_sort_key(gallery_dict):
    """Extracts numbers from chapter names for correct numerical sorting."""
    name = gallery_dict['name']
    match = re.search(r'Chapter-(\d+)', name)
    if match:
        return int(match.group(1))
    # Fallback for names that don't match the chapter format
    return name

@app.route('/gallery')
def gallery():
    galleries = {}
    # Handle standard sites separately from Bato series
    known_site_folders = {'Rule34', 'E-Hentai', 'NHentai', 'ImHentai'}
    image_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
    
    # This list will hold all category names for display
    display_folders = []

    try:
        all_dirs_in_root = [d for d in os.listdir(DOWNLOAD_DIRECTORY) if os.path.isdir(os.path.join(DOWNLOAD_DIRECTORY, d))]
        
        # 1. Process standard site folders (e.g., Rule34, E-Hentai)
        standard_site_folders = [d for d in all_dirs_in_root if d in known_site_folders]
        display_folders.extend(standard_site_folders)

        for site_folder in standard_site_folders:
            site_path = os.path.join(DOWNLOAD_DIRECTORY, site_folder)
            galleries[site_folder] = []
            gallery_folders = [d for d in os.listdir(site_path) if os.path.isdir(os.path.join(site_path, d))]
            for gallery_name in gallery_folders:
                gallery_item = {
                    'name': gallery_name,
                    'path': os.path.join(site_folder, gallery_name).replace('\\', '/')
                }
                galleries[site_folder].append(gallery_item)

        # 2. Process Bato folder to treat each series as a main category
        bato_path = os.path.join(DOWNLOAD_DIRECTORY, 'Bato')
        if 'Bato' in all_dirs_in_root and os.path.isdir(bato_path):
            series_folders = [d for d in os.listdir(bato_path) if os.path.isdir(os.path.join(bato_path, d))]
            display_folders.extend(series_folders) # Add each series name as a display category
            
            for series_name in series_folders:
                galleries[series_name] = [] # Use the series name as the key
                series_path_full = os.path.join(bato_path, series_name)
                chapter_folders = [d for d in os.listdir(series_path_full) if os.path.isdir(os.path.join(series_path_full, d))]
                for chapter_name in chapter_folders:
                    gallery_item = {
                        'name': chapter_name, # The card title will be the chapter name
                        'path': os.path.join('Bato', series_name, chapter_name).replace('\\', '/')
                    }
                    galleries[series_name].append(gallery_item)
        
        # Sort all folders alphabetically for a clean look
        display_folders.sort()

    except FileNotFoundError:
        display_folders = []

    # --- Thumbnail and Sorting Logic (Applied to the new structure) ---
    for category_name in display_folders:
        for gallery_item in galleries.get(category_name, []):
            thumbnail_url = None
            full_gallery_dir = os.path.join(DOWNLOAD_DIRECTORY, gallery_item['path'])
            try:
                first_image_file = next(
                    (f for f in sorted(os.listdir(full_gallery_dir)) if f.lower().endswith(image_extensions)),
                    None
                )
                if first_image_file:
                    thumb_relative_path = os.path.join(gallery_item['path'], first_image_file).replace('\\', '/')
                    thumbnail_url = url_for('serve_downloaded_file', filepath=thumb_relative_path)
            except (FileNotFoundError, StopIteration):
                pass
            gallery_item['thumbnail_url'] = thumbnail_url

    for category_name in display_folders:
        if category_name in galleries:
            # --- UPDATED: Use the natural sort key for sorting ---
            galleries[category_name] = sorted(galleries[category_name], key=natural_sort_key)
    
    return render_template('gallery.html', galleries=galleries, site_folders=display_folders, cache_buster=int(time.time()))

@app.route('/scrape', methods=['POST'])
def start_scrape():
    if job_status['is_running']: return "A job is already in progress.", 400
    site = request.form.get('site')
    mode = request.form.get('mode')
    worker_kwargs = {}
    try:
        if mode == 'direct':
            urls_list = [line.strip() for line in request.form.get('urls_or_codes', '').splitlines() if line.strip()]
            if not urls_list: return "Please enter at least one URL or code.", 400
            worker_kwargs['urls_or_codes'] = urls_list
            if site == 'bato':
                worker_kwargs['chapter_selection'] = request.form.get('chapter_selection', '')
        elif mode == 'tags':
            if site == 'bato': return f"Tag search is not supported for {site}.", 400
            tags_list = [t.strip() for t in request.form.get('tags', '').split(",") if t.strip()]
            if not tags_list: return "Please enter at least one tag.", 400
            limit = 1
            if site != 'rule34':
                limit = int(request.form.get('limit', 1))
            worker_kwargs['tags'] = tags_list
            worker_kwargs['limit'] = limit
        else: return "Invalid mode selected.", 400
    except Exception as e: return f"An error occurred: {e}", 500
    
    thread = threading.Thread(target=scraper_worker, args=(site, mode), kwargs=worker_kwargs)
    thread.start()
    return redirect(url_for('results'))
    
@app.route('/delete', methods=['POST'])
def delete_gallery():
    gallery_path = request.form.get('path')
    if not gallery_path: return "Error: No path provided.", 400
    full_path = os.path.join(DOWNLOAD_DIRECTORY, gallery_path)
    if not os.path.abspath(full_path).startswith(os.path.abspath(DOWNLOAD_DIRECTORY)): return "Error: Invalid path.", 403
    try:
        if os.path.exists(full_path) and os.path.isdir(full_path): shutil.rmtree(full_path)
    except Exception as e: return f"Error deleting gallery: {e}", 500
    return redirect(url_for('gallery'))

@app.route('/results')
def results():
    return render_template('results.html', cache_buster=int(time.time()))

@app.route('/status')
def status():
    return jsonify(job_status)

# MODIFICATION: Added the /cancel route
@app.route('/cancel', methods=['POST'])
def cancel_scrape():
    global job_status
    if job_status['is_running']:
        job_status['is_cancelled'] = True
        print("LOG: [CANCEL] Cancellation request received by server.")
    return jsonify({'status': 'cancellation_requested'})

@app.route('/view/<path:gallery_path>')
def view_gallery(gallery_path):
    full_gallery_path = os.path.join(DOWNLOAD_DIRECTORY, gallery_path)
    if not os.path.isdir(full_gallery_path):
        return "Gallery not found.", 404

    gallery_name = os.path.basename(gallery_path)
    image_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
    video_extensions = ('.mp4', '.webm', '.ogg')
    media_files = []

    files_to_process = []
    for root, _, files in os.walk(full_gallery_path):
        for f in files:
            relative_path = os.path.relpath(os.path.join(root, f), DOWNLOAD_DIRECTORY)
            files_to_process.append(relative_path.replace('\\', '/'))

    for file_path in sorted(files_to_process):
        file_url = url_for('serve_downloaded_file', filepath=file_path)
        if file_path.lower().endswith(image_extensions):
            media_files.append({'type': 'image', 'url': file_url})
        elif file_path.lower().endswith(video_extensions):
            media_files.append({'type': 'video', 'url': file_url})

    view_mode = request.args.get('mode', 'manga')
    # Render a different template based on the view_mode
    if view_mode == 'manhwa':
        return render_template('manhwaview.html', gallery_name=gallery_name, media_files=media_files)
    else: # Default to manga view
        return render_template('viewer.html', gallery_name=gallery_name, media_files=media_files)


@app.route('/downloads/<path:filepath>')
def serve_downloaded_file(filepath):
    return send_from_directory(DOWNLOAD_DIRECTORY, filepath)

if __name__ == '__main__':
    app.run(debug=True)