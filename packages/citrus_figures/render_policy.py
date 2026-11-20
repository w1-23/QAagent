"""Capture geometry during the exact PDF export, then audit the exported bytes."""
from __future__ import annotations
from .verification import sha256, verify_current_figure

RENDER_PROTOCOL = 2


def _layout_at_draw(figure):
    # Adapted from pinned nature-skills matplotlib_layout_manifest. Unlike that
    # helper this observes an already completed PDF draw and never calls draw().
    from matplotlib.colorbar import Colorbar
    width, height = [float(v) * 72 for v in figure.get_size_inches()]
    panels, grids, seen = [], {}, set()
    for index, ax in enumerate(figure.axes):
        colorbar = getattr(ax, "_colorbar", None)
        if not ax.get_visible() or (isinstance(colorbar, Colorbar) and colorbar.ax is ax):
            continue
        spec = ax.get_subplotspec() if hasattr(ax, "get_subplotspec") else None
        if spec is not None:
            key = (id(spec.get_gridspec()), spec.num1, spec.num2)
            if key in seen:
                continue
            seen.add(key)
        position = ax.get_position(original=False)
        panel = {"id": ax.get_gid() or f"axes_{index}", "bbox_pt": [float(position.x0 * width), float(position.y0 * height), float(position.x1 * width), float(position.y1 * height)]}
        if spec is not None:
            grid = id(spec.get_gridspec())
            grids.setdefault(grid, f"grid_{len(grids)}")
            panel.update(grid_id=grids[grid], row_start=int(spec.rowspan.start), row_stop=int(spec.rowspan.stop), col_start=int(spec.colspan.start), col_stop=int(spec.colspan.stop))
        for text in ax.texts:
            label = text.get_text().strip()
            if len(label) == 1 and "a" <= label <= "z" and str(text.get_fontweight()) in {"bold", "semibold", "demibold", "heavy", "black"}:
                display = text.get_transform().transform(text.get_position())
                axis_xy = ax.transAxes.inverted().transform(display)
                if -.35 <= axis_xy[0] <= .2 and .8 <= axis_xy[1] <= 1.35:
                    inches = figure.dpi_scale_trans.inverted().transform(display)
                    panel.update(panel_label=label, panel_label_anchor_pt=[float(v * 72) for v in inches])
                    break
        panels.append(panel)
    return {"schema_version": 1, "backend": "python-matplotlib", "figure": {"width_pt": width, "height_pt": height}, "panels": panels, "exemptions": []}


def export_with_layout(rendered, *, exporter, dpi=300):
    fig = rendered.figure
    snapshots = []
    # Existing callbacks may change the figure after painting; do not attest to
    # geometry for such figures. The artifact audits still inspect final bytes.
    dynamic = bool(fig.canvas.callbacks.callbacks.get("draw_event"))
    def capture(event):
        snapshots.append(_layout_at_draw(event.canvas.figure))
    token = fig.canvas.mpl_connect("draw_event", capture)
    try:
        pdf = exporter.export_all(rendered, formats=("pdf",), dpi=dpi)["pdf"]
    finally:
        fig.canvas.mpl_disconnect(token)
    # Matplotlib may draw once to measure layout and again to paint the PDF.
    # Certify geometry only when every observation from this export agrees.
    layout = None
    if not dynamic and snapshots and all(snapshot == snapshots[0] for snapshot in snapshots):
        layout = {"artifact_sha256": sha256(pdf), "layout": snapshots[-1],
                  "capture": "pdf_draw_event", "rerendered": False}
    return pdf, layout


def export_figure(rendered, *, formats=("pdf", "svg", "png"), dpi=300, source="",
                  mode="initial", vendor_root=None, checks=None, policy=None,
                  supersedes=None, exporter=None):
    if exporter is None:
        from . import exporter
    if mode not in {"initial", "edit", "reaudit"}:
        raise ValueError("invalid render mode")
    pdf, layout = export_with_layout(rendered, exporter=exporter, dpi=dpi)
    blobs = exporter.export_all(rendered, formats=tuple(fmt for fmt in formats if fmt != "pdf"), dpi=dpi)
    # Bundles always retain the PDF actually audited, even if callers requested
    # only a preview format. Never make a second PDF after QA.
    blobs["pdf"] = pdf
    receipt = verify_current_figure(artifacts=blobs, source=source, layout_manifest=layout,
                                    mode=mode, vendor_root=vendor_root, checks=checks,
                                    policy=policy, supersedes=supersedes)
    return blobs, receipt, layout
