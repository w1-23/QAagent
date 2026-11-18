# Citrus figure adapter 2.0.0

This skill is installed beside the support distribution; its resources are not assumed to live inside a Python package. The host supplies the skill resource location explicitly. No legacy `src` import, agent, model initialization, secret discovery or environment mutation is used.

| Path or input | Resolution base | Contract |
|---|---|---|
| `scripts/citrus/*.py` | Directory containing SKILL.md | Thin installed-package CLI adapters; do not add repository paths to sys.path |
| Adapter default vendor root | `Path(__file__).resolve().parents[2] / 'vendor/nature-skills'` | Absolute injected path; caller may override `--vendor-root` |
| `vendor/nature-skills` | Directory containing SKILL.md | Validate source, commit, pinned inventory, containment and the executed helper's SHA-256; full release validation separately checks every resource |
| Original vendor reference links | Original document directory; upstream repository-root mentions refer to vendor root | See references/bridge.md for prose shorthand |
| CLI artifact/source/spec/data/policy/output | Explicit caller path, relative paths use caller working directory | CLI reads only named inputs; bundle destination must not exist |
| Bundle member paths | Bundle directory | Validate manifest membership, containment and hashes when reopening |
| Runtime scratch | Private TemporaryDirectory | PDF collision check receives only audited bytes; scratch path not retained |
| `tables` library argument / CLI `--table KIND:KEY=CSV_PATH` | Explicit map `(kind, key) -> DataFrame` or explicitly named CSV | Caller resolves ownership, parsing budgets and data completeness; no host file lookup |
| Visual inspection | Explicit `visual_inspector(bytes, identity)` callback | Caller owns model/network authorization; absent callback is not_run |

Public entry:

```python
from citrus_figures import verify_current_figure
receipt = verify_current_figure(
    artifacts={"pdf": final_pdf_bytes, "svg": final_svg_bytes},
    source=original_source,
    layout_manifest={"artifact_sha256": pdf_sha256, "layout": captured_layout},
    figure=None, backend="python", mode="initial",
    vendor_root=explicit_vendor_root,
    checks=["format_integrity", "source_preflight", "panel_alignment", "pdf_text", "pdf_collisions", "visual"],
    policy={"blocking_checks": ["format_integrity", "panel_alignment", "pdf_text", "pdf_collisions"]},
)
```

Artifacts may also be a list of `{role, format, bytes}`. Resolve external references before calling. `figure` is accepted for interface compatibility but does not authorize a rerender or prove old PDF geometry. Layout must name a supplied artifact hash. Default checks cover format signatures, source, panel geometry, PDF text/collisions, SVG schematic rules and visual capability. Default source preflight is advisory; its raw FAIL is never relabeled PASS. Format integrity checks signatures/XML roots, not complete decoding. PDF text has the upstream stream-coverage limitations; raw reports retain them.

The pinned SVG helper at commit `7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8` uses `math` in rotation handling without importing it. The adapter supplies the standard-library module in that helper's namespace, without changing vendor bytes or hashes. SVG receipts declare `coverage.adapter_compatibility=["pinned_svg_missing_math_import"]`; raw findings and verdicts remain the upstream result.

Each check separately reports `executed=not_run|attempted|executed` and `conclusion=pass|warn|fail|not_applicable|undetermined`. Exceptions produce structured nonretryable errors and `attempted/undetermined`, never pass. Unsupported or unauditable inputs retain raw reports and incomplete coverage. Missing tools do not suppress independent checks. Overall fail/error/incomplete remains visible alongside advisories and unexecuted checks. Reaudit requires `supersedes`; edit also audits current bytes.

`render_figure(spec=..., tables=...)` retains host spec parsing, aligned rows, error definitions and chart rendering. `render_figure(source=..., data=..., parameters=...)` handles arbitrary Matplotlib plots without template selection. `revise_figure` reuses an existing FigureSession; `write_bundle` exports and audits one final PDF. `load_spec_bundle` rebuilds from resolved numbers and reapplies styles without statistical recomputation. Arbitrary source reopening does not deserialize pickles or execute source; use the original in-memory session for frozen style revisions, or explicitly authorize a new initial render.

The library handles computation and local file bundles only. Ownership, leases, sandboxing, cancellation, deadlines, execution budgets, publication, project history and UI/API behavior remain host responsibilities. The old worker receipt validator remains as `nature_receipts.validate_nature_receipt` for explicitly versioned legacy integrations; it does not accept or impersonate the new receipt schema.
