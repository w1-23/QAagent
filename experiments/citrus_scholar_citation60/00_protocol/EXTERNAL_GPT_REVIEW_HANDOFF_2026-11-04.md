# External GPT review handoff

Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Review target: GitHub `w1-23/QAagent` `main`
Starting baseline reviewed for this change: `b6bd31327b34584b0f3b5ec7ac6a7974d508efb2`
Current phase: plan, expert score materials, and server execution contract only

## Please review

1. `00_protocol/DECISION_ADDENDUM_2026-11-04_JUDGE_MODELS_OPENCODE_GO.md`
2. `00_protocol/EXPERT_SCORING_PROTOCOL.md`
3. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
4. `00_protocol/02_实验执行与论文写作总计划.md`
5. `00_protocol/05_论文写作执行计划.md`
6. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
7. `07_scoring/rubric.md` and `07_scoring/expert/`
8. `09_analysis/analysis_contract.md`
9. `AUDIT_INDEX.md` and this handoff

## Decisions requiring audit

- Agent uses the Alibaba Bailian endpoint; judges use OpenCode Go and are from GLM, MiMo, and MiniMax families.
- Two human experts independently score all 36 Pilot responses and all 180 formal QA60 responses; a third expert adjudicates any disagreement across the four fields. Human consensus is the primary QA60 measure; LLM ratings are a separately reported automated measure.
- Expert materials must include the question, answer, normalized citations, and independently extracted common evidence context, while concealing condition, model/provider, run/question IDs, LLM scores, and the other reviewer's ratings.
- The supplemental citation60 endpoint remains deterministic target-chunk ID inclusion only. No semantic-support metric is collected or reported.
- All experiment commands and data operations run on the isolated server instance at `/home/WZQ/citrus_experiment/`, not the local workstation or production instance.
- Completed work units are pushed to GitHub and paused for GPT review before the next unit.
- The two credentials pasted in chat are exposed and must be rotated; no existing plaintext credential is authorized for use or storage.
- The expert CSV in this planning commit is a blank schema only. Real per-question answer/evidence packets must be built from authentic server outputs after the corresponding run; do not create mock answers or ratings.
- Existing `model_config.json`, `judge_config.json`, prompts, and judge scripts remain legacy/unconfigured and are not authorized for execution until a separately reviewed v4.2 implementation commit.

## Current blockers and execution boundary

- Latest known SSH probe to `211.69.141.120:28022` timed out. Server work and G01 remain blocked pending a successful connectivity/isolation check.
- G01-G15 remain `PENDING`; this plan submission is not gate evidence.
- No provider connectivity, production update, pilot, formal experiment, scoring, or results generation is part of this review submission.
- The first implementation phase after review is offline code/config and packet-builder work, with all executable checks performed on the server. No external model calls occur until the credential owner has rotated keys and confirmed endpoint access/terms.

## Required review response

Identify concrete protocol contradictions, leakage paths, scoring ambiguities, statistical issues, server isolation risks, or unsupported model assumptions. For each finding, give severity and the affected path/section. Mark the design approved only after those issues are resolved; an approval is not evidence that gates, model access, or experiments passed.
