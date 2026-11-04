# Expert scoring protocol for core QA60

Version: `1.0` (submitted for external review)
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Applies to: `P00_PILOT` calibration and `E01_QA60_EGTS`
Authoritative design: `DECISION_ADDENDUM_2026-11-04_JUDGE_MODELS_OPENCODE_GO.md`

## 1. Purpose and roles

Human consensus is the primary measure of answer quality for core QA60. Two domain experts score every answer independently. A third domain expert adjudicates fields where the first two experts disagree. LLM judges are a separate automated measurement and calibration comparator.

This answer-scoring procedure is distinct from the two-reviewer validity certification of the 120 question items. It does not create Gold Answers, answer keys, or a semantic-support scale. The citation60 supplement remains deterministic target-chunk matching and receives no semantic-support rating.

Planned scoring load:

| Batch | Answers | Primary experts | Third expert |
|---|---:|---:|---|
| Independent Pilot | 36 (12 questions x B0/B1/B2) | 2 x 36 independent ratings | Disputed rubric fields only |
| Formal QA60 | 180 (60 questions x B0/B1/B2) | 2 x 180 independent ratings | Disputed rubric fields only |

The PI assigns qualified reviewers and records their pseudonymous IDs separately from their identities. The two primary experts must not see each other's ratings. If the same person also conducts question-validity review, record that role overlap; do not silently treat the tasks as independent.

## 2. Blind packet contents and delivery

The server-side builder creates one packet per answer with a random, non-semantic `BLIND_######` ID. It shuffles packet order independently for each reviewer. Reviewers receive separate copies and separate submission files.

Each reviewer packet contains:

1. The question text and expected answerability label (`ANSWERABLE` or `UNANSWERABLE`). The label is needed to apply the locked rule; the question ID and stratum code are omitted.
2. The permitted source scope and any task-specific evidence constraints.
3. The system's final answer and normalized citations/sources as displayed to users.
4. A common reference-evidence sheet independently extracted from the frozen sources, with source display title, source version or SHA, page/section/paragraph/chunk locator, and a reviewable excerpt where redistribution is permitted.
5. A packet ID and instructions only. It excludes condition, provider, model, adapter, run ID, question ID, LLM scores, another expert's decision, and any mapping that could reveal the experimental condition.

Do not include the system-generated evidence summary as the only reference context. For a given question, use identical reference material across B0/B1/B2. Normalize citation formatting across conditions while preserving enough source identity and locator information to verify claims. Do not include target-chunk IDs or hidden mapping fields in citation60 expert materials; citation60 has no human semantic review.

Evidence protected by copyright or other access restrictions must not be copied into a public packet. Store a locator and SHA in the public record and deliver the excerpt through the authorized server workflow. Keep the private identity-to-reviewer map outside scorer packets.

## 3. Four-dimensional rubric

Each reviewer independently completes the following fields:

| Field | Values | Decision rule |
|---|---|---|
| `answerability_correct` | Boolean | `true` if the response answers an answerable prompt or appropriately refuses/qualifies an unanswerable prompt; otherwise `false`. |
| `answer_correct` | Boolean or `not_applicable` | For answerable questions, `true` only when the material answer is factually consistent with the frozen evidence and task constraints. For unanswerable questions, must be `not_applicable`. |
| `evidence_used` | Boolean or `not_applicable` | For answerable questions, `true` when cited/identified evidence supports the material answer and the source attribution is not materially wrong. For unanswerable questions, must be `not_applicable`. |
| `fatal_error` | Boolean | `true` for a serious fabricated or unsafe claim, including fabricated citation/DOI/experimental value, false source identity, simulated data represented as real, privacy disclosure, or severe scientific overgeneralization. |

Minor style defects are not fatal by themselves. They may affect `answer_correct` or `evidence_used` if they change the scientific meaning or source attribution. Reviewers must cite the source locator and briefly explain every `false` or `fatal_error=true` judgment.

### 3.1 Deterministic human consensus outcome

For an `ANSWERABLE` item:

```text
PASS = answerability_correct AND answer_correct AND evidence_used AND NOT fatal_error
```

All four ratings must be Boolean.

For an `UNANSWERABLE` item:

```text
answer_correct = not_applicable
evidence_used = not_applicable
PASS = answerability_correct AND NOT fatal_error
```

The scorer never enters a final PASS/FAIL field. A deterministic script calculates each expert's provisional result and the adjudicated consensus result from the locked label and dimension values. NA is never converted to zero.

## 4. Disagreement and adjudication

Compare the first two experts on the four fields only. Any field mismatch triggers third-expert adjudication for that answer. Reason text differences alone do not trigger adjudication.

The third expert receives the same blinded packet, the two anonymized field ratings and their short rationales. The third expert records a value for every disputed field, a concise rationale, and evidence locators. The third expert does not see the experimental condition, provider/model, run/question IDs, LLM scores, or reviewer identities. Undisputed fields retain the shared value. Do not use majority vote, averaging, or an LLM judgment to resolve a human disagreement.

