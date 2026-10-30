# Fig.4 caption draft (HA style; final wording for review)

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
