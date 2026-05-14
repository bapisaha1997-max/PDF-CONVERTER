import os
import re
import json
import fitz  # PyMuPDF
from groq import Groq
from flask import Flask, request, jsonify, send_file, render_template
from fpdf import FPDF
from fpdf.enums import MethodReturnValue, WrapMode, XPos, YPos
from dotenv import load_dotenv
import traceback

load_dotenv()

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ==========================================
# Groq API Configuration
# ==========================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_TIMEOUT_SECONDS = int(os.getenv("GROQ_TIMEOUT_SECONDS", "90"))

if GROQ_API_KEY and GROQ_API_KEY != "YOUR_GROQ_KEY_HERE":
    groq_client = Groq(api_key=GROQ_API_KEY, timeout=GROQ_TIMEOUT_SECONDS)
else:
    groq_client = None
# ==========================================

# Keywords in font names that indicate Legacy or Unicode Hindi fonts
HINDI_FONT_KEYWORDS = [
    "kruti", "dev", "devlys", "mangal", "chanakya", "shusha", "akruti", 
    "hindi", "lipi", "agra", "kokila", "aparajita", "utsah",
    "krishna", "yogesh", "kundli", "abbasi", "bhaskar", "shree", "kanak",
    "ambika", "shivaji", "dv-"
]

# Standard Devanagari Unicode range
HINDI_UNICODE_REGEX = re.compile(r'[\u0900-\u097F]')

def is_hindi_font_name(font_name):
    fn = font_name.lower()
    # Exclude false positives from standard PDF/System names
    if "device" in fn or "dejavu" in fn or "standard" in fn:
        return False
    return any(kw in fn for kw in HINDI_FONT_KEYWORDS)

def looks_like_garbled_legacy(text):
    """Heuristic to detect garbled ASCII produced by legacy Hindi fonts."""
    if not text.strip():
        return False
    # Common legacy markers: characters like ~ { } | \ ^ [ ] are used for matras/half-chars
    legacy_markers = "~{}|\\^[]"
    marker_count = sum(1 for c in text if c in legacy_markers)
    
    # If it has multiple markers, it's almost certainly legacy Hindi
    if marker_count >= 2:
        return True
    # If it has one marker and is short, likely legacy
    if marker_count >= 1 and len(text) < 15:
        return True
    
    # Check for character distribution that is unlikely in English
    # e.g. very frequent lowercase vowels with weird punctuation
    # This is a bit risky, so we'll stick to markers for now.
    return False

def clean_extracted_line(line):
    if not line.strip():
        return ""
    
    # 1. Start/End of line slashes (minimal)
    line = re.sub(r'^\s*/\s*', '', line)
    line = re.sub(r'\s*/\s*$', '', line)
    # 2. Around brackets/options labels - be careful not to remove content
    line = re.sub(r'[\s/]*([\(\[][a-zA-Z0-9][\)\]])[\s/]*', r' \1 ', line).strip()
    # 3. Around question numbers
    line = re.sub(r'[\s/]*(\d+[\.\)])[\s/]*', r'\1 ', line).strip()
    # 4. Between content (loose slashes) - only remove isolated slashes
    line = re.sub(r'\s+/\s+', ' ', line)
    # 5. Collapse multiple spaces
    line = re.sub(r'\s+', ' ', line).strip()
    
    # Don't aggressively filter - only remove if it's obviously noise
    # A line with at least one alphanumeric character should be kept
    alnum_count = sum(1 for c in line if c.isalnum())
    if alnum_count == 0:
        return ""
            
    return line


def normalize_question_boundaries(text):
    """Fix PDF extraction artifacts like 'th6. What...' glued to the previous line."""
    text = re.sub(r'\b(?:st|nd|rd|th)([0-9]+[.)])\s+', r'\n\1 ', text, flags=re.IGNORECASE)
    text = re.sub(r'(?<!^)(?<!\n)(?<!\d)([0-9]{1,3}[.)]\s+(?:What|If|The|Find|Arrange|Which|When)\b)', r'\n\1', text)
    return text


