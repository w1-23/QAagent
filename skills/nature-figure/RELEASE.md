# nature-figure adapter 2.0.0

The standalone skill calls the single `citrusscholar-support` 2.0.0 distribution's `citrus_figures` package. No second distribution or private root pyproject is introduced.

The extraction retains the existing spec/resolution/statistical error semantics, Matplotlib engine, scientific style, semantic object manifest, overrides, SVG sanitization/projection, typed DataFrame codec, frozen geometry receipt and legacy receipt validator. Host file readers are replaced by explicit DataFrame mappings. Worker-only multi-output capture and worker/sandbox/store/project/readiness dependencies are excluded.

The new audit API separates execution from quality and binds artifact identities to exact bytes. The PDF layout snapshot is observed during its PDF draw event, without an extra draw. Dynamic draw callbacks or multiple export draws yield no layout attestation rather than an invented binding. Source preflight remains advisory with raw findings. Visual inspection without an explicitly injected capability is unexecuted. Reaudit receipts reference their predecessors.

Bundles include audited figure.pdf, runnable/saving plot.py, original source.py, source datasets and parameters with hashes, provenance, raw QA reports and receipts, object edit inventory and file-hash/version/revision manifest. Existing FigureSessions allow style-only edits without source/statistical recomputation. Reopening spec bundles uses resolved values. Arbitrary-source frozen reopening is explicitly limited to the existing session; no unsafe object deserialization is provided.

Upstream identity: `https://github.com/Yuan1z0825/nature-skills`, version 2.8.0, commit `7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8`. The original 40 files and licenses are copied verbatim. Four missing peripheral scripts were downloaded from that exact pin in a fresh empty directory and compared to the pinned tree's Git blob SHA-1; each new SHA-256 is recorded in provenance.json. Acquisition executed no downloaded script and installed no dependency. Upstream immutable files remain separate from Citrus adaptations. The copied INTEGRATION.md describes the older 40-file snapshot; this release/provenance describe the 44-file snapshot.

This is a curated skill snapshot, not a complete upstream repository. Excluded assets and execution limits are enumerated in references/bridge.md. Apache-2.0 upstream license and figures4papers notices remain intact, including upstream wording inconsistencies. No new permission for third-party figures is implied.

Acceptance is independent package-domain tests plus main-thread noneditable wheel verification. P2 establishes library/CLI behavior; API/UI/project history, existing agent tool aliases and sandbox execution remain host integration work.
