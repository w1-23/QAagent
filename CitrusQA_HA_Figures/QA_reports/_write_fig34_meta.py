# -*- coding: utf-8 -*-
"""Captions + manifests for Fig.3 / Fig.4 (Round 3)."""
import json
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
F3, F4 = ROOT / "Fig3", ROOT / "Fig4"

CAP3 = """# Fig.3 caption draft (HA style; final wording for review)

**(EN)** **Fig.3** Cross-lingual retrieval and comparative answer quality of CitrusQA.

**(a1)** Retrieval-language effect on the synthetic cross-lingual set (n = 96 questions; 2 × 2 design
of retrieval language × reranking language). Bars show the share of questions whose gold passage
passed the dynamic threshold and entered the delivered top-10 evidence set, with Wilson 95%
confidence intervals. Queries issued in English (the faithful translation of the Chinese question)
raise the pass rate from 45.8% to 93.8–95.8% under both rerankers, whereas the reranking language
alone changes little.

**(a2)** The same questions restricted to the gold-anchored subset (n = 48), where each question has a
verified gold passage: with a raw-English reranker, English queries pass 95.8% (95% CI 86.0–98.9)
versus 81.2% (95% CI 68.1–89.8) for Chinese queries; a paired exact McNemar test gives p = 0.016.
The two denominators (96 synthetic questions, 48 gold-anchored) are labelled separately and are never
pooled.

**(b)(c)** Mean answer-quality scores of the five system-layer configurations (Track A = CitrusQA with
the full retrieval–assembly–audit chain; Track B = same base model and prompts without retrieval;
Track C = same base model, factory prompts, no retrieval; Tracks D/E = a second base model with and
without the chain) on dimensions D3–D10, scored blind by two independent judges (kimi-k2.6, glm-5.3)
on a 1–5 anchored rubric. Citation dimensions D1/D2 are excluded because they are undefined for the
non-retrieval configurations (see Fig.4b1). n = 84 questions per track, except Track D (n = 80).

**(d)(e)** Paired differences Track A − Track B per dimension, computed from the per-question scores of
the same 84 questions (positive = CitrusQA better), with 95% confidence intervals (paired t, t_{.975,83}).
Filled markers exceed that dimension's minimum detectable difference (MDD); hollow markers do not.
The ten-dimension combined paired difference is +0.6372 (glm-5.3, MDD 0.1596, distinguishable) and
+0.0173 (kimi-k2.6, MDD 0.1643, not distinguishable) — the two judges are always reported separately
and never averaged.
"""

CAP4 = """# Fig.4 caption draft (HA style; final wording for review)

**(EN)** **Fig.4** Citation traceability, answer quality and web-input risk.

**(a)** Citation resolvability — the share of in-text citation markers that resolve to a real source in
the evidence pool (denominators are stated per bar). In the frozen system-layer batch all 1,649
markers resolve and none points outside the citation pool; the user-visible layers remain at
99.96% (three-pool batch) and 99.78% (real users); the 85.07% value is a streaming intermediate state
recorded for diagnosis only and is not a user-facing result. Resolvability is a structural property
and is not the same as whether a citation supports its claim.

**(b1)** Judge-scored citation support (D2, 1–5) for Track A, the only configuration that retrieves and
therefore the only one for which D2 is defined; n = 68 (kimi-k2.6) and 78 (glm-5.3) judged answers
(D2 is left unscored where a claim has no citation).

**(b2)** Judge-scored answer factuality (D3, 1–5) across Tracks A–E (n = 84 per track; Track D n = 80),
reported separately for the two judges. Support and factuality are distinct calibers from
resolvability and from each other.

**(c)** Model-side hallucination rate under five web-input treatments on the 48-question timeliness
subset (Pool C): A no web input, B web evidence mixed into the local pool unfiltered, C legacy
filtering level, C' filtering to the model-visible level, D excerpts-only. Each arm contributes 96
judge × question verdicts (48 questions × 2 judges) and the rate is the per-question mean of
hallucination booleans. Unfiltered mixing is the worst condition (24.0%); receipt-level and
model-visible filtering reduce it to 18.8–33.3%. Pairwise calls use the minimum detectable difference
(A−B = −20.8 pp, MDD 7.8 pp, distinguishable; A−C' = −15.6 pp, MDD 8.6 pp, distinguishable;
B−C' = 5.2 pp < MDD 12.4 pp, not distinguishable). The 3.1% no-web baseline characterises that arm
only and is not evidence for the two-section isolation mechanism.
"""

