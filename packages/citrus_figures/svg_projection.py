"""Renderer-owned SVG identities for local style projection, never data editing."""
from __future__ import annotations


def projection_manifest(rendered, bbox):
    from matplotlib.lines import Line2D
    from matplotlib.collections import PathCollection
    from citrus_figures.overrides import _capture_legend_links, _legend_rows
    metadata = {}
    for gid, entry in rendered.locators.items():
        role, artist = entry.get("role"), entry.get("artist")
        if role in {"grid", "errorbar", "colorbar"}:
            # These groups can share artists with existing axes/series IDs.
            # Preserve their identities and use authoritative server preview.
            metadata[gid] = {"svg_nodes": []}
            continue
        if role in {"point", "bar"}:
            # ScatterPoint is not a separately emitted SVG artist. Bar children
            # may share identity with a series: do not invent an index mapping.
            continue
        members = list(entry.get("members") or [artist]) + list(entry.get("error_members") or [])
        members = list({id(item): item for item in members if item is not None}.values())
        ids = []
        for index, member in enumerate(members):
            node_id = gid if index == 0 else f"{gid}.member_{index}"
            if hasattr(member, "set_gid"):
                member.set_gid(node_id)
                ids.append(node_id)
        record = {"svg_nodes": ids}
        if isinstance(artist, Line2D):
            record["markerface_auto"] = getattr(artist, "_markerfacecolor", None) == "auto"
            record["markeredge_auto"] = getattr(artist, "_markeredgecolor", None) == "auto"
        if role == "series":
            # Exactly the same ambiguity rules as the authoritative override applier.
            try:
                links = _capture_legend_links(rendered, {"objects": [{"gid": gid, "role": role}]},
                                               [{"gid": gid, "prop": "color", "value": "#000000"}])
                record["legend_links"] = []
                if gid in links:
                    link = links[gid]
                    # Shared legends retain the same identity across linked series.
                    text = link["text"]
                    text_gid = getattr(text, "_qa_projection_gid", None) or f"{gid}.legend_text"
                    text._qa_projection_gid = text_gid
                    text.set_gid(text_gid)
                    mark_ids = []
                    for index, mark in enumerate(link["marks"]):
                        mark_gid = getattr(mark, "_qa_projection_gid", None) or f"{gid}.legend_mark_{index}"
                        mark._qa_projection_gid = mark_gid
                        mark.set_gid(mark_gid)
                        mark_ids.append(mark_gid)
                    record["legend_links"] = [{"text_gid": text_gid, "mark_gids": mark_ids}]
            except Exception:
                pass  # Missing link metadata explicitly means server-only.
        if role == "legend":
            frame = artist.get_frame()
            frame_gid = f"{gid}.frame"
            frame.set_gid(frame_gid)
            record["svg_frame"] = frame_gid
            boxstyle = frame.get_boxstyle()
            bounds = bbox(frame, rendered.figure, draw=False)
            if type(boxstyle).__name__ in {"Square", "Round"} and bounds:
                radius = (getattr(boxstyle, "rounding_size", None) or boxstyle.pad) if type(boxstyle).__name__ == "Round" else 0
                record["svg_frame_shape"] = {"bbox": bounds,
                    "radius": radius * frame.get_mutation_scale() * 72 / rendered.figure.dpi}
            try:
                rows = []
                for index, row in enumerate(_legend_rows(artist)):
                    row_ids = []
                    for item in row.get_children():
                        children = item.get_children()
                        for child_index, child in enumerate(children):
                            if not hasattr(child, "set_gid"):
                                continue
                            node_gid = getattr(child, "_qa_projection_gid", None) or f"{gid}.row_{index}_{len(row_ids)}"
                            child._qa_projection_gid = node_gid
                            child.set_gid(node_gid)
                            row_ids.append(node_gid)
                    bounds = bbox(row, rendered.figure, draw=False)
                    rows.append({"gids": row_ids, "bbox": bounds})
                axes_bounds = bbox(artist.axes, rendered.figure, draw=False) if artist.axes else None
                if rows and all(row["bbox"] for row in rows):
                    record["svg_legend"] = {
                        "rows": rows, "axes_bbox": axes_bounds,
                        "has_title": bool(artist.get_title().get_text()), "mode": getattr(artist, "_mode", None),
                        "custom_anchor": getattr(artist, "_bbox_to_anchor", None) is not None,
                        "origin": [min(row["bbox"]["x0"] for row in rows), min(row["bbox"]["y0"] for row in rows)],
                        "row_gap": artist.labelspacing * artist._fontsize,
                        "column_gap": artist.columnspacing * artist._fontsize,
                        "padding": artist.borderpad * artist._fontsize,
                        "borderaxespad": artist.borderaxespad * artist._fontsize,
                    }
            except (AttributeError, TypeError, ValueError):
                pass
        metadata[gid] = record
    # Row discovery and series linking may visit the same legend in either
    # order; use the final stable identity, not the earlier proposed string.
    for gid, entry in rendered.locators.items():
        if entry.get("role") == "series" and "legend_links" in metadata.get(gid, {}):
            try:
                links = _capture_legend_links(rendered, {"objects": [{"gid": gid, "role": "series"}]},
                                               [{"gid": gid, "prop": "color", "value": "#000000"}])
                if gid in links:
                    link = links[gid]
                    metadata[gid]["legend_links"] = [{"text_gid": link["text"].get_gid(),
                                                      "mark_gids": [mark.get_gid() for mark in link["marks"]]}]
            except Exception:
                metadata[gid].pop("legend_links", None)
    return metadata


