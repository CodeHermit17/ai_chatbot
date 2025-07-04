# kg_extractor.py

import spacy
import json
import re
from collections import defaultdict

# --- Load SpaCy English model ---
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    print("Downloading spacy model 'en_core_web_sm'. Please wait...")
    spacy.cli.download("en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")

# --- Define the Knowledge Graph Schema and Rules ---
# This dictionary maps potential entity mentions to a canonical entity name and type.
CANONICAL_ENTITIES = {
    "insat-3d": {"text": "INSAT-3D", "type": "Satellite"},
    "megha-tropiques": {"text": "Megha-Tropiques", "type": "Satellite"},
    "eos-07": {"text": "EOS-07", "type": "Satellite"},
    "scatsat-1": {"text": "SCATSAT-1", "type": "Satellite"},
    "kalpana-1": {"text": "KALPANA-1", "type": "Satellite"},
    "saral-altika": {"text": "SARAL-AltiKa", "type": "Satellite"},
    "jason-2": {"text": "Jason-2", "type": "Satellite"},
    "oceansat-2": {"text": "OCEANSAT-2", "type": "Satellite"},
    "oceansat-3": {"text": "OCEANSAT-3", "type": "Satellite"},
    "insat-3ds": {"text": "INSAT-3DS", "type": "Satellite"},
    "rainfall estimate": {"text": "Rainfall Estimate", "type": "Product"},
    "rainfall product": {"text": "Rainfall Product", "type": "Product"},
    "cloud mask": {"text": "Cloud Mask", "type": "Product"},
    "oceanstate": {"text": "OceanState", "type": "Product"},
    "sea state forecast": {"text": "Sea State Forecast", "type": "Product"},
    "cloud burst nowcast": {"text": "Cloud Burst Nowcast", "type": "Product"},
    "soil wetness index": {"text": "Soil Wetness Index", "type": "Product"},
    "quantitative precipitation estimation": {"text": "Quantitative Precipitation Estimation (QPE)", "type": "Product"},
    "qpe": {"text": "Quantitative Precipitation Estimation (QPE)", "type": "Product"},
    "sea surface temperature": {"text": "Sea Surface Temperature (SST)", "type": "Product"},
    "sst": {"text": "Sea Surface Temperature (SST)", "type": "Product"},
    "inland water bodies monitoring": {"text": "Inland Water Bodies Monitoring", "type": "Application"},
    "water height estimation": {"text": "Water Height Estimation", "type": "Product"},
    "inland water height": {"text": "Inland Water Height", "type": "Product"},
    "atmospheric studies mission": {"text": "Atmospheric Studies Mission", "type": "Mission"},
    "ocean observation mission": {"text": "Ocean Observation Mission", "type": "Mission"},
    "hydrology mission": {"text": "Hydrology Mission", "type": "Mission"},
    "insat mission": {"text": "INSAT Mission", "type": "Mission"},
    "eos mission": {"text": "EOS Mission", "type": "Mission"},
    "isro": {"text": "ISRO", "type": "Organization"},
    "mosdac": {"text": "MOSDAC", "type": "Organization"},
    "space applications centre": {"text": "Space Applications Centre (SAC)", "type": "Organization"},
    "sac": {"text": "Space Applications Centre (SAC)", "type": "Organization"},
    "indian navy": {"text": "Indian Navy", "type": "Organization"},
    "wmo": {"text": "WMO", "type": "Organization"},
    "imd": {"text": "IMD", "type": "Organization"},
    "ukai reservoir": {"text": "Ukai reservoir", "type": "Location"},
    "brahmaputra river": {"text": "Brahmaputra River", "type": "Location"},
    "wind speed": {"text": "Wind Speed", "type": "Parameter"},
    "rainfall rate": {"text": "Rainfall Rate", "type": "Parameter"},
    "water height": {"text": "Water Height", "type": "Parameter"},
    "temperature": {"text": "Temperature", "type": "Parameter"},
    "humidity": {"text": "Humidity", "type": "Parameter"},
    "outgoing longwave radiation": {"text": "Outgoing Longwave Radiation", "type": "Parameter"},
    "aerosol optical depth": {"text": "Aerosol Optical Depth", "type": "Parameter"},
    "cloud motion vector": {"text": "Cloud Motion Vector", "type": "Parameter"},
    "total precipitable water": {"text": "Total Precipitable Water", "type": "Parameter"},
    "15 minutes": {"text": "15 minutes", "type": "TimeInterval"},
    "daily": {"text": "daily", "type": "TimeInterval"},
    "real-time": {"text": "real-time", "type": "TimeInterval"},
    "3-hourly": {"text": "3-hourly", "type": "TimeInterval"},
    "6 hours": {"text": "6 hours", "type": "TimeInterval"},
    "near real time": {"text": "Near Real Time", "type": "TimeInterval"},
    "35 days repetivity": {"text": "35 Days Repetivity", "type": "TimeInterval"},
    "india": {"text": "India", "type": "Location"},
    "indian region": {"text": "Indian Region", "type": "Location"},
    "tropics": {"text": "Tropics", "type": "Location"},
    "ocean": {"text": "Ocean", "type": "Location"},
    "indian ocean": {"text": "Indian Ocean", "type": "Location"},
    "asia sector": {"text": "Asia Sector", "type": "Location"},
    "western himalayan region": {"text": "Western Himalayan region", "type": "Location"},
    "all-india beaches": {"text": "All-India beaches", "type": "Location"},
    "new delhi": {"text": "New Delhi", "type": "Location"},
    "ahmedabad": {"text": "Ahmedabad", "type": "Location"},
    "imager": {"text": "IMAGER", "type": "Instrument"},
    "sounder": {"text": "SOUNDER", "type": "Instrument"},
    "scarab": {"text": "SCARAB", "type": "Instrument"},
    "gps radio-occultation receiver": {"text": "GPS radio-occultation receiver", "type": "Instrument"},
    "radar altimeters": {"text": "Radar Altimeters", "type": "Instrument"},
    "waveform retracking": {"text": "Waveform Retracking", "type": "Technique"},
    "geophysical range corrections": {"text": "Geophysical Range Corrections", "type": "Technique"},
    "hdf": {"text": "HDF", "type": "DataType/Format"},
    "netcdf": {"text": "netCDF", "type": "DataType/Format"},
    "geotiff": {"text": "geoTiff", "type": "DataType/Format"},
    "jpg": {"text": "JPG", "type": "DataType/Format"},
    "gif": {"text": "GIF", "type": "DataType/Format"},
    "png": {"text": "PNG", "type": "DataType/Format"},
    "level 3": {"text": "Level 3", "type": "ProcessingLevel"},
    "level 1a": {"text": "Level-1A", "type": "DataType/Format"},
    "level 2b": {"text": "Level-2B", "type": "DataType/Format"},
    "data product derived from altimeter igdr/gdr product": {"text": "Data product derived from altimeter IGDR/GDR product", "type": "ProcessingLevel"},
    "text format": {"text": "text format", "type": "DataType/Format"},
    "png format": {"text": "PNG format", "type": "DataType/Format"},
    "online download": {"text": "Online Download", "type": "Service"},
    "data access": {"text": "Data Access", "type": "Service"},
    "forecast service": {"text": "Forecast Service", "type": "Service"},
    "nowcast service": {"text": "Nowcast Service", "type": "Service"},
    "alerts": {"text": "Alerts", "type": "Service"},
    "training programs": {"text": "Training Programs", "type": "Service"},
    "weather forecasting": {"text": "Weather Forecasting", "type": "Application"},
    "oceanography": {"text": "Oceanography", "type": "Application"},
    "agriculture": {"text": "Agriculture", "type": "Application"},
    "disaster management": {"text": "Disaster Management", "type": "Application"},
    "inland water bodies": {"text": "Inland Water Bodies", "type": "LocationFeature"},
    "rivers": {"text": "Rivers", "type": "LocationFeature"},
    "reservoirs": {"text": "Reservoirs", "type": "LocationFeature"},
    "water levels": {"text": "Water Levels", "type": "Parameter"}
}

