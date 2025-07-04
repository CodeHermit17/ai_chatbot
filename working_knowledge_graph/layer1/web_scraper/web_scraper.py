import os
import logging
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import hashlib
from datetime import datetime
import mimetypes

# Conditional imports for Selenium
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.firefox.service import Service as FirefoxService
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    from webdriver_manager.chrome import ChromeDriverManager
    from webdriver_manager.firefox import GeckoDriverManager
    _SELENIUM_AVAILABLE = True
except ImportError:
    _SELENIUM_AVAILABLE = False
    logging.getLogger(__name__).warning("Selenium or webdriver-manager not installed. Dynamic content loading will be disabled.")

# Conditional import for language detection
try:
    from langdetect import detect, DetectorFactory
    # Set seed for reproducibility in langdetect (optional, but good for consistent behavior)
    DetectorFactory.seed = 0
    _LANGDETECT_AVAILABLE = True
except ImportError:
    _LANGDETECT_AVAILABLE = False
    logging.getLogger(__name__).warning("langdetect not installed. Language detection will be disabled.")

from web_scraper.download_manager import DownloadManager
from config import (
    USER_AGENT, OUTPUT_DIR, ENABLE_DYNAMIC_CONTENT_LOADING, SELENIUM_BROWSER,
    SELENIUM_HEADLESS, SELENIUM_WAIT_TIME, DOMAIN_WHITELIST,
    SKIP_UNSUPPORTED_LANGUAGES, PREFERRED_LANGUAGES
)
from web_scraper.utils import get_domain, compute_md5, is_downloadable_asset

logger = logging.getLogger(__name__)

