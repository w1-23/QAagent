# Experiment script layout

The top-level `scripts/*.py` files are stable orchestration modules kept for
backward compatibility. Formal execution entry points are owned by the
experiment-specific directories below so that inputs, outputs and stopping
conditions cannot be mixed.

| Directory | Experiment |
|---|---|
| `S00_simulated_dry_run/` | complete, explicitly synthetic tool-chain test; never a study result |
| `P00_pilot/` | public/pilot readiness and SSE capture |
| `A00_validity/` | 120-question, two-reviewer validity workflow |
| `A01_llm_judge/` | frozen core-QA machine judging |
| `E01_qa60_egts/` | core QA60 answer quality |
| `E01_chunk_citation/` | supplementary citation60 target-chunk hit |
| `E02_route192/` | observable tool-routing compliance |
| `E03_multi24/` | composite tool-set execution |
| `E04_citation_repeat72/` | fixed-panel citation repeat sensitivity |
| `E05_wf01_pdf_to_report/` | WF01 |
| `E06_wf02_variety_evidence/` | WF02 |
| `E07_wf03_editing_compare/` | WF03 |
| `E08_wf04_table_audit/` | WF04 |
| `E09_wf05_doi_verify/` | WF05 |
| `E10_wf06_svg/` | WF06 |
| `E11_wf07_fusion/` | WF07 |
| `U01_user12/` | optional ethics-gated user study |
| `A01_analysis/` | strict analysis and figures |
| `M01_manuscript_audit/` | manuscript claim/number/citation audit |

Rules:

1. Every experiment writes only to its registered manifest and raw/scoring
   paths.
2. Inputs must be hash-locked before an experiment entry point can execute.
3. Missing, partial, cancelled, unavailable and judge-error outcomes remain in
   the denominator audit; they are never rewritten as success or zero.
4. No entry point stores credentials. Model endpoints and keys come from
   environment variables named in the frozen configuration.
5. A failed gate or schema check stops the formal experiment. It never falls
   back to a simulated result.
6. The complete experiment package is versioned, including frozen chunk text,
   raw runs, judge responses, scores, tables, figures and manuscript sources.
   Credentials and personal authentication data are the only exclusions.