# Rule-based relationship patterns using SpaCy's dependency parsing and token attributes
RELATIONSHIP_RULES = [
    ("Satellite", "provide", "Product", "provides"),
    ("Satellite", "deliver", "Product", "delivers"),
    ("Product", "update", "TimeInterval", "updated_every"),
    ("Product", "belong", "Mission", "belongs_to"),
    ("Product", "be", "Mission", "belongs_to"),
    ("Organization", "manage", "Satellite", "manages"),
    ("Organization", "manage", "Mission", "manages"),
    ("Organization", "operate", "Satellite", "operates"),
    ("Organization", "develop", "Portal", "developed_by"),
    ("Organization", "develop", "Product", "developed_by"),
    ("Portal", "offer", "Product", "offers"),
    ("Portal", "offer", "Service", "offers"),
    ("Satellite", "support", "Application", "supports"),
    ("Product", "support", "Application", "supports"),
    ("Product", "cover", "Location", "covers_region"),
    ("Product", "cover", "LocationFeature", "covers_region"),
    ("Product", "available", "DataType/Format", "available_in"),
    ("Instrument", "measure", "Parameter", "measures"),
    ("Instrument", "generate", "Product", "generates"),
    ("Satellite", "generate", "Product", "generates"),
    ("Organization", "collaborate", "Organization", "collaborates_with"),
    ("Organization", "utilize", "Product", "utilizes"),
    ("Organization", "utilize", "Service", "utilizes"),
    ("Mission", "include", "Parameter", "includes"),
    ("Mission", "include", "Product", "includes"),
    ("Service", "include", "Product", "includes"),
    ("Application", "use", "Product", "uses"),
    ("Parameter", "use", "Application", "used_for"),
    ("Product", "use", "Application", "used_for"),
    ("Product", "derive", "Satellite", "is_derived_from"),
    ("Product", "derive", "Instrument", "is_derived_from"),
    ("Product", "derive", "ProcessingLevel", "is_derived_from"),
    ("Product", "derive", "DataType/Format", "is_derived_from"),
    ("Satellite", "use", "Instrument", "uses"),
    ("Application", "use", "Technique", "uses"),
    ("Product", "use", "Technique", "uses"),
    ("Organization", "located", "Location", "located_at"),
    ("Product", "process", "ProcessingLevel", "has_processing_level"),
    ("Portal", "host", "Service", "hosts"),
    ("Portal", "host", "Product", "hosts"),
    ("Portal", "host", "Mission", "hosts"),
    ("Service", "provide", "DataType/Format", "provides_format"),
    ("Product", "be", "LocationFeature", "is_over"),
]

