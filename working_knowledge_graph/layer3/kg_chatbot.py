import json
import re
import os

# --- Configuration ---
# Ensure this path is correct relative to where you run the script,
# or provide the full path to your all_extracted_kg.json file.
KG_FILE = "C:/Users/kashy/Documents/Code/Projects/ai_chatbot/dynamic_web_crawler/layer3/all_extracted_kg.json"

# Global variable to store the loaded knowledge graph
knowledge_graph = {}

def load_knowledge_graph(file_path):
    """
    Loads the knowledge graph from a JSON file.
    """
    global knowledge_graph
    if not os.path.exists(file_path):
        print(f"Error: Knowledge Graph file not found at '{file_path}'")
        return False
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            knowledge_graph = json.load(f)
        print(f"Knowledge Graph loaded successfully from '{file_path}' with {len(knowledge_graph)} documents.")
        return True
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from '{file_path}': {e}")
        return False
    except Exception as e:
        print(f"An unexpected error occurred while loading the KG: {e}")
        return False

def ask_chatbot(query):
    """
    Processes a user query and attempts to find answers in the knowledge graph.
    This version includes more flexible search based on document content.
    """
    query = query.strip().lower()
    response = []

    # --- Query Patterns (Ordered by specificity/preference) ---

    # Pattern 1: "What is [predicate] of [subject_id]?" (Direct document property)
    # Example: "What is has_title of 0bc51275-f12b-4913-b829-d608eee4fab0?"
    match_predicate_doc_id = re.match(r"what is (has_[a-z_]+) of ([a-f0-9-]+)\??", query)
    if match_predicate_doc_id:
        predicate = match_predicate_doc_id.group(1)
        subject_id = match_predicate_doc_id.group(2)
        
        if subject_id in knowledge_graph:
            found_triples = [
                (s, p, o) for s, p, o in knowledge_graph[subject_id]
                if s.lower() == subject_id.lower() and p.lower() == predicate.lower()
            ]
            if found_triples:
                for s, p, o in found_triples:
                    response.append(f"  {s} {p.replace('has_', '').replace('_', ' ')} is: {o}")
                return response
            else:
                response.append(f"  I couldn't find information about '{predicate.replace('has_', '').replace('_', ' ')}' for document '{subject_id}'.")
        else:
            response.append(f"  I don't have information for document ID '{subject_id}'.")
        return response

    # Pattern 2: "Tell me about [subject_id]" (retrieves all direct properties of a document)
    # Example: "Tell me about 0bc51275-f12b-4913-b829-d608eee4fab0"
    match_about_doc_id = re.match(r"tell me about ([a-f0-9-]+)\??", query)
    if match_about_doc_id:
        subject_id = match_about_doc_id.group(1)
        if subject_id in knowledge_graph:
            doc_triples = knowledge_graph[subject_id]
            if doc_triples:
                response.append(f"  Here's what I know about '{subject_id}':")
                found_info = False
                for s, p, o in doc_triples:
                    if s.lower() == subject_id.lower() and p.startswith("has_"):
                        response.append(f"  - {p.replace('has_', '').replace('_', ' ')}: {o}")
                        found_info = True
                    elif s.lower() == subject_id.lower() and p == "contains":
                        response.append(f"  - Contains entity: {o}")
                        found_info = True
                    elif s.lower() == subject_id.lower() and p == "links_to":
                        response.append(f"  - Links to: {o}")
                        found_info = True
                    elif s.lower() == subject_id.lower() and p in ["provides", "developed_by", "updated_every", "covers_region", "uses", "is_derived_from"]:
                        response.append(f"  - {p.replace('_', ' ')}: {o}")
                        found_info = True
                if not found_info:
                    response.append(f"  I found the document, but no specific properties or links to list for '{subject_id}'.")
            else:
                response.append(f"  I found document ID '{subject_id}', but it has no associated information.")
        else:
            response.append(f"  I don't have information for document ID '{subject_id}'.")
        return response

    # Pattern 3: "What is [relationship] of [entity]?" or "What does [entity] [verb]?"
    # This is the more flexible pattern for entities that might not be doc_ids.
    # Example: "What is provides of INSAT-3D?"
    # Example: "What does INSAT-3D provide?"
    match_general_relation_entity = re.match(r"(what is|what does) ([a-z0-9\s-]+) (provides|provide|delivers|deliver|generates|generate|products)\??", query)
    if match_general_relation_entity:
        subject_entity_raw = match_general_relation_entity.group(2).strip()
        # Normalize the subject entity to match canonical names if possible
        # This requires access to CANONICAL_ENTITIES from kg_extractor, or a simplified version
        # For this example, we'll just use the raw entity for broad search
        subject_entity_normalized = subject_entity_raw # Simplistic normalization for now

        # Keywords for "provides" type relationships
        provides_keywords = ["provides", "provide", "delivers", "deliver", "generates", "generate", "products"]

        found_answers = set()
        
        # First, try to find direct triples where subject_entity is the subject
        for doc_id, triples in knowledge_graph.items():
            for s, p, o in triples:
                if s.lower() == subject_entity_normalized.lower() and p.lower() in provides_keywords:
                    found_answers.add(o)

        # If no direct triples, search in document text
        if not found_answers:
            # Find documents that contain the subject_entity
            relevant_doc_ids = set()
            for doc_id, triples in knowledge_graph.items():
                for s, p, o in triples:
                    if p == "contains" and o.lower() == subject_entity_normalized.lower():
                        relevant_doc_ids.add(s)
            
            # Search within descriptive fields of relevant documents
            for doc_id in relevant_doc_ids:
                doc_triples = knowledge_graph.get(doc_id, [])
                for s, p, o in doc_triples:
                    if s.lower() == doc_id.lower() and p in ["has_abstract", "has_title", "has_data_lineage_or_quality", "has_html_meta_description", "has_html_meta_abstract"]:
                        # Perform a simple keyword search within the text
                        text_content = str(o).lower()
                        
                        # Look for "subject_entity [provides_keyword] [something]"
                        for kw in provides_keywords:
                            # Regex to find patterns like "INSAT-3D provides rainfall data"
                            # This is a very basic regex and can be improved with more NLP
                            pattern = r"\b" + re.escape(subject_entity_normalized) + r"\b.*?(" + "|".join(provides_keywords) + r")\b(.*?)(?:\.|\n|$)"
                            matches = re.findall(pattern, text_content)
                            for match in matches:
                                # Extract the 'something' part
                                potential_product = match[1].strip()
                                if potential_product:
                                    # Basic cleaning: remove leading/trailing punctuation, common conjunctions
                                    potential_product = re.sub(r'^[.,;:\s]+|[.,;:\s]+$', '', potential_product)
                                    potential_product = re.sub(r'\band\b|\bor\b', '', potential_product).strip()
                                    if potential_product:
                                        found_answers.add(potential_product)
        
        if found_answers:
            response.append(f"  {subject_entity_raw} provides/generates: {', '.join(list(found_answers))}")
        else:
            response.append(f"  I couldn't find any specific information about what '{subject_entity_raw}' provides.")
        return response

    # Default fallback
    return ["  I'm a simple chatbot. Try asking:"] + \
           ["  - 'What is has_title of [document_id]?'"] + \
           ["  - 'Tell me about [document_id]'"] + \
           ["  - 'What does [entity_name] provide?' (e.g., 'What does INSAT-3D provide?')"] + \
           ["  - 'list docs' to see available document IDs."]


