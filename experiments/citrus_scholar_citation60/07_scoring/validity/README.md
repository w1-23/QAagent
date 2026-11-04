# Question validity review

The unified file covers all 120 experimental questions:

- 60 core `E01_QA60_EGTS` questions.
- 60 supplementary `E01_SUPP_CHUNK_CITATION60` questions.

Each question has two independent review slots (`REVIEWER_1`, `REVIEWER_2`).
Reviews may be completed by trained graduate students in the relevant field.
The machine may pre-screen the packet, but only the two human decisions and any
documented adjudication can create the locked validity file.

The tracked `question_validity_review_packet.csv` and
`question_validity_adjudication_TEMPLATE.csv` are blank templates. Never enter
reviews into them. Build working copies without overwriting an existing file:

```powershell
python scripts/build_validity_review_packet.py
```

This writes:

- `question_validity_review_WORKING.csv`
- `question_validity_adjudication_WORKING.csv`

After both reviewers finish all 240 rows and disagreements are adjudicated,
verify completeness and create the write-once local lock:

```powershell
python scripts/verify_validity_reviews.py --check
python scripts/verify_validity_reviews.py --lock
```

The protocol gate reads the same working copies by default and accepts explicit
`--validity-review`, `--validity-adjudication`, `--validity-lock`,
`--qa-lock` and `--manifest` paths for a documented subdirectory inside the
experiment root. The working copies and lock contain reviewer decisions and
are versioned with the experiment.

Do not put accounts, credentials or personal authentication data in the
repository. Use pseudonymous reviewer IDs; if identity linkage is required, keep
it in an appropriately protected institutional record.
