import sqlite3
import logging
from datetime import datetime
import threading

logger = logging.getLogger(__name__)

class CacheManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._initialize_db()

    def _get_db_connection(self):
        if not hasattr(self._local, 'conn'):
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.cursor = self._local.conn.cursor()
            logger.debug(f"Opened new SQLite connection for thread {threading.get_ident()}")
        return self._local.conn, self._local.cursor

    def _initialize_db(self):
        conn, cursor = self._get_db_connection()
        try:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS crawled_urls (
                    url TEXT PRIMARY KEY,
                    last_crawled TEXT,
                    md5_hash TEXT,
                    etag TEXT,
                    last_modified TEXT,
                    content_type TEXT,
                    http_status INTEGER,  -- NEW COLUMN
                    language TEXT       -- NEW COLUMN
                )
            ''')
            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Error creating table: {e}")

    def update_metadata(self, url: str, last_crawled: str, md5_hash: str | None, etag: str | None, last_modified: str | None, content_type: str | None, http_status: int, language: str | None = None):
        """
        Updates the metadata for a given URL in the cache.
        If the URL does not exist, a new entry is created.
        """
        conn, cursor = self._get_db_connection()
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO crawled_urls
                (url, last_crawled, md5_hash, etag, last_modified, content_type, http_status, language)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (url, last_crawled, md5_hash, etag, last_modified, content_type, http_status, language))
            conn.commit()
            logger.debug(f"Updated cache for {url} with status {http_status}")
        except sqlite3.Error as e:
            logger.error(f"Error updating metadata for {url}: {e}")
            conn.rollback()

    def get_metadata(self, url: str) -> dict | None:
        """
        Retrieves metadata for a given URL from the cache.
        Returns a dictionary of metadata including http_status and language, or None if not found.
        """
        conn, cursor = self._get_db_connection()
        try:
            cursor.execute('SELECT last_crawled, md5_hash, etag, last_modified, content_type, http_status, language FROM crawled_urls WHERE url = ?', (url,))
            row = cursor.fetchone()
            if row:
                return {
                    "last_crawled": row[0],
                    "md5_hash": row[1],
                    "etag": row[2],
                    "last_modified": row[3],
                    "content_type": row[4],
                    "http_status": row[5],
                    "language": row[6]
                }
            return None
        except sqlite3.Error as e:
            logger.error(f"Error retrieving metadata for {url}: {e}")
            return None

    def close(self):
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            logger.debug(f"Closed SQLite connection for thread {threading.get_ident()}")