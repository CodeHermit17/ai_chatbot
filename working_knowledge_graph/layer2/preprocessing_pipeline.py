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
import time # Added for time.sleep for exponential backoff
import multiprocessing # Added for multiprocessing

# --- Configuration ---
# IMPORTANT: This should be the path to your crawler's ACTUAL 'output/' directory.
# Example: INPUT_ROOT_DIR = "C:/Users/kashy/Documents/Code/Projects/ai_chatbot/dynamic_web_crawler/layer1/output"
INPUT_ROOT_DIR = "C:/Users/kashy/Documents/Code/Projects/ai_chatbot/dynamic_web_crawler/layer1/output" # Default to 'output' in the current working directory
# Directory where processed JSON files will be saved. Output will be structured by language.
OUTPUT_DIR = "processed"
LOG_FILE = "pipeline.log" # Log file for pipeline activities
CRAWLED_DB_PATH = os.path.join(INPUT_ROOT_DIR, "crawled_urls.db")
CHANGED_FILES_JSON_PATH = os.path.join(INPUT_ROOT_DIR, "changed_files.json")

# Configure preferred languages for HTML content.
# Only HTML content detected in these languages will be fully processed.
# Set to None or empty list to process all languages.
PREFERRED_LANGUAGES = ["en"] # Example: English and Hindi

# --- Advanced Filtering Configuration (RELAXED) ---
# Files with these extensions will be skipped immediately as they typically don't contain primary content.
SKIP_COMMON_NON_CONTENT_EXTENSIONS = [".css", ".js", ".txt", ".json"]
# Files smaller than this size (in bytes) will be skipped. Set to 0 to process all files regardless of size.
MIN_FILE_SIZE_BYTES = 0
# After text extraction and cleaning, if the text length is below this, the document is considered unimportant.
# Set to 0 to process all files regardless of cleaned text length.
MIN_CLEANED_TEXT_LENGTH = 0
# For images, if either dimension (width or height) is below this, they might be skipped.
# Set to 0 to process all images regardless of dimension.
MIN_IMAGE_DIMENSION = 0 # pixels

# --- Logging Setup ---
# For multiprocessing, it's generally better to configure logging in each process
# or use a QueueHandler. For simplicity here, we'll let each process log,
# but be aware of potential interleaved console output.
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler(LOG_FILE),
                        logging.StreamHandler()
                    ])

# --- Library Import Status Check ---
_library_status = {
    "fitz": False, "docx": False, "pandas": False, "beautifulsoup4": False,
    "spacy": False, "geopandas": False, "shapely": False, "Pillow": False,
    "pytesseract": False, "requests": False, "tesseract_installed": False,
    "xml_etree": True # xml.etree.ElementTree is built-in
}

# --- External Library Imports (with graceful handling for missing libraries) ---
# These are imported globally but will be re-initialized/re-checked in each worker process
# if they are not picklable (like spaCy's nlp object).
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

# nlp is intentionally not loaded globally here as it's not picklable.
# It will be loaded within each worker process if needed.
nlp = None
try:
    import spacy
    _library_status["spacy"] = True
    # Do NOT load nlp model globally here. It will be loaded in NLPProcessor constructor.
except ImportError: logging.warning("spaCy not found. NLP preprocessing will be skipped.")

try:
    import geopandas as gpd
    from shapely.geometry import mapping
    _library_status["geopandas"] = True
    _library_status["shapely"] = True
except ImportError: logging.warning("geopandas or shapely not found. GeoJSON parsing will be skipped.")

try:
    from PIL import Image
    import pytesseract
    _library_status["Pillow"] = True
    _library_status["pytesseract"] = True
    try:
        pytesseract.get_tesseract_version()
        _library_status["tesseract_installed"] = True
    except pytesseract.TesseractNotFoundError:
        logging.warning("Tesseract OCR engine not found in system PATH. OCR for images will be skipped.")
        _library_status["tesseract_installed"] = False
