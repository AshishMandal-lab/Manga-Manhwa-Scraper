import os
import re
import time
import requests
import cloudscraper
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urlparse, urlunparse

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

MAX_THREADS = 16
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"}

def safe_folder_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " _-").strip() or "Gallery"

def download_image(args):
    idx, img_url, output_dir, scraper = args
    try:
        r = scraper.get(img_url, headers=HEADERS, stream=True, timeout=20)
        r.raise_for_status()
        ext = img_url.split(".")[-1].split("?")[0]
        if len(ext) > 4:
            ext = 'jpg'
        filepath = os.path.join(output_dir, f"{idx:04d}.{ext}")
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(1024):
                f.write(chunk)
        print(f"[{idx}] ✅ Saved {filepath}")
        return True
    except Exception as e:
        print(f"[{idx}] ❌ Failed {img_url}: {e}")
        return False

# -------------------- Rule34 (Maintained from previous version) --------------------
MAX_DOWNLOAD_WORKERS_R34 = 15

def get_clean_media_url_r34(driver, post_url):
    original_window = driver.current_window_handle
    raw_url = None
    try:
        driver.switch_to.new_window('tab')
        driver.get(post_url)
        try:
            link_element = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, "Original image")))
            raw_url = link_element.get_attribute('href')
        except (NoSuchElementException, TimeoutException):
            try:
                media_element = driver.find_element(By.CSS_SELECTOR, "#image, #gelcomVideoPlayer > source")
                raw_url = media_element.get_attribute("src")
            except NoSuchElementException:
                print(f"Could not find a downloadable media element on page: {post_url}")
                raw_url = None
    finally:
        driver.close()
        driver.switch_to.window(original_window)

    if not raw_url: return None
    
    parsed = urlparse(raw_url)
    clean_path = '/' + parsed.path.lstrip('/')
    if "/samples/" in clean_path and "sample_" in clean_path:
        clean_path = clean_path.replace("/samples/", "/images/").replace("sample_", "")
    hostname = parsed.netloc
    if hostname == 'rule34.xxx': hostname = 'wimg.rule34.xxx'
    return urlunparse((parsed.scheme, hostname, clean_path, parsed.params, parsed.query, parsed.fragment))

def download_file_r34(media_url, filepath, headers):
    try:
        r = requests.get(media_url, headers=headers, stream=True, allow_redirects=True, timeout=30)
        r.raise_for_status()
        with open(filepath, "wb") as f: f.write(r.content)
        print(f"Downloaded: {os.path.basename(filepath)}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Failed to download {os.path.basename(filepath)}: {e}")
        return False

def scraper_producer_r34(driver, download_queue, total_posts, found_files_tracker, status_dict):
    post_counter = 0
    page_num = 1
    total_pages_approx = (total_posts // 42) + 1 if total_posts > 0 else 1

    while True:
        if status_dict and status_dict.get('is_cancelled'):
            print("LOG: [RULE34] Cancellation detected in producer. Stopping page scraping.")
            break
        try:
            print(f"\nScraping page {page_num}...")
            if status_dict:
                status_dict['progress'] = min(10 + int((page_num / total_pages_approx) * 85), 95)

            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "span.thumb a")))
            post_urls_on_page = [link.get_attribute("href") for link in driver.find_elements(By.CSS_SELECTOR, "span.thumb a")]
            if not post_urls_on_page: break

            for post_url in post_urls_on_page:
                post_counter += 1
                final_media_url = get_clean_media_url_r34(driver, post_url)
                if final_media_url:
                    found_files_tracker[0] += 1
                    download_queue.put((final_media_url, post_counter))
                else:
                    print(f"[{post_counter}] ⚠️ Could not find a valid media URL for post: {post_url}")

            next_button = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//a[@alt='next']")))
            next_button.click()
            page_num += 1
            time.sleep(1)
        except (NoSuchElementException, TimeoutException):
            print("\nScraping finished. No more pages found.")
            break
        except Exception as e:
            print(f"\nAn unexpected error in the producer: {e}")
            break
    
    for _ in range(MAX_DOWNLOAD_WORKERS_R34):
        download_queue.put(None)

def downloader_consumer_r34(download_queue, download_dir, headers, total_posts, successful_downloads_tracker, status_dict):
    filename_padding = len(str(total_posts)) if total_posts > 0 else 5
    while True:
        task = download_queue.get()
        if task is None: break
        
        if status_dict and status_dict.get('is_cancelled'):
            print("LOG: [RULE34] Consumer cancelling before new download.")
            download_queue.task_done()
            break

        media_url, post_index = task
        
        original_filename = os.path.basename(urlparse(media_url).path)
        sequence_filename = f"{str(post_index).zfill(filename_padding)}_{original_filename}"
        filepath = os.path.join(download_dir, sequence_filename)
        
        if download_file_r34(media_url, filepath, headers):
            successful_downloads_tracker[0] += 1
            
        download_queue.task_done()

