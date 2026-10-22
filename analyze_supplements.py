"""
Milestone 1.3: Statistical Analysis & Analytical Querying.

Reads  : pubmed_supplements.db -> supplement_trials_processed
Writes : pubmed_supplements.db -> analysis_* tables (used for reporting and plotting)
"""

import os
import sqlite3
import pandas as pd

DB_PATH = "pubmed_supplements.db"


def classify_solubility(c_class):
    if not isinstance(c_class, str):
        return "Unclassified"
    c = c_class.lower()
    if any(term in c for term in ["fat-soluble", "omega-3", "lipid"]):
        return "Lipophilic (Fat-Soluble)"
    if any(term in c for term in ["water-soluble", "mineral", "amino acid"]):
        return "Hydrophilic / Polar"
    return "Other / Unclassified"


def has_text(s):
    s = s.fillna("").astype(str).str.strip().str.lower()
    return ~s.isin(["", "none"])


def analyze_q1(df, con):
    """Q1: timing windows by supplement class & plot summary."""
    q1 = df.groupby(["chemical_class", "timing_window"]).size().unstack(fill_value=0)
    print("\n=== Q1: When is the best time to take supplements? ===")
    print(q1.to_string() if not q1.empty else "No timing data yet.")
    q1.to_sql("analysis_q1_timing_by_class", con, if_exists="replace", index=True)

    # Clean summary table for plotting (Milestone 1.4 dependency)
    sub = df[df["timing_window"].isin(["Morning", "Evening"])]
    counts = sub["timing_window"].value_counts().reset_index()
    counts.columns = ["timing_window", "count"]
    counts.to_sql("analysis_q1_timing_summary", con, if_exists="replace", index=False)


def analyze_q2(df, con):
    """Q2: trials that state an absorption limit or interaction & plot summary."""
    mask = has_text(df["absorption_limit_stated"]) | has_text(df["interactions_noted"])
    q2 = df.loc[
        mask,
        [
            "pmid",
            "supplement_name",
            "absorption_limit_stated",
            "interactions_noted",
            "limit_evidence",
        ],
    ]
    print(
        f"\n=== Q2: Is there a cap? ({len(q2)} trials state a limit or interaction) ==="
    )
    print(
        q2.to_string(max_colwidth=60)
        if not q2.empty
        else "No limits or interactions stated yet."
    )
    q2.to_sql("analysis_q2_limits_interactions", con, if_exists="replace", index=False)

    # Clean summary table for plotting (Milestone 1.4 dependency)
    yes_count = int(mask.sum())
    no_count = int((~mask).sum())
    summary = pd.DataFrame(
        {"has_limit_or_interaction": ["Yes", "No"], "count": [yes_count, no_count]}
    )
    summary.to_sql("analysis_q2_limits_summary", con, if_exists="replace", index=False)


def analyze_q3(df, con):
    """Q3: fed vs fasted by solubility group & plot summary."""
    df = df.copy()
    df["solubility_group"] = df["chemical_class"].apply(classify_solubility)
    fed_fasted = df[df["administration_state"].isin(["Fed", "Fasted"])]
    print("\n=== Q3: Before or after a meal? ===")
    if fed_fasted.empty:
        print("No rows with an explicit Fed or Fasted state yet.")
        # Create empty summary tables to prevent plotting errors
        pd.DataFrame(columns=["administration_state", "count"]).to_sql(
            "analysis_q3_fed_fasted_summary", con, if_exists="replace", index=False
        )
        return

    counts = pd.crosstab(
        fed_fasted["solubility_group"], fed_fasted["administration_state"]
    )
    pct = counts.div(counts.sum(axis=1), axis=0) * 100
    q3 = counts.add_suffix("_n").join(pct.round(1).add_suffix("_pct"))
    print(q3.to_string())
    q3.to_sql("analysis_q3_solubility_fed_fasted", con, if_exists="replace", index=True)

    # Clean summary table for plotting (Milestone 1.4 dependency)
    fed_fasted_counts = fed_fasted["administration_state"].value_counts().reset_index()
    fed_fasted_counts.columns = ["administration_state", "count"]
    fed_fasted_counts.to_sql(
        "analysis_q3_fed_fasted_summary", con, if_exists="replace", index=False
    )


def milestone1_3():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file '{DB_PATH}' not found. Run Milestone 1.2 first.")
        return

    con = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM supplement_trials_processed", con)
    except Exception as e:
        print(f"Error loading table: {e}")
        con.close()
        return

    if df.empty:
        print(
            "The 'supplement_trials_processed' table is empty. Process some abstracts first!"
        )
        con.close()
        return

    print(f"Loaded {len(df)} records from supplement_trials_processed.")
    analyze_q1(df, con)
    analyze_q2(df, con)
    analyze_q3(df, con)
    con.commit()
    con.close()
    print("\nMilestone 1.3 complete! Analysis summary tables saved to database.")


if __name__ == "__main__":
    milestone1_3()