@app.errorhandler(Exception)
def handle_exception(e):
    # Pass through HTTP errors
    if hasattr(e, 'code') and e.code < 500:
        return jsonify({"error": str(e)}), e.code
    
    # Capture the full traceback
    tb = traceback.format_exc()
    print(f"CRITICAL ERROR:\n{tb}")
    
    return jsonify({
        "error": "Internal Server Error",
        "details": str(e),
        "traceback": tb
    }), 500


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    try:
        filename = os.path.basename(file.filename)
        if not filename.lower().endswith('.pdf'):
            return jsonify({"error": "Only PDF files are supported."}), 400
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        doc = fitz.open(filepath)
        extracted_english_lines = []
        
        for page in doc:
            # Grouping by lines manually for better control
            blocks = page.get_text("dict")["blocks"]
            spans_on_page = []
            for b in blocks:
                if "lines" in b:
                    for line in b["lines"]:
                        for span in line["spans"]:
                            spans_on_page.append(span)
            
            # Sort spans by vertical position (Y), then horizontal (X)
            spans_on_page.sort(key=lambda x: (round(x["bbox"][1]), x["bbox"][0]))
            
            # Split into columns (assuming standard A4 / center split around 297)
            mid = page.rect.width / 2
            left_spans = [s for s in spans_on_page if s["bbox"][0] < mid]
            right_spans = [s for s in spans_on_page if s["bbox"][0] >= mid]
            
            # Sort each column individually (Reading order: Top to Bottom)
            left_spans.sort(key=lambda x: (round(x["bbox"][1]), x["bbox"][0]))
            right_spans.sort(key=lambda x: (round(x["bbox"][1]), x["bbox"][0]))
            
            # Sequence the extraction: Left column first, then Right column
            for spans_subset in [left_spans, right_spans]:
                current_line_y = -1
                current_line_text = ""
                for span in spans_subset:
                    if abs(span["bbox"][1] - current_line_y) > 2: # New line detected
                        if current_line_text.strip():
                            cleaned = clean_extracted_line(current_line_text)
                            if cleaned and (not extracted_english_lines or cleaned != extracted_english_lines[-1]):
                                extracted_english_lines.append(cleaned)
                        current_line_y = span["bbox"][1]
                        current_line_text = ""
                    
                    # Clean text of common noise
                    text = span["text"].strip()
                    if not text or text == "/":
                        continue
                    
                    # Check for option labels and common English words
                    contains_option = bool(re.search(r'[\(\[]?[a-dA-D0-9][\.\)\]।]', text))
                    # Also check if this is a standalone option letter (a, b, c, d) or digit that follows an opening paren
                    is_option_letter = bool(re.match(r'^[a-dA-D0-9]$', text)) and (
                        current_line_text.endswith('(') or current_line_text.endswith('[')
                    )
                    # Extended list of common English words including days, months, and exam terms
                    common_eng = any(w in text.lower() for w in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", 
                                                                  "thursday", "monday", "tuesday", "wednesday", "friday", "saturday", "sunday",
                                                                  "day", "date", "year", "question", "answer", "option", "the", "and", "but",
                                                                  "reasoning", "ssc", "cgl", "chsl", "rrb", "alp", "police", "bank", "clerk"])

                    # Filter Hindi/Legacy fonts as before, BUT always keep options or common English
                    if contains_option or is_option_letter or common_eng:
                         if current_line_text.endswith('(') or text.startswith(')') or text.startswith('.'):
                             current_line_text += text
                         else:
                             current_line_text += " " + text
                    elif not is_hindi_font_name(span["font"]) and not bool(HINDI_UNICODE_REGEX.search(span["text"])):
                        # Don't filter garbled_legacy check - keep all non-Hindi content
                        if current_line_text.endswith('(') or text.startswith(')') or text.startswith('.'):
                            current_line_text += text
                        else:
                            current_line_text += " " + text

                if current_line_text.strip():
                    cleaned = clean_extracted_line(current_line_text)
                    # Less strict deduplication - allow consecutive identical lines if they contain options
                    if cleaned:
                        is_option = bool(re.search(r'[\(\[]?[a-dA-D][\.\)\]]', cleaned))
                        if is_option or not extracted_english_lines or cleaned != extracted_english_lines[-1]:
                            extracted_english_lines.append(cleaned)
        
        doc.close()
        
        full_text = normalize_question_boundaries("\n".join(extracted_english_lines))
        return jsonify({"english_text": full_text, "filename": filename})
        
    except Exception as e:
        print(f"PDF Extraction Error: {str(e)}")
        return jsonify({"error": f"PDF Extraction Error: {str(e)}"}), 500

