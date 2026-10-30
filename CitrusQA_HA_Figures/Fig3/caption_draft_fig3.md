# Fig.3 caption draft (HA style; final wording for review)

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

**(d)(e)** Paired differences Track A − Track B per dimension on the common evaluation
dimensions **D3–D10**, computed per question first (positive = CitrusQA better), with the zero
reference line and 95% paired confidence intervals (Student t, df = 83). Citation dimensions D1/D2
are excluded because they are not commonly comparable between the two configurations (Track B does
not retrieve). The bottom row, **Mean (D3–D10)**, summarises the pooled effect:
Δᵢ = (1/8) Σ_{d=3..10} (S_A,i,d − S_B,i,d) per question, then the paired mean and its 95% CI —
+0.580 [+0.418, +0.743] for glm-5.3 and +0.079 [−0.070, +0.228] for kimi-k2.6. No 80 % power band
and no MDD verdict are drawn in the figure; MDD definitions remain in Methods. The two judges are
reported separately and never averaged.

> Aggregation note (2026-10-16): the previously reported ten-dimension summary (+0.6372 glm /
> +0.0173 kimi) was produced by averaging **each configuration over its own available dimensions**
> (Track A included D1/D2, Track B did not) — an inconsistent aggregation. The corrected
> paired-common-dimension value is +0.5804 / +0.0789; direction and conclusions are unchanged.
