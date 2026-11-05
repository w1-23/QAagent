# External GPT review handoff: v4.3

Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Repository: `w1-23/QAagent`
Reviewed baseline: `9719f40daa8d2e4122d451bedafdeca271b5e9d5`
Review unit: protocol, score schemas, prompt/config candidates, statistics,
server isolation, data classification, and paper plan
Execution authorization: `NONE`

## 1. Decision under review

The second external review required seven P0 corrections before Pilot or any
server execution. This commit implements the corrections as a documentation
and schema unit only.

The retained research design is:

- CitrusScholar as an integrated citrus evidence/workflow workbench;
- B0 model-only diagnostic, B1 fair simple RAG, B2 complete system;
- core QA60 and supplemental citation60 as separate formal experiments;
- two human experts score all Pilot/formal QA60 answers;
- a third human expert adjudicates disputed fields;
- GLM/MiMo score all 180 machine records, MiniMax only adjudicates;
- human consensus is primary, LLM is an independent automated measurement;
- no Gold Answer and no semantic-support metric;
- `B2 vs B1` is the sole confirmatory comparison.

## 2. Seven corrections implemented

1. `L0/L1/L2` data classes replace unconditional public release.
2. Four dimensions are operationally defined and `evidence_traceable`
   replaces the prior internal-use construct.
3. Expert score, field adjudication, issue, attestation, and consensus schemas
   are defined.
4. Exact McNemar plus 10,000 stratified paired bootstrap is frozen for
   `B2 vs B1`; B0 comparisons are descriptive.
5. Model-written final decisions are removed from active prompts and schemas;
   J3 returns disputed fields only.
6. Agent environment variables are unified under `CITRUS_AGENT_*`.
7. `strict-human`, `strict-judge`, `strict-citation`, and `strict-all` are
   independent.

## 3. Files to audit first

1. `DECISION_ADDENDUM_2026-11-04_V4_3.md`
2. `EXPERT_SCORING_PROTOCOL.md`
3. `../07_scoring/rubric.md`
4. `../07_scoring/expert/SCORING_CODEBOOK_v4.3.md`
5. `../07_scoring/expert/expert_scoring_TEMPLATE.csv`
6. `../07_scoring/expert/expert_adjudication_TEMPLATE.csv`
7. `../07_scoring/expert/human_consensus_TEMPLATE.csv`
8. `../09_analysis/analysis_contract.md`
9. `../03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
10. `../PUBLICATION_SCOPE.md`
11. `SERVER_EXECUTION_PROTOCOL.md`
12. `../05_baselines/model_config.json`
13. `../05_baselines/judge_config.json`
14. `../05_baselines/prompts/qa_judge_prompt.md`
15. `../05_baselines/prompts/qa_adjudicator_prompt.md`
16. `../AUDIT_INDEX.md`

## 4. Explicit implementation hold

The following are legacy and must not run:

- existing Python judge dispatcher/aggregator/adjudicator scripts;
- existing legacy adapter runtime where it conflicts with v4.3;
- old prompt behavior;
- `model_config.json` or `judge_config.json` above `UNCONFIGURED`.

This review unit does not implement the replacement packet builder, client,
aggregation, schema validators, or tests. That must be a separate v4.3
implementation commit after protocol approval.

## 5. Execution blockers

- Server connectivity and isolation were not independently verified for that work unit.
- G01 is `BLOCKED` after the known SSH timeout; G02-G15 remain `PENDING`.
- No model connectivity or provider terms are proven.
- No 120-question validity lock or 36-answer Pilot exists.
- No expert rating, judge response, formal run, or analysis output exists.

Do not treat this commit as a gate evidence record.

## 6. Review questions

Please audit specifically:

1. Are the four field definitions and fatal-error boundary operational enough
   for two independent experts to apply consistently?
2. Do the score, adjudication, issue, attestation, and consensus schemas
   preserve every original value and support deterministic traceback?
3. Does the statistical contract correctly make `B2 vs B1` the sole
   confirmatory comparison, retain 60-question pairing, and avoid treating
   runs as independent?
4. Do `strict-human` and `strict-judge` remain independent while
   `strict-all` fail-closes on missing modules?
5. Can any condition, model, run/question identity, mapping, or machine score
   leak through the proposed expert packet?
6. Does the judge contract reject model-written final decisions and restrict
   J3 to disputed fields?
7. Are `L0/L1/L2` sufficiently strict for a public repository while retaining
   auditability of protected evidence?
8. Is server isolation enforceable by named controls rather than operator
   promises?
9. Does the paper plan avoid unsupported claims about B2 superiority,
   semantic support, classifier accuracy, cross-crop generality, and user
   benefit?

Please return severity-ranked findings with exact path/section references. A
design approval does not imply that any model, gate, Pilot, or experiment has
passed.
