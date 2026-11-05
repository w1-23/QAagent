# A02 human scoring

Protocol version: `4.4.0`
Status: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4`
Execution authorization: `NONE`

This directory implements the condition-blinded expert scoring chain in
candidate form. It has been exercised only with synthetic offline tests.
The protected root is an expert/scoring confidentiality boundary, not a
restriction on normal Agent corpus retrieval.

## Workflow

1. `00_build_expert_materials.py` reads protected condition-blinded packets and
   reviewer-specific orders, then emits independent reviewer directories with
   instructions, question/answer and evidence CSVs, CSV/XLSX score sheets,
   codebook, checklist, and attestation.
2. `10_validate_expert_scores.py` validates an immutable primary submission
   against the frozen schema, answerability labels, reason codes, NA placement,
   locators, packet set, and attestation.
3. `20_validate_corrections.py` validates append-only corrections and emits an
   immutable effective-score copy.
4. `30_import_expert_adjudication.py` validates third-expert field
   adjudications and rejects an adjudicator who is one of the primary experts.
5. `40_aggregate_human_consensus.py` computes deterministic human consensus and
   separates `human_consensus_egts` from `egts_system_outcome`.
6. `50_compute_human_agreement.py` computes raw agreement, Gwet AC1, and Cohen
   kappa with question-within-six-strata bootstrap intervals.

Every input and output must remain below
`CITRUS_EXPERIMENT_L1_ROOT`, outside the public Git tree. Strict commands
reject stale validation reports, missing attestations, missing adjudications,
and packet-manifest mismatches.

## Required materials

Each reviewer receives a separate directory containing:

- `00_评分说明.md`
- `01_问题与回答.csv`
- `02_参考证据.csv`
- `03_专家评分表.xlsx`
- `03_专家评分表.csv`
- `04_评分代码表.md`
- `05_完成检查清单.md`
- `reviewer_attestation.json`

The audited tree retains templates and the codebook only. Real answer packets,
reviewer identity linkage, scores, and corrections are `L1` until a
file-specific `L2` release review is completed.

## Current stop

No reviewer has received a real packet and no human score exists. Do not create
materials from synthetic or missing runs, and do not treat these scripts or
tests as Pilot calibration evidence.