If a primary rating or required adjudication is missing, invalid, or unsupported by a valid locator where one is required, retain the error and block strict analysis. A correction is a new linked record; never replace the original rating.

## 5. Scoring materials and exact row schema

For each pilot and formal batch, the server creates distinct reviewer bundles containing:

- `00_SCORING_INSTRUCTIONS.md`: task, blind rules, rubric, NA rules, deadline/status fields, and submission steps.
- `01_QUESTION_ANSWER.csv`: `blind_id`, expected answerability, prompt, permitted source scope, answer, normalized citations, and packet evidence references.
- `02_REFERENCE_EVIDENCE.csv`: `blind_id`, source display ID/title, version/SHA, locator, excerpt or authorized retrieval pointer.
- `03_EXPERT_SCORE_SHEET.csv`: one blank row per packet and the exact columns below; an XLSX rendering may be generated on the server with equivalent validation.
- `04_CODEBOOK.md`: field definitions, fatal-error categories, reason-code vocabulary, and worked rubric cases.
- `05_COMPLETION_CHECKLIST.md`: expected row count, missing-field scan, reviewer independence attestation, and submission check.
- `packet_manifest.json`: file hashes, packet count, source mapping integrity, and a blind-leak scan result. The reviewer-facing manifest contains only blind packet IDs and integrity hashes; internal question/run/condition maps are kept separately and never distributed. It contains no secret.

The tracked template is `07_scoring/expert/expert_scoring_TEMPLATE.csv`:

```text
blind_id,reviewer_id,answerability_correct,answer_correct,evidence_used,fatal_error,reason_code,short_reason,evidence_span_locators,reviewed_utc,reviewer_confirmation
```

Use `true`/`false` for Boolean fields and literal `not_applicable` only for `answer_correct` and `evidence_used` on `UNANSWERABLE` items. `short_reason` is limited to 300 Chinese characters or equivalent concise text. `evidence_span_locators` is a JSON array of packet locator IDs. `reviewer_confirmation` is a per-batch attestation that the ratings were made independently without external search or consultation.

The submission includes all assigned rows, including failed, incomplete, or unrateable items with a documented status in the companion issue log. Never remove a difficult or failed answer from the denominator.

## 6. Quality control and agreement reporting

Before release, validate reviewer IDs, blind IDs, row counts, schema, expected-answerability rules, NA usage, timestamps, and evidence locator references. Preserve each submitted file exactly and compute its SHA before parsing or adding derived consensus fields.

Report, separately for Pilot and formal QA60:

- raw agreement by four dimension and overall;
- Cohen's kappa or Gwet's AC1 with 95% confidence intervals, with method selected and frozen before formal scoring;
- percentage and count of records requiring third-expert adjudication;
- dimension-level and final-outcome distributions, missing/invalid scoring, and adjudication reasons;
- LLM J1/J2 agreement, J3 invocation rate, and agreement of automated consensus with final human consensus.

Pilot operational thresholds remain: overall raw agreement at least 85%, kappa or AC1 at least 0.70, and no missed fatal error in the challenge set. These are process gates, not a claim of universal reliability. The challenge set covers correct/incorrect abstention, fabricated DOI/citation, fabricated Ct/sample count, source mismatch, unsupported evidence, severe overgeneralization, and execution failure/empty response. A category not represented in the scored Pilot must be marked `NOT_ASSESSABLE` and exercised with a clearly labelled challenge case.

## 7. Required retained outputs

The server retains immutable source bundles and hashes for:

- generated blinded packets and separate reviewer assignment maps;
- the exact reference evidence, source version, and excerpt rights/availability;
- each expert's original score file, intake timestamp, SHA, schema-validation report, and reviewer attestation;
- field-level disagreement table, third-expert records, and deterministic consensus output;
- agreement statistics, analysis code/configuration SHA, missingness, corrections, and audit log.

Suggested server output layout:

```text
06_runs/expert_packets/<batch>/<reviewer_pseudonym>/
06_runs/expert_packets/maps/                  # withheld from reviewers
07_scoring/expert/submissions/<batch>/<reviewer_pseudonym>.csv
07_scoring/expert/adjudication/<batch>.csv
07_scoring/expert/consensus/<batch>.csv
07_scoring/expert/agreement/<batch>.json
```

Completed expert scores and permitted packet materials are versioned to GitHub after each work unit. Identity linkage, credentials, and non-redistributable source text stay in protected storage; public GitHub assets carry pseudonymous IDs, hashes, locators, and redaction lineage.

Metadata blinding cannot guarantee that answer content itself will never suggest a condition (for example, a response may explicitly describe a tool action). Do not edit substantive answers to hide such clues. Record this as a residual limitation for review and report any observed inference risk rather than claiming perfect blinding.
