import hashlib
from urllib.parse import urlparse
import os # Added for os.path.splitext

def compute_md5(data):
    """Computes the MD5 hash of given data."""
    return hashlib.md5(data).hexdigest()

def get_domain(url):
    """Extracts the domain from a URL."""
    try:
        return urlparse(url).netloc
    except Exception:
        return None

def is_downloadable_asset(url):
    """
    Checks if the URL likely points to a downloadable asset (not an HTML page to crawl).
    Extends checks for common document types, archives, and a wider range of map data formats.
    """
    path = urlparse(url).path
    # Common document and media file extensions
    asset_extensions = (
        '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.xls', '.xlsx', '.odt',
        '.ods', '.odp', '.rtf', '.txt', '.csv', '.xml', # XML can be general, but also map data (e.g., GML)
        '.json', '.geojson', '.topojson', # GeoJSON, TopoJSON, and general JSON (for embedded data)
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.svg', # Images (including GeoTIFFs if named .tif/.tiff)
        '.mp3', '.wav', '.ogg', '.mp4', '.avi', '.mov', '.wmv', '.flv',
        '.zip', '.tar', '.gz', '.rar', '.7z', # Archives
        '.exe', '.dmg', '.deb', '.rpm', # Executables/Packages
        '.js', '.css', # JavaScript and CSS files
        '.kml', '.kmz', # KML/KMZ for geographic data
        '.shp', '.shx', '.dbf', '.prj', '.sbn', '.sbx', '.cpg', '.fbn', '.idx', '.qix', # ESRI Shapefile components
        '.gpx',         # GPS Exchange Format
        '.grib',        # GRIB (Gridded Binary) for meteorological data
        '.hdf', '.h5',  # HDF (Hierarchical Data Format) for scientific data
        '.geotiff',     # Specific GeoTIFF extension, though .tif is more common
        '.nc',          # NetCDF (for scientific data, including geospatial)
        '.dem',         # Digital Elevation Model
        '.tiff',        # TIFF, often used for georeferenced images
        '.wms', '.wfs', '.wcs', # Sometimes used as direct file extensions for OGC web services responses
        '.img',         # ERDAS IMAGINE
        '.asc',         # ASCII Grid
        '.las',         # LiDAR data
        '.laz',         # Compressed LiDAR data
        '.dxf',         # AutoCAD DXF (can contain geospatial data)
        '.dwg',         # AutoCAD Drawing (can contain geospatial data)
        '.gml',         # Geography Markup Language (XML-based)
        '.nc',          # NetCDF (for scientific data, including geospatial)
        '.sqlite',      # SpatiaLite (GIS extension for SQLite)
        '.tab', '.dat', '.id', '.map', # MapInfo TAB files (often a set)
    )
    
    # Check if the path ends with any of the asset extensions
    if path.lower().endswith(asset_extensions):
        return True

    # Additionally, if the URL has no extension but contains common map tile patterns,
    # it might still be a map tile image. This is a heuristic.
    # E.g., /tiles/1/2/3, /osm_tiles/z/x/y
    if not os.path.splitext(path)[1]: # No explicit file extension
        # Common tile patterns (adjust if MOSDAC uses different patterns)
        tile_patterns = ['/tiles/', '/osm_tiles/', '/mapbox/']
        for pattern in tile_patterns:
            if pattern in path.lower():
                # Assume it's a tile that will resolve to an image (PNG/JPG)
                # The DownloadManager will handle content-type detection upon download.
                return True
    
    return False

def normalize_url(url):
    """
    Normalizes a URL by removing fragments and sorting query parameters.
    Ensures consistency for visited_urls set.
    """
    parsed = urlparse(url)
    # Remove fragment
    clean_url = parsed._replace(fragment="").geturl()
    
    # Sort query parameters
    parsed_clean = urlparse(clean_url)
    query_params = parsed_clean.query.split('&')
    if query_params and query_params[0]: # Check if query exists and is not empty string
        query_params.sort()
        normalized_query = '&'.join(query_params)
        normalized_url = parsed_clean._replace(query=normalized_query).geturl()
    else:
        normalized_url = clean_url
    
    return normalized_url