except ImportError: logging.warning("Pillow or pytesseract not found. Image (OCR) parsing will be skipped.")

try:
    import requests
    _library_status["requests"] = True
except ImportError: logging.warning("The 'requests' library is not found. Gemini VLM image description will be skipped.")


# --- NLP Utilities ---
class NLPProcessor:
    def __init__(self):
        # Load nlp model here, so each process gets its own instance
        global nlp # Refer to the global nlp variable
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
        global nlp # Ensure we use the nlp loaded in this process
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
            # Relaxed filter: only skip if cleaned_text is truly empty AND MIN_CLEANED_TEXT_LENGTH > 0
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

class ImageParser(BaseParser):
    def __init__(self, file_path):
        super().__init__(file_path); self.file_type = "image"
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")

    def _get_image_base64(self):
        try:
            with open(self.file_path, "rb") as image_file: return base64.b64encode(image_file.read()).decode("utf-8")
        except Exception as e: logging.error(f"Error encoding image to base64 for {self.file_name}: {e}"); return None

    def _get_image_mime_type(self):
        mime_type, _ = mimetypes.guess_type(self.file_path);
        if mime_type and mime_type.startswith('image/'): return mime_type
        return "application/octet-stream"

    def _describe_image_with_gemini(self, base64_image_data, mime_type, max_retries=3, base_delay=1):
        if not _library_status["requests"] or not self.gemini_api_key or not base64_image_data:
            if not self.gemini_api_key: logging.warning(f"Gemini API key not set for {self.file_name}. Skipping VLM.");
            return None
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.gemini_api_key}"
        payload = {"contents": [{"role": "user", "parts": [{"text": "Describe this image in detail, focusing on objects, scenes, and any text present."}, {"inlineData": {"mimeType": mime_type, "data": base64_image_data}}]}]}
        headers = {'Content-Type': 'application/json'}

        for attempt in range(max_retries):
            try:
                response = requests.post(api_url, headers=headers, json=payload, timeout=60)
                response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
                result = response.json()
                if result and result.get("candidates") and len(result["candidates"]) > 0 and result["candidates"][0].get("content") and result["candidates"][0]["content"].get("parts") and len(result["candidates"][0]["content"]["parts"]) > 0:
                    description = result["candidates"][0]["content"]["parts"][0].get("text", ""); return description
                else:
                    logging.warning(f"Gemini API response for {self.file_name} was unexpected or empty on attempt {attempt + 1}: {result}")
                    # If response structure is bad, don't retry, just fail
                    return None
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 or 500 <= e.response.status_code < 600:
                    # Retry on Too Many Requests (429) or Server Errors (5xx)
                    sleep_time = base_delay * (2 ** attempt)
                    logging.warning(f"Gemini API rate limit hit or server error for {self.file_name} (Status: {e.response.status_code}). Retrying in {sleep_time:.2f} seconds (Attempt {attempt + 1}/{max_retries}).")
                    time.sleep(sleep_time)
                else:
                    # For other HTTP errors (e.g., 400, 401, 403, 404), don't retry
                    logging.error(f"Non-retryable HTTP error calling Gemini API for {self.file_name}: {e}")
                    return None
            except requests.exceptions.Timeout:
                sleep_time = base_delay * (2 ** attempt)
                logging.warning(f"Gemini API request timed out for {self.file_name}. Retrying in {sleep_time:.2f} seconds (Attempt {attempt + 1}/{max_retries}).")
                time.sleep(sleep_time)
            except requests.exceptions.RequestException as e:
                # Catch all other requests-related exceptions (e.g., connection errors)
                logging.error(f"Network or request error calling Gemini API for {self.file_name}: {e}")
                return None
            except Exception as e:
                logging.error(f"Unexpected error processing Gemini API response for {self.file_name}: {e}")
                return None
        
        logging.error(f"Failed to get Gemini VLM description for {self.file_name} after {max_retries} attempts.")
        return None # All retries exhausted

    def _extract_text(self):
        ocr_text = ""
        if _library_status["Pillow"] and _library_status["pytesseract"] and _library_status["tesseract_installed"]:
            try:
                img = Image.open(self.file_path)
                # Relaxed filter: only skip if MIN_IMAGE_DIMENSION > 0 and image is smaller
                if MIN_IMAGE_DIMENSION > 0 and (img.width < MIN_IMAGE_DIMENSION or img.height < MIN_IMAGE_DIMENSION):
                    logging.info(f"Skipping OCR/VLM for small image {self.file_name} ({img.width}x{img.height} < {MIN_IMAGE_DIMENSION}x{MIN_IMAGE_DIMENSION}).")
                    return ""
                ocr_text = pytesseract.image_to_string(img)
            except Exception as e: logging.warning(f"OCR failed for {self.file_name}: {e}")
        else: logging.info(f"Skipping OCR for {self.file_name} due to missing Pillow, pytesseract, or Tesseract engine.")

        vlm_description = ""
        if _library_status["requests"] and self.gemini_api_key:
            base64_image = self._get_image_base64()
            if base64_image:
                mime_type = self._get_image_mime_type()
                vlm_description = self._describe_image_with_gemini(base64_image, mime_type)
                if vlm_description: self.metadata["image_description_vlm"] = vlm_description
                else: logging.warning(f"Could not get VLM description for {self.file_name}.")
            else: logging.warning(f"Could not encode image to base64 for VLM processing: {self.file_name}")
        else: logging.info(f"Skipping Gemini VLM for {self.file_name} due to missing 'requests' library or API key.")

        combined_text = ""
        if vlm_description: combined_text += "Image Description (VLM):\n" + vlm_description.strip()
        
        if ocr_text and ocr_text.strip():
            # Ensure vlm_description is treated as an empty string if None for comparison
            vlm_desc_for_comparison = vlm_description if vlm_description is not None else ""
            if ocr_text.strip().lower() not in vlm_desc_for_comparison.lower():
                if combined_text: combined_text += "\n\nText from OCR:\n"
                combined_text += ocr_text.strip()

        return combined_text.strip()

