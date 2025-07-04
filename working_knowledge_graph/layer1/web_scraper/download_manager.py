import os
import logging
import requests
import hashlib
from urllib.parse import urlparse
import mimetypes
from datetime import datetime

from web_scraper.utils import compute_md5
from config import USER_AGENT # OUTPUT_DIR is imported via self.output_base_dir

logger = logging.getLogger(__name__)

class DownloadManager:
    def __init__(self, output_base_dir, cache_manager):
        self.output_base_dir = output_base_dir
        self.cache_manager = cache_manager
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT})

    def download_file(self, url):
        """
        Downloads a file (e.g., PDF, image) if it's new or modified.
        Saves it to a structured path within the output directory.
        Returns a tuple (status_string, file_type, http_status_code) for logging.
        """
        status = "SKIPPED"
        file_type = "UNKNOWN"
        http_status_code = None # Initialize http_status_code
        downloaded_md5 = None # Initialize MD5 for potential cache update

        # --- FIX: Initialize etag and last_modified ---
        etag = None
        last_modified = None
        # --- END FIX ---

        parsed_url = urlparse(url)
        if not parsed_url.netloc:
            logger.debug(f"Skipping download, invalid URL: {url}")
            return "INVALID_URL", file_type, 0 # 0 for invalid/non-HTTP status

        path_without_query = parsed_url.path.split('?')[0].split('#')[0]
        
        extension = os.path.splitext(path_without_query)[1].lower()
        if extension:
            mime_type = mimetypes.guess_type(path_without_query)[0]
        else:
            mime_type = mimetypes.guess_type(url)[0]
            extension = mimetypes.guess_extension(mime_type) if mime_type else '.bin'

        file_type = mime_type if mime_type else "application/octet-stream"

        path_segments = [seg for seg in parsed_url.path.split('/') if seg]
        
        if not path_segments or not os.path.splitext(path_segments[-1])[1]:
            # If no clear filename in path, use MD5 hash of URL as filename
            filename_base = hashlib.md5(url.encode()).hexdigest()
            filename = f"{filename_base}{extension}"
            # Save in a subdirectory under the domain, based on path segments if any
            save_path_dir = os.path.join(self.output_base_dir, parsed_url.netloc, *path_segments)
        else:
            # If there's a filename, use it and construct path based on preceding segments
            filename = path_segments[-1]
            save_path_dir = os.path.join(self.output_base_dir, parsed_url.netloc, *path_segments[:-1])

        os.makedirs(save_path_dir, exist_ok=True)
        file_path = os.path.join(save_path_dir, filename)

        # Handle potential filename collisions by appending a counter
        counter = 0
        original_file_path_base, original_file_path_ext = os.path.splitext(file_path)
        while os.path.exists(file_path):
            counter += 1
            file_path = f"{original_file_path_base}_{counter}{original_file_path_ext}"

        try:
            cached_metadata = self.cache_manager.get_metadata(url)
            is_modified = True # Assume modified until proven otherwise by 304 or content hash

            headers = {'User-Agent': USER_AGENT}
            if cached_metadata:
                etag = cached_metadata.get('etag')
                last_modified = cached_metadata.get('last_modified')
                if etag:
                    headers['If-None-Match'] = etag
                if last_modified:
                    headers['If-Modified-Since'] = last_modified

            response = self.session.get(url, headers=headers, stream=True, timeout=10)
            http_status_code = response.status_code

            if response.status_code == 304: # Not Modified
                status = "SKIPPED_NOT_MODIFIED"
                is_modified = False
                logger.debug(f"File not modified: {url}")
                downloaded_md5 = cached_metadata.get('md5_hash') # Retain old MD5
                # Update last_crawled time even if not modified
                self.cache_manager.update_metadata(
                    url=url,
                    last_crawled=datetime.now().isoformat(),
                    md5_hash=downloaded_md5,
                    etag=etag,
                    last_modified=last_modified,
                    content_type=file_type,
                    http_status=http_status_code
                )
                return status, file_type, http_status_code

            response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)

            # Extract ETag and Last-Modified from new response
            etag = response.headers.get('ETag')
            last_modified = response.headers.get('Last-Modified')

            total_size = 0
            hasher = hashlib.md5()
            with open(file_path + ".part", 'wb') as f: # Write to a temp file first
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk: # filter out keep-alive new chunks
                        f.write(chunk)
                        hasher.update(chunk)
                        total_size += len(chunk)
            
            downloaded_md5 = hasher.hexdigest()

            if cached_metadata and cached_metadata.get('md5_hash') == downloaded_md5:
                status = "SKIPPED_SAME_CONTENT"
                is_modified = False
                logger.debug(f"File content unchanged: {url}")
                os.remove(file_path + ".part") # Remove temp file
            else:
                os.rename(file_path + ".part", file_path) # Rename temp file to final name
                if not cached_metadata:
                    status = "NEWLY_DOWNLOADED"
                    logger.info(f"Downloaded new file: {url} to {file_path}. Size: {total_size} bytes")
                else:
                    status = "MODIFIED"
                    logger.info(f"Updated modified file: {url} to {file_path}. Size: {total_size} bytes")

            # Update cache after successful download or confirmed unchanged
            self.cache_manager.update_metadata(
                url=url,
                last_crawled=datetime.now().isoformat(),
                md5_hash=downloaded_md5,
                etag=etag,
                last_modified=last_modified,
                content_type=file_type,
                http_status=http_status_code
            )
            return status, file_type, http_status_code

        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP/Network error downloading {url}: {e}")
            status = "FAILED_HTTP_ERROR"
            if e.response is not None:
                http_status_code = e.response.status_code
            else:
                http_status_code = 0 # Indicate a connection/request error without HTTP status
        except Exception as e:
            logger.error(f"An unexpected error occurred downloading {url}: {e}", exc_info=True)
            status = "FAILED_GENERIC_ERROR"
            http_status_code = 0 # Generic error, no specific HTTP status
        finally:
            if os.path.exists(file_path + ".part"):
                os.remove(file_path + ".part") # Clean up partial downloads
            
            # Ensure metadata is always updated with the final status code
            self.cache_manager.update_metadata(
                url=url,
                last_crawled=datetime.now().isoformat(),
                md5_hash=downloaded_md5 if downloaded_md5 else (cached_metadata.get('md5_hash') if cached_metadata else None),
                etag=etag if etag else (cached_metadata.get('etag') if cached_metadata else None),
                last_modified=last_modified if last_modified else (cached_metadata.get('last_modified') if cached_metadata else None),
                content_type=file_type,
                http_status=http_status_code
            )
            return status, file_type, http_status_code