def translate_with_groq(text):
    """Fallback translation using Groq (Llama 3)."""
    if not groq_client:
        return None
        
    prompt = (
        "Translate this English exam/document into professional Bengali. "
        "IMPORTANT RULES:\n"
        "1. Strictly maintain the structure of questions and options.\n"
        "2. Keep ALL option letters (a, b, c, d) and question numbers (e.g., 1., 2., 3.) identical to the source.\n"
        "3. Output ONLY the translated text, no conversational filler or intro/outro.\n"
        "4. DO NOT omit any text. Every sentence must be translated.\n"
        "5. Standard format:\n"
        "   Number. Question Text\n"
        "   (a) Option text (b) Option text\n"
        "   (c) Option text (d) Option text\n\n"
        f"Text to translate:\n{text}"
    )
    
    try:
        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are a professional translator specializing in English to Bengali."},
                {"role": "user", "content": prompt}
            ],
            model=GROQ_MODEL,
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f"Groq API Error: {str(e)}")
        return None


QUESTION_START_RE = re.compile(r'(?m)^\s*(?:\*\*|__)?([0-9০-৯]+[.)।].*)')
OPTION_LABEL_RE = re.compile(r'(?i)(?:^|\s)(\(?[a-d]\)|\[[a-d]\]|[a-d][.)])')
BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


DAY_TRANSLATIONS = {
    "monday": "সোমবার",
    "tuesday": "মঙ্গলবার",
    "wednesday": "বুধবার",
    "thursday": "বৃহস্পতিবার",
    "friday": "শুক্রবার",
    "saturday": "শনিবার",
    "sunday": "রবিবার",
    "none": "কোনোটিই নয়",
}


COMMON_ENGLISH_RE = re.compile(
    r'\b(what|which|when|where|if|then|find|day|week|born|birthday|date|will|was|is|on|of|the|january|february|march|april|may|june|july|august|september|october|november|december)\b',
    re.IGNORECASE,
)


def is_exam_or_brand_line(text):
    lowered = text.lower()
    return any(marker in lowered for marker in [
        "ssc", "cgl", "chsl", "rrb", "alp", "police", "rankers", "gurukul",
        "vikramjeet", "reasoning", "calendar", "join", "official", "youtube",
    ])


def is_real_english_line(text):
    return bool(COMMON_ENGLISH_RE.search(text))


def is_option_text(text):
    return "/" in text and any(day in text.lower() for day in DAY_TRANSLATIONS)


def translate_option_line(text):
    def replace_match(match):
        english_word = match.group(1)
        return f"{english_word}/{DAY_TRANSLATIONS[english_word.lower()]}"

    text = re.sub(
        r'\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|None)\b\s*/\s*[^)\s]+(?:\s+[^\s]+)?',
        replace_match,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def get_option_bengali_word(text):
    match = re.search(r'\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|None)\b\s*/', text, flags=re.IGNORECASE)
    if not match:
        return None
    return DAY_TRANSLATIONS[match.group(1).lower()]


def get_option_prefix(text):
    slash_index = text.find("/")
    if slash_index < 0:
        return text
    return text[:slash_index].rstrip() + "/"


def looks_like_legacy_hindi_line(text):
    if not text or is_exam_or_brand_line(text) or is_real_english_line(text):
        return False
    if re.fullmatch(r'\d+[.)]?', text.strip()):
        return False
    if is_option_text(text):
        return False
    if len(text.strip()) < 8:
        return False
    legacy_hits = sum(1 for token in ["dks", "dk", "gks", "gS", "Fkk", "fn", "fnu", "lIrkg", "tUe", "iQ", "vxj", "rks"] if token in text)
    return legacy_hits >= 1 or looks_like_garbled_legacy(text)


def union_rect(rects, padding=1.2):
    rect = fitz.Rect(rects[0])
    for item in rects[1:]:
        rect.include_rect(fitz.Rect(item))
    rect.x0 -= padding
    rect.y0 -= padding
    rect.x1 += padding
    rect.y1 += padding
    return rect


def translate_lines_to_bengali(lines):
    if not lines:
        return []
    if not groq_client:
        raise Exception("Groq API not initialized. Check your GROQ_API_KEY in environment variables.")

    prompt = (
        "Translate each English exam line into natural Bengali. "
        "Return ONLY a valid JSON array of strings, same length and same order as input. "
        "Do not include markdown. Keep numbers and dates accurate.\n\n"
        f"Input JSON array:\n{json.dumps(lines, ensure_ascii=False)}"
    )
    chat_completion = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": "You translate English exam questions into concise Bengali."},
            {"role": "user", "content": prompt}
        ],
        model=GROQ_MODEL,
    )
    content = chat_completion.choices[0].message.content.strip()
    content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        translated = json.loads(content)
    except json.JSONDecodeError:
        # Try to extract a JSON array from partial/malformed responses
        start = content.find('[')
        end = content.rfind(']')
        if start >= 0 and end > start:
            translated = json.loads(content[start:end + 1])
        else:
            raise Exception("Groq returned an un-parseable translation response.")
    if not isinstance(translated, list):
        raise Exception("Groq returned a non-list translation response.")
    # Pad with empty strings if Groq returned fewer items than requested
    while len(translated) < len(lines):
        translated.append("")
    return [str(item).strip() for item in translated[:len(lines)]]


