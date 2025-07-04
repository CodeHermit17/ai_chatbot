import os
import logging
import time
from collections import deque
from urllib.parse import urlparse, urljoin
from urllib import robotparser
import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# --- Import configurations ---
from config import (
    TARGET_URLS, OUTPUT_DIR, LOG_DIR, CRAWL_DEPTH, DOMAIN_WHITELIST,
    CRAWL_DELAY_SECONDS, MAX_PAGES_TO_CRAWL, USER_AGENT,
    ENABLE_DELTA_CRAWLING, CACHE_DB_PATH, CHANGED_FILES_LOG_PATH,
    MAX_CONCURRENT_WORKERS, ENABLE_DYNAMIC_CONTENT_LOADING,
    FOUR_OH_FOUR_RECHECK_INTERVAL_DAYS
)

# --- Import WebScraper components ---
from web_scraper.web_scraper import WebScraper
from web_scraper.cache_manager import CacheManager
from web_scraper.download_manager import DownloadManager
from web_scraper.utils import get_domain, normalize_url, is_downloadable_asset # Corrected: Removed is_asset_url

# --- Setup Logging ---
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

log_file_path = os.path.join(LOG_DIR, f"crawler_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Global Variables and Locks ---
crawl_queue = deque() # Stores tuples of (url, depth)
visited_urls = set()  # Stores normalized URLs that have been added to the queue or visited
crawl_queue_lock = threading.Lock()
visited_urls_lock = threading.Lock()
crawled_pages_count = 0
changed_files_log = [] # To store details of new/modified files

# --- Robots.txt Parser (Global) ---
rp = robotparser.RobotFileParser()
if TARGET_URLS:
    try:
        # Assuming all target URLs are on the same domain for robots.txt parsing simplicity
        # or that each target URL's domain's robots.txt will be fetched dynamically if needed.
        # For a simple setup, we'll try to fetch for the first TARGET_URL's domain.
        base_url = TARGET_URLS[0] if isinstance(TARGET_URLS, list) else TARGET_URLS
        rp_url = urljoin(base_url, '/robots.txt')
        rp.set_url(rp_url)
        rp.read()
        logger.info(f"Loaded robots.txt from: {rp_url}")
    except Exception as e:
        logger.warning(f"Could not load robots.txt for {base_url}: {e}. Proceeding without robots.txt rules.")


# --- Helper function for adding URLs to the queue and visited set ---
def add_url_to_queue(url, depth, cache_manager): # cache_manager passed for 404 check
    # Normalize URL before any checks or adding to visited set
    parsed_url = urlparse(url)
    
    # Normalize the path component to handle trailing slashes consistently
    normalized_path = parsed_url.path.strip('/')
    if not normalized_path:
        normalized_path = '/' # Ensures base path is always '/'
    
    # Reconstruct URL with normalized path and sorted query parameters
    normalized_url_base = urljoin(url, normalized_path)
    
    normalized_url = normalized_url_base
    if parsed_url.query:
        query_params = sorted(parsed_url.query.split('&'))
        normalized_url += '?' + '&'.join(query_params)
    
    # --- DEPTH CHECK: IMMEDIATELY FILTER OUT URLs EXCEEDING CRAWL_DEPTH ---
    if CRAWL_DEPTH is not None and depth > CRAWL_DEPTH:
        logger.debug(f"Skipping {normalized_url}: Exceeds max crawl depth ({CRAWL_DEPTH}) before queuing.")
        return

    # Check against DOMAIN_WHITELIST
    if DOMAIN_WHITELIST and get_domain(normalized_url) not in DOMAIN_WHITELIST:
        logger.debug(f"Skipping {normalized_url}: Not in allowed domains (whitelist).")
        return

    # Check robots.txt rules
    if not rp.can_fetch(USER_AGENT, normalized_url):
        logger.debug(f"Skipping {normalized_url}: Disallowed by robots.txt.")
        return

    # Check cache for previous errors like 404 (only if delta crawling is enabled)
    if ENABLE_DELTA_CRAWLING and FOUR_OH_FOUR_RECHECK_INTERVAL_DAYS is not None:
        cached_metadata = cache_manager.get_metadata(normalized_url)
        if cached_metadata:
            status = cached_metadata.get('http_status')
            last_crawled_str = cached_metadata.get('last_crawled')

            if status in [404, 410]: # Check for 404 or 410
                if last_crawled_str:
                    try:
                        last_crawled_dt = datetime.fromisoformat(last_crawled_str)
                        if datetime.now() - last_crawled_dt < timedelta(days=FOUR_OH_FOUR_RECHECK_INTERVAL_DAYS):
                            logger.info(f"Skipping {normalized_url} due to previous {status} status (re-check in {FOUR_OH_FOUR_RECHECK_INTERVAL_DAYS} days).")
                            return # Do not add to queue
                        else:
                            logger.info(f"Re-queuing {normalized_url} as its {status} status is older than {FOUR_OH_FOUR_RECHECK_INTERVAL_DAYS} days.")
                    except ValueError:
                        logger.warning(f"Invalid last_crawled timestamp in cache for {normalized_url}: {last_crawled_str}. Will re-crawl.")
                else: # If no last_crawled timestamp for a 404/410, assume recent and skip for now to avoid immediate re-attempt
                    logger.info(f"Skipping {normalized_url} due to previous {status} status (no last_crawled time, assuming recent).")
                    return # Do not add to queue


    with visited_urls_lock:
        if normalized_url in visited_urls:
            logger.debug(f"Skipping already visited link: {normalized_url}")
            return
        visited_urls.add(normalized_url) # Add to visited set immediately

    # Check for invalid schemes, empty netloc, or fragment-only links
    if parsed_url.scheme not in ('http', 'https') or \
       not parsed_url.netloc or \
       parsed_url.fragment: # Check for fragment-only links like #section1
        logger.debug(f"Skipping invalid scheme or fragment-only link: {url}")
        return # Do not add to queue if it's not a valid crawlable URL

    # Check for mailto, tel, etc. (using original URL as they don't parse well into standard http/s links)
    if any(url.startswith(scheme) for scheme in ['mailto:', 'tel:', 'ftp:', 'file:']):
        logger.debug(f"Skipping non-HTTP/HTTPS scheme: {url}")
        return

    with crawl_queue_lock:
        crawl_queue.append((normalized_url, depth))
        logger.debug(f"Added {normalized_url} (depth {depth}) to queue.")

# --- Worker function for ThreadPoolExecutor ---
def worker(url, depth, web_scraper, download_manager, cache_manager):
    global crawled_pages_count, changed_files_log

    page_links = []
    content_md5 = None
    http_status_code = None # Initialize http_status_code
    
    # Delta crawling check: check if content changed
    is_html_page = not is_downloadable_asset(url) # This heuristic helps decide if we should use scrape_page or download_file
    
    if is_html_page:
        # For HTML pages, scrape_page handles delta crawling internally
        extracted_links, content_md5, etag, last_modified, asset_info_list, http_status_code, language_detected = web_scraper.scrape_page(url, download_manager)
        page_links.extend(extracted_links)
        
        # Log asset changes
        for asset_url, asset_status, asset_type in asset_info_list:
            if asset_status in ["NEWLY_DOWNLOADED", "MODIFIED"]:
                changed_files_log.append({
                    "url": asset_url,
                    "status": asset_status,
                    "type": asset_type,
                    "timestamp": datetime.now().isoformat()
                })
        
        # Check if the HTML content itself was new or modified
        cached_metadata = cache_manager.get_metadata(url)
        if cached_metadata and cached_metadata.get('md5_hash') != content_md5:
            # If MD5 is different, or it's a new entry, log it
            changed_files_log.append({
                "url": url,
                "status": "NEWLY_DOWNLOADED" if not cached_metadata else "MODIFIED",
                "type": "html",
                "timestamp": datetime.now().isoformat(),
                "language": language_detected # Include detected language
            })
    else:
        # For assets, download_file handles delta crawling internally
        download_status, file_type, http_status_code = download_manager.download_file(url)
        if download_status in ["NEWLY_DOWNLOADED", "MODIFIED"]:
            changed_files_log.append({
                "url": url,
                "status": download_status,
                "type": file_type,
                "timestamp": datetime.now().isoformat()
            })
    
    # Increment crawled pages count if successful HTML scrape or asset download
    if http_status_code and 200 <= http_status_code < 300:
        with threading.Lock(): # Protect shared counter
            global crawled_pages_count
            crawled_pages_count += 1
            logger.info(f"Crawled: {url} (Depth: {depth}, Status: {http_status_code}). Total processed: {crawled_pages_count}")
            if MAX_PAGES_TO_CRAWL and crawled_pages_count >= MAX_PAGES_TO_CRAWL:
                logger.info(f"Max pages to crawl ({MAX_PAGES_TO_CRAWL}) reached. Stopping.")
                # Signal to stop processing by clearing queue and preventing new additions
                with crawl_queue_lock:
                    crawl_queue.clear()
    elif http_status_code:
        logger.warning(f"Failed/Skipped: {url} (Depth: {depth}, Status: {http_status_code})")
    else:
        logger.warning(f"Failed/Skipped: {url} (Depth: {depth}, No HTTP Status Recorded)")
    
    time.sleep(CRAWL_DELAY_SECONDS) # Be polite

    return page_links, depth + 1, http_status_code # Return extracted links and next depth


def main():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    # 1. Initialize Managers
    cache_manager = CacheManager(CACHE_DB_PATH)
    web_scraper = WebScraper(
        output_dir=OUTPUT_DIR,
        cache_manager=cache_manager,
        enable_dynamic_content_loading=ENABLE_DYNAMIC_CONTENT_LOADING
    )
    download_manager = DownloadManager(OUTPUT_DIR, cache_manager)

    # 2. Add initial URLs to queue
    for url in TARGET_URLS:
        add_url_to_queue(url, 0, cache_manager)

    # 3. Start crawling with ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORKERS) as executor:
        futures = set()
        
        while crawl_queue or futures:
            # Check if max pages reached, clear queue if so
            if MAX_PAGES_TO_CRAWL and crawled_pages_count >= MAX_PAGES_TO_CRAWL:
                with crawl_queue_lock:
                    if crawl_queue: # Clear only if not already empty
                        logger.info("Max pages reached, clearing crawl queue.")
                        crawl_queue.clear()
                # Also cancel any waiting futures if possible, though ThreadPoolExecutor doesn't easily allow
                # direct cancellation of submitted tasks before they start execution.
                # The worker function itself has a check for MAX_PAGES_TO_CRAWL to stop adding new links.
            
            # Submit new tasks if there's capacity
            while len(futures) < MAX_CONCURRENT_WORKERS and crawl_queue:
                with crawl_queue_lock:
                    if crawl_queue:
                        current_url, current_depth = crawl_queue.popleft()
                        logger.debug(f"Popped {current_url} (depth {current_depth}) from queue.")
                    else:
                        break # Queue became empty while acquiring lock
                
                # Check for max pages again before submitting
                if MAX_PAGES_TO_CRAWL and crawled_pages_count >= MAX_PAGES_TO_CRAWL:
                    logger.info("Max pages reached during submission, not submitting new tasks.")
                    break
                
                future = executor.submit(worker, current_url, current_depth, web_scraper, download_manager, cache_manager)
                futures.add(future)
            
            # Process completed futures
            for future in as_completed(futures):
                futures.remove(future)
                try:
                    new_links, next_depth, _ = future.result() # Discard HTTP status for link processing
                    for link in new_links:
                        add_url_to_queue(link, next_depth, cache_manager)
                except Exception as exc:
                    logger.error(f'Task generated an exception: {exc}')

            # Implement a small delay if the queue is empty but futures are still running
            if not crawl_queue and futures:
                time.sleep(0.5) # Wait a bit for currently running tasks to finish and potentially add new links

        logger.info("Crawl queue is empty and all tasks are completed.")
    
    # 5. Finalization
    web_scraper.close_browser()
    cache_manager.close()
    
    logger.info(f"Crawl finished. Total pages processed: {crawled_pages_count}")
    
    if changed_files_log:
        try:
            with open(CHANGED_FILES_LOG_PATH, 'w', encoding='utf-8') as f:
                json.dump(changed_files_log, f, indent=4, ensure_ascii=False)
            logger.info(f"Logged {len(changed_files_log)} new/modified files to {CHANGED_FILES_LOG_PATH}")
        except Exception as e:
            logger.error(f"Failed to write changed files log: {e}")
    else:
        logger.info("No new or modified files detected during this crawl.")

if __name__ == "__main__":
    main()