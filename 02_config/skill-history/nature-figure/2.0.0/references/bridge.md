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