def extract_json_array(text):
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def option_labels_in_text(text):
    labels = []
    for match in OPTION_LABEL_RE.finditer(text):
        label = re.sub(r'[^a-dA-D]', '', match.group(1)).lower()
        if label:
            labels.append(label)
    return labels


def translation_kept_options(source, translated):
    source_labels = option_labels_in_text(source)
    translated_labels = option_labels_in_text(translated)
    return all(label in translated_labels for label in source_labels)


def translate_question_batch(blocks):
    prompt = (
        "Translate these English exam question blocks into professional Bengali.\n"
        "Return ONLY a valid JSON array of strings, same length and same order as input.\n"
        "Critical rules:\n"
        "1. Do not omit anything.\n"
        "2. Preserve every question number exactly, like 1., 2., 14.\n"
        "3. Preserve every option label exactly, like (a), (b), (c), (d).\n"
        "4. If a block has options, the translated block must contain all options from the source block.\n"
        "5. Preserve line breaks inside each string as much as possible.\n"
        "6. Output no markdown and no explanation.\n\n"
        f"Input JSON array:\n{json.dumps(blocks, ensure_ascii=False)}"
    )
    chat_completion = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": "You are a precise English-to-Bengali exam translator. Completeness is more important than style."},
            {"role": "user", "content": prompt}
        ],
        model=GROQ_MODEL,
        temperature=0,
    )
    translated = extract_json_array(chat_completion.choices[0].message.content)
    if not isinstance(translated, list) or len(translated) != len(blocks):
        raise Exception("Groq returned an invalid translation batch.")
    return [str(item).strip() for item in translated]


def translate_question_block_strict(block):
    translated = translate_question_batch([block])[0]
    if translation_kept_options(block, translated):
        return translated

    repair_prompt = (
        "Your previous translation missed one or more options. Translate this ONE exam block again.\n"
        "Return ONLY the translated text, not JSON.\n"
        "Keep the question number and every option label exactly. Include all source options.\n\n"
        f"Source block:\n{block}"
    )
    chat_completion = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": "You are a precise English-to-Bengali exam translator. Never omit MCQ options."},
            {"role": "user", "content": repair_prompt}
        ],
        model=GROQ_MODEL,
        temperature=0,
    )
    repaired = chat_completion.choices[0].message.content.strip()
    repaired = re.sub(r'^```(?:text)?\s*|\s*```$', '', repaired, flags=re.IGNORECASE | re.MULTILINE).strip()
    return repaired


def translate_document_by_questions(text):
    if not groq_client:
        raise Exception("Groq API not initialized. Check your GROQ_API_KEY in environment variables.")

    blocks = get_question_blocks(text)
    if not blocks:
        translated = translate_with_groq(text)
        if translated:
            return translated
        raise Exception("Groq translation failed.")

    translated_blocks = []
    batch = []
    batch_chars = 0
    max_batch_chars = int(os.getenv("GROQ_BATCH_MAX_CHARS", "8000"))
    max_batch_blocks = int(os.getenv("GROQ_BATCH_MAX_BLOCKS", "30"))

    def flush_batch():
        nonlocal batch, batch_chars
        if not batch:
            return
        print(f"Translating batch: {len(batch)} blocks, {batch_chars} chars", flush=True)
        try:
            translated = translate_question_batch(batch)
        except Exception as e:
            print(f"Batch translation failed, falling back to strict per-question translation: {e}", flush=True)
            translated = [translate_question_block_strict(block) for block in batch]

        translated_blocks.extend(translated)

        batch = []
        batch_chars = 0

    for block in blocks:
        block_len = len(block)
        if batch and (batch_chars + block_len > max_batch_chars or len(batch) >= max_batch_blocks):
            flush_batch()
        batch.append(block)
        batch_chars += block_len

    flush_batch()
    return "\n\n".join(translated_blocks)


