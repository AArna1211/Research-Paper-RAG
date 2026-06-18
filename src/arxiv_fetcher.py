"""
arXiv paper fetcher for RAG ingestion pipeline.

Usage:
    python arxiv_fetcher.py

Requirements:
    pip install arxiv
"""

import arxiv
import json
import time
import logging
from pathlib import Path
from dataclasses import dataclass, asdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config — edit these before running
# ---------------------------------------------------------------------------

KEYWORDS = [
    "retrieval augmented generation",
    "RAG language model",
]

MAX_PAPERS   = 50        # total papers to fetch across all keywords
DOWNLOAD_PDF = True      # set False to fetch metadata only (much faster)
OUTPUT_DIR   = Path("papers")
DELAY        = 3.0       # seconds between API calls (respect arXiv ToS)

# arXiv category filter — leave empty to search all categories
# Examples: "cs.CL", "cs.IR", "cs.LG", "stat.ML"
CATEGORIES = ["cs.CL", "cs.IR"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PaperRecord:
    arxiv_id:   str
    title:      str
    authors:    list[str]
    abstract:   str
    categories: list[str]
    published:  str           # ISO date string
    updated:    str
    pdf_url:    str
    pdf_path:   str | None    # local path if downloaded, else None
    primary_category: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_query(keywords: list[str], categories: list[str]) -> str:
    """
    Build an arXiv query string.

    Combines keyword phrases with OR logic and optionally restricts to
    specific subject categories.

    arXiv query syntax:
        ti:   title
        abs:  abstract
        cat:  category
        AND / OR / ANDNOT for boolean logic
    """
    # Search title and abstract for any of the keyword phrases
    kw_parts = [f'(ti:"{kw}" OR abs:"{kw}")' for kw in keywords]
    kw_query = " OR ".join(kw_parts)

    if categories:
        cat_parts = " OR ".join(f"cat:{c}" for c in categories)
        return f"({kw_query}) AND ({cat_parts})"

    return kw_query


def fetch_papers(
    query: str,
    max_results: int,
    delay: float,
) -> list[arxiv.Result]:
    """Fetch paper metadata from the arXiv API."""
    client = arxiv.Client(
        page_size=min(max_results, 100),
        delay_seconds=delay,
        num_retries=3,
    )
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance,
        sort_order=arxiv.SortOrder.Descending,
    )

    results = []
    log.info("Fetching up to %d papers …", max_results)
    for result in client.results(search):
        results.append(result)
        log.info("  [%d] %s", len(results), result.title[:80])

    log.info("Fetched %d papers total", len(results))
    return results


def download_pdf(result: arxiv.Result, out_dir: Path) -> Path | None:
    """
    Download the PDF for a single result.

    Returns the local Path on success, None on failure.
    Skips download if the file already exists (safe to re-run).
    """
    # Use the short arXiv ID as the filename, e.g. "2312.10997v1.pdf"
    safe_id  = result.get_short_id().replace("/", "_")
    pdf_path = out_dir / f"{safe_id}.pdf"

    if pdf_path.exists():
        log.info("  Already downloaded: %s", pdf_path.name)
        return pdf_path

    try:
        result.download_pdf(dirpath=str(out_dir), filename=pdf_path.name)
        log.info("  Downloaded: %s", pdf_path.name)
        return pdf_path
    except Exception as exc:
        log.warning("  PDF download failed for %s: %s", safe_id, exc)
        return None


def result_to_record(result: arxiv.Result, pdf_path: Path | None) -> PaperRecord:
    return PaperRecord(
        arxiv_id         = result.get_short_id(),
        title            = result.title,
        authors          = [str(a) for a in result.authors],
        abstract         = result.summary.replace("\n", " "),
        categories       = result.categories,
        published        = result.published.date().isoformat(),
        updated          = result.updated.date().isoformat(),
        pdf_url          = result.pdf_url,
        pdf_path         = str(pdf_path) if pdf_path else None,
        primary_category = result.primary_category,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    pdf_dir  = OUTPUT_DIR / "pdfs"
    meta_dir = OUTPUT_DIR / "metadata"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    query = build_query(KEYWORDS, CATEGORIES)
    log.info("Query: %s", query)

    results = fetch_papers(query, MAX_PAPERS, DELAY)

    records: list[PaperRecord] = []
    for i, result in enumerate(results, 1):
        log.info("Processing %d/%d: %s", i, len(results), result.get_short_id())

        pdf_path = None
        if DOWNLOAD_PDF:
            pdf_path = download_pdf(result, pdf_dir)
            # Brief pause between downloads to be polite
            if i < len(results):
                time.sleep(1)

        record = result_to_record(result, pdf_path)
        records.append(record)

        # Save individual metadata file alongside the PDF
        meta_path = meta_dir / f"{record.arxiv_id.replace('/', '_')}.json"
        meta_path.write_text(json.dumps(asdict(record), indent=2))

    # Save a combined manifest — useful for the next pipeline stage
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps([asdict(r) for r in records], indent=2)
    )
    log.info("Saved manifest → %s (%d papers)", manifest_path, len(records))

    # Quick summary
    downloaded = sum(1 for r in records if r.pdf_path)
    print(f"\n{'─' * 50}")
    print(f"  Papers fetched   : {len(records)}")
    print(f"  PDFs downloaded  : {downloaded}")
    print(f"  Metadata dir     : {meta_dir}")
    print(f"  Manifest         : {manifest_path}")
    print(f"{'─' * 50}\n")
    print("Next step: run the PDF parser on papers/pdfs/ → see pdf_parser.py")


if __name__ == "__main__":
    main()