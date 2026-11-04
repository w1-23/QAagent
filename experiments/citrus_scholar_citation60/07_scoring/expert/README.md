# Human expert scoring materials

The final human consensus is the primary answer-quality measurement for core QA60. Two experts independently score every Pilot (36) and formal QA60 response (180). A third expert adjudicates every disagreement across the four rubric fields. Scoring follows `00_protocol/EXPERT_SCORING_PROTOCOL.md`.

This directory holds the blank score-sheet schema and public scoring instructions. It does not contain answer packets or completed expert judgments. Build reviewer-specific, condition-blinded packets on the server after the corresponding answers and common reference evidence exist. Keep the condition/question mapping out of reviewer folders.

`expert_scoring_TEMPLATE.csv` is a header-only starter. The server packet builder will create one row per assigned blind ID and may also render an XLSX with matching validation. Preserve each submitted CSV/XLSX byte-for-byte, compute its SHA, then validate and create separate derived consensus files. Never edit a returned reviewer file in place.

Public packets may include only material permitted for redistribution. For restricted sources, preserve the source SHA and locator in the public manifest and deliver the actual reference excerpt through approved server-side access.