def translate_blocks_with_fallback(blocks):
    try:
        return translate_question_batch(blocks)
    except Exception as e:
        print(f"Chunk translation failed, falling back to strict per-question translation: {e}", flush=True)
        return [translate_question_block_strict(block) for block in blocks]


def build_translation_chunk(blocks, start_index, max_blocks=10, max_chars=4000):
    chunk = []
    chunk_chars = 0
    index = start_index

    while index < len(blocks) and len(chunk) < max_blocks:
        block = blocks[index]
        block_len = len(block)
        if chunk and chunk_chars + block_len > max_chars:
            break
        chunk.append(block)
        chunk_chars += block_len
        index += 1

    return chunk, index


def get_overlay_operations(doc):
    operations = []
    translation_sources = []

    for page_index, page in enumerate(doc):
        line_rows = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if not text:
                    continue
                bbox = fitz.Rect(line["bbox"])
                if bbox.y0 < 205 or bbox.y0 > page.rect.height - 55:
                    continue
                if bbox.height > 35:
                    continue
                line_rows.append({"text": text, "bbox": bbox, "column": 0 if bbox.x0 < page.rect.width / 2 else 1})

        line_rows.sort(key=lambda row: (row["bbox"].y0, row["bbox"].x0))
        english_buffers = {0: [], 1: []}
        pending_legacy = {0: None, 1: None}

        def flush_legacy(column):
            group = pending_legacy[column]
            if not group:
                return
            source = " ".join(english_buffers[column]).strip()
            if source:
                op = {
                    "page_index": page_index,
                    "rect": union_rect(group["rects"]),
                    "source": source,
                    "translated": None,
                }
                operations.append(op)
                translation_sources.append(source)
            pending_legacy[column] = None
            english_buffers[column] = []

        for row in line_rows:
            text = row["text"]
            column = row["column"]

            if is_option_text(text):
                flush_legacy(column)
                translated_option = get_option_bengali_word(text)
                if translated_option:
                    operations.append({
                        "page_index": page_index,
                        "rect": union_rect([row["bbox"]], padding=0.8),
                        "source": None,
                        "translated": translated_option,
                        "kind": "option",
                        "prefix": get_option_prefix(text),
                    })
                continue

            if looks_like_legacy_hindi_line(text):
                group = pending_legacy[column]
                if group and row["bbox"].y0 - group["last_y"] <= 16:
                    group["rects"].append(row["bbox"])
                    group["last_y"] = row["bbox"].y0
                else:
                    flush_legacy(column)
                    pending_legacy[column] = {"rects": [row["bbox"]], "last_y": row["bbox"].y0}
                continue

            flush_legacy(column)

            if is_real_english_line(text) and not is_exam_or_brand_line(text):
                english_buffers[column].append(text)
                if len(english_buffers[column]) > 3:
                    english_buffers[column] = english_buffers[column][-3:]

        flush_legacy(0)
        flush_legacy(1)

    return operations, translation_sources


def fit_textbox_fontsize(page, rect, text, fontname, fontfile, max_size=8.0, min_size=5.5):
    size = max_size
    while size >= min_size:
        probe = page.insert_textbox(
            rect,
            text,
            fontname=fontname,
            fontfile=fontfile,
            fontsize=size,
            render_mode=3,
            align=fitz.TEXT_ALIGN_LEFT,
        )
        if probe >= 0:
            return size
        size -= 0.5
    return min_size