MAN3 = {
    "figure_id": "Fig3",
    "figure_title": "Cross-lingual retrieval and comparative answer quality",
    "data_sources": [
        "_sources/exp1_summary.json (2×2 cross-lingual, synthetic 96 questions; gold-anchored subset 48)",
        "_sources/judge_scores_{kimi-k2.6,glm-5.3}.jsonl (84 questions × 5 tracks, blind)",
        "statistics/fig3_recomputed_stats.json (this project's independent recomputation)",
        "experiment/13_评测管道_pipeline/results/retrieval/crosslingual_20260918/per_question.jsonl",
    ],
    "source_code_refs": ["experiment/mechanism/scripts/analyze_exp1.py (upstream)",
                         "statistics/_recompute_fig3.py (this project)"],
    "experiment_conditions": "2×2 retrieval/rerank language on synthetic set; five-track system-layer comparison",
    "number_of_samples": "96 (a1) · 48 (a2) · 84 per track (Track D 80) for b–e",
    "plot_types": ["bar + Wilson CI", "paired dumbbell", "heatmap", "paired-difference forest"],
    "statistical_methods": ["Wilson 95% CI", "exact McNemar", "paired t 95% CI",
                            "minimum detectable difference (MDD)"],
    "output_dimensions": "174.0 × 132.0 mm",
    "output_formats": ["svg(editable text)", "pdf", "png(300dpi)", "tif(600dpi)"],
    "verification_status": "Round-3: recomputed from per-question scores; ten-dim combined deltas "
                           "reproduce the manuscript exactly (+0.6372 / +0.0173); manuscript MDDs use "
                           "z=1.96 while this figure uses t_{.975,83} (0.1621 vs 0.1596 glm; 0.1669 vs "
                           "0.1643 kimi) — flagged, not silently changed",
}

MAN4 = {
    "figure_id": "Fig4",
    "figure_title": "Citation traceability, answer quality and web-input risk",
    "data_sources": [
        "data_sources.CITATION_LEVELS (frozen reporting layers; headline 1,649 markers / 0 outside)",
        "_sources/judge_scores_{kimi,glm}.jsonl (D2/D3 per question)",
        "_sources/web_arms_summary.json (five treatments; 48-question subset; MDD calls)",
        "experiment/09_联网五组对照_web_arms/01_实验脚本_scripts/analyze_web_arms.py (arm definitions)",
        "data_archive/04_web_experiments/threshold/support_offline_arms.json (claim-support audit)",
    ],
    "source_code_refs": ["analyze_web_arms.py, tables_webarms.py (n=96 = 48 q × 2 judges)"],
    "experiment_conditions": "five web-input treatments (A/B/C/C'/D) on 48 timeliness questions",
    "number_of_samples": "1,649 markers (a) · 68/78 (b1) · 84 (b2) · 96 verdicts/arm (c)",
    "plot_types": ["horizontal bar", "bar + SD/CI-free means", "bar with MDD annotations"],
    "statistical_methods": ["descriptive rates", "MDD-based pairwise calls"],
    "output_dimensions": "174.0 × 92.0 mm",
    "output_formats": ["svg(editable text)", "pdf", "png(300dpi)", "tif(600dpi)"],
    "verification_status": "Round-3: denominators and the n=96 semantics verified against the "
                           "experiment source; 3.1% labelled as the no-web baseline only",
}

(F3 / "caption_draft_fig3.md").write_text(CAP3, encoding="utf-8")
(F4 / "caption_draft_fig4.md").write_text(CAP4, encoding="utf-8")
(F3 / "figure_manifest_fig3.json").write_text(json.dumps(MAN3, ensure_ascii=False, indent=1), encoding="utf-8")
(F4 / "figure_manifest_fig4.json").write_text(json.dumps(MAN4, ensure_ascii=False, indent=1), encoding="utf-8")
print("captions + manifests written for Fig3/Fig4")