class XmlParser(BaseParser):
    """Parser for XML files, using xml.etree.ElementTree to extract all text content."""
    def __init__(self, file_path):
        super().__init__(file_path)
        self.file_type = "xml"

    def _extract_text(self):
        if not _library_status["xml_etree"]:
            raise ImportError("xml.etree.ElementTree is not available (should be built-in).")
        text_content = []
        try:
            tree = ET.parse(self.file_path)
            root = tree.getroot()
            for element in root.iter():
                if element.text:
                    text_content.append(element.text.strip())
        except Exception as e:
            raise Exception(f"Failed to extract text from XML: {e}")
        return " ".join(text_content)


# --- Worker function for multiprocessing pool ---
def _process_single_file(file_path, url_metadata, parser_registry_config, preferred_languages):
    """
    Processes a single file. This function is designed to be run by a multiprocessing worker.
    It returns a tuple: (success_status, file_path, original_url, output_path_if_success).
    """
    # Re-initialize NLPProcessor in each worker process because spaCy models are not picklable
    nlp_processor = NLPProcessor()

    # Re-map parser classes from their string names (passed via config)
    local_parser_registry = {}
    for ext, parser_name in parser_registry_config.items():
        # Dynamically get the class object from its name in the global scope
        parser_class = globals().get(parser_name)
        if parser_class:
            local_parser_registry[ext] = parser_class
        else:
            logging.error(f"Parser class '{parser_name}' not found for extension '.{ext}'.")
            return (False, file_path, url_metadata.get("url"), None) # Indicate failure

    _, file_ext = os.path.splitext(file_path)
    file_ext = file_ext[1:].lower()
    original_url = url_metadata.get("url", "N/A")

    if f".{file_ext}" in SKIP_COMMON_NON_CONTENT_EXTENSIONS:
        logging.info(f"Skipping {file_path} due to common non-content extension '{file_ext}'.")
        return (False, file_path, original_url, None) # Indicate skipped

    try:
        if MIN_FILE_SIZE_BYTES > 0 and os.path.getsize(file_path) < MIN_FILE_SIZE_BYTES:
            logging.info(f"Skipping {file_path} due to small file size ({os.path.getsize(file_path)} bytes < {MIN_FILE_SIZE_BYTES}).")
            return (False, file_path, original_url, None) # Indicate skipped
    except FileNotFoundError:
        logging.warning(f"File not found during processing: {file_path}. Skipping.")
        return (False, file_path, original_url, None) # Indicate skipped

    parser_class = local_parser_registry.get(file_ext)

    if parser_class:
        if file_ext == "html" and preferred_languages:
            detected_language = url_metadata.get('language', 'unknown').lower()
            if detected_language not in preferred_languages:
                logging.info(f"Skipping HTML file {file_path} due to unsupported language: {detected_language}. (URL: {original_url})")
                return (False, file_path, original_url, None) # Indicate skipped
        try:
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

                if final_doc["file_type"] in ["html", "pdf", "docx", "xlsx", "csv", "xml"] or \
                   (final_doc["file_type"] == "image" and final_doc["cleaned_text"]):
                    nlp_results = nlp_processor.process_text(final_doc["cleaned_text"]); final_doc.update(nlp_results)
                else: final_doc.update({"sentences": [], "tokens": [], "lemmas": [], "entities": []})

                lang_from_metadata = final_doc["metadata"].get("language")
                output_language = "unknown"
                if lang_from_metadata is not None:
                    output_language = str(lang_from_metadata).lower()
                
                language_output_dir = os.path.join(OUTPUT_DIR, output_language)
                os.makedirs(language_output_dir, exist_ok=True)

                output_filename = f"{final_doc['doc_id']}.json"; output_path = os.path.join(language_output_dir, output_filename)
                with open(output_path, 'w', encoding='utf-8') as f: json.dump(final_doc, f, ensure_ascii=False, indent=2)
                logging.info(f"Saved processed data to {output_path}"); return (True, file_path, original_url, output_path) # Indicate success
            else:
                logging.info(f"Skipping {file_path} as it did not yield important content or parsing failed. (URL: {original_url})")
                return (False, file_path, original_url, None) # Indicate skipped
        except Exception as e:
            logging.error(f"Critical error during processing {file_path}: {e}", exc_info=True)
            return (False, file_path, original_url, None) # Indicate failure
    else:
        logging.warning(f"Skipping file: {file_path}. No parser registered for .{file_ext}")
        return (False, file_path, original_url, None) # Indicate skipped