# Helper function to find canonical entity and type
def get_canonical_entity_info(text):
    lower_text = text.lower().strip()
    return CANONICAL_ENTITIES.get(lower_text, {"text": text.strip(), "type": "Unknown"})

def extract_content_triples(text, existing_entities=None):
    """
    Extracts domain-specific entities and semantic relationships from cleaned text.
    Focuses on the relationships *within* the content.

    Args:
        text (str): A cleaned piece of text.
        existing_entities (list): Optional list of pre-extracted entities from JSON.

    Returns:
        tuple: A tuple containing:
            - list: A list of extracted triples (Subject Entity, Relationship, Object Entity).
            - list: A list of unique entities found in the text, with their canonical info.
    """
    if not text:
        return [], []

    doc = nlp(text)
    extracted_triples = []

    # Combine SpaCy's NER with our canonical entities and any pre-existing entities
    found_entities = []

    # 1. Add entities from CANONICAL_ENTITIES via regex matching
    for phrase, info in CANONICAL_ENTITIES.items():
        for match in re.finditer(r'\b' + re.escape(phrase) + r'\b', text.lower()):
            start_char, end_char = match.span()
            found_entities.append({"text": info["text"], "type": info["type"], "span": (start_char, end_char)})

    # 2. Add entities from SpaCy's default NER, if not already captured and useful
    for ent in doc.ents:
        canonical_info = get_canonical_entity_info(ent.text)
        if canonical_info["type"] != "Unknown":
            found_entities.append({"text": canonical_info["text"], "type": canonical_info["type"], "span": (ent.start_char, ent.end_char)})
        else:
            if ent.label_ == "ORG":
                found_entities.append({"text": ent.text, "type": "Organization", "span": (ent.start_char, ent.end_char)})
            elif ent.label_ == "GPE":
                found_entities.append({"text": ent.text, "type": "Location", "span": (ent.start_char, ent.end_char)})
            elif ent.label_ == "PRODUCT":
                found_entities.append({"text": ent.text, "type": "Product", "span": (ent.start_char, ent.end_char)})
            elif ent.label_ == "DATE" or ent.label_ == "TIME":
                 found_entities.append({"text": ent.text, "type": "TimeInterval", "span": (ent.start_char, ent.end_char)})
            elif ent.label_ == "LOC":
                 found_entities.append({"text": ent.text, "type": "Location", "span": (ent.start_char, ent.end_char)})


    # 3. Add entities from the input JSON's `entities` array
    if existing_entities:
        for ent_data in existing_entities:
            if not isinstance(ent_data, dict):
                print(f"Warning: Skipping malformed entity entry (not a dictionary): {ent_data}")
                continue
            
            entity_text = ent_data.get("text")
            if not entity_text:
                print(f"Warning: Skipping entity entry with no 'text' key: {ent_data}")
                continue

            entity_type = ent_data.get("type")
            if not entity_type:
                entity_type = ent_data.get("label", "Unknown")

            canonical_info = get_canonical_entity_info(entity_text)
            
            if canonical_info["type"] != "Unknown":
                 found_entities.append({"text": canonical_info["text"], "type": canonical_info["type"], "span": (text.lower().find(entity_text.lower()), text.lower().find(entity_text.lower()) + len(entity_text))})
            else:
                 found_entities.append({"text": entity_text, "type": entity_type, "span": (text.lower().find(entity_text.lower()), text.lower().find(entity_text.lower()) + len(entity_text))})


    # Remove duplicate/overlapping entities and sort
    unique_entities_list = []
    found_entities.sort(key=lambda x: (x["span"][0], -len(x["text"])))

    for ent_data in found_entities:
        start_char, end_char = ent_data["span"]
        is_overlap = False
        for existing_ent in unique_entities_list:
            ex_start, ex_end = existing_ent["span"]
            if start_char >= ex_start and end_char <= ex_end:
                is_overlap = True
                break
            elif start_char <= ex_start and end_char >= ex_end:
                unique_entities_list.remove(existing_ent)
                unique_entities_list.append(ent_data)
                is_overlap = True
                break
        if not is_overlap:
            unique_entities_list.append(ent_data)

    unique_entities_list.sort(key=lambda x: x["span"][0])

    token_to_entity_map = {}
    for ent_data in unique_entities_list:
        if not isinstance(ent_data, dict) or "text" not in ent_data or "type" not in ent_data:
            print(f"DEBUG: Malformed entity in unique_entities_list, skipping: {ent_data}")
            continue

        start_char, end_char = ent_data["span"]
        entity_tokens_in_doc = [token for token in doc if token.idx >= start_char and (token.idx + len(token.text)) <= end_char]
        for token in entity_tokens_in_doc:
            token_to_entity_map[token.i] = ent_data


    # 2. Extract Relationships using Dependency Parsing and Rule-Matching
    for sent in doc.sents:
        sentence_entities = []
        added_sent_entities_texts = set()
        for token in sent:
            if token.i in token_to_entity_map:
                ent_data = token_to_entity_map[token.i]
                if ent_data["text"] not in added_sent_entities_texts:
                    sentence_entities.append(ent_data)
                    added_sent_entities_texts.add(ent_data["text"])

        sentence_entities.sort(key=lambda x: x["span"][0])

        for i, ent1_data in enumerate(sentence_entities):
            if not isinstance(ent1_data, dict) or "text" not in ent1_data or "type" not in ent1_data:
                print(f"DEBUG: Malformed ent1_data CAUGHT in relationship extraction: {ent1_data}")
                print(f"Warning: Skipping malformed ent1_data in relationship extraction: {ent1_data}")
                continue

            for j, ent2_data in enumerate(sentence_entities):
                if i == j:
                    continue

                if not isinstance(ent2_data, dict) or "text" not in ent2_data or "type" not in ent2_data:
                    print(f"DEBUG: Malformed ent2_data CAUGHT in relationship extraction: {ent2_data}")
                    print(f"Warning: Skipping malformed ent2_data in relationship extraction: {ent2_data}")
                    continue

                ent1_text = ent1_data["text"]
                ent1_type = ent1_data["type"]
                ent2_text = ent2_data["text"]
                ent2_type = ent2_data["type"]

                ent1_token_obj = None
                for token in sent:
                    if token.idx == ent1_data["span"][0]:
                        ent1_token_obj = token
                        break
                ent2_token_obj = None
                for token in sent:
                    if token.idx == ent2_data["span"][0]:
                        ent2_token_obj = token
                        break

                if not ent1_token_obj or not ent2_token_obj:
                    continue

                for rule_e1_type, verb_lemma, rule_e2_type, rel_name in RELATIONSHIP_RULES:
                    if (ent1_type == rule_e1_type) and (ent2_type == rule_e2_type):

                        if ent1_token_obj.dep_ == "nsubj" and ent1_token_obj.head.lemma_ == verb_lemma:
                            for child in ent1_token_obj.head.children:
                                if child in sent and child.idx >= ent2_data["span"][0] and (child.idx + len(child.text)) <= ent2_data["span"][1] and \
                                   (child.dep_ == "dobj" or child.dep_ == "attr" or child.dep_ == "pobj"):
                                    extracted_triples.append((ent1_text, rel_name, ent2_text))
                                    break

                        elif ent2_token_obj.dep_ == "nsubjpass" and ent2_token_obj.head.lemma_ == verb_lemma:
                             for child in ent2_token_obj.head.children:
                                 if child.dep_ == "agent":
                                     for grand_child in child.children:
                                         if grand_child in sent and grand_child.idx >= ent1_data["span"][0] and (grand_child.idx + len(grand_child.text)) <= ent1_data["span"][1]:
                                            extracted_triples.append((ent1_text, rel_name, ent2_text))
                                            break
                                 if child.dep_ == "prep" and child.lemma_ == "by":
                                     for grand_child in child.children:
                                         if grand_child in sent and grand_child.idx >= ent1_data["span"][0] and (grand_child.idx + len(grand_child.text)) <= ent1_data["span"][1]:
                                            extracted_triples.append((ent1_text, rel_name, ent2_text))
                                            break

                        if rel_name == "covers_region" and verb_lemma == "cover" and ent1_token_obj.head.lemma_ == "be":
                            span_between_lower = sent.text[ent1_data["span"][1]-sent.start_char : ent2_data["span"][0]-sent.start_char].lower()
                            if re.search(r'\b(over)\b', span_between_lower):
                                extracted_triples.append((ent1_text, rel_name, ent2_text))

                        keywords_for_rel = {
                            "provides": [r"provides", r"offers", r"delivers", r"generates"],
                            "belongs_to": [r"belongs to", r"is part of", r"is a part of"],
                            "updated_every": [r"updated every", r"updates every", r"is updated every"],
                            "manages": [r"manages", r"managed by", r"oversees"],
                            "developed_by": [r"developed by", r"is developed by", r"built by"],
                            "offers": [r"offers", r"provides"],
                            "covers_region": [r"covers", r"covering", r"is for", r"available for", r"over"],
                            "available_in": [r"available in", r"in format"],
                            "measures": [r"measures", r"measures the"],
                            "is_derived_from": [r"is derived from", r"derived from", r"using"],
                            "supports": [r"supports", r"for supporting"],
                            "includes": [r"includes", r"including"],
                            "utilizes": [r"utilizes", r"uses"],
                            "uses": [r"uses", r"utilizes"],
                            "located_at": [r"located at", r"in", r"from"],
                            "has_processing_level": [r"processing level"],
                            "provides_format": [r"in text", r"and png formats"]
                        }

                        if rel_name in keywords_for_rel:
                            for kw_pattern in keywords_for_rel[rel_name]:
                                if re.search(kw_pattern, sent.text.lower()):
                                    if ent1_text.lower() in sent.text.lower() and ent2_text.lower() in sent.text.lower():
                                        if (ent1_text, rel_name, ent2_text) not in extracted_triples:
                                            if kw_pattern == r"using" and rel_name == "is_derived_from":
                                                if ent1_type == "Product" and (ent2_type == "Satellite" or ent2_type == "Instrument"):
                                                    prod_idx = sent.text.lower().find(ent1_text.lower())
                                                    inst_idx = sent.text.lower().find(ent2_text.lower())
                                                    if prod_idx != -1 and inst_idx != -1 and prod_idx < inst_idx:
                                                        if re.search(r'\busing\b', sent.text.lower()[prod_idx:inst_idx]):
                                                            extracted_triples.append((ent1_text, rel_name, ent2_text))
                                            elif rel_name == "provides_format":
                                                if ent1_type == "Service" and ent2_type == "DataType/Format":
                                                    if ent1_text.lower() == "online download" and kw_pattern in sent.text.lower():
                                                        extracted_triples.append((ent1_text, rel_name, ent2_text))
                                            else:
                                                extracted_triples.append((ent1_text, rel_name, ent2_text))


    return list(set(extracted_triples)), unique_entities_list

