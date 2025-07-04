import os

# --- General Settings ---
TARGET_URLS = [
    "https://www.mosdac.gov.in/"
    # Add more starting URLs if needed
]

OUTPUT_DIR = "output"
LOG_DIR = "logs"

# --- Crawl Behavior ---
CRAWL_DEPTH = 3  # Max depth to crawl from TARGET_URLS (0 for only target, 1 for target and its direct links, etc.)
MAX_PAGES_TO_CRAWL = None  # Set a number (e.g., 100) to limit the total pages crawled, or None for no limit.
CRAWL_DELAY_SECONDS = 0.5 # Delay between requests *per worker*. Adjust for politeness.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Domain Whitelist: Only crawl links within these domains.
# Example: If TARGET_URLS include 'example.com' and you only want links from 'example.com'
# and 'sub.example.com', list them here. If empty, all domains are allowed.
DOMAIN_WHITELIST = [
    "www.mosdac.gov.in",
    # Add other allowed domains if necessary
]

# --- Concurrency Settings ---
MAX_CONCURRENT_WORKERS = 5 # Number of threads to use for concurrent scraping.
                          # Start with 5-10, increase if your network/server allows, decrease if you get blocked.

# --- Delta Crawling Settings ---
ENABLE_DELTA_CRAWLING = True # Set to True to enable intelligent re-crawling based on changes.
CACHE_DB_PATH = os.path.join("output", "crawled_urls.db") # SQLite database to store crawled URL metadata.
CHANGED_FILES_LOG_PATH = os.path.join("output", "changed_files.json") # Log of newly crawled or modified files.

# --- Error Handling & Re-check Intervals ---
# For URLs that returned a 404 (Not Found) or 410 (Gone), don't re-check them for this many days.
# Set to 0 or None to always re-check 404s.
FOUR_OH_FOUR_RECHECK_INTERVAL_DAYS = 7 # Re-check 404s after 7 days

# --- Dynamic Content Loading (Selenium) ---
ENABLE_DYNAMIC_CONTENT_LOADING = False # Set to True if pages require JavaScript rendering.
                                      # WARNING: This significantly slows down crawling and increases resource usage.
                                      # Each worker needs its own Selenium driver if enabled with multithreading.
                                      # Set MAX_CONCURRENT_WORKERS much lower (e.g., 1 or 2) if True.
SELENIUM_BROWSER = "chrome"           # 'chrome' or 'firefox'
SELENIUM_HEADLESS = True               # Run browser in headless mode (no UI)
SELENIUM_WAIT_TIME = 5                 # Seconds to wait for dynamic content to load

# --- Language Filtering Settings ---
# Set to True to skip crawling pages not in the PREFERRED_LANGUAGES list.
SKIP_UNSUPPORTED_LANGUAGES = True 
# List of ISO 639-1 language codes (e.g., 'en' for English, 'hi' for Hindi).
# If SKIP_UNSUPPORTED_LANGUAGES is True, only pages with these detected languages will be processed.
PREFERRED_LANGUAGES = ['en'] #