# --- Pipeline Manager ---

class PipelineManager:
    def __init__(self, input_root_dir, output_dir, preferred_languages=None):
        self.input_root_dir = input_root_dir
        self.output_dir = output_dir
        self.crawled_db_path = os.path.join(input_root_dir, "crawled_urls.db")
        self.changed_files_json_path = os.path.join(input_root_dir, "changed_files.json")
        self.preferred_languages = [lang.lower() for lang in preferred_languages] if preferred_languages else []

        self._parser_registry = {} # Stores class objects directly
        # NLPProcessor will be instantiated per process in _process_single_file
        self._setup_directories()

    def _setup_directories(self):
        if not os.path.exists(self.input_root_dir):
            logging.error(f"Input root directory does not exist: {self.input_root_dir}. Please ensure your crawler output is in this path.")
            sys.exit(1)
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            logging.info(f"Created output directory: {self.output_dir}")

    def register_parser(self, file_extension, parser_class):
        if not issubclass(parser_class, BaseParser):
            logging.error(f"Error: {parser_class.__name__} is not a subclass of BaseParser. Not registering.")
            return
        # Store the class name (string) instead of the class object itself for pickling
        self._parser_registry[file_extension.lower()] = parser_class.__name__
        logging.info(f"Registered parser for .{file_extension}: {parser_class.__name__}")

    def _load_changed_files(self):
        if not os.path.exists(self.changed_files_json_path):
            logging.warning(f"'{self.changed_files_json_path}' not found. Processing all files in '{self.input_root_dir}' (this may be slower).")
            return None
        try:
            with open(self.changed_files_json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if not isinstance(data, list):
                    logging.error(f"Expected a list in '{self.changed_files_json_path}', but got {type(data).__name__}. Processing all files.")
                    return None
                return data
        except json.JSONDecodeError as e:
            logging.error(f"Error reading '{self.changed_files_json_path}': {e}. Processing all files.")
            return None
        except Exception as e:
            logging.error(f"Unexpected error loading '{self.changed_files_json_path}': {e}. Processing all files.")
            return None

    def _get_url_metadata(self, url):
        if not os.path.exists(self.crawled_db_path):
            logging.error(f"Crawler database '{self.crawled_db_path}' not found. Cannot fetch URL metadata. This will impact processing accuracy.")
            return None
        try:
            conn = sqlite3.connect(self.crawled_db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM crawled_urls WHERE url = ?", (url,))
            row = cursor.fetchone()
            conn.close()
            if row: return dict(row)
            return None
        except sqlite3.DatabaseError as e:
            logging.error(f"Database error when connecting to '{self.crawled_db_path}': {e}. This file might be corrupted or not a valid SQLite DB. Cannot fetch URL metadata. This will impact processing accuracy.")
            return None
        except Exception as e:
            logging.error(f"Error fetching metadata for {url} from DB: {e}", exc_info=True)
            return None

    def _derive_file_path(self, url_metadata):
        url = url_metadata.get('url'); md5_hash = url_metadata.get('md5_hash'); content_type = url_metadata.get('content_type', '').lower()
        if not url: logging.error(f"Cannot derive file path, URL missing for metadata: {url_metadata}"); return None
        domain_name_raw = url.split('://', 1)[-1].split('/', 1)[0]; domain_name = domain_name_raw.replace(':', '_')
        domain_base_path = os.path.join(self.input_root_dir, domain_name)

        if md5_hash and ('text/html' in content_type or url.endswith(f'{md5_hash}.html')):
            file_name = f"{md5_hash}.html"; return os.path.join(domain_base_path, "html", file_name)
        else:
            parsed_url = url.split('://', 1)[-1]; path_segments = parsed_url.split('/', 1)[-1] if '/' in parsed_url else ''
            if '?' in path_segments: path_segments = path_segments.split('?', 1)[0]
            if '#' in path_segments: path_segments = path_segments.split('#', 1)[0]
            path_segments = path_segments.replace(':', '_').replace('*', '_').replace('?', '_').replace('|', '_')
            file_path = os.path.join(domain_base_path, path_segments)

            if os.path.exists(file_path): return file_path
            elif os.path.isdir(file_path) and os.path.exists(os.path.join(file_path, "index.html")): return os.path.join(file_path, "index.html")
            elif not os.path.splitext(file_path)[1] and os.path.exists(f"{file_path}.html"): return f"{file_path}.html"
            elif md5_hash and os.path.exists(os.path.join(domain_base_path, "html", f"{md5_hash}.html")):
                return os.path.join(domain_base_path, "html", f"{md5_hash}.html")
            else: logging.warning(f"Could not find local file for URL: {url} -> Tried: {file_path}"); return None

    def run(self):
        logging.info(f"Starting pipeline processing in '{self.input_root_dir}'...")
        logging.info(f"Output will be saved to '{self.output_dir}'")
        if self.preferred_languages: logging.info(f"HTML content will be filtered by preferred languages: {', '.join(self.preferred_languages)}")
        else: logging.info("No language filtering applied for HTML content.")
        logging.info(f"Minimum file size for processing: {MIN_FILE_SIZE_BYTES} bytes (set to 0 for no size filtering).")
        logging.info(f"Minimum cleaned text length for processing: {MIN_CLEANED_TEXT_LENGTH} characters (set to 0 for no length filtering).")
        logging.info(f"Minimum image dimension for processing: {MIN_IMAGE_DIMENSION} pixels (set to 0 for no dimension filtering).")

        processed_count = 0; skipped_count = 0; failed_count = 0; total_files_identified = 0

        changed_files_list = self._load_changed_files()
        items_to_process_args = [] # List to hold arguments for multiprocessing pool

        if changed_files_list is not None:
            logging.info(f"Processing {len(changed_files_list)} entries from '{CHANGED_FILES_JSON_PATH}'.")
            for item in changed_files_list:
                if not isinstance(item, dict):
                    logging.error(f"Skipping malformed entry in changed_files.json: Expected dictionary, got {type(item).__name__}. Content: {item}")
                    skipped_count += 1
                    continue

                url = item['url']
                url_metadata = self._get_url_metadata(url)
                
                if url_metadata is None:
                    logging.warning(f"Could not retrieve metadata for URL: {url}. Proceeding with default/limited metadata.")
                    url_metadata = {"url": url, "language": "unknown", "content_type": "unknown", "md5_hash": ""}

                file_path = self._derive_file_path(url_metadata)
                if file_path and os.path.exists(file_path):
                    total_files_identified += 1
                    items_to_process_args.append((file_path, url_metadata, self._parser_registry, self.preferred_languages))
                else:
                    logging.warning(f"Local file not found for URL: {url} (expected path: {file_path}). Skipping.")
        else:
            logging.warning(f"Proceeding with full directory scan as '{CHANGED_FILES_JSON_PATH}' was not used.")
            for dirpath, dirnames, filenames in os.walk(self.input_root_dir):
                if dirpath == self.input_root_dir:
                    filenames = [f for f in filenames if f not in ["crawled_urls.db", "changed_files.json"]]
                for filename in filenames:
                    total_files_identified += 1
                    file_path = os.path.join(dirpath, filename)
                    url_from_path = file_path.replace(self.input_root_dir + os.sep, 'http://').replace(os.sep, '/')
                    url_metadata_full_scan = self._get_url_metadata(url_from_path)
                    if not url_metadata_full_scan:
                        url_metadata_full_scan = {"url": url_from_path, "language": "unknown", "content_type": "unknown", "md5_hash": ""}
                    items_to_process_args.append((file_path, url_metadata_full_scan, self._parser_registry, self.preferred_languages))

        logging.info(f"Identified {len(items_to_process_args)} files to attempt processing.")

        # Use multiprocessing pool
        num_processes = os.cpu_count() or 1 # Use all available CPU cores
        logging.info(f"Starting multiprocessing pool with {num_processes} processes.")
        with multiprocessing.Pool(num_processes) as pool:
            # starmap applies arguments from each tuple in items_to_process_args to _process_single_file
            results = pool.starmap(_process_single_file, items_to_process_args)

        for success, file_path, original_url, output_path in results:
            if success:
                processed_count += 1
            else:
                skipped_count += 1 # This includes both skipped and failed processing in worker

        logging.info("-" * 50); logging.info(f"Pipeline processing finished.")
        logging.info(f"Total files identified for processing: {total_files_identified}")
        logging.info(f"Total files processed: {processed_count}")
        logging.info(f"Total files skipped or failed in workers: {skipped_count}") # Combined for simplicity in multiprocessing context
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
    if not _library_status["Pillow"]: missing_pylibs.append("Pillow")
    if not _library_status["pytesseract"]: missing_pylibs.append("pytesseract")
    if not _library_status["requests"]: missing_pylibs.append("requests")

    if missing_pylibs:
        print("\n--- ATTENTION: Missing Python Libraries ---")
        print("The following Python libraries are required for full pipeline functionality:")
        print(f"Please install them using pip: pip install {' '.join(missing_pylibs)}")
        if "spacy" in missing_pylibs: print("Additionally, for spaCy, download the English model: python -m spacy download en_core_web_sm")
        print("-------------------------------------------\n")
    else: print("\nAll core Python libraries appear to be installed.")

    if _library_status["Pillow"] and _library_status["pytesseract"] and not _library_status["tesseract_installed"]:
        print("\n--- ATTENTION: Tesseract OCR Engine Missing ---")
        print("Tesseract OCR engine is required for text extraction from images.")
        print("Please install it on your system:")
        print("   - Windows: https://tesseract-ocr.github.io/tessdoc/Downloads.html")
        print("   - macOS (Homebrew): brew install tesseract")
        print("   - Linux (Debian/Ubuntu): sudo apt-get install tesseract-ocr")
        print("   You might also need to set 'pytesseract.pytesseract.tesseract_cmd' in the ImageParser if Tesseract is not in your system's PATH.")
        print("-----------------------------------------------\n")
    elif _library_status["Pillow"] and _library_status["pytesseract"] and _library_status["tesseract_installed"]:
        print("Tesseract OCR engine appears to be installed and accessible.")

    if not os.getenv("GEMINI_API_KEY") and _library_status["requests"]:
        print("\n--- ATTENTION: Gemini API Key Missing ---")
        print("To enable image description using the Gemini Vision Model, please obtain a Gemini API Key from Google AI Studio (aistudio.google.com).")
        print("Then, set it as an environment variable named 'GEMINI_API_KEY' before running the script.")
        print("   Example (Linux/macOS): export GEMINI_API_KEY=\"YOUR_API_KEY_HERE\"")
        print("   Example (Windows CMD): set GEMINI_API_KEY=\"YOUR_API_KEY_HERE\"")
        print("-----------------------------------------\n")
    elif os.getenv("GEMINI_API_KEY"):
        print("Gemini API Key is set as an environment variable.")


# --- Main Execution Block ---
if __name__ == "__main__":
    # This is crucial for multiprocessing on Windows, otherwise it can lead to issues
    # where child processes try to re-import the main script.
    multiprocessing.freeze_support()

    check_and_suggest_installations()

    # This version of the pipeline does NOT modify the INPUT_ROOT_DIR.
    # It expects your crawler's output to already exist in this directory.
    # If your 'output' directory is not in the same location as this script,
    # please update INPUT_ROOT_DIR at the top of this file.
    
    # Initialize and Run the Pipeline
    pipeline = PipelineManager(
        input_root_dir=INPUT_ROOT_DIR,
        output_dir=OUTPUT_DIR,
        preferred_languages=PREFERRED_LANGUAGES
    )

    # Register all the parsers for the file types you expect to process.
    # Note: We register the class name (string) now, not the class object itself,
    # because class objects are not directly picklable across processes.
    pipeline.register_parser("pdf", PdfParser)
    pipeline.register_parser("docx", DocxParser)
    pipeline.register_parser("xlsx", XlsxParser)
    pipeline.register_parser("csv", CsvParser)
    pipeline.register_parser("html", HtmlParser)
    pipeline.register_parser("geojson", GeoJsonParser)
    pipeline.register_parser("png", ImageParser)
    pipeline.register_parser("jpg", ImageParser)
    pipeline.register_parser("jpeg", ImageParser)
    pipeline.register_parser("xml", XmlParser)

    pipeline.run()

    print(f"\nProcessing complete. Check the '{OUTPUT_DIR}' directory for output JSON files (organized by language) and '{LOG_FILE}' for detailed logs.")
    print("\nRemember to refer to the 'ATTENTION' messages above for any missing installations or API key configurations.")
