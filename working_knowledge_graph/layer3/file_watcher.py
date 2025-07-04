# file_watcher.py

import os
import json
import hashlib
import time

# Import functions and constants from the kg_extractor module
from kg_extractor import process_document_node, nlp, CANONICAL_ENTITIES, RELATIONSHIP_RULES, get_canonical_entity_info, extract_content_triples

# --- Configuration ---
#e.g. INPUT_DIRECTORY = "C:/Users/kashy/Documents/Code/Projects/ai_chatbot/dynamic_web_crawler/layer2/processed"
INPUT_DIRECTORY = "C:/Users/kashy/Documents/Code/Projects/ai_chatbot/dynamic_web_crawler/layer2/processed" # <--- IMPORTANT: SET THIS TO YOUR DIRECTORY
STATE_FILE = "C:/Users/kashy/Documents/Code/Projects/ai_chatbot/dynamic_web_crawler/layer3/processed_files_state.json"
OUTPUT_KG_FILE = "C:/Users/kashy/Documents/Code/Projects/ai_chatbot/dynamic_web_crawler/layer3/all_extracted_kg.json"


# --- Helper Functions ---
def load_processed_state():
    """Loads the state of processed files from a JSON file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: {STATE_FILE} is corrupted. Starting with an empty state.")
            return {}
    return {}

def save_processed_state(state):
    """Saves the current state of processed files to a JSON file."""
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=4)

def calculate_file_hash(filepath):
    """Calculates the MD5 hash of a file's content."""
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b""): # Read in chunks
            hasher.update(chunk)
    return hasher.hexdigest()

# --- Main Processing Logic ---
def process_new_or_modified_files(base_dir):
    """
    Scans the base directory for JSON files, processes new or modified ones,
    and updates a state file to track changes.
    """
    processed_state = load_processed_state()
    all_extracted_knowledge = {} # To accumulate KG from all processed files in this run

    # Load existing KG results if any
    if os.path.exists(OUTPUT_KG_FILE):
        try:
            with open(OUTPUT_KG_FILE, 'r', encoding='utf-8') as f:
                all_extracted_knowledge = json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: {OUTPUT_KG_FILE} is corrupted or empty. Starting with an empty KG output.")
            all_extracted_knowledge = {}

    found_files_in_run = set() # Track files found in this run to remove old entries from state

    for root, _, files in os.walk(base_dir):
        for file in files:
            if file.lower().endswith(".json"): # Ensure case-insensitive check
                filepath = os.path.join(root, file)
                found_files_in_run.add(filepath)

                current_hash = calculate_file_hash(filepath)
                
                # Check if the file is new or its content has changed
                if filepath not in processed_state or processed_state[filepath].get('hash') != current_hash:
                    print(f"Processing: {filepath} (New or Modified)")
                    try:
                        with open(filepath, 'r', encoding='utf-8') as f:
                            doc_data = json.load(f)
                            
                            # Validate doc_id before processing
                            doc_id = doc_data.get("doc_id")
                            if not doc_id:
                                print(f"Warning: Skipping {filepath} - 'doc_id' not found in JSON.")
                                continue

                            # Call the processing function from kg_extractor
                            triples = process_document_node(doc_data)
                            
                            # Store triples indexed by doc_id
                            all_extracted_knowledge[doc_id] = triples
                            
                            # Update the processed state with the new hash
                            processed_state[filepath] = {'hash': current_hash}
                            print(f"  Extracted {len(triples)} triples for Document ID: {doc_id}.")
                            
                    except json.JSONDecodeError as e:
                        print(f"Error: Could not parse JSON file {filepath}. Error: {e}")
                    except Exception as e:
                        print(f"An unexpected error occurred while processing {filepath}: {e}")
                else:
                    print(f"Skipping: {filepath} (No changes detected)")

    # Clean up state file for files that no longer exist
    files_to_remove = [f for f in processed_state if f not in found_files_in_run]
    for f in files_to_remove:
        print(f"Removing {f} from state: File no longer exists.")
        del processed_state[f]
        # Optional: If you want to remove the corresponding doc_id from all_extracted_knowledge,
        # you'd need a mapping from filepath to doc_id, which isn't directly stored in 'state'.
        # For now, it will remain in all_extracted_knowledge until explicitly cleaned.

    save_processed_state(processed_state)
    
    # Save the accumulated knowledge graph
    with open(OUTPUT_KG_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_extracted_knowledge, f, indent=2)
    print(f"\nAll extracted knowledge graph data saved to {OUTPUT_KG_FILE}")

    return all_extracted_knowledge

if __name__ == "__main__":
    if not os.path.exists(INPUT_DIRECTORY):
        print(f"Error: The input directory '{INPUT_DIRECTORY}' does not exist.")
        print("Please create this directory and place your JSON files inside it,")
        print("or update the 'INPUT_DIRECTORY' variable in this script.")
        exit()

    print(f"Starting JSON file watcher in '{INPUT_DIRECTORY}'...")
    
    # Run the processing
    processed_kg = process_new_or_modified_files(INPUT_DIRECTORY)
    print("\n--- Summary of current Knowledge Graph ---")
    print(f"Total documents processed/tracked: {len(processed_kg)}")
    
    # You can uncomment the following lines to run in a continuous loop for monitoring
    # while True:
    #     print(f"\nMonitoring '{INPUT_DIRECTORY}' for changes (next check in 60 seconds)...")
    #     processed_kg = process_new_or_modified_files(INPUT_DIRECTORY)
    #     time.sleep(60) # Wait for 60 seconds before checking again