def scrape_rule34(tags, status_dict=None, driver_setup_func=None):
    if not driver_setup_func: raise ValueError("A valid driver setup function must be provided.")
    SITE_FOLDER = "Rule34"
    url_tags = "+".join(quote_plus(tag) for tag in tags)
    folder_name = "_".join(tag.replace("/", "_") for tag in tags)
    download_dir = os.path.join(SITE_FOLDER, folder_name)
    os.makedirs(download_dir, exist_ok=True)
    
    driver = driver_setup_func()
    try:
        driver.get(f"https://rule34.xxx/index.php?page=post&s=list&tags={url_tags}")
        try:
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "span.thumb a")))
        except TimeoutException:
            print(f"No posts found for tags: {' '.join(tags)}")
            return None

        total_posts = 0
        try:
            match = re.search(r'of ([\d,]+)', driver.find_element(By.ID, "stats").text)
            if match: total_posts = int(match.group(1).replace(",", ""))
        except (NoSuchElementException, ValueError): print("Could not determine total number of posts.")
        if status_dict: status_dict['progress'] = 10

        download_queue = queue.Queue(maxsize=MAX_DOWNLOAD_WORKERS_R34 * 2)
        headers = { "User-Agent": "Mozilla/5.0", "Referer": "https://rule34.xxx/" }
        found_files_tracker = [0]
        successful_downloads_tracker = [0] 

        scraper_thread = threading.Thread(target=scraper_producer_r34, args=(driver, download_queue, total_posts, found_files_tracker, status_dict))
        scraper_thread.start()

        with ThreadPoolExecutor(max_workers=MAX_DOWNLOAD_WORKERS_R34) as executor:
            for _ in range(MAX_DOWNLOAD_WORKERS_R34):
                executor.submit(downloader_consumer_r34, download_queue, download_dir, headers, total_posts, successful_downloads_tracker, status_dict)
        
        print("\nAll download tasks have been completed. Finalizing...")
        
        if successful_downloads_tracker[0] == 0:
            print("\n❌ CRITICAL: Scraper found files but failed to download any of them.")
            return None
        else:
            print(f"\n✅ Download process finished. Successfully downloaded {successful_downloads_tracker[0]} files.")
            return {'name': folder_name, 'path': download_dir.replace('\\', '/')}
    finally:
        driver.quit()

# -------------------- E-Hentai (Updated Logic from test.py) --------------------
def search_ehentai_urls_by_tags(tags, limit, driver_setup_func=None):
    if not driver_setup_func: raise ValueError("A valid driver setup function must be provided.")
    query = " ".join(tags)
    base_search_url = f"https://e-hentai.org/?f_search={quote_plus(query)}"
    print(f"🔍 Searching E-Hentai for: '{query}' using Selenium.")

    driver = driver_setup_func()
    try:
        driver.get("https://e-hentai.org/")
        driver.add_cookie({'name': 'nw', 'value': '1'})
        driver.add_cookie({'name': 'sl', 'value': 'dm_2'})
        
        found_urls = []
        page = 0
        link_selector = ".itg a[href*='/g/']"

        while len(found_urls) < limit:
            current_url = f"{base_search_url}&page={page}"
            print(f"Scraping search results page: {current_url}")
            driver.get(current_url)
            try:
                WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CLASS_NAME, "itg")))
                WebDriverWait(driver, 10).until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, link_selector)))
                print("✅ Page content and gallery links loaded.")
            except Exception:
                print("❌ Timed out waiting for page content or gallery links.")
                break

            links = driver.find_elements(By.CSS_SELECTOR, link_selector)
            if not links:
                print("No more galleries found on this page. Stopping search.")
                break

            for link in links:
                if len(found_urls) < limit:
                    url = link.get_attribute("href")
                    if url and url not in found_urls:
                        found_urls.append(url)
                else:
                    break
            page += 1
            if not driver.find_elements(By.ID, "dnext"):
                 break
        return found_urls[:limit]
    finally:
        driver.quit()

