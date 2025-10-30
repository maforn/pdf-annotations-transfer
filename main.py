import fitz  # PyMuPDF
import sys
import os
import Levenshtein

# escape codes for console output colors
RED = "\033[91m"
BLUE = "\033[94m"
ENDC = "\033[0m"

# define the local page window size (e.g., +/- 5 pages) for first search
LOCAL_PAGE_WINDOW = 5

# set maximum distance for a transfer to be considered valid (e.g., 5 pages)
# can be different from the local pages limit
MAX_PAGE_DISTANCE = 5
# optionally stricter limit for fuzzy
FUZZY_MAX_PAGE_DISTANCE = 5

def find_best_fuzzy_match_in_pages(doc, text_to_find, threshold_ratio, base_allowance, page_indices):
    """
    Searches a subset of pages in the document for the "best" fuzzy match.
    
    Args:
        doc (fitz.Document): The document to search.
        text_to_find (str): The text to find.
        threshold_ratio (float): Allowed percentage difference (e.g., 0.3 for 30%).
        base_allowance (int): A small base number of allowed edits.
        page_indices (list[int]): A list of 0-based page indices to search.
        
    Returns:
        (fitz.Page, list[fitz.Quad]): The page and list of quads for the best
                                      match below the threshold, or (None, None).
    """
    words_to_find = text_to_find.split()
    n_words = len(words_to_find)
    if n_words == 0:
        return None, None
        
    best_match = {'page': None, 'quads': [], 'distance': float('inf')}
    
    # calculate the allowed edit distance using the configurable parameters
    allowed_distance = int(len(text_to_find) * threshold_ratio) + base_allowance
    
    for page_idx in page_indices:
        # Load the page from the document using its index
        page = doc.load_page(page_idx)
        page_words = page.get_text("words") 
        
        if len(page_words) < n_words:
            continue
            
        # Use a sliding window to check word combinations in nearby pages
        for i in range(len(page_words) - n_words + 1):
            window_words_info = page_words[i : i + n_words]
            candidate_text = " ".join([w[4] for w in window_words_info])
            
            # check with Levenshtein distance
            distance = Levenshtein.distance(text_to_find, candidate_text)
            
            if distance < best_match['distance']:
                best_match['distance'] = distance
                best_match['page'] = page
                best_match['quads'] = [fitz.Rect(w[:4]).quad for w in window_words_info]

    # only return the match if it's within the reasonable distance threshold
    if best_match['distance'] <= allowed_distance:
        return best_match['page'], best_match['quads']
        
    return None, None

def find_text_occurrence(doc, text, fuzzy_ratio, fuzzy_allowance, old_page_number):
    """
    Searches the document for a given text using a prioritized search order:
    1. Local Exact Match
    2. Full Document Exact Match
    3. Local Fuzzy Match
    4. Full Document Fuzzy Match
    
    Args:
        doc (fitz.Document): The document to search.
        text (str): The text to find.
        fuzzy_ratio (float): The configurable fuzzy threshold ratio.
        fuzzy_allowance (int): The configurable fuzzy threshold allowance.
        old_page_number (int): The 0-based page index of the original annotation.
        
    Returns:
        (fitz.Page, list[fitz.Quad], str): The page, list of quads, and
                                            match type ("exact", "fuzzy", "none").
    """
    search_text = " ".join(text.split())  # Normalize whitespace
    if not search_text:
        return None, None, "none"
        
    doc_pages = doc.page_count
    
    # calculate start and end page indices (0-based) for the local window
    start_page_idx = max(0, old_page_number - LOCAL_PAGE_WINDOW)
    end_page_idx = min(doc_pages, old_page_number + LOCAL_PAGE_WINDOW + 1) # +1 for slicing end
    local_page_indices = list(range(start_page_idx, end_page_idx))
    all_page_indices = list(range(doc_pages))

    # --- 1. Local Exact Match Search ---
    for page_idx in local_page_indices:
        page = doc.load_page(page_idx)
        quads = page.search_for(search_text, quads=True)
        if quads:
            return page, quads, "exact"

    # --- 2. Full Document Exact Match Search (Fallback) ---
    # Iterating all pages ensures we don't miss anything. The page distance check handles rejections later.
    for page_idx in all_page_indices:
        page = doc.load_page(page_idx)
        quads = page.search_for(search_text, quads=True)
        if quads:
            return page, quads, "exact"
            
    # --- 3. Local Fuzzy Match Search ---
    page, quads = find_best_fuzzy_match_in_pages(doc, search_text, fuzzy_ratio, fuzzy_allowance, local_page_indices)
    if page and quads:
        return page, quads, "fuzzy"

    # --- 4. Full Document Fuzzy Match Search (Fallback) ---
    page, quads = find_best_fuzzy_match_in_pages(doc, search_text, fuzzy_ratio, fuzzy_allowance, all_page_indices)
    if page and quads:
        return page, quads, "fuzzy"
            
    return None, None, "none"

