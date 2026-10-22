"""
Milestone 1.2: extract structured supplement features from PubMed abstracts with Gemini.

Reads  : pubmed_supplements.db -> articles (built in Milestone 1.1)
Writes : pubmed_supplements.db -> supplement_trials_processed (one row per supplement per trial)
         pubmed_supplements.db -> extraction_log (one row per abstract; makes runs resumable)

Setup (PowerShell):
    pip install google-genai pydantic python-dotenv
    .env must contain:  GEMINI_API_KEY=your_key      (GOOGLE_API_KEY also works)
    optional in .env:   GEMINI_MODEL=gemini-3.5-flash

Usage (run from the folder with the .env file and the .db file):
    python extract_supplements.py --limit 25     # test run on 25 abstracts, prints results
    python extract_supplements.py --all          # process every abstract not done yet
"""

import argparse
import os
import sqlite3
import sys
import time
from typing import Literal, Optional

from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel, Field, ValidationError

load_dotenv()

DB_PATH = "pubmed_supplements.db"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")


# ---------------------------------------------------------------------------
# Extraction schema. Every field may be null: "not stated" is a valid answer.
# ---------------------------------------------------------------------------
class SupplementRecord(BaseModel):
    # Identity
    supplement_name: str = Field(
        description="Supplement studied, e.g. 'vitamin D3', 'magnesium glycinate'."
    )
    chemical_class: Optional[str] = Field(
        description="e.g. 'fat-soluble vitamin', 'water-soluble vitamin', 'mineral', "
        "'omega-3 fatty acid', 'amino acid', 'other'. Null if unclear."
    )
    supplement_form: Optional[str] = Field(
        description="Specific form if stated, e.g. 'D3', 'citrate', 'oxide', 'ethyl ester'."
    )

    # Q1: best time
    timing_window: Optional[
        Literal["Morning", "Evening", "Split-Dose", "Pre-Workout", "Other"]
    ] = Field(
        description="When the supplement was taken. Null if the abstract does not state it."
    )
    timing_detail: Optional[str] = Field(
        description="Timing in the authors' words, e.g. 'at bedtime', 'before breakfast'."
    )
    dosing_frequency: Optional[str] = Field(
        description="e.g. 'once daily', 'twice daily'."
    )
    compared_timings: Optional[bool] = Field(
        description="True only if the trial directly compared two or more administration timings."
    )

    # Q2: absorption cap
    dose_per_administration: Optional[str] = Field(
        description="Dose per intake with unit, e.g. '2000 IU', '500 mg'."
    )
    total_daily_dose: Optional[str] = Field(
        description="Total daily dose with unit if stated."
    )
    absorption_limit_stated: Optional[str] = Field(
        description="Any stated absorption limit, saturation threshold, or maximum single dose."
    )
    co_administered_supplements: Optional[str] = Field(
        description="Other supplements taken at the same time, if stated."
    )
    interactions_noted: Optional[str] = Field(
        description="Any stated interaction or competition between supplements."
    )

    # Q3: with or without meals
    administration_state: Optional[Literal["Fed", "Fasted"]] = Field(
        description="'Fed' if taken with or after a meal, 'Fasted' if on an empty stomach or before a meal. Null if not stated."
    )
    meal_timing_detail: Optional[str] = Field(
        description="Meal detail in the authors' words, e.g. 'with breakfast', '30 min before meals'."
    )
    meal_contained_fat: Optional[bool] = Field(
        description="True/False only if the abstract says whether the meal contained fat."
    )

    # Outcome
    outcome_measured: Optional[str] = Field(
        description="What was measured, e.g. 'serum 25(OH)D', 'bioavailability', 'sleep quality'."
    )
    better_condition: Optional[str] = Field(
        description="Which timing/condition performed better, or 'no difference'. Null if the trial did not compare conditions."
    )

    # Supporting evidence: short verbatim quotes from the abstract
    timing_evidence: Optional[str] = Field(
        description="Short verbatim quote supporting the timing fields."
    )
    meal_evidence: Optional[str] = Field(
        description="Short verbatim quote supporting the fed/fasted fields."
    )
    limit_evidence: Optional[str] = Field(
        description="Short verbatim quote supporting the dose/absorption-limit fields."
    )


class ExtractionResult(BaseModel):
    supplements: list[SupplementRecord] = Field(
        description="One record per supplement studied. Empty list if no supplement is clearly studied."
    )


SYSTEM_PROMPT = (
    "You extract structured data from clinical trial abstracts about dietary supplements. "
    "Rules: (1) Use ONLY what the abstract explicitly states; never guess or use outside knowledge. "
    "(2) If a field is not stated, return null for it. "
    "(3) Create one record per supplement actually administered (ignore placebo). "
    "(4) Evidence fields must be short verbatim quotes copied from the abstract. "
    "(5) 'compared_timings' is true only when the trial itself compared different administration timings."
)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
FIELDS = list(SupplementRecord.model_fields.keys())