def process_document_node(doc_data):
    """
    Processes a single DOCUMENT NODE to extract all types of triples.

    Args:
        doc_data (dict): A dictionary representing a single DOCUMENT NODE.

    Returns:
        list: A list of all extracted triples for this document.
    """
    all_doc_triples = []
    doc_id = doc_data.get("doc_id")
    if not doc_id:
        print(f"Warning: Document node missing 'doc_id'. Skipping: {doc_data}")
        return []

    # 1. Triples from Metadata (Document Properties)
    metadata_fields = ["original_url", "file_type", "language", "html_meta_title", "html_meta_description", "html_meta_abstract", "html_meta_keywords", "html_meta_generator"]
    for key in metadata_fields:
        value = doc_data.get("metadata", {}).get(key)
        if value is not None and value != "":
            all_doc_triples.append((doc_id, f"has_{key}", str(value)))

    # Track entities found in the document's main descriptive fields for higher-level inference
    doc_level_satellites_instruments = set()
    doc_level_products_applications = set()
    doc_level_techniques = set()

    # 2. Extract from extracted_tables (Primary content source)
    if doc_data.get("extracted_tables"):
        for table in doc_data["extracted_tables"]:
            headers = [h.lower() for h in table.get("headers", [])]
            data = table.get("data", [])

            try:
                core_metadata_col_idx = headers.index("core metadata elements")
                definition_col_idx = headers.index("definition")
            except ValueError:
                continue

            table_text_for_nlp = []

            for row in data:
                if len(row) > max(core_metadata_col_idx, definition_col_idx):
                    element_name = row[core_metadata_col_idx].strip()
                    definition_text = row[definition_col_idx].strip()

                    if element_name and definition_text:
                        clean_element_name = re.sub(r'[^a-zA-Z0-9_]', '', element_name.lower().replace(" ", "_"))
                        if clean_element_name:
                            all_doc_triples.append((doc_id, f"has_{clean_element_name}", definition_text))

                        # --- Enhanced Inference based on specific metadata fields ---
                        current_field_entities = []
                        current_field_triples = []

                        if element_name.lower() == "title":
                            table_text_for_nlp.append(f"The document title is: {definition_text}.")
                            current_field_triples, current_field_entities = extract_content_triples(definition_text, doc_data.get("entities"))
                            all_doc_triples.extend(current_field_triples)
                            for ent_info in current_field_entities:
                                if ent_info["type"] in ["Product", "Application", "Mission"]:
                                    all_doc_triples.append((doc_id, "describes", ent_info["text"]))
                                if ent_info["type"] in ["Satellite", "Instrument"]:
                                    doc_level_satellites_instruments.add(ent_info["text"])
                                if ent_info["type"] in ["Product", "Application"]:
                                    doc_level_products_applications.add(ent_info["text"])

                        elif element_name.lower() == "abstract":
                            table_text_for_nlp.append(definition_text)
                            current_field_triples, current_field_entities = extract_content_triples(definition_text, doc_data.get("entities"))
                            all_doc_triples.extend(current_field_triples)
                            for ent_info in current_field_entities:
                                if ent_info["type"] != "Unknown":
                                    all_doc_triples.append((doc_id, "mentions", ent_info["text"]))
                                if ent_info["type"] in ["Satellite", "Instrument"]:
                                    doc_level_satellites_instruments.add(ent_info["text"])
                                if ent_info["type"] in ["Product", "Application"]:
                                    doc_level_products_applications.add(ent_info["text"])

                        elif element_name.lower() == "data lineage or quality":
                            table_text_for_nlp.append(definition_text)
                            current_field_triples, current_field_entities = extract_content_triples(definition_text, doc_data.get("entities"))
                            all_doc_triples.extend(current_field_triples)
                            # Infer (Product, uses, Instrument/Technique)
                            products_in_lineage = {e["text"] for e in current_field_entities if e["type"] == "Product"}
                            instruments_techniques_in_lineage = {e["text"] for e in current_field_entities if e["type"] in ["Instrument", "Technique"]}
                            for prod in products_in_lineage:
                                for inst_tech in instruments_techniques_in_lineage:
                                    all_doc_triples.append((prod, "uses", inst_tech))
                            for ent_info in current_field_entities: # Add to doc-level tracking
                                if ent_info["type"] in ["Satellite", "Instrument"]:
                                    doc_level_satellites_instruments.add(ent_info["text"])
                                if ent_info["type"] in ["Product", "Application"]:
                                    doc_level_products_applications.add(ent_info["text"])
                                if ent_info["type"] == "Technique":
                                    doc_level_techniques.add(ent_info["text"])


                        elif element_name.lower() == "update frequency":
                            table_text_for_nlp.append(f"It is updated {definition_text}.")
                            canonical_time = get_canonical_entity_info(definition_text)
                            if canonical_time["type"] == "TimeInterval":
                                all_doc_triples.append((doc_id, "updated_every", canonical_time["text"]))
                            else:
                                all_doc_triples.append((doc_id, "has_update_frequency", definition_text))
                        
                        elif element_name.lower() == "responsible party" or element_name.lower() == "organization" or element_name.lower() == "dataset contact":
                            # Process contact info for organization and person
                            table_text_for_nlp.append(definition_text) # Add for general NLP
                            
                            # Extract organizations
                            org_matches = re.findall(r'(SAC \(ISRO\)|ISRO|MOSDAC|Indian Navy|WMO|IMD)', definition_text, re.IGNORECASE)
                            for org_match in set(org_matches):
                                canonical_org_contact = get_canonical_entity_info(org_match)
                                if canonical_org_contact["type"] == "Organization":
                                    all_doc_triples.append((doc_id, "has_contact_organization", canonical_org_contact["text"]))
                            
                            # Simple name extraction (can be improved with NER for PERSON)
                            # Look for capitalized words that are not common stop words or known organizations
                            potential_names = re.findall(r'\b[A-Z][a-z]+(?: [A-Z][a-z]+)*\b', definition_text)
                            for name in potential_names:
                                # Simple filter to avoid common single words or known orgs
                                if len(name.split()) > 1 and name.lower() not in [o.lower() for o in CANONICAL_ENTITIES if CANONICAL_ENTITIES[o]["type"] == "Organization"]:
                                    all_doc_triples.append((doc_id, "has_contact_person", name))


                        elif element_name.lower() == "keywords":
                            keywords = [k.strip() for k in re.split(r'[,/]', definition_text) if k.strip()]
                            for kw in keywords:
                                all_doc_triples.append((doc_id, "has_keyword", kw))
                                canonical_info = get_canonical_entity_info(kw)
                                if canonical_info["type"] != "Unknown":
                                    table_text_for_nlp.append(canonical_info["text"]) # Add keywords for general NLP processing
                                    # Add keywords to relevant doc-level sets
                                    if canonical_info["type"] in ["Satellite", "Instrument"]:
                                        doc_level_satellites_instruments.add(canonical_info["text"])
                                    if canonical_info["type"] in ["Product", "Application"]:
                                        doc_level_products_applications.add(canonical_info["text"])
                                    if canonical_info["type"] == "Technique":
                                        doc_level_techniques.add(canonical_info["text"])


                        elif element_name.lower() == "geographic extent" or element_name.lower() == "geographic name, geographic identifier" or element_name.lower() == "bounding box":
                             loc_matches = re.findall(r'\b(Indian Region|India|Ukai reservoir|Brahmaputra River|Tropics|Ocean|Indian Ocean|Asia Sector|Western Himalayan region|All-India beaches|New Delhi|Ahmedabad)\b', definition_text, re.IGNORECASE)
                             for loc_match in set(loc_matches):
                                 canonical_loc = get_canonical_entity_info(loc_match)
                                 if canonical_loc["type"] == "Location":
                                     all_doc_triples.append((doc_id, "covers_region", canonical_loc["text"]))
                        
                        elif element_name.lower() == "distribution information":
                            dist_format_matches = re.findall(r'\b(text|PNG|HDF|netCDF|geoTiff|JPG|GIF)\b', definition_text, re.IGNORECASE)
                            for fmt_match in set(dist_format_matches):
                                canonical_format = get_canonical_entity_info(fmt_match)
                                if canonical_format["type"] == "DataType/Format":
                                    all_doc_triples.append((doc_id, "available_in", canonical_format["text"]))
                            if "online download" in definition_text.lower():
                                all_doc_triples.append((doc_id, "provides", get_canonical_entity_info("Online Download")["text"]))
                        
                        elif element_name.lower() == "topic category":
                            canonical_app = get_canonical_entity_info(definition_text)
                            if canonical_app["type"] == "Application":
                                all_doc_triples.append((doc_id, "is_about_topic", canonical_app["text"]))
                                # If we have a primary satellite/instrument for this document, link it to the application
                                for sat_inst in doc_level_satellites_instruments:
                                    all_doc_triples.append((sat_inst, "supports", canonical_app["text"]))


            # After processing all rows, ensure general NLP is run on combined text
            if table_text_for_nlp:
                full_table_content_text = " ".join(table_text_for_nlp)
                content_triples, _ = extract_content_triples(full_table_content_text, doc_data.get("entities"))
                all_doc_triples.extend(content_triples)


    # --- Post-table processing for higher-level inference (using collected doc-level entities) ---
    # Infer (Satellite/Instrument, provides, Product/Application)
    for sat_inst in doc_level_satellites_instruments:
        for prod_app in doc_level_products_applications:
            # Add a direct 'provides' triple if not already present
            if (sat_inst, "provides", prod_app) not in all_doc_triples:
                all_doc_triples.append((sat_inst, "provides", prod_app))
    
    # Infer (Product, uses, Technique) from doc-level entities
    for prod_app in doc_level_products_applications:
        for tech in doc_level_techniques:
            if (prod_app, "uses", tech) not in all_doc_triples:
                all_doc_triples.append((prod_app, "uses", tech))


    # 3. "CONTAINS" relationships (Document -> Entity)
    # Re-collect all entities found during NLP processing of table data and other fields
    all_found_entities_in_text = set() # Use a set to avoid duplicates
    
    # Add entities from doc_level_sets (these were populated during table processing)
    all_found_entities_in_text.update(doc_level_satellites_instruments)
    all_found_entities_in_text.update(doc_level_products_applications)
    all_found_entities_in_text.update(doc_level_techniques)

    # Also add entities from keywords (already handled in keywords section, but ensure consistency)
    for table in doc_data.get("extracted_tables", []):
        headers = [h.lower() for h in table.get("headers", [])]
        data = table.get("data", [])
        try:
            core_metadata_col_idx = headers.index("core metadata elements")
            definition_col_idx = headers.index("definition")
            for row in data:
                if len(row) > max(core_metadata_col_idx, definition_col_idx):
                    element_name = row[core_metadata_col_idx].strip().lower()
                    definition_text = row[definition_col_idx].strip()
                    if element_name == "keywords":
                        keywords = [k.strip() for k in re.split(r'[,/]', definition_text) if k.strip()]
                        for kw in keywords:
                            canonical_info = get_canonical_entity_info(kw)
                            if canonical_info["type"] != "Unknown":
                                all_found_entities_in_text.add(canonical_info["text"])
        except ValueError:
            pass # Table format not as expected
            
    for entity_text in all_found_entities_in_text:
        # Ensure the doc_id contains the entity, if it's not already there
        if (doc_id, "contains", entity_text) not in all_doc_triples:
            all_doc_triples.append((doc_id, "contains", entity_text))


    # 4. "LINKS_TO" relationships (Document -> Other Document/URL)
    for link_info in doc_data.get("extracted_links", []):
        href = link_info.get("href")
        if href and not href.startswith("javascript:"):
            all_doc_triples.append((doc_id, "links_to", href))

    # 5. "HAS_TABLE" relationships
    if doc_data.get("extracted_tables"):
        for i, table_data in enumerate(doc_data["extracted_tables"]):
            table_id = f"Table_{doc_id}_{i+1}"
            all_doc_triples.append((doc_id, "has_table", table_id))


    return list(set(all_doc_triples)) # Ensure uniqueness across all types of triples for the doc

# No __main__ block here, as this file is now a module to be imported.