@app.route('/replace_hindi_pdf', methods=['POST'])
def replace_hindi_pdf():
    data = request.json or {}
    filename = os.path.basename(data.get("filename", ""))
    if not filename:
        return jsonify({"error": "No source filename provided"}), 400

    source_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(source_path):
        return jsonify({"error": f"Source PDF not found: {filename}"}), 404

    font_path = os.path.join("fonts", "NotoSansBengali-Regular.ttf")
    if not os.path.exists(font_path):
        return jsonify({"error": "Bengali font missing: fonts/NotoSansBengali-Regular.ttf"}), 500

    try:
        doc = fitz.open(source_path)
        operations, translation_sources = get_overlay_operations(doc)
        translated_lines = translate_lines_to_bengali(translation_sources)
        translated_iter = iter(translated_lines)

        for op in operations:
            if op["translated"] is None:
                try:
                    op["translated"] = next(translated_iter)
                except StopIteration:
                    op["translated"] = ""

            page = doc[op["page_index"]]
            rect = fitz.Rect(op["rect"])
            page.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
            if op.get("kind") == "option":
                option_size = 7.5
                prefix = op.get("prefix", "")
                prefix_rect = fitz.Rect(rect)
                page.insert_textbox(
                    prefix_rect,
                    prefix,
                    fontname="helv",
                    fontsize=option_size,
                    color=(0, 0, 0),
                    align=fitz.TEXT_ALIGN_LEFT,
                    overlay=True,
                )
                prefix_width = fitz.get_text_length(prefix, fontname="helv", fontsize=option_size)
                bengali_rect = fitz.Rect(rect)
                bengali_rect.x0 = min(bengali_rect.x1 - 6, bengali_rect.x0 + prefix_width + 1)
                page.insert_textbox(
                    bengali_rect,
                    op["translated"],
                    fontname="BengaliOverlay",
                    fontfile=font_path,
                    fontsize=option_size,
                    color=(0, 0, 0),
                    align=fitz.TEXT_ALIGN_LEFT,
                    overlay=True,
                )
                continue

            fontsize = fit_textbox_fontsize(page, rect, op["translated"], "BengaliOverlay", font_path)
            page.insert_textbox(
                rect,
                op["translated"],
                fontname="BengaliOverlay",
                fontfile=font_path,
                fontsize=fontsize,
                color=(0, 0, 0),
                align=fitz.TEXT_ALIGN_LEFT,
                overlay=True,
            )

        output_path = os.path.join(app.config['UPLOAD_FOLDER'], "hindi_replaced_bengali.pdf")
        doc.save(output_path, garbage=4, deflate=True)
        doc.close()
        return send_file(output_path, as_attachment=True)
    except Exception as e:
        print(f"Hindi Replacement Error: {str(e)}")
        return jsonify({"error": f"Hindi Replacement Error: {str(e)}"}), 500

@app.route('/translate', methods=['POST'])
def translate_text():
    if groq_client is None:
        return jsonify({"error": "Groq API not initialized. Check your GROQ_API_KEY in environment variables."}), 400
        
    data = request.json
    text = data.get("text")
    
    if not text:
        return jsonify({"error": "No text provided"}), 400
    
    try:
        translated_text = translate_document_by_questions(text)
        return jsonify({"bengali_text": translated_text})
    except Exception as e:
        print(f"AI Translation Error: {str(e)}")
        return jsonify({"error": f"AI Translation Error: {str(e)}"}), 500


@app.route('/translate_chunk', methods=['POST'])
def translate_chunk():
    if groq_client is None:
        return jsonify({"error": "Groq API not initialized. Check your GROQ_API_KEY in environment variables."}), 400

    data = request.json or {}
    text = data.get("text", "")
    start_index = int(data.get("start_index", 0))

    if not text.strip():
        return jsonify({"error": "No text provided"}), 400

    try:
        blocks = get_question_blocks(text)
        if not blocks:
            blocks = [text]

        if start_index < 0 or start_index > len(blocks):
            return jsonify({"error": "Invalid translation chunk index."}), 400

        chunk, next_index = build_translation_chunk(blocks, start_index)
        print(f"Translating chunk: {start_index}-{next_index} of {len(blocks)}", flush=True)
        translated_blocks = translate_blocks_with_fallback(chunk) if chunk else []

        return jsonify({
            "bengali_text": "\n\n".join(translated_blocks),
            "next_index": next_index,
            "total_blocks": len(blocks),
            "done": next_index >= len(blocks),
        })
    except Exception as e:
        print(f"AI Translation Chunk Error: {str(e)}")
        return jsonify({"error": f"AI Translation Chunk Error: {str(e)}"}), 500