def annotate_svg(source: str, rendered) -> str:
    """Identify actual Line2D marker instances before flattening removes use.

    Definitions are never moved. Local markers replace only the instance path
    inside its existing translated wrapper; sample/data coordinates stay fixed.
    """
    import xml.etree.ElementTree as ET
    from matplotlib.lines import Line2D
    from matplotlib.collections import PathCollection
    from citrus_figures.exporter import SVG_NS, XLINK_NS, flatten_svg
    root = ET.fromstring(source)
    by_id = {node.get("id"): node for node in root.iter() if node.get("id")}
    artists = []
    for entry in rendered.locators.values():
        artists.extend(list(entry.get("members") or [entry.get("artist")]))
        artists.extend(entry.get("error_members") or [])
    for legend in [ax.get_legend() for ax in rendered.axes] + list(rendered.figure.legends):
        if legend:
            artists.extend(legend.findobj(match=lambda item: isinstance(item, (Line2D, PathCollection))))
    marker_ids, line_ids = {}, {}
    for artist in {id(item): item for item in artists if item is not None}.values():
        if not isinstance(artist, (Line2D, PathCollection)) or not artist.get_gid():
            continue
        gid = artist.get_gid()
        group = by_id.get(gid)
        if group is None:
            continue
        markers, lines = [], []
        for node in list(group.iter()):
            if node.tag == f"{{{SVG_NS}}}use":
                href = node.get(f"{{{XLINK_NS}}}href") or node.get("href") or ""
                definition = by_id.get(href[1:]) if href.startswith("#") else None
                if definition is None or definition.tag != f"{{{SVG_NS}}}path":
                    continue
                node_id = f"{gid}.marker_{len(markers)}"
                node.set("id", node_id)
                markers.append(node_id)
        # Line2D's direct path is the drawn data path; marker definitions live
        # in defs and use instances have their own translated wrappers.
        for node in list(group):
            if node.tag == f"{{{SVG_NS}}}path" and isinstance(artist, Line2D):
                node_id = f"{gid}.line_{len(lines)}"
                node.set("id", node_id)
                lines.append(node_id)
        if isinstance(artist, PathCollection) and len(markers) != len(artist.get_offsets()):
            markers = []  # mixed/direct paths have no reliable per-instance origin
        marker_ids[gid], line_ids[gid] = markers, lines
    flattened = ET.fromstring(flatten_svg(ET.tostring(root, encoding="unicode")))
    actual = {node.get("id"): node for node in flattened.iter() if node.get("id")}
    for ids in marker_ids.values():
        for gid in ids:
            wrapper = actual.get(gid)
            if wrapper is not None and len(wrapper) == 1 and wrapper[0].tag == f"{{{SVG_NS}}}path":
                wrapper.attrib.pop("id", None)
                wrapper[0].set("id", gid)
                # use paint is inherited; making it explicit permits replacement
                # without losing its original edge/fill colour.
                for key in ("fill", "stroke", "stroke-width", "opacity", "fill-opacity", "stroke-opacity"):
                    if key in wrapper.attrib and key not in wrapper[0].attrib:
                        wrapper[0].set(key, wrapper.attrib[key])
    rendered._svg_style_nodes = {gid: {"svg_markers": marker_ids.get(gid, []), "svg_lines": line_ids.get(gid, [])}
                                 for gid in set(marker_ids) | set(line_ids)}
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(flattened, encoding="unicode")
