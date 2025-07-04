import os
import uuid
import json
import logging
import base64
import sqlite3
import mimetypes
import hashlib
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
import time
import multiprocessing
from urllib.parse import urlparse, unquote

# --- Configuration ---
# IMPORTANT: This should be the absolute path to your crawler's ACTUAL 'output/' directory.
INPUT_ROOT_DIR = "C:/Users/kashy/Documents/Code/Projects/ai_chatbot/dynamic_web_crawler/layer1/output"
# Directory where processed JSON files will be saved. Output will be structured by language.
OUTPUT_DIR = "processed"
LOG_FILE = "pipeline.log" # Log file for pipeline activities
CRAWLED_DB_PATH = os.path.join(INPUT_ROOT_DIR, "crawled_urls.db")
CHANGED_FILES_JSON_PATH = os.path.join(INPUT_ROOT_DIR, "changed_files.json")

# Language filtering for HTML files is completely removed for now.
PREFERRED_LANGUAGES = []

# --- Advanced Filtering Configuration (RELAXED) ---
# Files with these extensions will be skipped immediately as they typically don't contain primary content.
SKIP_COMMON_NON_CONTENT_EXTENSIONS = [".css", ".js", ".txt", ".json", ".png", ".jpg", ".jpeg", ".gif", ".ico"]
# Files smaller than this size (in bytes) will be skipped. Set to 0 to process all files regardless of size.
MIN_FILE_SIZE_BYTES = 0
# After text extraction and cleaning, if the text length is below this, the document is considered unimportant.
# Set to 0 to process all files regardless of cleaned text length.
MIN_CLEANED_TEXT_LENGTH = 0

# --- Logging Setup ---
logging.basicConfig(level=logging.DEBUG, # Keep DEBUG for verbose output
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler(LOG_FILE),
                        logging.StreamHandler()
                    ])

# --- Library Import Status Check ---
_library_status = {
    "fitz": False, "docx": False, "pandas": False, "beautifulsoup4": False,
    "spacy": False, "geopandas": False, "shapely": False,
    "xml_etree": True
}

# --- External Library Imports (with graceful handling for missing libraries) ---
try:
    import fitz
    _library_status["fitz"] = True
except ImportError: logging.warning("PyMuPDF (fitz) not found. PDF parsing will be skipped.")

try:
    from docx import Document as DocxDocument
    _library_status["docx"] = True
except ImportError: logging.warning("python-docx not found. DOCX parsing will be skipped.")

try:
    import pandas as pd
    _library_status["pandas"] = True
except ImportError: logging.warning("pandas not found. XLSX/CSV parsing will be skipped.")

try:
    from bs4 import BeautifulSoup
    _library_status["beautifulsoup4"] = True
except ImportError: logging.warning("BeautifulSoup4 not found. HTML parsing will be skipped.")

nlp = None
try:
    import spacy
    _library_status["spacy"] = True
except ImportError: logging.warning("spaCy not found. NLP preprocessing will be skipped.")

try:
    import geopandas as gpd
    from shapely.geometry import mapping
    _library_status["geopandas"] = True
    _library_status["shapely"] = True
except ImportError: logging.warning("geopandas or shapely not found. GeoJSON parsing will be skipped.")


# --- NLP Utilities ---
class NLPProcessor:
    def __init__(self):
        global nlp
        if nlp is None and _library_status["spacy"]:
            try:
                nlp = spacy.load("en_core_web_sm")
                logging.info(f"spaCy 'en_core_web_sm' model loaded successfully in process {os.getpid()}.")
            except OSError:
                nlp = None
                logging.warning(f"spaCy 'en_core_web_sm' model not found in process {os.getpid()}. Run 'python -m spacy download en_core_web_sm'. NLP preprocessing will be skipped.")
        if nlp is None:
            logging.warning(f"spaCy NLP model not loaded in process {os.getpid()}. NLP processing will be unavailable.")

    def process_text(self, text):
        global nlp
        if nlp is None or not text: return {"sentences": [], "tokens": [], "lemmas": [], "entities": []}
        doc = nlp(text)
        sentences = [sent.text for sent in doc.sents]
        tokens = [token.text for token in doc]
        lemmas = [token.lemma_ for token in doc if not token.is_punct and not token.is_space]
        entities = [{"text": ent.text, "label": ent.label_} for ent in doc.ents]
        return {"sentences": sentences, "tokens": tokens, "lemmas": lemmas, "entities": entities}

