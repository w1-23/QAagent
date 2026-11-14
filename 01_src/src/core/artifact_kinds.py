"""Stored renderer identities, separate from model-visible tool capabilities.

Old artifact producer names remain immutable and readable after consolidation.
The unified entrypoint's union of possible outputs is never evidence of what
one particular file contains.
"""
RENDERER_OUTPUT_KINDS = {
    "export_table": frozenset({"table_file"}),
    "export_literature_evidence": frozenset({"bibliography"}),
    "export_paper_analysis": frozenset({"report_html"}),
    "export_experiment_design": frozenset({"report_html"}),
    "export_research_notes": frozenset({"report_html"}),
    "export_corpus_gap_report": frozenset({"gap_csv"}),
    "render_research_report": frozenset({"report_html"}),
    "figure_workspace": frozenset({"figure", "figure_package"}),
}