def transfer_annotations(old_pdf_path, new_pdf_path, output_pdf_path, fuzzy_ratio, fuzzy_allowance):
    """
    Transfers annotations from an old PDF to a new version based on text content,
    and preserves the Table of Contents (Outline/Bookmarks).
    
    Supports: Highlight, Underline, Squiggly, and their associated reply comments.
    """
    
    print(f"Opening old PDF: {old_pdf_path}")
    print(f"Opening new PDF: {new_pdf_path}")
    
    try:
        old_doc = fitz.open(old_pdf_path)
        new_doc = fitz.open(new_pdf_path)
    except Exception as e:
        print(f"Error opening files: {e}")
        return

    # get the TOC structure from the new PDF before its file handle is closed.
    toc_data = new_doc.get_toc()
    output_doc = fitz.open()
    output_doc.insert_pdf(new_doc)
    new_doc.close() # Close the source document handle
    
    print(f"Created editable copy in memory.")

    # annotation supported by this script (Text Markups)
    markup_types = [
        fitz.PDF_ANNOT_HIGHLIGHT,
        fitz.PDF_ANNOT_UNDERLINE,
        fitz.PDF_ANNOT_SQUIGGLY
    ]
    
    transferred_count = 0
    transferred_fuzzy_count = 0
    failed_count = 0
    unsupported_count = 0
    failed_annotations = []
    
    # map to store newly created annotations, mapping old_xref -> new_annot
    new_annot_map = {}  

    # transfer text markup annotations ---
    print("\n--- Pass 1: Transferring Text Markups (Highlights, Underlines, etc.) ---")

    for old_page in old_doc:
        old_page_number = old_page.number # 0-based index
        for annot in old_page.annots():
            if annot.type[0] not in markup_types:
                unsupported_count += 1
                continue

            # get the text covered by the annotation
            target_text = old_page.get_text("text", clip=annot.rect).strip()
            
            # normalize whitespace (no new lines or multitple spaces)
            target_text = " ".join(target_text.split())

            if not target_text:
                print(f"{RED}  [FAIL] Page {old_page_number+1}: Skipping empty annotation.{ENDC}")
                failed_annotations.append((old_page_number + 1, "Empty Annotation Text"))
                failed_count += 1
                continue

            # search for this text in the new document, passing the old page index as a base
            new_page, quads, match_type = find_text_occurrence(output_doc, target_text, fuzzy_ratio, fuzzy_allowance, old_page_number)
            
            if match_type != "none":
                page_distance = abs(new_page.number - old_page_number)
                
                # reasonable page distance check
                if page_distance > MAX_PAGE_DISTANCE:
                    reason = f"Too far away (distance exceeds {MAX_PAGE_DISTANCE} pages)"
                    color = RED
                    
                    # log an error for transfers that are wildly distant
                    print(f"{color}  [FAIL] Page {old_page_number+1} -> Rejected {match_type.capitalize()} Match (New Page {new_page.number+1}): {reason}. '{target_text[:40]}...'{ENDC}")
                    failed_annotations.append((old_page_number + 1, target_text))
                    failed_count += 1
                    continue # skip this annotation

                # optional stricter check if it was a fuzzy match
                if match_type == "fuzzy" and page_distance > FUZZY_MAX_PAGE_DISTANCE:  
                    # Use a stricter 5-page threshold for fuzzy matches to ensure context similarity
                    reason = "Too far away for a safe fuzzy match (distance exceeds 5 pages)"
                    color = RED
                    print(f"{color}  [FAIL] Page {old_page_number+1} -> Rejected Fuzzy Match (New Page {new_page.number+1}): {reason}. '{target_text[:40]}...'{ENDC}")
                    failed_annotations.append((old_page_number + 1, target_text))
                    failed_count += 1
                    continue # skip this annotation

                # found the text (either exact or fuzzy) and the page distance is reasonable.
                new_annot = None
                annot_type = annot.type[0]
                
                if annot_type == fitz.PDF_ANNOT_HIGHLIGHT:
                    new_annot = new_page.add_highlight_annot(quads)
                elif annot_type == fitz.PDF_ANNOT_UNDERLINE:
                    new_annot = new_page.add_underline_annot(quads)
                elif annot_type == fitz.PDF_ANNOT_SQUIGGLY:
                    new_annot = new_page.add_squiggly_annot(quads)
                
                if new_annot:
                    old_info = annot.info
                    
                    if match_type == "exact":
                        if annot.colors.get("stroke"):
                            new_annot.set_colors(stroke=annot.colors["stroke"])
                        
                        new_annot.set_info(
                            content=old_info.get("content", ""),
                            title=old_info.get("title", "Note")
                        )
                        transferred_count += 1
                        print(f"  [OK-EXACT] Page {old_page_number+1} -> {new_page.number+1}: Transferred '{target_text[:40]}...'{ENDC}")
                        
                    elif match_type == "fuzzy":                      
                        old_content = old_info.get("content", "")
                        # include the original text and distance in the note for review
                        fuzzy_note = f"[FUZZY MATCH] Page distance: {page_distance}. Original text:\n'{target_text}'"
                        new_content = f"{fuzzy_note}\n\n{old_content}" if old_content else fuzzy_note
                        
                        new_annot.set_info(
                            content=new_content,
                            title=old_info.get("title", "Note (Fuzzy)")
                        )
                        transferred_fuzzy_count += 1
                        print(f"{BLUE}  [OK-FUZZY] Page {old_page_number+1} -> {new_page.number+1}: Transferred (Fuzzy) '{target_text[:40]}...'{ENDC}")
                    
                    new_annot.update()  # apply the changes
                    
                    # store in map for replies
                    new_annot_map[annot.xref] = new_annot
                
            else:
                # could not find the text in the new document
                failed_annotations.append((old_page_number + 1, target_text))
                failed_count += 1
                print(f"{RED}  [FAIL] Page {old_page_number+1}: Could not find text: '{target_text[:40]}...'{ENDC}")

    # check for sticky notes replies
    print("\n--- Pass 2: Transferring Replies (Sticky Notes) ---")
    for old_page in old_doc:
        for annot in old_page.annots():
            # check if it's a sticky note ('Text' annotation)
            if annot.type[0] == fitz.PDF_ANNOT_TEXT:
                # check if it's a reply to an annotation we just transferred
                if annot.irt_xref in new_annot_map:
                    parent_annot = new_annot_map[annot.irt_xref]
                    new_page = parent_annot.page
                    
                    # get content from old note
                    old_info = annot.info
                    content = old_info.get("content", "Reply")
                    title = old_info.get("title", "Reply")

                    # place the new sticky note near the top-right of its parent
                    tr = parent_annot.rect.tr  # top-right
                    point = fitz.Point(tr.x + 5, tr.y - 2)  # offset slightly
                    
                    new_note = new_page.add_text_annot(point, content)
                    new_note.set_info(content=content, title=title)
                    new_note.update()
                    
                    transferred_count += 1
                    print(f"  [OK] Page {new_page.number+1}: Transferred reply: '{content[:40]}...'{ENDC}")
                else:
                    # standalone sticky note, or parent wasn't transferred. Not supported, skip.
                    unsupported_count += 1

    # final save
    try:
        # transfer TOC
        if toc_data:
             output_doc.set_toc(toc_data)

        # save the final document with all new annotations
        output_doc.save(output_pdf_path, garbage=3, deflate=True)
        
        print("\n--- Transfer Complete ---")
        print(f"Successfully transferred (Exact): {transferred_count}")
        print(f"Successfully transferred (Fuzzy, Blue): {transferred_fuzzy_count}")
        print(f"Failed (Text not found/Too far): {failed_count}")
        print(f"Skipped (unsupported type): {unsupported_count}")
        print(f"\nFinal annotated file saved to: {output_pdf_path}")
        
        if failed_annotations:
            print(f"\n{RED}--- Failed Annotations Summary ({len(failed_annotations)} total) ---{ENDC}")
            for page, text in failed_annotations:
                # Print failed annotations in red
                print(f"{RED}Original Page {page}: '{text[:80]}...'{ENDC}")

    except Exception as e:
        print(f"Error saving final file: {e}")
    finally:
        old_doc.close()
        if 'output_doc' in locals():
            output_doc.close()