def normalize_pdf_text(text):
    """Normalize common punctuation while preserving Bengali/Unicode text."""
    replacements = {
        '\u2019': "'",   # Right single quotation mark
        '\u2018': "'",   # Left single quotation mark
        '\u201D': '"',   # Right double quotation mark
        '\u201C': '"',   # Left double quotation mark
        '\u2013': '-',   # En dash
        '\u2014': '-',   # Em dash
        '\u2010': '-',   # Hyphen
        '\u00A0': ' ',   # Non-breaking space
        '\u2026': '...',  # Ellipsis
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def sanitize_for_pdf(text):
    """Backward-compatible wrapper that no longer destroys Bengali text."""
    return normalize_pdf_text(text)


def get_question_blocks(text):
    """Splits text into chunks starting with a question number (e.g., '1.', '2.', '১।')."""
    text = normalize_question_boundaries(text)
    # Regex to find numbers (English or Bengali) followed by a dot, bracket, or daari,
    # at the start of a line (ignoring leading whitespace/markdown)
    blocks = QUESTION_START_RE.split(text)
    
    result = []
    # Re-assemble blocks
    if blocks and blocks[0].strip():
        result.append(blocks[0].strip())
        
    for i in range(1, len(blocks), 2):
        q_head = blocks[i]
        q_body = blocks[i+1] if i+1 < len(blocks) else ""
        result.append((q_head + q_body).strip())
    
    return result


def starts_with_question_number(text):
    return bool(re.match(r'^\s*(?:\*\*|__)?[0-9০-৯]+[.)।]', text or ""))


def get_numbered_question_blocks(text):
    return [block for block in get_question_blocks(text) if starts_with_question_number(block)]


def get_question_id(block):
    match = re.match(r'^\s*(?:\*\*|__)?([0-9০-৯]+)[.)।]', block or "")
    if not match:
        return None
    return match.group(1).translate(BENGALI_DIGITS)


def pair_question_blocks(english_blocks, bengali_blocks):
    """Pair English and Bengali question blocks by question ID.
    Falls back to positional pairing if ID matching fails for most blocks
    (e.g. Groq changed the number format or used Bengali digits).
    """
    if not bengali_blocks:
        return [(eng, "") for eng in english_blocks]

    bengali_by_id = {}
    used_bengali_indexes = set()
    for index, block in enumerate(bengali_blocks):
        qid = get_question_id(block)
        if qid and qid not in bengali_by_id:
            bengali_by_id[qid] = (index, block)

    pairs = []
    matched_count = 0
    for english_block in english_blocks:
        qid = get_question_id(english_block)
        bengali_match = ""
        if qid in bengali_by_id:
            bengali_index, bengali_match = bengali_by_id[qid]
            used_bengali_indexes.add(bengali_index)
            matched_count += 1
        pairs.append((english_block, bengali_match))

    # --- Positional fallback ---
    # If fewer than half the English blocks matched by ID AND the Bengali
    # split produced roughly the same number of blocks, pair by position.
    # This recovers from Groq returning different number formats.
    if matched_count < max(1, len(english_blocks) // 2) and len(bengali_blocks) >= len(english_blocks) // 2:
        print(f"pair_question_blocks: ID matching only found {matched_count}/{len(english_blocks)} "
              f"matches — falling back to positional pairing.", flush=True)
        return [
            (english_blocks[i], bengali_blocks[i] if i < len(bengali_blocks) else "")
            for i in range(len(english_blocks))
        ]

    for index, bengali_block in enumerate(bengali_blocks):
        if index not in used_bengali_indexes and get_question_id(bengali_block) is None:
            pairs.append(("", bengali_block))

    return pairs


def measure_multicell_height(pdf, width, line_height, text, font_family, font_size):
    """Measure wrapped text height without writing it to the page."""
    if not text:
        return line_height

    pdf.set_font(font_family, size=font_size)
    return pdf.multi_cell(
        width,
        line_height,
        text=text,
        dry_run=True,
        output=MethodReturnValue.HEIGHT,
        wrapmode=WrapMode.WORD,
    )


def write_wrapped_text(pdf, x, y, width, line_height, text, font_family, font_size):
    """Write wrapped text at (x, y). Cursor is NOT advanced by this call;
    callers manage current_y themselves to avoid double-advance."""
    pdf.set_xy(x, y)
    pdf.set_font(font_family, size=font_size)
    pdf.multi_cell(
        width,
        line_height,
        text=text,
        border=0,
        align='L',
        new_x=XPos.LEFT,
        new_y=YPos.TOP,   # Keep cursor at the top of this cell — caller advances Y
        wrapmode=WrapMode.WORD,
    )


def render_side_by_side_pdf(pdf, english_text, bengali_text, english_font, bengali_font):
    margin = 10
    gutter = 4
    line_height = 4.8
    col_width = (pdf.w - (margin * 2) - gutter) / 2
    page_bottom = pdf.h - 12
    current_y = pdf.get_y()

    english_blocks = get_numbered_question_blocks(english_text) or get_question_blocks(english_text) or [english_text]
    bengali_blocks = get_numbered_question_blocks(bengali_text) or get_question_blocks(bengali_text) or [bengali_text]
    pairs = pair_question_blocks(english_blocks, bengali_blocks)

    pdf.set_font(english_font, "B", 9)
    pdf.set_xy(margin, current_y)
    pdf.cell(col_width, 5, "English", border=0)
    pdf.set_xy(margin + col_width + gutter, current_y)
    pdf.cell(col_width, 5, "Bengali", border=0)
    current_y += 7

    for eng_block, ben_block in pairs:
        eng_block = eng_block.strip()
        ben_block = ben_block.strip()
        eng_height = measure_multicell_height(pdf, col_width, line_height, eng_block, english_font, 9)
        ben_height = measure_multicell_height(pdf, col_width, line_height, ben_block, bengali_font, 9)
        row_height = max(eng_height, ben_height, line_height) + 3

        if current_y + row_height > page_bottom:
            pdf.add_page()
            current_y = margin

        write_wrapped_text(pdf, margin, current_y, col_width, line_height, eng_block, english_font, 9)
        write_wrapped_text(pdf, margin + col_width + gutter, current_y, col_width, line_height, ben_block, bengali_font, 9)

        current_y += row_height


def render_standard_pdf(pdf, english_text, bengali_text, english_font, bengali_font):
    margin = 10
    width = pdf.w - (margin * 2)
    line_height = 5

    # Bengali section
    pdf.set_font(bengali_font, "", 12)
    pdf.set_xy(margin, pdf.get_y())
    pdf.cell(width, 8, "Bengali Translation", border=0, new_x=XPos.LEFT, new_y=YPos.NEXT)
    write_wrapped_text(pdf, margin, pdf.get_y(), width, line_height, bengali_text, bengali_font, 10)

    pdf.add_page()
    # English section
    pdf.set_font(english_font, "B", 12)
    pdf.set_xy(margin, pdf.get_y())
    pdf.cell(width, 8, "English Reference", border=0, new_x=XPos.LEFT, new_y=YPos.NEXT)
    write_wrapped_text(pdf, margin, pdf.get_y(), width, line_height, english_text, english_font, 10)


def find_latin_fallback_font():
    """Find a local TrueType font for Latin text inside Bengali runs."""
    candidates = [
        os.path.join("fonts", "NotoSans-Regular.ttf"),
        os.path.join("fonts", "DejaVuSans.ttf"),
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None

@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    data = request.json
    english_text = data.get("english_text", "")
    bengali_text = data.get("bengali_text", "")
    layout = data.get("layout", "side-by-side")
    
    # Normalize punctuation, but preserve Unicode Bengali characters.
    english_text = normalize_pdf_text(english_text)
    bengali_text = normalize_pdf_text(bengali_text)
    
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=10)
    pdf.add_page()
    
    font_path = os.path.join("fonts", "NotoSansBengali-Regular.ttf")
    has_font = os.path.exists(font_path)
    if not has_font:
        return jsonify({"error": "Bengali font missing: fonts/NotoSansBengali-Regular.ttf"}), 500
    
    pdf.add_font("Bengali", "", font_path)
    latin_fallback_path = find_latin_fallback_font()
    if latin_fallback_path:
        pdf.add_font("LatinFallback", "", latin_fallback_path)
        pdf.set_fallback_fonts(["LatinFallback"])

    english_font = "helvetica"
    bengali_font = "Bengali"
    
    # Title — written first so get_y() in render_* sees correct starting position
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 8, "English - Bengali Translation", border=0, align='C', new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    if layout == "standard":
        render_standard_pdf(pdf, english_text, bengali_text, english_font, bengali_font)
    else:
        render_side_by_side_pdf(pdf, english_text, bengali_text, english_font, bengali_font)
    
    output_path = os.path.join(app.config['UPLOAD_FOLDER'], "translated_result.pdf")
    pdf.output(output_path)
    
    return send_file(output_path, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
