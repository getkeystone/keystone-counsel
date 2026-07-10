"""Corpus loader for Keystone Counsel.

Reads markdown files from classified subdirectories under data/corpus/.
The directory name determines the document classification:

  data/corpus/
    regulatory-guidance/      -> regulatory_guidance
    legal-opinions/           -> legal_opinion
    kyc-requirements/         -> kyc_document
    suitability-assessments/  -> suitability_assessment

Each markdown file is split into chunks by H2 headings. Each chunk
carries the classification from its parent directory.

Contact center heritage: this is the knowledge base with category
tagging. Each article belongs to a queue. The routing engine uses the
category to determine which agents (advisors) can see it.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from keystone_counsel.vectorstore import Chunk

logger = logging.getLogger(__name__)

# Directory name -> DocumentClassification value
_DIR_TO_CLASSIFICATION = {
    "regulatory-guidance": "regulatory_guidance",
    "legal-opinions": "legal_opinion",
    "kyc-requirements": "kyc_document",
    "suitability-assessments": "suitability_assessment",
}


def _split_by_headings(content: str, source: str, classification: str) -> list[Chunk]:
    """Split markdown content by H2 headings into chunks."""
    sections = re.split(r"(?m)^## ", content)
    chunks: list[Chunk] = []

    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue

        if i == 0 and not content.lstrip().startswith("## "):
            # Content before first H2 (usually H1 + intro)
            heading = "Introduction"
            body = section
            # Strip H1 if present
            lines = body.split("\n", 1)
            if lines[0].startswith("# "):
                heading = lines[0].lstrip("# ").strip()
                body = lines[1].strip() if len(lines) > 1 else ""
        else:
            lines = section.split("\n", 1)
            heading = lines[0].strip()
            body = lines[1].strip() if len(lines) > 1 else ""

        if not body:
            continue

        chunk_id = f"{source}::{i:03d}::{_slugify(heading)}"
        chunks.append(Chunk(
            chunk_id=chunk_id,
            content=body,
            source_document=source,
            section=heading,
            classification=classification,
        ))

    return chunks


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_corpus(corpus_dir: str | Path = "data/corpus") -> list[Chunk]:
    """Load all classified corpus documents.

    Returns chunks tagged with their classification based on the
    parent directory name.
    """
    corpus_path = Path(corpus_dir)
    if not corpus_path.exists():
        logger.warning("Corpus directory not found: %s", corpus_path)
        return []

    all_chunks: list[Chunk] = []

    for subdir in sorted(corpus_path.iterdir()):
        if not subdir.is_dir():
            continue

        classification = _DIR_TO_CLASSIFICATION.get(subdir.name)
        if classification is None:
            logger.warning("Unknown corpus category: %s (skipping)", subdir.name)
            continue

        md_files = sorted(subdir.glob("*.md"))
        for md_file in md_files:
            content = md_file.read_text(encoding="utf-8")
            chunks = _split_by_headings(content, md_file.name, classification)
            logger.info(
                "Loaded %d chunks from %s [%s]",
                len(chunks), md_file.name, classification,
            )
            all_chunks.extend(chunks)

    logger.info(
        "Total corpus: %d chunks from %d categories",
        len(all_chunks),
        len(set(c.classification for c in all_chunks)),
    )
    return all_chunks