def scrape_ehentai(base_url, folder=None, status_dict=None):
    ## DIRECT COPY of test.py logic, adapted for Flask App
    SITE_FOLDER = "E-Hentai"
    scraper = cloudscraper.create_scraper()
    cookies = {'nw': '1'}
    
    r = scraper.get(base_url, headers=HEADERS, cookies=cookies)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    title_tag = soup.select_one("#gn") or soup.select_one(".pretty")
    title = title_tag.text.strip() if title_tag else "E-Hentai_Gallery"
    gallery_folder = safe_folder_name(title)
    full_path = os.path.join(SITE_FOLDER, gallery_folder)
    os.makedirs(full_path, exist_ok=True)
    print(f"Downloading gallery '{title}' to folder '{full_path}'")
    if status_dict: status_dict['progress'] = 2

    seen_urls = set()
    page = 0
    img_count = 1

    # First, discover all images to calculate total for progress bar
    all_thumb_urls = []
    print("Discovering all images...")
    while True:
        url = f"{base_url}?p={page}" if page > 0 else base_url
        r = scraper.get(url, headers=HEADERS, cookies=cookies)
        if not r.ok: break
        soup = BeautifulSoup(r.text, "html.parser")
        thumbs = [a["href"] for a in soup.find_all("a", href=True) if "/s/" in a["href"]]
        new_thumbs = [t for t in thumbs if t not in seen_urls]
        if not new_thumbs: break
        all_thumb_urls.extend(new_thumbs)
        seen_urls.update(new_thumbs)
        page += 1
    
    total_images = len(all_thumb_urls)
    print(f"Found {total_images} total images. Starting download...")
    if status_dict: status_dict['progress'] = 10
    
    completed_downloads = 0
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = []
        for thumb_url in all_thumb_urls:
            if status_dict and status_dict.get('is_cancelled'):
                print("LOG: [E-HENTAI] Cancellation detected. Halting job submission.")
                break
            try:
                img_page = scraper.get(thumb_url, headers=HEADERS, cookies=cookies)
                img_page.raise_for_status()
                img_soup = BeautifulSoup(img_page.text, "html.parser")
                img_tag = img_soup.select_one("#img")
                if img_tag:
                    job = (img_count, img_tag["src"], full_path, scraper)
                    futures.append(executor.submit(download_image, job))
                    img_count += 1
            except Exception as e:
                print(f"❌ Failed to process thumbnail {thumb_url}: {e}")
        
        for f in as_completed(futures):
            f.result()
            completed_downloads += 1
            if status_dict and total_images > 0:
                progress = 10 + int((completed_downloads / total_images) * 90)
                status_dict['progress'] = progress

    return {'name': title, 'path': full_path.replace('\\', '/')}

# -------------------- NHentai (Updated Logic) --------------------
NH_HEADERS = {"Referer": "https://nhentai.net/"}
nh_scraper = cloudscraper.create_scraper()

def download_image_nh(img_url, filename, index):
    if img_url.startswith("//"): img_url = "https:" + img_url
    for attempt in range(5):
        try:
            r = nh_scraper.get(img_url, headers=NH_HEADERS, stream=True, timeout=20)
            r.raise_for_status()
            with open(filename, "wb") as f:
                for chunk in r.iter_content(1024): f.write(chunk)
            print(f"[{index}] ✅ Saved {filename}")
            return True
        except Exception as e:
            print(f"[{index}] ❌ Error {e}, retrying ({attempt+1}/5)...")
            time.sleep(3 + attempt * 2)
    print(f"[{index}] ❌ Failed after retries")
    return False

def scrape_nhentai(code, folder=None, status_dict=None):
    SITE_FOLDER = "NHentai"
    api_url = f"https://nhentai.net/api/gallery/{code}"
    try:
        r = nh_scraper.get(api_url, headers=NH_HEADERS)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"LOG: [NHENTAI] API call FAILED for code {code}. Error: {e}")
        return None

    title = data.get("title", {}).get("english") or f"nhentai_{code}"
    gallery_folder = safe_folder_name(title)
    full_path = os.path.join(SITE_FOLDER, gallery_folder)
    os.makedirs(full_path, exist_ok=True)
    if status_dict: status_dict['progress'] = 10

    media_id = data.get("media_id")
    images = data.get("images", {}).get("pages", [])
    if not media_id or not images:
        print(f"LOG: [NHENTAI] FAILED to find 'media_id' or 'pages' in JSON for code {code}.")
        return None

    total_pages = len(images)
    print(f"LOG: [NHENTAI] Found {total_pages} pages for '{title}'. Starting download.")
    
    jobs = []
    ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
    for i, img in enumerate(images, 1):
        ext = ext_map.get(img["t"], "jpg")
        filename = os.path.join(full_path, f"{i:04d}.{ext}")
        img_url = f"https://i.nhentai.net/galleries/{media_id}/{i}.{ext}"
        jobs.append((img_url, filename, i))

    completed_downloads = 0
    with ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        futures = []
        for job in jobs:
            if status_dict and status_dict.get('is_cancelled'):
                print("LOG: [NHENTAI] Cancellation detected. Halting job submission.")
                break
            futures.append(executor.submit(download_image_nh, *job))
        for future in as_completed(futures):
            future.result()
            completed_downloads += 1
            if status_dict and total_pages > 0:
                progress = 10 + int((completed_downloads / total_pages) * 90)
                status_dict['progress'] = progress
    
    return {'name': title, 'path': full_path.replace('\\', '/')}

def search_nhentai_codes_by_tags(tags, limit):
    query = " ".join([f'"{tag}"' for tag in tags])
    print(f"Searching NHentai for tags: {query}")
    found_codes = []
    page = 1
    while len(found_codes) < limit:
        search_url = f"https://nhentai.net/search/?q={quote_plus(query)}&page={page}"
        try:
            r = nh_scraper.get(search_url, headers=NH_HEADERS)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            gallery_links = soup.select("div.container a.cover[href^='/g/']")
            if not gallery_links:
                print("No more galleries found on this page. Stopping search.")
                break
            for link in gallery_links:
                if len(found_codes) >= limit: break
                href = link.get('href')
                code = href.strip('/').split('/')[-1]
                if code.isdigit() and code not in found_codes:
                    found_codes.append(code)
            page += 1
            time.sleep(0.5)
        except Exception as e:
            print(f"❌ An error occurred during NHentai search: {e}")
            break
    return found_codes[:limit]