# --- Base Parser ---
class BaseParser(ABC):
    def __init__(self, file_path):
        self.file_path = file_path; self.file_name = os.path.basename(file_path); self.doc_id = str(uuid.uuid4())
        self.metadata = {}; self.cleaned_text = ""; self.geotags = []
        self.file_type = "unknown"; self.extracted_tables = []; self.extracted_links = []
    @abstractmethod
    def _extract_text(self): pass
    def _extract_metadata(self):
        self.metadata["file_name"] = self.file_name; self.metadata["file_size_bytes"] = os.path.getsize(self.file_path)
        self.metadata["file_last_modified"] = datetime.fromtimestamp(os.path.getmtime(self.file_path)).isoformat()
        self.metadata["file_creation_time"] = datetime.fromtimestamp(os.path.getctime(self.file_path)).isoformat()
        self.metadata["relative_path"] = os.path.relpath(self.file_path, start=INPUT_ROOT_DIR)
    def _clean_text(self, text):
        if not text: return ""; text = ' '.join(text.split()); return text.strip()
    def parse(self):
        try:
            self._extract_metadata(); raw_text = self._extract_text(); self.cleaned_text = self._clean_text(raw_text)
            if MIN_CLEANED_TEXT_LENGTH > 0 and len(self.cleaned_text) < MIN_CLEANED_TEXT_LENGTH:
                logging.info(f"Skipping {self.file_name} due to insufficient cleaned text length ({len(self.cleaned_text)} < {MIN_CLEANED_TEXT_LENGTH}).")
                return None
            logging.info(f"Successfully extracted content from {self.file_name}")
            return {
                "doc_id": self.doc_id, "source_file_path": self.file_path, "file_type": self.file_type,
                "cleaned_text": self.cleaned_text, "metadata": self.metadata, "geotags": self.geotags,
                "extracted_tables": self.extracted_tables, "extracted_links": self.extracted_links
            }
        except ImportError as ie: logging.warning(f"Skipping parsing {self.file_name}: {ie}"); return None
        except Exception as e: logging.error(f"Error parsing {self.file_name}: {e}", exc_info=True); return None

# --- Specific Parser Implementations ---
class PdfParser(BaseParser):
    def __init__(self, file_path): super().__init__(file_path); self.file_type = "pdf"
    def _extract_text(self):
        if not _library_status["fitz"]: raise ImportError("PyMuPDF (fitz) is not installed.")
        text = ""; doc = fitz.open(self.file_path)
        for page_num in range(doc.page_count): text += doc.load_page(page_num).get_text()
        doc.close(); return text

class DocxParser(BaseParser):
    def __init__(self, file_path): super().__init__(file_path); self.file_type = "docx"
    def _extract_text(self):
        if not _library_status["docx"]: raise ImportError("python-docx is not installed.")
        text = ""; doc = DocxDocument(self.file_path)
        for para in doc.paragraphs: text += para.text + "\n"
        return text

class XlsxParser(BaseParser):
    def __init__(self, file_path): super().__init__(file_path); self.file_type = "xlsx"
    def _extract_text(self):
        if not _library_status["pandas"]: raise ImportError("pandas is not installed.")
        text = ""; tables_data = []
        xls = pd.ExcelFile(self.file_path)
        for sheet_name in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet_name)
            text += f"--- Sheet: {sheet_name} ---\n{df.to_string(index=False)}\n\n"
            tables_data.append({"sheet_name": sheet_name, "data": df.values.tolist(), "headers": df.columns.tolist()})
        self.extracted_tables = tables_data; return text

class CsvParser(BaseParser):
    def __init__(self, file_path): super().__init__(file_path); self.file_type = "csv"
    def _extract_text(self):
        if not _library_status["pandas"]: raise ImportError("pandas is not installed.")
        text = ""; tables_data = []
        df = pd.read_csv(self.file_path)
        text += df.to_string(index=False) + "\n"
        tables_data.append({"sheet_name": "default", "data": df.values.tolist(), "headers": df.columns.tolist()})
        self.extracted_tables = tables_data; return text