class WebScraper:
    def __init__(self, output_dir, cache_manager, enable_dynamic_content_loading=False):
        self.output_dir = output_dir
        self.cache_manager = cache_manager
        self.enable_dynamic_content_loading = enable_dynamic_content_loading
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})
        self.driver = None # Selenium WebDriver instance

        if self.enable_dynamic_content_loading:
            if not _SELENIUM_AVAILABLE:
                logger.error("Selenium is enabled in config but dependencies are not installed. Disabling dynamic content loading.")
                self.enable_dynamic_content_loading = False
            else:
                self._initialize_webdriver()

    def _initialize_webdriver(self):
        """Initializes the Selenium WebDriver."""
        try:
            if SELENIUM_BROWSER == "chrome":
                options = ChromeOptions()
                if SELENIUM_HEADLESS:
                    options.add_argument("--headless")
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument(f"user-agent={USER_AGENT}")
                service = ChromeService(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=options)
            elif SELENIUM_BROWSER == "firefox":
                options = FirefoxOptions()
                if SELENIUM_HEADLESS:
                    options.add_argument("--headless")
                options.add_argument(f"user-agent={USER_AGENT}")
                service = FirefoxService(GeckoDriverManager().install())
                self.driver = webdriver.Firefox(service=service, options=options)
            else:
                raise ValueError(f"Unsupported browser: {SELENIUM_BROWSER}. Choose 'chrome' or 'firefox'.")
            logger.info(f"Initialized Selenium WebDriver for {SELENIUM_BROWSER} (headless: {SELENIUM_HEADLESS}).")
        except Exception as e:
            logger.error(f"Failed to initialize Selenium WebDriver: {e}. Dynamic content loading will be disabled.", exc_info=True)
            self.driver = None
            self.enable_dynamic_content_loading = False # Disable if init fails

    def _close_webdriver(self):
        """Closes the Selenium WebDriver if it's open."""
        if self.driver:
            self.driver.quit()
            self.driver = None
            logger.info("Selenium WebDriver closed.")

    def scrape_page(self, url, download_manager):
        """
        Scrapes a single URL, extracts links, saves HTML, and returns metadata.
        Args:
            url (str): The URL to scrape.
            download_manager (DownloadManager): Instance to handle asset downloads.
        Returns:
            tuple: (list of extracted links, MD5 hash of content, ETag, Last-Modified, list of asset_info, http_status_code, detected_language)
        """
        extracted_links = []
        content_md5 = None
        etag = None
        last_modified = None
        asset_info_list = [] # List of (asset_url, status, type) for logging changes
        http_status_code = None
        content_text = "" # To store content for MD5 and language detection
        detected_language = "unknown" # Default language

        try:
            cached_metadata = self.cache_manager.get_metadata(url)
            headers = {'User-Agent': USER_AGENT}

            # Add If-None-Match and If-Modified-Since headers for delta crawling
            if cached_metadata:
                etag = cached_metadata.get('etag')
                last_modified = cached_metadata.get('last_modified')
                if etag:
                    headers['If-None-Match'] = etag
                if last_modified:
                    headers['If-Modified-Since'] = last_modified

            if self.enable_dynamic_content_loading and self.driver:
                logger.info(f"Scraping dynamically: {url}")
                self.driver.get(url)
                self.driver.implicitly_wait(SELENIUM_WAIT_TIME)
                content_text = self.driver.page_source
                http_status_code = 200 # Assume 200 for Selenium unless navigated to error page
                # Selenium doesn't directly give ETag/Last-Modified from response headers easily
                # We'll rely on content MD5 for change detection for dynamic content
                response_headers = {} # Simulate empty headers for now
            else:
                logger.info(f"Scraping statically: {url}")
                response = self.session.get(url, headers=headers, timeout=10)
                http_status_code = response.status_code
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
                content_text = response.text
                etag = response.headers.get('ETag')
                last_modified = response.headers.get('Last-Modified')
                response_headers = response.headers # Keep actual response headers

            # Language Detection
            if _LANGDETECT_AVAILABLE:
                try:
                    detected_language = detect(content_text)
                    if SKIP_UNSUPPORTED_LANGUAGES and detected_language not in PREFERRED_LANGUAGES:
                        logger.info(f"Skipping {url}: Detected language '{detected_language}' not in preferred list {PREFERRED_LANGUAGES}.")
                        return [], None, None, None, [], http_status_code, detected_language # Skip further processing
                except Exception as e:
                    logger.warning(f"Could not detect language for {url}: {e}. Proceeding assuming 'unknown'.")
            
            # If 304 Not Modified, skip processing content but record success
            if http_status_code == 304:
                logger.info(f"Page not modified (304): {url}. Using cached content for links.")
                # Retrieve old MD5 and language from cache for consistency
                if cached_metadata:
                    content_md5 = cached_metadata.get('md5_hash')
                    detected_language = cached_metadata.get('language', 'unknown')
                # For 304, we don't re-parse links from network. If old links needed, they'd be from cache.
                # For simplicity here, we'll return an empty list of links and rely on subsequent crawling
                # to pick up any new links from other pages.
                return [], content_md5, etag, last_modified, [], http_status_code, detected_language


            content_md5 = compute_md5(content_text.encode('utf-8'))
            
            # Only save and parse if content is new or modified compared to cache
            is_modified = True
            if cached_metadata and cached_metadata.get('md5_hash') == content_md5:
                is_modified = False
                logger.debug(f"Content unchanged for {url}. MD5: {content_md5}")

            if is_modified:
                # Save HTML content
                parsed_url = urlparse(url)
                domain_dir = os.path.join(self.output_dir, parsed_url.netloc)
                os.makedirs(domain_dir, exist_ok=True)
                
                # Use MD5 hash of the URL as the filename to avoid issues with long/invalid chars in URLs
                file_name = f"{compute_md5(url.encode('utf-8'))}.html"
                file_path = os.path.join(domain_dir, file_name)
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content_text)
                logger.info(f"Saved HTML for {url} to {file_path}")

                # Parse HTML for links and assets
                soup = BeautifulSoup(content_text, 'html.parser')

                # Extract all links (a href)
                for a_tag in soup.find_all('a', href=True):
                    link = urljoin(url, a_tag['href'])
                    extracted_links.append(link)

                # Extract assets (img src, link href for CSS, script src for JS, etc.)
                for tag in soup.find_all(['img', 'script', 'link'], src=True):
                    asset_url = urljoin(url, tag['src'])
                    if is_downloadable_asset(asset_url):
                        asset_status, asset_type, asset_http_status = download_manager.download_file(asset_url)
                        asset_info_list.append((asset_url, asset_status, asset_type))
                
                # Handle <link> tags that might point to assets (e.g., stylesheets, favicons)
                for link_tag in soup.find_all('link', href=True):
                    if link_tag.get('rel') and ('stylesheet' in link_tag['rel'] or 'icon' in link_tag['rel']):
                        asset_url = urljoin(url, link_tag['href'])
                        if is_downloadable_asset(asset_url):
                            asset_status, asset_type, asset_http_status = download_manager.download_file(asset_url)
                            asset_info_list.append((asset_url, asset_status, asset_type))
            else: # If not modified, use cached links (if available) or return empty for efficiency
                logger.info(f"Page content for {url} is unchanged based on MD5. Not re-parsing.")
                # You might choose to re-parse from cached content or simply return no new links
                # For now, returning empty links to rely on queue to find new links via other paths
                
        except requests.exceptions.HTTPError as e:
            logger.warning(f"HTTP Error while scraping {url}: {e.response.status_code} - {e.response.reason}")
            http_status_code = e.response.status_code
            # Do not compute MD5 or extract links/assets
            # Mark MD5 as None for non-successful responses
            content_md5 = None
            logger.warning(f"Did not save content for {url} due to HTTP status {http_status_code}")

        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP/Network error while scraping {url}: {e}")
            if e.response is not None:
                http_status_code = e.response.status_code # Capture status code even on error
            else:
                http_status_code = 0 # Indicate a connection/request error without HTTP status
        except Exception as e:
            logger.error(f"An unexpected error occurred while scraping {url}: {e}", exc_info=True)
            http_status_code = 0 # Generic error, no specific HTTP status

        # Always update cache with the latest status, even if it's an error
        self.cache_manager.update_metadata(
            url=url,
            last_crawled=datetime.now().isoformat(),
            md5_hash=content_md5, # Will be None if not 2xx or skipped by language filter
            etag=etag,
            last_modified=last_modified,
            content_type='text/html' if content_md5 is not None else 'N/A', # Set content_type based on content availability
            http_status=http_status_code,
            language=detected_language # Store detected language
        )

        return extracted_links, content_md5, etag, last_modified, asset_info_list, http_status_code, detected_language

    def close_browser(self):
        """Closes the Selenium browser instance if it was opened."""
        self._close_webdriver()