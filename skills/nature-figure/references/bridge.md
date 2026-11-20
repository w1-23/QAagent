# Original-resource bridge

All paths below resolve from this file's directory. Original bytes live under `../vendor/nature-skills/`; originals keep their internal `skills/` layout.

| Topic | Original resource |
|---|---|
| Main upstream skill | [SKILL.md](../vendor/nature-skills/skills/nature-figure/SKILL.md) |
| Figure and QA contracts | [figure-contract.md](../vendor/nature-skills/skills/nature-figure/references/figure-contract.md), [qa-contract.md](../vendor/nature-skills/skills/nature-figure/references/qa-contract.md) |
| Design and form | [design-theory.md](../vendor/nature-skills/skills/nature-figure/references/design-theory.md), [chart-types.md](../vendor/nature-skills/skills/nature-figure/references/chart-types.md) |
| Multipanel design | [multipanel-evidence-architecture.md](../vendor/nature-skills/skills/nature-figure/references/multipanel-evidence-architecture.md) |
| Statistical annotations and legends | [figure-legend-conventions.md](../vendor/nature-skills/skills/nature-figure/references/figure-legend-conventions.md) |
| Python and R | [backend-selection.md](../vendor/nature-skills/skills/nature-figure/references/backend-selection.md), [api.md](../vendor/nature-skills/skills/nature-figure/references/api.md), [r-workflow.md](../vendor/nature-skills/skills/nature-figure/references/r-workflow.md) |
| Schematic vector workflow | [vector-schematic-workflow.md](../vendor/nature-skills/skills/nature-figure/references/vector-schematic-workflow.md), [vector-schematic-style-rules.md](../vendor/nature-skills/skills/nature-figure/references/vector-schematic-style-rules.md) |
| Image model workflow | [ai-graphical-abstract-workflow.md](../vendor/nature-skills/skills/nature-figure/references/ai-graphical-abstract-workflow.md), [openrouter-image-generation.md](../vendor/nature-skills/skills/nature-figure/references/openrouter-image-generation.md) |
| Journal distinctions | [nature-article-requirements.md](../vendor/nature-skills/skills/nature-figure/references/nature-article-requirements.md), [nature-machine-intelligence.md](../vendor/nature-skills/skills/nature-shared/journal-formats/nature-machine-intelligence.md) |
| Integrity | [ethics.md](../vendor/nature-skills/skills/nature-shared/core/ethics.md), [research-compliance.md](../vendor/nature-skills/skills/nature-shared/core/research-compliance.md) |

Upstream prose `core/contract.md` and `core/stance.md` means `skills/nature-figure/static/core/contract.md` and `static/core/stance.md` under the vendor root. Do not reinterpret repository-root paths relative to the current working directory.

The four peripheral scripts referenced by upstream are present at the exact pin: `panel_alignment.R`, `audit_svg_schematic.py`, `nature_figure_backend.py`, `generate_openrouter_schematic.py`. Packaging them grants no permission to run R, persist backend preferences or call image APIs. Citrus exposes an in-process SVG-byte audit; other peripheral execution remains explicit host work.

The original demo references mention nine figures4papers directories not packaged here. Gallery/chart-atlas images, private R collections, binary assets, upstream tests/evals and other shared documents are not included. The figures4papers attribution notice is retained but provides no additional reuse rights. External URLs and current journal policies require separate retrieval and verification. Do not describe these resources as available merely because prose names them.


## Adapter resource identity

- Current adapter resource set: `nature-figure` version `2.1.0`. The authoritative manifest digest is `skills[].sha256` in `02_config/skills.catalog.json`; it covers every delivered adapter and vendor file listed there.
- Superseded adapter identity: version `2.0.0`, manifest digest `00779e01d2c8756245c3553adb8abf451c4eb1dcd15c46ce7192ce3892d7f127`. That identity referenced two entries absent from the fixed snapshot: `tests/test_resources.py` (catalog) and `vendor/nature-skills/.gitattributes` (outer provenance). The current identity removes them instead of fabricating them.
- The upstream pin is unchanged: commit `7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8`, skill version `2.8.0`, 44 fixed files, canonical inventory SHA-256 `a9b8e661bf460a401e17017f134a735432da9d7832e3f561a63c6cf47fb92b9e`. Vendor bytes are not modified.
- The separate 40-file legacy identity remains under `01_src/src/prompts/skills/vendor/nature-skills` and is still readable through `src/prompts/skill_registry.py` (version `2.8.0`, same commit). It is frozen for historical receipts and is not the canonical adapter source.
- `src/agent/research_skill_catalog.py` resolves only one pinned identity per skill. Reading the superseded `2.0.0` adapter identity through that API requires the main session to update `PINNED` or add an alias; no dual-identity path exists in the adapter catalog today.

## Fixed snapshot versus user-supplied ZIP

The packaged snapshot is the fixed 44-file set at commit `7bff1d0` / `2.8.0`. The uploaded `nature-skills-main` ZIP (130 files in the skill subtree) is a different upstream revision and is not equivalent to the pin: of 39 same-named comparable files, 31 are byte-identical and 8 differ. The differences are the ZIP's Windows UTF-8 `sys.stdout/stderr.reconfigure(...)` additions plus two functional additions:

- `scripts/audit_pdf_text.py`: adds `Tc`/`Tw` character/word-spacing collection and `spacing_issue_*` report fields.
- `scripts/validate_figure.py`: also flags `device=svglite` SVG usage.

The other six (`references/r-workflow.md`, `scripts/audit_figure_collisions.py`, `scripts/audit_svg_schematic.py`, `scripts/generate_openrouter_schematic.py`, `scripts/nature_figure_backend.py`, `scripts/plot_templates.py`) differ only by the UTF-8 reconfigure, and `r-workflow.md` switches to explicit device calls. The ZIP also contains 84 asset files plus `agents/`, `evals/`, `tests/`, READMEs and a `.gitignore` that are not packaged. Do not overwrite the fixed snapshot with ZIP bytes; a future upgrade needs a new pin, both provenance layers and the catalog recomputed.

## Python path and disabled adapters

- Python library: `packages/citrus_figures` (distribution `citrusscholar-support[figures]`), exposed as `citrus_figures` (`render_figure`, `revise_figure`, `write_bundle`, `load_spec_bundle`, `verify_current_figure`, `FigureSession`) and `python -m citrus_figures`.
- Adapters: `scripts/citrus/{render,bundle,revise,verify}_figure.py`, 430 B each, passing an explicit `vendor_root`; vendored scripts are loaded only through `citrus_figures.nature_runtime.load_module` with hash validation.
- R: `scripts/panel_alignment.R` and the R references are reference-only. The service does not require, install or execute `Rscript`/`R`.
- OpenRouter: `scripts/generate_openrouter_schematic.py` is retained as reference only. The service never calls it, performs no image-generation network request and needs no `OPENROUTER_API_KEY`.