class HtmlParser(BaseParser):
    def __init__(self, file_path): super().__init__(file_path); self.file_type = "html"
    def _extract_text(self):
        if not _library_status["beautifulsoup4"]: raise ImportError("BeautifulSoup4 is not installed.")
        text = ""; links = []; tables_data = []
        with open(self.file_path, 'r', encoding='utf-8') as f:
            soup = BeautifulSoup(f, 'html.parser')
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href'].strip();
                if href: links.append({"text": a_tag.get_text(strip=True), "href": href})
            self.extracted_links = links

            for table_tag in soup.find_all('table'):
                table_rows = []; headers = []
                thead = table_tag.find('thead');
                if thead: headers = [th.get_text(strip=True) for th in thead.find_all('th')]
                for row in table_tag.find_all('tr'):
                    cols = [col.get_text(strip=True) for col in row.find_all(['td', 'th'])];
                    if cols: table_rows.append(cols)
                if table_rows:
                    if not headers and table_rows: headers = table_rows.pop(0)
                    tables_data.append({"headers": headers, "data": table_rows})
            self.extracted_tables = tables_data

            for script in soup(["script", "style"]): script.extract()
            text = soup.get_text(separator=' ')
            for meta_tag in soup.find_all('meta'):
                name = meta_tag.get('name') or meta_tag.get('property'); content = meta_tag.get('content');
                if name and content: self.metadata[f"html_meta_{name.replace('.', '_').replace('-', '_')}"] = content
        return text

class GeoJsonParser(BaseParser):
    def __init__(self, file_path): super().__init__(file_path); self.file_type = "geojson"
    def _extract_text(self):
        if not (_library_status["geopandas"] and _library_status["shapely"]): raise ImportError("geopandas or shapely is not installed.")
        text_summary = []
        gdf = gpd.read_file(self.file_path)
        self.metadata["crs"] = str(gdf.crs) if gdf.crs else "unknown"
        self.metadata["geometry_types"] = list(gdf.geometry.geom_type.unique())
        for idx, row in gdf.iterrows():
            geo_info = {"type": row.geometry.geom_type, "coordinates": mapping(row.geometry)['coordinates']}
            self.geotags.append(geo_info)
            for prop_key, prop_val in row.drop('geometry').items():
                safe_prop_key = f"feature_prop_{prop_key}_{idx}".replace('.', '_').replace('-', '_')
                self.metadata[safe_prop_key] = str(prop_val)
            text_summary.append(f"Geometry Type: {row.geometry.geom_type}, Properties: {row.drop('geometry').to_dict()}")
        return gdf.to_string()

class XmlParser(BaseParser):
    """Parser for XML files, using xml.etree.ElementTree to extract all text content."""
    def __init__(self, file_path):
        super().__init__(file_path)
        self.file_type = "xml"

    def _extract_text(self):
        if not _library_status["xml_etree"]:
            raise ImportError("xml.etree.ElementTree is not available (should be built-in).")
        text_content_parts = []
        try:
            tree = ET.parse(self.file_path)
            root = tree.getroot()
            
            # Iterate through all elements in the tree
            for element in root.iter():
                # Extract text directly within the element
                if element.text and element.text.strip():
                    text_content_parts.append(element.text.strip())
                
                # Extract text that follows an element but is still within its parent (tail text)
                if element.tail and element.tail.strip():
                    text_content_parts.append(element.tail.strip())
                    
        except Exception as e:
            raise Exception(f"Failed to extract text from XML: {e}")
        
        # Join all extracted parts with a space and clean up
        return " ".join(text_content_parts)


