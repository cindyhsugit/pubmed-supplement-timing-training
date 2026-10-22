"""
Download ~20 years of double-blind randomized controlled trials on supplements
from PubMed (Bio.Entrez, XML) and stream them into a local SQLite database.

Usage:
    pip install biopython
    set NCBI_EMAIL=you@example.com          (PowerShell: $env:NCBI_EMAIL="you@example.com")
    set NCBI_API_KEY=your_key               (optional; raises limit from 3 to 10 requests/sec)
    python pubmed_supplement_rcts.py

Design notes:
  * SQLite is used for ingestion (fast, reliable batch inserts, no extra install).
    Analyze the result with DuckDB afterwards:
        import duckdb
        con = duckdb.connect()
        con.execute("ATTACH 'pubmed_supplements.db' AS pm (TYPE sqlite)")
        con.sql("SELECT pub_year, count(*) FROM pm.articles GROUP BY 1 ORDER BY 1").show()
  * One query per (category, year) keeps each result set small and resumable.
  * Each efetch batch (200 records) is parsed and committed immediately, so
    memory stays flat and a crash loses at most one batch. Re-running is safe:
    inserts are idempotent.
"""

import os
import re
import sqlite3
import sys
import time

from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from datetime import date, datetime, timedelta

from Bio import Entrez

from dotenv import load_dotenv

load_dotenv()

DB_PATH = "pubmed_supplements.db"
BATCH_SIZE = 200
YEARS = 20

Entrez.email = os.environ.get("NCBI_EMAIL", "")
Entrez.api_key = os.environ.get("NCBI_API_KEY") or None
DELAY = 0.11 if Entrez.api_key else 0.34  # stay under NCBI rate limits

# Category -> PubMed search terms (OR-ed together)
CATEGORIES = {
    "vitamins": [
        '"vitamins"[mh]',
        '"vitamin D"[tiab]',
        '"vitamin C"[tiab]',
        '"vitamin E"[tiab]',
        '"vitamin B12"[tiab]',
        '"folic acid"[tiab]',
        '"vitamin K"[tiab]',
        '"vitamin A"[tiab]',
        "multivitamin*[tiab]",
    ],
    "minerals": [
        "magnesium[tiab]",
        "zinc[tiab]",
        "iron[tiab]",
        "calcium[tiab]",
        "selenium[tiab]",
        "potassium[tiab]",
        "chromium[tiab]",
        '"trace elements"[mh]',
    ],
    "omega_fatty_acids": [
        '"fatty acids, omega-3"[mh]',
        '"omega-3"[tiab]',
        '"fish oil"[tiab]',
        '"docosahexaenoic acid"[tiab]',
        '"eicosapentaenoic acid"[tiab]',
        '"omega-6"[tiab]',
        '"flaxseed oil"[tiab]',
    ],
    "amino_acids": [
        '"amino acids"[mh]',
        "creatine[tiab]",
        "glutamine[tiab]",
        "arginine[tiab]",
        "carnitine[tiab]",
        "taurine[tiab]",
        '"branched-chain amino acids"[tiab]',
        "leucine[tiab]",
        "tryptophan[tiab]",
    ],
}

SUPPLEMENT_FILTER = '("dietary supplements"[mh] OR supplement*[tiab])'
STUDY_FILTER = (
    '"randomized controlled trial"[pt] AND '
    '("double-blind method"[mh] OR "double blind"[tiab] OR "double-blind"[tiab])'
)


def build_query(category, year):
    terms = " OR ".join(CATEGORIES[category])
    return (
        f"({STUDY_FILTER}) AND {SUPPLEMENT_FILTER} AND ({terms}) "
        f'AND ("{year}/01/01"[dp] : "{year}/12/31"[dp])'
    )


