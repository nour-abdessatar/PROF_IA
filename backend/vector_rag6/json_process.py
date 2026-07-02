"""
json_process.py
---------------
Production-ready preprocessing pipeline for RAG systems.
Handles Arabic (RTL) educational content mixed with mathematical
expressions and LaTeX, preparing it for embedding with
intfloat/multilingual-e5-base.

Usage:
    python json_process.py                        # input.json → output.json
    python json_process.py -i data.json -o out.json
    python json_process.py --max-tokens 256
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from transformers import AutoTokenizer

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TOKENIZER_NAME: str = "intfloat/multilingual-e5-base"
DEFAULT_MAX_TOKENS: int = 512   # hard ceiling for one chunk
CHUNK_OVERLAP_SENTENCES: int = 1  # sentences shared between consecutive chunks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-loaded tokenizer (singleton)
# ---------------------------------------------------------------------------

_tokenizer: Any = None


def _get_tokenizer() -> Any:
    """Return a cached instance of the multilingual-e5-base tokenizer."""
    global _tokenizer
    if _tokenizer is None:
        logger.info("Loading tokenizer '%s' …", TOKENIZER_NAME)
        _tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_NAME)
    return _tokenizer


# ---------------------------------------------------------------------------
# Step 1 – Citation & Markdown removal
# ---------------------------------------------------------------------------

# Matches [cite: 6], [cite_start], [cite: 14, 27], etc.
_CITE_RE = re.compile(r"\[cite(?:_start|_end)?(?:\s*:\s*[\d,\s]*)?\]", re.IGNORECASE)
# Markdown headings (##, ###, …)
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
# Markdown bold/italic markers (**, *, __, _)
_BOLD_ITALIC_RE = re.compile(r"(\*{1,3}|_{1,3})")
# Markdown unordered list bullet at line start
_BULLET_RE = re.compile(r"^\s*[*\-]\s+", re.MULTILINE)
# Consecutive blank lines → single blank line
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
# Leading/trailing whitespace per line
_LINE_WHITESPACE_RE = re.compile(r"[ \t]+")


def clean_text(text: str) -> str:
    """
    Remove citation artifacts and Markdown formatting from *text*, then
    normalize whitespace while preserving Arabic Unicode characters.

    Steps applied in order:
    1. Strip citation tags  ([cite: …], [cite_start])
    2. Strip Markdown headings, bold/italic markers, and list bullets
    3. Collapse multiple blank lines to one
    4. Normalize intra-line whitespace (multiple spaces/tabs → single space)
    5. Strip leading/trailing whitespace from the whole string

    Parameters
    ----------
    text : str
        Raw text extracted from the JSON ``content`` field.

    Returns
    -------
    str
        Cleaned, whitespace-normalized text.
    """
    # 1. Citations
    text = _CITE_RE.sub("", text)

    # 2. Markdown
    text = _HEADING_RE.sub("", text)
    text = _BOLD_ITALIC_RE.sub("", text)
    text = _BULLET_RE.sub("", text)

    # 3–4. Whitespace
    lines = text.split("\n")
    lines = [_LINE_WHITESPACE_RE.sub(" ", line).strip() for line in lines]
    text = "\n".join(lines)
    text = _MULTI_BLANK_RE.sub("\n\n", text)

    return text.strip()


# ---------------------------------------------------------------------------
# Step 2 – Math & LaTeX normalisation
# ---------------------------------------------------------------------------

# Unicode math operators that should become ASCII equivalents
_MATH_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"×"), "*"),       # multiplication sign → asterisk
    (re.compile(r"−"), "-"),       # minus sign U+2212 → hyphen-minus
    (re.compile(r"÷"), "/"),       # division sign → slash
    (re.compile(r"≤"), "<="),
    (re.compile(r"≥"), ">="),
    (re.compile(r"≠"), "!="),
]

# Simple subscript/superscript LaTeX: $D_{12}$ → D_12,  $a^{n}$ → a^n
_SIMPLE_LATEX_RE = re.compile(
    r"\$([A-Za-z]\w*(?:[_^]\{[^}]+\})*(?:[_^]\w*)*)\$"
)
# Inline LaTeX fallback: $...$  (remove dollar signs, keep content)
_LATEX_DOLLARS_RE = re.compile(r"\$([^$\n]+?)\$")


def _simplify_latex(match: re.Match[str]) -> str:
    """
    Convert a simple LaTeX expression (captured without ``$``) to plain text.

    Examples
    --------
    ``D_{12}``  → ``D_12``
    ``a^{n}``   → ``a^n``
    ``a^{n+p}`` → ``a^(n+p)``
    """
    inner = match.group(1)
    # Remove curly braces used purely as grouping for single tokens: a_{12} → a_12
    inner = re.sub(r"_\{(\w+)\}", r"_\1", inner)
    inner = re.sub(r"\^\{(\w+)\}", r"^\1", inner)
    # For multi-character exponents/subscripts wrap in parens: a^{n+p} → a^(n+p)
    inner = re.sub(r"_\{([^}]+)\}", r"_(\1)", inner)
    inner = re.sub(r"\^\{([^}]+)\}", r"^(\1)", inner)
    return inner


def normalize_math(text: str) -> str:
    """
    Normalize mathematical notation in *text* for embedding-friendliness.

    Operations performed:
    - Replace Unicode math operators (×, −, ÷, ≤, ≥, ≠) with ASCII equivalents.
    - Convert simple inline LaTeX (``$...$``) to plain-text math notation.
    - Strip remaining ``$`` delimiters from complex LaTeX while preserving content.

    Parameters
    ----------
    text : str
        Text after :func:`clean_text` has been applied.

    Returns
    -------
    str
        Text with normalized mathematical notation.
    """
    # Unicode operator substitutions
    for pattern, replacement in _MATH_REPLACEMENTS:
        text = pattern.sub(replacement, text)

    # Simple LaTeX → plain text
    text = _SIMPLE_LATEX_RE.sub(_simplify_latex, text)

    # Remaining $...$ → strip dollars, keep content
    text = _LATEX_DOLLARS_RE.sub(r"\1", text)

    return text


# ---------------------------------------------------------------------------
# Step 3 – Token counting
# ---------------------------------------------------------------------------


def count_tokens(text: str) -> int:
    """
    Return the number of tokens produced by the multilingual-e5-base tokenizer
    for the given *text*.

    Parameters
    ----------
    text : str
        Cleaned, normalized text.

    Returns
    -------
    int
        Token count (excluding special tokens added by the tokenizer).
    """
    tokenizer = _get_tokenizer()
    return len(tokenizer.encode(text, add_special_tokens=False))


# ---------------------------------------------------------------------------
# Step 4 – Sentence splitting helper
# ---------------------------------------------------------------------------

# Sentence boundary: period/newline followed by whitespace or end of string.
# Arabic full stop (U+06D4 ۔) is also handled.
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?\n\u06d4])\s+")


def _split_into_sentences(text: str) -> list[str]:
    """
    Heuristically split *text* into sentences.

    Splits on sentence-ending punctuation or newlines followed by whitespace.
    Returns individual non-empty sentence strings.

    Parameters
    ----------
    text : str
        Cleaned and normalized text block.

    Returns
    -------
    list[str]
        List of sentence strings.
    """
    # Treat paragraph breaks as hard sentence boundaries
    text = re.sub(r"\n{2,}", "\n", text)
    raw = _SENT_SPLIT_RE.split(text)
    return [s.strip() for s in raw if s.strip()]


# ---------------------------------------------------------------------------
# Step 5 – Chunking
# ---------------------------------------------------------------------------


def split_chunks(text: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[str]:
    """
    Split *text* into semantically coherent chunks, each within *max_tokens*.

    Strategy
    --------
    1. Count tokens for the entire text.  If within limit, return as-is.
    2. Otherwise split into sentences and greedily pack them into chunks.
    3. Add a one-sentence overlap between consecutive chunks to preserve context.

    Parameters
    ----------
    text : str
        Fully cleaned and normalized text.
    max_tokens : int
        Maximum number of tokens allowed per chunk (default 512).

    Returns
    -------
    list[str]
        List of chunk strings.  Always at least one element.
    """
    if count_tokens(text) <= max_tokens:
        return [text]

    sentences = _split_into_sentences(text)
    if not sentences:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens: int = 0

    for sent in sentences:
        sent_tokens = count_tokens(sent)

        # Single sentence already exceeds max → emit it alone
        if sent_tokens > max_tokens:
            if current:
                chunks.append(" ".join(current))
                current = current[-CHUNK_OVERLAP_SENTENCES:]
                current_tokens = sum(count_tokens(s) for s in current)
            chunks.append(sent)
            current = []
            current_tokens = 0
            continue

        if current_tokens + sent_tokens > max_tokens:
            chunks.append(" ".join(current))
            # Overlap: carry last N sentences into next chunk
            current = current[-CHUNK_OVERLAP_SENTENCES:]
            current_tokens = sum(count_tokens(s) for s in current)

        current.append(sent)
        current_tokens += sent_tokens

    if current:
        chunks.append(" ".join(current))

    return chunks if chunks else [text]


# ---------------------------------------------------------------------------
# Step 6 – Full record processing
# ---------------------------------------------------------------------------


def process_record(
    record: dict[str, Any],
    chunk_id_counter: list[int],
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[dict[str, Any]]:
    """
    Process a single JSON record into one or more embedding-ready chunks.

    Pipeline for the ``content`` field:
    1. Join the ``content`` list into a single string (newline-separated)
    2. :func:`clean_text`  – remove citations, Markdown
    3. :func:`normalize_math`  – normalize operators and LaTeX
    4. :func:`split_chunks`  – split if text exceeds *max_tokens*
    5. Assign deterministic ``chunk_id`` integers and compute ``token_count``

    All other metadata fields (lesson_number, lesson_title, subject, level)
    are copied to every produced chunk.

    Parameters
    ----------
    record : dict
        One object from the input JSON list.
    chunk_id_counter : list[int]
        A one-element list ``[n]`` acting as a mutable integer counter so the
        caller retains a reference to the running ID across records.
    max_tokens : int
        Maximum tokens per chunk.

    Returns
    -------
    list[dict]
        One or more processed chunk dictionaries.
    """
    raw_content = record.get("content", "")

    # Join list of strings into a single text block
    if isinstance(raw_content, list):
        raw_text = "\n".join(raw_content)
    else:
        raw_text = str(raw_content)

    # --- text pipeline ---
    cleaned = clean_text(raw_text)
    normalized = normalize_math(cleaned)
    chunks = split_chunks(normalized, max_tokens=max_tokens)

    # --- metadata (everything except content and chunk_id) ---
    meta = {k: v for k, v in record.items() if k not in ("content", "chunk_id")}

    results: list[dict[str, Any]] = []
    for chunk_text in chunks:
        output: dict[str, Any] = {
            "chunk_id": str(chunk_id_counter[0]),
            "content": chunk_text,
            "token_count": count_tokens(chunk_text),
        }
        output.update(meta)
        results.append(output)
        chunk_id_counter[0] += 1

    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """
    CLI entry point.

    Reads a JSON array from *input_path*, processes each record through the
    full cleaning/chunking pipeline, and writes the result to *output_path*.
    """
    parser = argparse.ArgumentParser(
        description="RAG preprocessing pipeline for Arabic + math educational content."
    )
    parser.add_argument(
        "-i", "--input",
        default="../data/mathgeo.json",
        help="Path to the input JSON file (default: ../data/mathgeo.json)",
    )
    parser.add_argument(
        "-o", "--output",
        default="output/embedded_chunk.json",
        help="Path to the output JSON file (default: output/embedded_chunk.json)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help=f"Maximum tokens per chunk (default: {DEFAULT_MAX_TOKENS})",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    logger.info("Reading input from '%s' …", input_path)
    with input_path.open(encoding="utf-8") as fh:
        data: list[dict[str, Any]] = json.load(fh)

    if not isinstance(data, list):
        logger.error("Input JSON must be a list of objects.")
        sys.exit(1)

    logger.info("Processing %d record(s) with max_tokens=%d …", len(data), args.max_tokens)

    counter = [0]  # mutable counter shared across process_record calls
    output_records: list[dict[str, Any]] = []

    for idx, record in enumerate(data):
        try:
            chunks = process_record(record, counter, max_tokens=args.max_tokens)
            output_records.extend(chunks)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping record %d due to error: %s", idx, exc)

    logger.info(
        "Done. %d input records → %d output chunks.",
        len(data),
        len(output_records),
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(output_records, fh, ensure_ascii=False, indent=2)

    logger.info("Output written to '%s'.", output_path)


if __name__ == "__main__":
    main()