# Fig.1 caption draft (HA style; final wording for review)

**(EN)** **Fig.1** CitrusQA system design and evaluation overview.

**(a)** Researchers ask questions in Chinese. An intake step detects ambiguity and, when needed,
confirms the intended interpretation among 1–3 candidate readings; the confirmed intent is then
translated faithfully into English search queries (no rewriting). The local RAG agent retrieves
evidence from a frozen English-only corpus (4,906 documents; 126,494 chunks; DOI-keyed index) with a
dense (multilingual e5-large, LanceDB) and a sparse (BM25Plus) path fused by reciprocal rank fusion
(k = 60) under a lower-bounded cutoff, re-ranks the top candidates with a cross-encoder
(bge-reranker, top-K = 10) and generates answers that carry inline [n] citations only
(web markers [Wn] never enter Section 1; budget and early-stop caps bound the loop). In parallel, a
web agent performs server-side web search (snippets with URL and access date), self-judges whether
evidence is sufficient (otherwise retries, ≤ 2 rounds), cleans and de-duplicates results, and labels
every source T1/T2/T3 (labels are annotations, not a filter). Outputs of both chains enter an
assembly-and-audit stage: Stage A is a deterministic code gate (citation renumbering, removal of
out-of-pool references), followed by Stage B, a fast-model rubric review; a citation audit checks
citation-pool membership, and answers flagged critical receive at most one minimal repair before
re-validation. The final answer is emitted in two sections in the user's language: Section 1 (local,
verified) built only on the authoritative literature with inline numbered, DOI-clickable citations,
and Section 2 (web, unverified) that carries timely information explicitly marked "unverified by
local literature" with source URLs and access dates. Bottom badges summarise the four guarantees:
source isolation, code-based verification, Chinese-friendly interaction, and bounded timeliness.

**(b)** Knowledge-base overview: the frozen snapshot contains 4,906 documents and 126,494 chunks —
research literature 103,089, variety records 10,018, translated corpus 13,137, and experimental
methods & protocols 250. No Chinese-dominant chunks exist: Chinese sources are ingested as faithful
English translations and then follow the identical cleaning, chunking and indexing pipeline.

**(c)** Evaluation overview: 84-question system-layer comparison across Tracks A–E under two
independent judges; a 144-question three-pool stress test (in-corpus facts, adversarial decoys,
stale-timeliness, 48 each); 132 real user queries from a four-day window; two judges (kimi-k2.6,
glm-5.3) report per-judge with minimum-detectable-difference boundaries; a 48-question web-input
experiment spans five treatments, where the model-side hallucination rate rises from 3.1% (no web)
to 24.0% (unfiltered mixing); 1,649/1,649 in-text citations resolve and none point outside the
citation pool.

QA note (Round 2): the "Citation audit" card and the "Re-validate → back to Stage A" arrow follow the
approved layout reference; their exact trigger semantics remain flagged for confirmation
(00_audit/module_status.csv). Panel labels are lowercase per HA; no figure title is embedded.
