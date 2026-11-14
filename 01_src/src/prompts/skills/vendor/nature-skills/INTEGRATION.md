# nature-figure core integration

This pinned snapshot supplies upstream figure guidance, aesthetics, Python drawing helpers and four QA implementations for internal CitrusSchalor integration. Host adapters may execute and adapt these capabilities under the project's authorization; keep adapters outside this original-byte snapshot. Vendoring alone does not establish that runtime checks have passed.

## Source and preservation

- Source: https://github.com/Yuan1z0825/nature-skills
- Commit: `7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8`
- Manifest version: `2.8.0`
- 40 upstream files, 418,041 bytes, preserving the original `skills/` layout and original bytes.
- `provenance.json` lists each copied upstream file with a vendor-relative path and SHA-256. Local `INTEGRATION.md`, `provenance.json`, and `.gitattributes` are excluded from upstream hashes (43 files total).
- Local `.gitattributes` disables Git text conversion for `/LICENSE` and `/skills/**`, preserving bytes across Windows autocrlf checkouts.

GitHub contents tools established the pinned directory structure. Bulk downloads used the GitHub tree API and fixed-commit raw URLs in fresh scratch staging directories, with custom download scripts stored separately and run using Python isolated mode. Each download was verified against the pinned tree's Git blob SHA-1. Final copied files were byte-compared with staging and SHA-256 verified. No upstream script, installer, image API, or dependency installation was executed as part of acquiring this snapshot. Runtime integration and its tests are separate work.

## Included resources

| Group | Files | Scope |
|---|---:|---|
| Entry and manifest | 2 | `skills/nature-figure/SKILL.md`, `manifest.yaml` |
| Static guidance | 4 | All `static/**/*.md` |
| References | 21 | All `references/**/*.md` |
| Shared Markdown | 4 | Recursive dependency closure below |
| License and attribution | 2 | Root `LICENSE`, figures4papers `THIRD_PARTY_NOTICES.md` |
| Python scripts | 6 | Four QA scripts and two drawing/helper modules below |
| Dependency declaration | 1 | Original `skills/nature-figure/requirements.txt` |

Shared closure: `skills/nature-shared/core/nature-results-discussion.md`, `skills/nature-shared/journal-formats/nature-machine-intelligence.md`, and that journal document's dependencies `skills/nature-shared/core/research-compliance.md` and `skills/nature-shared/core/ethics.md`.

The six scripts under `skills/nature-figure/scripts/` are:

| Script | Core entry points | Dependencies |
|---|---|---|
| `audit_panel_alignment.py` | `audit_layout_manifest(manifest, *, tolerance_pt=1.5, gutter_tolerance_pt=1.5, require_panel_labels=False)`, `matplotlib_layout_manifest(fig, ...)`, `require_matplotlib_panel_alignment(fig, *, json_out=None, overlay_svg=None, tolerance_pt=1.5, gutter_tolerance_pt=1.5, require_panel_labels=False, strict=False, **manifest_options)` | Standard library; geometry bridge consumes a Matplotlib figure |
| `audit_pdf_text.py` | `audit_pdf(data: bytes, minimum_pt=5.0)` | Standard library, including zlib |
| `audit_figure_collisions.py` | `audit_pdf(path: Path, *, text_inset_pt=0.6, min_overlap_ratio=0.05, clipping_tolerance_pt=0.5)`, `exit_code(result, strict=False)` | PyMuPDF |
| `validate_figure.py` | `validate_source(source: str, backend: str)`, `summarize(findings, strict=False)` | Standard library |
| `figure_safety.py` | `interp_monotone(target, xp, fp)`, `label_y_above(values, spread=None, pad_fraction=0.04)` | NumPy |
| `plot_templates.py` | `PALETTE`, `configure_style()`, `save_bundle(fig, output: Path)`, five CSV template functions | NumPy, Matplotlib |

Original requirements contain only `PyMuPDF>=1.24.0,<2.0.0`. The host must also provide NumPy and Matplotlib for drawing helpers; no R runtime or network-image dependency is introduced. Static AST inspection found no imports of other local scripts in these six files.

