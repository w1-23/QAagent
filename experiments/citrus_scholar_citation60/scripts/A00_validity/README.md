# A00 validity

This is the only human-review workflow used by both formal question sets.
It produces 120 questions x 2 independent reviewer records, adjudicates any
disagreement, and writes `question_validity_LOCKED.csv` once.

```powershell
python scripts/A00_validity/00_build_validity_packet.py
python scripts/A00_validity/10_validate_reviews.py --check
python scripts/A00_validity/10_validate_reviews.py --lock
```

The builder writes only the git-ignored working copies
`question_validity_review_WORKING.csv` and
`question_validity_adjudication_WORKING.csv`. Never edit the public blank
templates. Pass explicit paths to the verifier or protocol lock only when the
working files have been moved to a documented, git-ignored subdirectory inside
the experiment root.

Reviewer IDs may be pseudonymous. Names and signed consent/role evidence, if
required by the institution, remain in restricted storage. No Gold Answer is
written.