# --- Worker function for multiprocessing pool ---
def _process_single_file(file_path, url_metadata, parser_registry_config, preferred_languages):
    """
    Processes a single file. This function is designed to be run by a multiprocessing worker.
    It returns a tuple: (success_status, file_path, original_url, output_path_if_success).
    """
    nlp_processor = NLPProcessor()

    local_parser_registry = {}
    for ext, parser_name in parser_registry_config.items():
        parser_class = globals().get(parser_name)
        if parser_class:
            local_parser_registry[ext] = parser_class
        else:
            logging.error(f"Worker {os.getpid()}: Parser class '{parser_name}' not found for extension '.{ext}'.")
            return (False, file_path, url_metadata.get("url"), None)

    _, file_ext = os.path.splitext(file_path)
    file_ext = file_ext[1:].lower()
    original_url = url_metadata.get("url", "N/A")

    logging.debug(f"Worker {os.getpid()}: Attempting to process file: {file_path}, Ext: .{file_ext}, URL: {original_url}")

    if f".{file_ext}" in SKIP_COMMON_NON_CONTENT_EXTENSIONS:
        logging.info(f"Worker {os.getpid()}: Skipping {file_path} due to common non-content extension '{file_ext}'.")
        return (False, file_path, original_url, None)

    try:
        if MIN_FILE_SIZE_BYTES > 0 and os.path.getsize(file_path) < MIN_FILE_SIZE_BYTES:
            logging.info(f"Worker {os.getpid()}: Skipping {file_path} due to small file size ({os.path.getsize(file_path)} bytes < {MIN_FILE_SIZE_BYTES}).")
            return (False, file_path, original_url, None)
    except FileNotFoundError:
        logging.warning(f"Worker {os.getpid()}: File not found during processing (after initial check): {file_path}. Skipping.")
        return (False, file_path, original_url, None)

    parser_class = local_parser_registry.get(file_ext)

    if parser_class:
        # Language filtering for HTML is now only applied if PREFERRED_LANGUAGES is not empty
        if file_ext == "html" and preferred_languages: # preferred_languages is now [] so this block is skipped
            detected_language = url_metadata.get('language', 'unknown')
            if detected_language is None:
                detected_language = "unknown"
            detected_language_lower = str(detected_language).lower()

            logging.debug(f"Worker {os.getpid()}: HTML language check for {file_path}. Detected: '{detected_language_lower}', Preferred: {preferred_languages}")

            if detected_language_lower not in preferred_languages:
                logging.info(f"Worker {os.getpid()}: Skipping HTML file {file_path} due to unsupported language: '{detected_language_lower}'. (URL: {original_url})")
                return (False, file_path, original_url, None)
        try:
            logging.info(f"Worker {os.getpid()}: Attempting to parse {file_path} with {parser_class.__name__}.")
            parser_instance = parser_class(file_path)
            parsed_data = parser_instance.parse()
            if parsed_data:
                final_doc = {
                    "doc_id": parsed_data["doc_id"], "original_url": url_metadata.get("url"),
                    "file_type": parsed_data["file_type"], "cleaned_text": parsed_data["cleaned_text"],
                    "geotags": parsed_data["geotags"], "extracted_tables": parsed_data["extracted_tables"],
                    "extracted_links": parsed_data["extracted_links"],
                    "metadata": {**url_metadata, **parsed_data["metadata"], "processing_timestamp": datetime.now(timezone.utc).isoformat()}
                }
                if 'url' in final_doc['metadata']: del final_doc['metadata']['url']

                if final_doc["file_type"] in ["html", "pdf", "docx", "xlsx", "csv", "xml"]:
                    nlp_results = nlp_processor.process_text(final_doc["cleaned_text"]); final_doc.update(nlp_results)
                else:
                    final_doc.update({"sentences": [], "tokens": [], "lemmas": [], "entities": []})

                lang_from_metadata = final_doc["metadata"].get("language")
                output_language = "unknown"
                if lang_from_metadata is not None:
                    output_language = str(lang_from_metadata).lower()
                
                language_output_dir = os.path.join(OUTPUT_DIR, output_language)
                os.makedirs(language_output_dir, exist_ok=True)

                output_filename = f"{final_doc['doc_id']}.json"; output_path = os.path.join(language_output_dir, output_filename)
                with open(output_path, 'w', encoding='utf-8') as f: json.dump(final_doc, f, ensure_ascii=False, indent=2)
                logging.info(f"Worker {os.getpid()}: Saved processed data to {output_path}"); return (True, file_path, original_url, output_path)
            else:
                logging.info(f"Worker {os.getpid()}: Skipping {file_path} as it did not yield important content or parsing failed. (URL: {original_url})")
                return (False, file_path, original_url, None)
        except Exception as e:
            logging.error(f"Worker {os.getpid()}: Critical error during processing {file_path}: {e}", exc_info=True)
            return (False, file_path, original_url, None)
    else:
        logging.warning(f"Worker {os.getpid()}: Skipping file: {file_path}. No parser registered for .{file_ext}")
        return (False, file_path, original_url, None)


# --- Pipeline Manager ---