def init_db(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            pmid      INTEGER PRIMARY KEY,
            title     TEXT,
            abstract  TEXT,
            pub_year  INTEGER
        );
        CREATE TABLE IF NOT EXISTS article_categories (
            pmid      INTEGER NOT NULL,
            category  TEXT NOT NULL,
            PRIMARY KEY (pmid, category)
        );
        CREATE INDEX IF NOT EXISTS idx_articles_year ON articles(pub_year);
        CREATE INDEX IF NOT EXISTS idx_cat ON article_categories(category);
        """)
    return con


def with_retries(fn, *args, tries=5, **kwargs):
    """Call an Entrez function, retrying on transient network/NCBI errors."""
    for attempt in range(1, tries + 1):
        try:
            time.sleep(DELAY)
            return fn(*args, **kwargs)
        except (
            HTTPError,
            URLError,
            IncompleteRead,
            RuntimeError,
            ConnectionError,
        ) as exc:
            if attempt == tries:
                raise
            wait = 2**attempt
            print(
                f"  ! {type(exc).__name__}: {exc}; retry {attempt}/{tries - 1} in {wait}s"
            )
            time.sleep(wait)


def search_ids(query):
    """Return (count, webenv, query_key) using the NCBI history server."""
    handle = with_retries(
        Entrez.esearch, db="pubmed", term=query, usehistory="y", retmax=0
    )
    result = Entrez.read(handle)
    handle.close()
    return int(result["Count"]), result["WebEnv"], result["QueryKey"]


def parse_year(article, medline):
    """Publication year from the journal PubDate, with sensible fallbacks."""
    pub_date = article.get("Journal", {}).get("JournalIssue", {}).get("PubDate", {})
    if "Year" in pub_date:
        return int(pub_date["Year"])
    if "MedlineDate" in pub_date:
        match = re.search(r"\d{4}", str(pub_date["MedlineDate"]))
        if match:
            return int(match.group())
    for art_date in article.get("ArticleDate", []):
        if "Year" in art_date:
            return int(art_date["Year"])
    return None


def parse_abstract(article):
    parts = []
    for node in article.get("Abstract", {}).get("AbstractText", []):
        label = getattr(node, "attributes", {}).get("Label")
        parts.append(f"{label}: {node}" if label else str(node))
    return "\n".join(parts) or None


def parse_records(records):
    """Yield (pmid, title, abstract, year) for each PubmedArticle in an efetch result."""
    for rec in records.get("PubmedArticle", []):
        medline = rec["MedlineCitation"]
        article = medline["Article"]
        yield (
            int(medline["PMID"]),
            str(article.get("ArticleTitle", "")).strip() or None,
            parse_abstract(article),
            parse_year(article, medline),
        )


def fetch_and_store(con, category, year):
    query = build_query(category, year)
    count, webenv, query_key = search_ids(query)
    print(f"[{category} {year}] {count} records")
    stored = 0
    for start in range(0, count, BATCH_SIZE):
        handle = with_retries(
            Entrez.efetch,
            db="pubmed",
            retmode="xml",
            rettype="abstract",
            retstart=start,
            retmax=BATCH_SIZE,
            webenv=webenv,
            query_key=query_key,
        )
        records = Entrez.read(handle)  # one small batch at a time keeps memory flat
        handle.close()
        rows = list(parse_records(records))
        with con:  # one transaction (commit) per batch
            con.executemany(
                "INSERT INTO articles (pmid, title, abstract, pub_year) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(pmid) DO UPDATE SET title=excluded.title, "
                "abstract=excluded.abstract, pub_year=excluded.pub_year",
                rows,
            )
            con.executemany(
                "INSERT OR IGNORE INTO article_categories (pmid, category) VALUES (?, ?)",
                [(r[0], category) for r in rows],
            )
        stored += len(rows)
    return stored


def recently_fetched(con, days=90):
    """True if a complete download finished within the last `days` days."""
    con.execute("CREATE TABLE IF NOT EXISTS fetch_log (fetched_at TEXT)")
    last = con.execute("SELECT MAX(fetched_at) FROM fetch_log").fetchone()[0]
    if last is None:
        return False
    if datetime.now() - datetime.fromisoformat(last) < timedelta(days=days):
        print(f"Last fetch was {last}; skipping download.")
        return True
    return False


def milestone1_1():
    if not Entrez.email:
        sys.exit(
            "Set the NCBI_EMAIL environment variable (NCBI requires a contact email)."
        )
    this_year = date.today().year
    con = init_db(DB_PATH)
    if recently_fetched(con):
        con.close()
        return

    for year in range(this_year - YEARS + 1, this_year + 1):
        for category in CATEGORIES:
            fetch_and_store(con, category, year)
    con.execute(
        "INSERT INTO fetch_log (fetched_at) VALUES (?)", (datetime.now().isoformat(),)
    )
    con.commit()
    total = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    print(f"Done. {total} unique articles in {DB_PATH}")
    con.close()


if __name__ == "__main__":
    start_time = time.perf_counter()
    milestone1_1()
    end_time = time.perf_counter()
    elapsed_time = end_time - start_time

    print(f"Execution time: {elapsed_time:.6f} seconds")
