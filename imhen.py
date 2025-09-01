import os
import re
import requests
import concurrent.futures
from concurrent.futures import as_completed
from seleniumwire import webdriver as webdriver_wire
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Helper function to create safe folder names
def safe_folder_name(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " _-").strip() or "Gallery"

def download_image_imhentai(url, save_path, headers, image_num, total_images):
    """Downloads a single image for ImHentai and handles errors."""
    if os.path.exists(save_path):
        print(f"[{image_num}/{total_images}] ⏩ Skipping already downloaded file.")
        return True
    
    try:
        with requests.Session() as session:
            response = session.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            with open(save_path, 'wb') as f:
                f.write(response.content)
            print(f"[{image_num}/{total_images}] ✅ Downloaded {os.path.basename(save_path)}")
            return True
    except requests.exceptions.RequestException as e:
        print(f"[{image_num}/{total_images}] ❌ Failed to download {url}. Error: {e}")
        return False

def scrape_imhentai(gallery_url, status_dict=None, driver_setup_func=None):
    """
    Fully automates the ImHentai gallery download process using multi-threading.
    """
    SITE_FOLDER = "ImHentai"
    if not driver_setup_func:
        raise ValueError("A valid selenium-wire driver setup function must be provided.")

    total_images = 0
    base_image_path = None
    driver = driver_setup_func()
    
    try:
        # Part 1: Gather information
        if status_dict: status_dict['progress'] = 2
        print(f"🔗 Navigating to gallery page: {gallery_url}")
        driver.get(gallery_url)

        print("⏳ Reading page to find total image count...")
        page_count_element = WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Pages:')]"))
        )
        total_images = int(re.search(r'\d+', page_count_element.text).group())
        print(f"✅ Found total images: {total_images}")

        print("📡 Intercepting network traffic to find the image path...")
        image_request = driver.wait_for_request(
            pat=r'https://m\d+\.imhentai\.xxx/.*?\.(webp|jpg|png)',
            timeout=60
        )
        base_image_path = os.path.dirname(image_request.url) + '/'
        print(f"✅ Discovered base image path: {base_image_path}")

    except Exception as e:
        print(f"❌ CRITICAL ERROR during discovery phase: {e}")
        return None # Stop this specific gallery
    finally:
        driver.quit()
        print("🚪 Browser closed. Proceeding to download.")

    if status_dict and status_dict.get('is_cancelled'):
        print("LOG: [IMHENTAI] Cancellation detected before download phase.")
        return None

    # Part 2: High-speed multi-threaded download
    folder_name_raw = gallery_url.strip('/').split('/')[-1]
    folder_name = safe_folder_name(folder_name_raw)
    download_dir = os.path.join(SITE_FOLDER, folder_name)
    os.makedirs(download_dir, exist_ok=True)
    if status_dict: status_dict['progress'] = 10
    
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': gallery_url}
    extensions_to_try = ['.webp', '.jpg', '.png']
    completed_downloads = 0
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for i in range(1, total_images + 1):
            if status_dict and status_dict.get('is_cancelled'):
                print("LOG: [IMHENTAI] Cancellation detected during job submission.")
                break

            found_image = False
            for ext in extensions_to_try:
                current_url = f"{base_image_path}{i}{ext}"
                save_path = os.path.join(download_dir, f"{i:04d}{ext}")
                
                try:
                    # Use a quick HEAD request to check if the URL is valid before submitting
                    if requests.head(current_url, headers=headers, timeout=5).status_code == 200:
                        job = (current_url, save_path, headers, i, total_images)
                        futures.append(executor.submit(download_image_imhentai, *job))
                        found_image = True
                        break 
                except requests.exceptions.RequestException:
                    continue
            
            if not found_image:
                print(f"[{i}/{total_images}] ❌ Could not find image {i} with any tested extension.")
        
        for future in as_completed(futures):
            if future.result():
                completed_downloads += 1
            if status_dict and total_images > 0:
                progress = 10 + int((completed_downloads / total_images) * 90)
                status_dict['progress'] = progress

    print(f"\n✅ Finished downloading gallery: {folder_name}")
    return {'name': folder_name, 'path': download_dir.replace('\\', '/')}