class PipelineManager:
    def __init__(self, input_root_dir, output_dir, preferred_languages=None):
        self.input_root_dir = input_root_dir
        self.output_dir = output_dir
        self.crawled_db_path = os.path.join(input_root_dir, "crawled_urls.db")
        self.changed_files_json_path = os.path.join(input_root_dir, "changed_files.json")
        self.preferred_languages = [lang.lower() for lang in preferred_languages] if preferred_languages else []

        self._parser_registry = {}
        self._setup_directories()

    def _setup_directories(self):
        logging.critical(f"PipelineManager: Checking INPUT_ROOT_DIR: {self.input_root_dir}")
        if not os.path.exists(self.input_root_dir):
            logging.critical(f"CRITICAL ERROR: Input root directory does not exist: {self.input_root_dir}. Please ensure your crawler output is in this path.")
            sys.exit(1)
        if not os.path.isdir(self.input_root_dir):
            logging.critical(f"CRITICAL ERROR: Input root path is not a directory: {self.input_root_dir}.")
            sys.exit(1)
        if not os.access(self.input_root_dir, os.R_OK):
            logging.critical(f"CRITICAL ERROR: Input root directory is not readable: {self.input_root_dir}. Check file permissions.")
            sys.exit(1)

        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            logging.info(f"Created output directory: {self.output_dir}")

    def register_parser(self, file_extension, parser_class):
        if not issubclass(parser_class, BaseParser):
            logging.error(f"Error: {parser_class.__name__} is not a subclass of BaseParser. Not registering.")
            return
        self._parser_registry[file_extension.lower()] = parser_class.__name__
        logging.info(f"Registered parser for .{file_extension}: {parser_class.__name__}")
        logging.critical(f"DEBUG: Parser registry after registering .{file_extension}: {self._parser_registry}")


    def _load_all_urls_from_db(self):
        """Loads all URLs and their metadata from crawled_urls.db."""
        logging.info(f"Attempting to load all URLs from DB: {self.crawled_db_path}")
        if not os.path.exists(self.crawled_db_path):
            logging.error(f"Crawler database '{self.crawled_db_path}' not found. Cannot load URLs from DB.")
            return []
        
        urls_from_db = []
        try:
            conn = sqlite3.connect(self.crawled_db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM crawled_urls")
            rows = cursor.fetchall()
            conn.close()
            for row in rows:
                urls_from_db.append(dict(row))
            logging.info(f"Successfully loaded {len(urls_from_db)} URLs from '{self.crawled_db_path}'.")
            return urls_from_db
        except sqlite3.DatabaseError as e:
            logging.error(f"Database error when connecting to '{self.crawled_db_path}': {e}. This file might be corrupted or not a valid SQLite DB.")
            return []
        except Exception as e:
            logging.error(f"Error loading URLs from DB: {e}", exc_info=True)
            return []

    def _get_url_metadata(self, url):
        logging.debug(f"Fetching metadata for URL: {url} from DB: {self.crawled_db_path}")
        if not os.path.exists(self.crawled_db_path):
            logging.error(f"Crawler database '{self.crawled_db_path}' not found. Cannot fetch URL metadata.")
            return None
        try:
            conn = sqlite3.connect(self.crawled_db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM crawled_urls WHERE url = ?", (url,))
            row = cursor.fetchone()
            conn.close()
            if row:
                metadata = dict(row)
                logging.debug(f"Found metadata for URL: {url}. Content Type: {metadata.get('content_type')}, Language: {metadata.get('language')}")
                return metadata
            logging.warning(f"No metadata found in DB for URL: {url}")
            return None
        except sqlite3.DatabaseError as e:
            logging.error(f"Database error when connecting to '{self.crawled_db_path}': {e}. This file might be corrupted or not a valid SQLite DB.")
            return None
        except Exception as e:
            logging.error(f"Error fetching metadata for {url} from DB: {e}", exc_info=True)
            return None

    def _derive_file_path(self, url_metadata):
        url = url_metadata.get('url')
        md5_hash = url_metadata.get('md5_hash') # This will be ignored for HTML/XML but used for other types
        content_type = url_metadata.get('content_type', '').lower()

        logging.debug(f"Derive_file_path: Attempting to derive path for URL: {url}, MD5: {md5_hash}, Content-Type: {content_type}")

        if not url:
            logging.error(f"Derive_file_path: Cannot derive file path, URL missing for metadata: {url_metadata}"); return None

        parsed_url = urlparse(url)
        domain_name = parsed_url.netloc.replace(':', '_').replace('/', '_') # Clean domain for path
        domain_base_path = os.path.join(self.input_root_dir, domain_name)
        logging.debug(f"Derive_file_path: Domain base path: {domain_base_path}")

        derived_file_path = None

        # --- Strategy for HTML and XML files: Search within the domain's folder for any .html or .xml file ---
        if 'text/html' in content_type or 'text/xml' in content_type or 'application/xml' in content_type:
            target_extension = '.html' if 'text/html' in content_type else '.xml'
            logging.debug(f"Derive_file_path: {target_extension.upper()} content type detected. Searching for {target_extension} file in {domain_base_path}")
            
            # First, try to derive filename directly from URL path (e.g., isrocast.xml, index.html)
            path_segments_from_url = unquote(parsed_url.path.strip('/'))
            filename_from_url = ""
            if not path_segments_from_url: # Root URL
                filename_from_url = "index.html" if target_extension == '.html' else "" # XML might not have an index.xml
                # For XML, if it's a root URL like example.com/rss.xml, path_segments_from_url would be 'rss.xml'
                # If it's just example.com/, then path_segments_from_url is empty.
                # In that case, we need to know the actual filename (e.g., rss.xml).
                # Since we cannot infer it from the URL alone for XML, we'll rely on the os.walk below.
            else:
                last_segment = os.path.basename(path_segments_from_url)
                if not os.path.splitext(last_segment)[1]: # If no explicit extension in URL path
                    filename_from_url = f"{last_segment}{target_extension}"
                else: # URL path already has an extension
                    filename_from_url = last_segment

            if filename_from_url:
                direct_path_attempt = os.path.join(domain_base_path, filename_from_url)
                direct_path_attempt = os.path.normpath(direct_path_attempt) # Normalize path
                logging.debug(f"Derive_file_path: Direct filename attempt: {direct_path_attempt}")
                if os.path.exists(direct_path_attempt):
                    logging.critical(f"Derive_file_path: FOUND {target_extension.upper()} FILE (Direct URL-derived): {direct_path_attempt} for URL: {url}")
                    return direct_path_attempt
                else:
                    logging.debug(f"Derive_file_path: Direct filename attempt path does not exist.")

            # Fallback: Perform a limited os.walk within the domain's directory to find *any* .html or .xml file
            for root, _, files in os.walk(domain_base_path):
                for filename in files:
                    if filename.lower().endswith(target_extension):
                        found_path = os.path.join(root, filename)
                        logging.critical(f"Derive_file_path: FOUND {target_extension.upper()} FILE (Generic {target_extension} search): {found_path} for URL: {url}")
                        return found_path
            
            logging.warning(f"Derive_file_path: No {target_extension} file found within '{domain_base_path}' for URL: {url}. This URL will be skipped.")
            return None # If no HTML/XML file found for this URL in its domain folder


        # --- Strategy for NON-HTML/NON-XML files: Use MD5 hash from DB or derive from URL path ---
        else: # Not HTML or XML content type
            path_segments_from_url = unquote(parsed_url.path.strip('/'))
            
            filename = ""
            if md5_hash: # If MD5 hash is available, prioritize it for non-HTML/XML files
                filename = f"{md5_hash}{mimetypes.guess_extension(content_type) or ''}" # Append extension if guessed
                logging.debug(f"Derive_file_path: Non-HTML/XML, using MD5 hash: {filename}")
            elif path_segments_from_url: # If no MD5, use basename from URL path
                filename = os.path.basename(path_segments_from_url)
                logging.debug(f"Derive_file_path: Non-HTML/XML, using URL basename: {filename}")
            elif parsed_url.query: # If no path basename, use hash of query
                filename = hashlib.md5(parsed_url.query.encode()).hexdigest() + (mimetypes.guess_extension(content_type) or '.bin')
                logging.debug(f"Derive_file_path: Non-HTML/XML, empty path, using query hash: {filename}")
            else: # Still no filename, use a generic based on content type
                filename = f"unnamed{mimetypes.guess_extension(content_type) or '.bin'}"
                logging.debug(f"Derive_file_path: Non-HTML/XML, no filename, using generic: {filename}")

            # Construct the full derived path based on URL structure
            if path_segments_from_url and os.path.dirname(path_segments_from_url):
                derived_file_path = os.path.join(domain_base_path, os.path.dirname(path_segments_from_url), filename)
            else:
                derived_file_path = os.path.join(domain_base_path, filename)
            
            derived_file_path = os.path.normpath(derived_file_path) # Normalize path for consistency
            logging.debug(f"Derive_file_path: Non-HTML/XML derived path candidate: {derived_file_path}")

            if os.path.exists(derived_file_path):
                logging.info(f"Derive_file_path: Found local file for URL: {url} at {derived_file_path}")
                return derived_file_path
            else:
                logging.warning(f"Derive_file_path: Could not find local file for URL: {url}. Derived path tried: {derived_file_path}. Exists: {os.path.exists(derived_file_path) if derived_file_path else 'N/A'}")
                return None
        
        logging.warning(f"Derive_file_path: Failed to derive a valid file path for URL: {url}. Returning None.")
        return None # Return None if no path found after all attempts


    def run(self):
        logging.info(f"Starting pipeline processing in '{self.input_root_dir}'...")
        logging.info(f"Output will be saved to '{self.output_dir}'")
        logging.info("HTML and XML content will NOT be filtered by language in this run.")
        logging.info(f"Minimum file size for processing: {MIN_FILE_SIZE_BYTES} bytes (set to 0 for no size filtering).")
        logging.info(f"Minimum cleaned text length for processing: {MIN_CLEANED_TEXT_LENGTH} characters (set to 0 for no length filtering).")

        logging.critical(f"DEBUG: Final Parser Registry: {self._parser_registry}")
        if 'html' not in self._parser_registry:
            logging.critical("CRITICAL ERROR: 'html' parser is NOT registered! This is why HTML files are not processed.")
            sys.exit(1)
        if 'xml' not in self._parser_registry:
            logging.critical("CRITICAL ERROR: 'xml' parser is NOT registered! This is why XML files are not processed.")
            sys.exit(1)


        processed_count = 0; skipped_count = 0; failed_count = 0; total_files_identified = 0

        # --- Primary File Discovery Strategy: Iterate through all URLs in crawled_urls.db ---
        all_urls_metadata = self._load_all_urls_from_db()
        items_to_process_args = []

        if not all_urls_metadata:
            logging.critical("CRITICAL ERROR: No URLs loaded from crawled_urls.db. Cannot proceed with DB-driven processing.")
            logging.warning("Falling back to full directory scan as a last resort. This might process files not intended by the crawler.")
            # Fallback to full directory scan if DB load fails or is empty
            for dirpath, dirnames, filenames in os.walk(self.input_root_dir):
                for filename in filenames:
                    full_file_path = os.path.join(dirpath, filename)
                    _, file_ext = os.path.splitext(full_file_path)
                    file_ext = file_ext[1:].lower()

                    if file_ext not in self._parser_registry:
                        logging.debug(f"Skipping {full_file_path}: No parser registered for .{file_ext} (during fallback scan).")
                        continue
                    if f".{file_ext}" in SKIP_COMMON_NON_CONTENT_EXTENSIONS:
                        logging.info(f"Skipping {full_file_path} due to common non-content extension '{file_ext}' (during fallback scan).")
                        continue
                    try:
                        if MIN_FILE_SIZE_BYTES > 0 and os.path.getsize(full_file_path) < MIN_FILE_SIZE_BYTES:
                            logging.info(f"Skipping {full_file_path} due to small file size ({os.path.getsize(full_file_path)} bytes < {MIN_FILE_SIZE_BYTES}) (during fallback scan).")
                            continue
                    except FileNotFoundError:
                        logging.warning(f"File not found during fallback scan: {full_file_path}. Skipping.")
                        continue

                    # Reconstruct a basic URL and metadata for fallback processing
                    relative_path = os.path.relpath(full_file_path, start=self.input_root_dir)
                    url_parts = relative_path.split(os.sep, 1)
                    reconstructed_url = ""
                    if len(url_parts) > 1:
                        domain_segment = url_parts[0]
                        path_segment = url_parts[1].replace(os.sep, '/')
                        reconstructed_url = f"http://{domain_segment}/{path_segment}"
                    else:
                        reconstructed_url = f"http://localhost/{relative_path.replace(os.sep, '/')}"

                    guessed_mime_type = mimetypes.guess_type(full_file_path)[0] or "application/octet-stream"
                    url_metadata_for_processing = {
                        "url": reconstructed_url, "content_type": guessed_mime_type,
                        "language": "unknown", "md5_hash": ""
                    }
                    total_files_identified += 1
                    items_to_process_args.append((full_file_path, url_metadata_for_processing, self._parser_registry, self.preferred_languages))
                    logging.info(f"Identified file for processing (fallback scan): {full_file_path} (Reconstructed URL: {reconstructed_url})")

        else: # If URLs were successfully loaded from DB, use them
            logging.info(f"Iterating through {len(all_urls_metadata)} URLs from crawled_urls.db to identify files.")
            for i, url_meta in enumerate(all_urls_metadata):
                url = url_meta.get('url')
                if not url:
                    logging.error(f"Skipping DB entry {i+1}: URL missing in metadata: {url_meta}")
                    continue

                logging.debug(f"Main process: Processing DB entry {i+1}: URL: {url}, Content-Type: {url_meta.get('content_type')}, Language: {url_meta.get('language')}")

                file_path = self._derive_file_path(url_meta)
                
                if file_path and os.path.exists(file_path):
                    _, file_ext = os.path.splitext(file_path)
                    file_ext = file_ext[1:].lower()

                    if file_ext not in self._parser_registry:
                        logging.debug(f"Skipping {file_path}: No parser registered for .{file_ext} (from DB list).")
                        continue
                    if f".{file_ext}" in SKIP_COMMON_NON_CONTENT_EXTENSIONS:
                        logging.info(f"Skipping {file_path} due to common non-content extension '{file_ext}' (from DB list).")
                        continue
                    try:
                        if MIN_FILE_SIZE_BYTES > 0 and os.path.getsize(file_path) < MIN_FILE_SIZE_BYTES:
                            logging.info(f"Skipping {file_path} due to small file size ({os.path.getsize(file_path)} bytes < {MIN_FILE_SIZE_BYTES}) (from DB list).")
                            continue
                    except FileNotFoundError:
                        logging.warning(f"File not found during DB-driven processing: {file_path}. Skipping.")
                        continue

                    total_files_identified += 1
                    items_to_process_args.append((file_path, url_meta, self._parser_registry, self.preferred_languages))
                    logging.info(f"Identified file for processing (from DB): {file_path} (URL: {url}, Content-Type: {url_meta.get('content_type')}, Language: {url_meta.get('language')})")
                else:
                    logging.warning(f"Skipping URL {url}: Local file not found at derived path '{file_path}' (from DB list).")


        logging.info(f"Identified {len(items_to_process_args)} files to attempt processing.")

        num_processes = os.cpu_count() or 1
        logging.info(f"Starting multiprocessing pool with {num_processes} processes.")
        with multiprocessing.Pool(num_processes) as pool:
            results = pool.starmap(_process_single_file, items_to_process_args)

        for success, file_path, original_url, output_path in results:
            if success:
                processed_count += 1
            else:
                skipped_count += 1

        logging.info("-" * 50); logging.info(f"Pipeline processing finished.")
        logging.info(f"Total files identified for processing: {total_files_identified}")
        logging.info(f"Total files processed: {processed_count}")
        logging.info(f"Total files skipped or failed in workers: {skipped_count}")
        logging.info("-" * 50)

# --- Library Installation Check Function ---
def check_and_suggest_installations():
    missing_pylibs = []
    if not _library_status["fitz"]: missing_pylibs.append("PyMuPDF")
    if not _library_status["docx"]: missing_pylibs.append("python-docx")
    if not _library_status["pandas"]: missing_pylibs.append("pandas")
    if not _library_status["beautifulsoup4"]: missing_pylibs.append("beautifulsoup4")
    if not _library_status["spacy"]: missing_pylibs.append("spacy")
    if not _library_status["geopandas"] or not _library_status["shapely"]: missing_pylibs.append("geopandas shapely")

    if missing_pylibs:
        print("\n--- ATTENTION: Missing Python Libraries ---")
        print("The following Python libraries are required for full pipeline functionality:")
        print(f"Please install them using pip: pip install {' '.join(missing_pylibs)}")
        if "spacy" in missing_pylibs: print("Additionally, for spaCy, download the English model: python -m spacy download en_core_web_sm")
        print("-------------------------------------------\n")
    else: print("\nAll core Python libraries appear to be installed.")

# --- Main Execution Block ---
if __name__ == "__main__":
    multiprocessing.freeze_support()

    check_and_suggest_installations()
    
    pipeline = PipelineManager(
        input_root_dir=INPUT_ROOT_DIR,
        output_dir=OUTPUT_DIR,
        preferred_languages=PREFERRED_LANGUAGES
    )

    pipeline.register_parser("pdf", PdfParser)
    pipeline.register_parser("docx", DocxParser)
    pipeline.register_parser("xlsx", XlsxParser)
    pipeline.register_parser("csv", CsvParser)
    pipeline.register_parser("html", HtmlParser)
    pipeline.register_parser("geojson", GeoJsonParser)
    pipeline.register_parser("xml", XmlParser)

    pipeline.run()

    print(f"\nProcessing complete. Check the '{OUTPUT_DIR}' directory for output JSON files (organized by language) and '{LOG_FILE}' for detailed logs.")
    print("\nRemember to refer to the 'ATTENTION' messages above for any missing installations or API key configurations.")
