# LLM judge records

Machine judgments are measurements generated under a frozen protocol. They are
not human expert annotations and must not be described as Gold.

Store one immutable raw record per judge and run. Aggregate only after both
judges have either completed or failed with an explicit status. A disagreement
requires the preconfigured adjudicator. Missing or failed judgments never
count as success or failure silently.

The authoritative design is:

`03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