`plot_templates.py` selects Matplotlib Agg on import; `configure_style()` changes rcParams; `save_bundle()` writes SVG/PDF/TIFF and closes the figure. It does not automatically run the four QA checks. Adapters should control these effects and attach QA to the final artifact. `require_matplotlib_panel_alignment()` raises `PanelAlignmentError` on a blocking report. The PDF text scanner reports supported content streams only and explicitly does not cover every PDF transform, such as Form XObject matrices.

## References and excluded resources

Static inspection covered Markdown link destinations, path mentions, manifest resource paths and the recursive shared Markdown closure. All referenced Markdown resources resolve within this snapshot. Anchors and external URLs were not validated. Upstream uses document-relative paths, skill-root paths, repository-root paths and prose shorthand: use canonical manifest paths with containment checking. `core/contract.md` and `core/stance.md` in prose mean `static/core/contract.md` and `static/core/stance.md`.

Four execution peripherals are deliberately excluded: `scripts/panel_alignment.R`, `scripts/audit_svg_schematic.py`, `scripts/nature_figure_backend.py`, and `scripts/generate_openrouter_schematic.py`. Their guidance remains available in original references, but this integration does not offer R execution, backend-preference CLI execution, SVG schematic CLI auditing or external image-generation API execution. The explicit Markdown link in `SKILL.md:36` to `scripts/generate_openrouter_schematic.py` therefore has no packaged target. Other excluded script references are path mentions/manifest fields. Python preferences, if needed, belong in the host's own settings.

Excluded: all upstream tests/evals, agents configuration, README files, unused shared documents, binary assets, third-party example scripts, chart-atlas and gallery images. `references/demos.md` points to unavailable figures4papers directories `figure_ImmunoStruct/`, `figure_CellSpliceNet/`, `figure_brainteaser/`, `figure_VIGIL/`, `figure_ophthal_review/`, `figure_RNAGenScape/`, `figure_Dispersion/`, `figure_Cflows/`, and `figure_FPGM/`. Only their attribution notice is included. Runtime placeholders such as `schematic_prompt.md`, `abstract.txt`, `figure.pdf`, `figure.alignment.json`, `path/to/figure.py` and `figures/*.png` are not missing static dependencies. Private R collections and external sites are not packaged.

## Host integration and truthful QA

Keep original scripts immutable and place host adaptations in separate files. Verify pinned hashes before loading code from an explicit trusted vendor path. Do not add user-upload directories to import paths. Execute only the explicitly integrated capabilities; the existence of command examples in text is not a request to run a CLI, install dependencies or call an external service.

The four-check pipeline consists of source preflight, final rendered panel geometry, final PDF glyph floor, and collisions on that same final PDF. Use the upstream defaults (including 1.5 pt alignment tolerance and 5 pt glyph floor); preserve per-check findings, warnings and artifact identity. A final-layout revision invalidates prior rendered QA. Source preflight is not statistical or visual validation. `FIX BEFORE DELIVERY`/exit 1 blocks publication; `NOT AUDITABLE`/exit 2 or a missing dependency must never become a pass; WARN requires retained review information. Single-panel alignment can be not-applicable rather than passed.

Final physical-size review remains necessary for uncertainty semantics, replicate definitions, source-data traceability, scientific meaning, accessibility, image integrity and editability. Preserve target-journal distinctions and consult current official requirements when preparing a submission. Vendored references and methods are not proof of journal acceptance.

Acquisition validation here: pinned-tree completeness, resource closure, static imports/signatures, byte identity, Git blob verification and SHA-256 provenance verification. Runtime integration tests must report their own actual results; acquisition did not run upstream self-tests, CLIs, figure QA or live journal-policy checks.

## License and attribution

Root `LICENSE` is Apache License 2.0 and is copied verbatim. The pinned root has no `NOTICE`. The figures4papers notice and embedded `academic-figure-skill` attribution in `asset-adaptation.md` and `plot_templates.py` are retained. Source modifications for internal adaptation belong outside this snapshot, with attribution preserved.

Upstream `references/demos.md` and `THIRD_PARTY_NOTICES.md` still refer to a root MIT license, an inconsistency with this commit's actual Apache-2.0 LICENSE. Original wording remains unchanged. The notice expressly warns that figures4papers materials have no established reuse license; this package includes their notice only, not their scripts or images, and grants no extra third-party rights.
