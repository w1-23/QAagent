# Fig.2 caption draft (HA style; final wording for review)

**(EN)** **Fig.2** Construction and quality control of the CitrusQA knowledge base.

**(a)** Corpus construction proceeds in four stages. (1) Source admission accepts whitelisted
journal sources (the authoritative-literature backbone spanning pathology & disease resistance,
physiology & metabolism, genetics & breeding, cultivation & management and post-harvest processing),
variety & rootstock records (including UCR germplasm entries and a curated citrus monograph) and
authorized private materials (experimental methods and reports). (2) Extraction and cleaning turns PDFs into text, normalises encodings,
removes layout noise (references, page furniture, standalone URLs, production notices) and
de-duplicates documents at two levels — a content hash of the raw file and a SHA-256 hash of the
normalised text, keeping one representative copy of identical normalised documents. (3) Metadata QA
normalises title, year and DOI (title 100%, year 90.7%); files that fail parsing or are too short
are excluded or flagged (branch shown). (4) Chunking and indexing split documents by Markdown
headings and then re-split recursively (target ≈ 800 characters; observed median 941 characters);
each chunk inherits its document DOI as the provenance key used at evidence assembly. Devoted dense
vectors (multilingual e5-large, 1,024-d, LanceDB IVF_HNSW) and a sparse BM25Plus inverted index are
built offline, and the frozen snapshot is version-controlled by a hash list and document manifest.

**(b)** Composition of the frozen corpus, drawn as flow bands whose widths are proportional to
chunk counts (minimum-width clamp at 0.2% for readability). Datasets: main corpus 112,890,
translated literature 13,137, special resources 467 — totalling 126,494 chunks from 4,906
documents. Source collections: parsed literature 62,803, supplementary literature 33,790, UCR
germplasm resources 9,801, additional literature 6,496 (main corpus); CN→EN translated corpus
13,137; experimental methods 250 and citrus monograph 217 (special resources). Resource classes:
research literature 103,089 (parsed + supplementary + additional), variety records 10,018 (UCR
germplasm + monograph), translated corpus 13,137, methods & protocols 250. Every column reconciles
to 126,494 chunks; Chinese sources enter only as faithful English translations, so the corpus
contains no Chinese-dominant chunks, and Chinese questions reach the corpus only through the
faithful CN→EN query rewriting of Fig.1.
