import sqlite3

con = sqlite3.connect("pubmed_supplements.db")

done_count = con.execute(
    "SELECT COUNT(DISTINCT pmid) FROM extraction_log WHERE status='done'"
).fetchone()[0]
total_rows = con.execute("SELECT COUNT(*) FROM supplement_trials_processed").fetchone()[
    0
]

print(f"Successfully processed abstracts: {done_count}")
print(f"Total structured records in table: {total_rows}")

con.close()