def main():
    if len(sys.argv) < 4 or len(sys.argv) > 6:
        print("Usage: python transfer_annotations.py <old_pdf> <new_pdf> <output_pdf> [fuzzy_ratio] [base_allowance]")
        print("Example 1 (Default Fuzzy): python transfer_annotations.py v1.pdf v2.pdf v2_with_annots.pdf")
        print("Example 2 (Custom Fuzzy): python transfer_annotations.py v1.pdf v2.pdf v2_with_annots.pdf 0.4 5")
        sys.exit(1)
        
    old_pdf_path = sys.argv[1]
    new_pdf_path = sys.argv[2]
    output_pdf_path = sys.argv[3]
    
    # default fuzzy parameters
    ratio = 0.3
    allowance = 5
    
    # Parse optional fuzzy ratio
    if len(sys.argv) >= 5:
        try:
            ratio = float(sys.argv[4])
        except ValueError:
            print(f"Warning: Invalid fuzzy ratio '{sys.argv[4]}'. Using default {ratio}.")
    
    # Parse optional base allowance
    if len(sys.argv) == 6:
        try:
            allowance = int(sys.argv[5])
        except ValueError:
            print(f"Warning: Invalid base allowance '{sys.argv[5]}'. Using default {allowance}.")
            
    if not os.path.exists(old_pdf_path):
        print(f"Error: File not found at {old_pdf_path}")
        sys.exit(1)
        
    if not os.path.exists(new_pdf_path):
        print(f"Error: File not found at {new_pdf_path}")
        sys.exit(1)
        
    print(
    """ _____                    __           __________________    ___                    _        _   _                 
|_   _|                  / _|          | ___ \\  _  \\  ___|  / _ \\                  | |      | | (_)                
  | |_ __ __ _ _ __  ___| |_ ___ _ __  | |_/ / | | | |_    / /_\\ \\_ __  _ __   ___ | |_ __ _| |_ _  ___  _ __  ___ 
  | | '__/ _` | '_ \\/ __|  _/ _ \\ '__| |  __/| | | |  _|   |  _  | '_ \\| '_ \\ / _ \\| __/ _` | __| |/ _ \\| '_ \\/ __|
  | | | | (_| | | | \\__ \\ ||  __/ |    | |   | |/ /| |     | | | | | | | | | | (_) | || (_| | |_| | (_) | | | \\__ \\
  \\_/_|  \\__,_|_| |_|___/_| \\___|_|    \\_|   |___/ \\_|     \\_| |_/_| |_|_| |_|\\___/ \\__\\__,_|\\__|_|\\___/|_| |_|___/ 
"""
    )
    transfer_annotations(old_pdf_path, new_pdf_path, output_pdf_path, ratio, allowance)

if __name__ == "__main__":
    main()