def init_tables(con):
    cols = ",\n    ".join(f"{name} TEXT" for name in FIELDS)
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS supplement_trials_processed (
            row_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            pmid    INTEGER NOT NULL,
            {cols}
        );
        CREATE INDEX IF NOT EXISTS idx_processed_pmid ON supplement_trials_processed(pmid);
        CREATE TABLE IF NOT EXISTS extraction_log (
            pmid          INTEGER PRIMARY KEY,
            status        TEXT NOT NULL,      -- done | no_abstract | error
            model         TEXT,
            raw_response  TEXT,
            error         TEXT,
            processed_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)


def pending_articles(con, limit):
    sql = (
        "SELECT a.pmid, a.title, a.abstract FROM articles a "
        "LEFT JOIN extraction_log l ON l.pmid = a.pmid "
        "WHERE l.pmid IS NULL OR l.status = 'error' ORDER BY a.pmid"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return con.execute(sql).fetchall()


def log_result(con, pmid, status, raw=None, error=None):
    con.execute(
        "INSERT INTO extraction_log (pmid, status, model, raw_response, error) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(pmid) DO UPDATE SET status=excluded.status, model=excluded.model, "
        "raw_response=excluded.raw_response, error=excluded.error, processed_at=CURRENT_TIMESTAMP",
        (pmid, status, MODEL, raw, error),
    )


def store_result(con, pmid, result, raw):
    """Replace any earlier rows for this PMID and write the new ones, in one transaction."""
    with con:
        con.execute("DELETE FROM supplement_trials_processed WHERE pmid = ?", (pmid,))
        placeholders = ", ".join("?" for _ in range(len(FIELDS) + 1))
        sql = f"INSERT INTO supplement_trials_processed (pmid, {', '.join(FIELDS)}) VALUES ({placeholders})"
        for rec in result.supplements:
            values = [
                None if v is None else str(v) for v in (getattr(rec, f) for f in FIELDS)
            ]
            con.execute(sql, [pmid] + values)
        log_result(con, pmid, "done", raw=raw)


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------
def extract_one(client, title, abstract, retries=5):
    """Return (ExtractionResult, raw_json_text). Retries on transient API errors (e.g. 429)."""
    contents = f"Title: {title}\n\nAbstract:\n{abstract}"
    config = {
        "system_instruction": SYSTEM_PROMPT,
        "response_mime_type": "application/json",
        "response_json_schema": ExtractionResult.model_json_schema(),
        "temperature": 0,
    }
    for attempt in range(1, retries + 1):
        try:
            response = client.models.generate_content(
                model=MODEL, contents=contents, config=config
            )
            raw = response.text
            return ExtractionResult.model_validate_json(raw), raw
        except ValidationError:
            raise  # bad JSON shape: retrying the same prompt rarely helps
        except Exception as exc:  # network / rate limit / server errors
            if attempt == retries:
                raise
            wait = 2**attempt
            print(
                f"  ! {type(exc).__name__}: {exc}; retry {attempt}/{retries - 1} in {wait}s"
            )
            time.sleep(wait)


def show(pmid, result):
    print(f"PMID {pmid}: {len(result.supplements)} supplement record(s)")
    for rec in result.supplements:
        shown = {k: v for k, v in rec.model_dump().items() if v is not None}
        print("   ", shown)


def milestone1_2():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--limit",
        type=int,
        default=25,
        help="process only N pending abstracts (default 25)",
    )
    group.add_argument(
        "--all", action="store_true", help="process every pending abstract"
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=4.1,
        help="seconds to wait between API calls (default 4.1s for 15 RPM free tier)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="do not print each extraction"
    )
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        sys.exit("Add GEMINI_API_KEY to your .env file.")

    client = genai.Client(api_key=api_key)
    con = sqlite3.connect(DB_PATH)
    init_tables(con)

    todo = pending_articles(con, None if args.all else args.limit)

    if not todo:
        if os.path.exists(DB_PATH):
            mod_time = os.path.getmtime(DB_PATH)
            last_modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mod_time))
        else:
            last_modified = "unknown"
        print(f"Nothing changed, last modified date: {last_modified}")
        con.close()
        return

    print(f"{len(todo)} abstracts to process with {MODEL}")
    done = skipped = failed = 0

    for pmid, title, abstract in todo:
        if not abstract or not abstract.strip():
            with con:
                log_result(con, pmid, "no_abstract")
            skipped += 1
            continue
        try:
            result, raw = extract_one(client, title, abstract)
            store_result(con, pmid, result, raw)
            done += 1
            if not args.quiet:
                show(pmid, result)
        except Exception as exc:
            with con:
                log_result(con, pmid, "error", error=f"{type(exc).__name__}: {exc}")
            failed += 1
            print(f"PMID {pmid} failed: {type(exc).__name__}: {exc}")
        time.sleep(args.sleep)

    print(f"Finished. done={done}, no_abstract={skipped}, errors={failed}")
    total = con.execute("SELECT COUNT(*) FROM supplement_trials_processed").fetchone()[
        0
    ]
    print(f"supplement_trials_processed now has {total} rows")
    con.close()


if __name__ == "__main__":
    start_time = time.perf_counter()
    milestone1_2()
    end_time = time.perf_counter()
    elapsed_time = end_time - start_time

    print(f"Execution time: {elapsed_time:.6f} seconds")
