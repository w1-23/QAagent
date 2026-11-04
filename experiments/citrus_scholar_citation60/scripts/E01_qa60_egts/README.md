# E01 core QA60

This directory owns `E01_QA60_EGTS`: 60 locked questions x B0/B1/B2 = 180
runs. Human reviewers only certify question validity. Two frozen LLM judges
score answer quality and disagreement goes to a frozen LLM adjudicator.

Execution order:

1. Validate the candidate question schema.
2. Verify the unified 120-question validity lock.
3. Write `qa60_LOCKED.csv` once.
4. Regenerate the run plan from locked input.
5. Run B0/B1/B2 through real adapters.
6. Build blinded judge packets, dispatch judges, then aggregate fail-closed.
7. Run strict analysis only from real raw and judge files.