def list_document_ids():
    """Lists all available document IDs in the knowledge graph."""
    if knowledge_graph:
        doc_ids = list(knowledge_graph.keys())
        return [f"  Available Document IDs:"] + [f"  - {doc_id}" for doc_id in doc_ids]
    else:
        return ["  No documents loaded in the knowledge graph."]

# --- Main Chatbot Loop ---
if __name__ == "__main__":
    print("Initializing Knowledge Graph Chatbot...")
    if not load_knowledge_graph(KG_FILE):
        print("Chatbot cannot start without a loaded Knowledge Graph. Exiting.")
    else:
        print("\nChatbot Ready! Type 'exit' to quit.")
        print("Try asking:")
        print("  - 'What is has_title of 0bc51275-f12b-4913-9b42-594d9fd374ae?'")
        print("  - 'Tell me about 0bc51275-f12b-4913-9b42-594d9fd374ae'")
        print("  - 'What does INSAT-3D provide?'") # This is the new type of query
        print("  - 'list docs'")

        while True:
            user_input = input("\nYou: ").strip()
            if user_input.lower() == 'exit':
                print("Chatbot: Goodbye!")
                break
            elif user_input.lower() == 'list docs':
                responses = list_document_ids()
            else:
                responses = ask_chatbot(user_input)
            
            print("Chatbot:")
            for res_line in responses:
                print(res_line)
