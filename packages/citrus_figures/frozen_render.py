"""Data-only render receipts; geometry proof deliberately has narrow coverage.

This module never executes source. Unsupported artists still export, but cannot
claim numerical replay equivalence. Digests are evidence, not a sandbox boundary.
"""
from __future__ import annotations

import hashlib
import json


def typed_payload_sha256(payload: dict) -> str:
    """Hash an accepted dataset_codec payload with canonical JSON formatting."""
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def geometry_signature(figure) -> dict:
    """Fingerprint supported 2D numerical geometry, excluding visual styling.

    Unknown projections, artist subclasses and coordinate transforms fail closed.
    This is not a claim that every possible Matplotlib artist is covered.
    """
    import numpy as np
    from matplotlib.axes import Axes
    from matplotlib.axis import XAxis, YAxis
    from matplotlib.collections import LineCollection, PathCollection, PolyCollection, QuadMesh
    from matplotlib.colors import Normalize, LogNorm, BoundaryNorm
    from matplotlib.colorbar import Colorbar, _ColorbarAxesLocator, _ColorbarSpine
    from matplotlib.image import AxesImage
    from matplotlib.legend import Legend
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle, Polygon, PathPatch, Wedge, Ellipse, Circle
    from matplotlib.spines import Spine
    from matplotlib.text import Text
    from matplotlib.transforms import IdentityTransform, TransformedBbox, CompositeGenericTransform

    unsupported = set()

    def reject(reason):
        unsupported.add(reason)

    def array(value):
        if value is None:
            return None
        values = np.ma.asarray(value)
        if values.dtype.kind not in "biuf":
            raise ValueError("unsupported nonnumeric geometry array")
        if values.dtype.kind == "f" and values.dtype.itemsize > 8:
            raise ValueError("unsupported extended precision geometry")
        # Hex floats and decimal integers avoid lossy JSON/native-byte coercion.
        raw = np.ma.getdata(values).ravel()
        encoded = ([float(v).hex() for v in raw] if values.dtype.kind == "f"
                   else [str(int(v)) for v in raw])
        return {"shape": list(values.shape), "kind": values.dtype.kind,
                "values": encoded, "mask": np.ma.getmaskarray(values).ravel().tolist()}

    def path(value):
        return {"vertices": array(value.vertices), "codes": array(value.codes)}

    def transform(value, ax):
        if value is ax.transData:
            return "data"
        if type(value) is IdentityTransform:
            return "identity"
        reject("custom coordinate transform")
        return "unsupported"

    def normalization(artist):
        norm = artist.norm
        if type(norm) not in (Normalize, LogNorm, BoundaryNorm):
            reject("unsupported scalar normalization")
        value = {"type": type(norm).__name__, "vmin": norm.vmin, "vmax": norm.vmax,
                 "clip": bool(norm.clip)}
        if type(norm) is BoundaryNorm:
            value.update(boundaries=array(norm.boundaries), ncolors=norm.Ncmap, extend=norm.extend)
        return value

    def common(artist):
        if artist.get_path_effects() or artist.get_agg_filter() is not None:
            reject("artist effects or filters")
        if artist.get_clip_path() is not None:
            reject("custom clip path")
        box = artist.get_clip_box()
        axes = artist.axes
        # Default clipping is the unit axes patch transformed to display space.
        # Compare that structure, not rounded display pixels: aspect adjustments
        # can introduce tiny differences between equivalent bbox calculations.
        canonical_box = axes is not None and (box is axes.bbox or (
            type(box) is TransformedBbox and type(box._transform) is CompositeGenericTransform
            and np.array_equal(box._bbox.bounds, (0., 0., 1., 1.))
            and type(axes.patch) is Rectangle
            and axes.patch.get_data_transform() is axes.transAxes
            and np.array_equal(axes.patch.get_bbox().bounds, (0., 0., 1., 1.))
            and box._transform == axes.patch.get_transform()))
        if box is not None and not canonical_box:
            reject("custom clipping bounds")
        if artist.get_animated() or "draw" in vars(artist):
            reject("dynamic artist")
        return {"visible": bool(artist.get_visible()), "clip_on": bool(artist.get_clip_on())}

    payload = []
    try:
        if (figure.artists or figure.lines or figure.patches or figure.images
                or figure.texts or getattr(figure, "subfigs", [])):
            reject("figure-level artists or subfigures")
        if figure.canvas.callbacks.callbacks.get("draw_event") or "draw" in vars(figure):
            reject("dynamic figure drawing")
        for ax in figure.axes:
            if type(ax) is not Axes:
                reject("nonstandard axes or projection")
            if ax.get_xscale() != "linear" or ax.get_yscale() != "linear":
                reject("nonlinear axis scale")
            if ax.child_axes:
                reject("child axes")
            locator = ax.get_axes_locator()
            colorbar = getattr(ax, "_colorbar", None)
            # The standard no-extension locator only returns the axes' original
            # position. A wrapped locator, extended ramp or custom Colorbar can
            # execute additional geometry logic and remains unsupported.
            standard_colorbar = (
                type(colorbar) is Colorbar and colorbar.ax is ax
                and type(locator) is _ColorbarAxesLocator
                and locator._cbar is colorbar and locator._orig_locator is None
                and colorbar.extend == "neither" and type(colorbar.norm) is Normalize
                and colorbar.orientation in ("vertical", "horizontal")
                and type(colorbar.outline) is _ColorbarSpine
                and colorbar.outline.get_transform() is ax.transAxes
                and colorbar.outline in ax.get_children()
            )
            if colorbar is not None and not standard_colorbar:
                reject("unsupported colorbar geometry")
            if (locator is not None and not standard_colorbar) or "draw" in vars(ax):
                reject("dynamic axes geometry")
            item = {"bounds": array(ax.viewLim.bounds), "data_bounds": array(ax.dataLim.bounds),
                    "x_ticks": array(ax.get_xticks()), "y_ticks": array(ax.get_yticks()),
                    "x_minor_ticks": array(ax.get_xticks(minor=True)),
                    "y_minor_ticks": array(ax.get_yticks(minor=True)),
                    "lines": [], "collections": [], "patches": [], "images": []}
            if standard_colorbar:
                item["colorbar"] = {
                    "orientation": colorbar.orientation, "spacing": colorbar.spacing,
                    "norm": normalization(colorbar),
                    "boundaries": array(colorbar._boundaries), "values": array(colorbar._values),
                    "declared_boundaries": array(colorbar.boundaries),
                    "declared_values": array(colorbar.values),
                    "outline": {**common(colorbar.outline), "transform": "axes",
                                "path": path(colorbar.outline.get_path())},
                }
            for artist in ax.get_children():
                if artist is ax.patch or artist in (ax.xaxis, ax.yaxis):
                    continue
                if type(artist) in (Spine, XAxis, YAxis, Legend):
                    continue
                if standard_colorbar and artist is colorbar.outline:
                    continue
                if type(artist) is Text:
                    if artist not in ax.texts:
                        continue  # axis titles; text styling does not change data
                    # Plain labels (e.g. n=7) are common in scientific figures.
                    # Fingerprint their baseline before overrides; do not treat
                    # Annotation subclasses or arbitrary transforms as text-only.
                    coords = artist.get_transform()
                    if coords is ax.transAxes or coords is ax.transData:
                        item.setdefault("texts", []).append({
                            **common(artist), "text": artist.get_text(),
                            "position": array(artist.get_position()),
                            "transform": "axes" if coords is ax.transAxes else "data"})
                        continue
                if type(artist) not in (Line2D, PathCollection, LineCollection, PolyCollection,
                                        QuadMesh, Rectangle, Polygon, PathPatch, Wedge, Ellipse,
                                        Circle, AxesImage):
                    reject("unsupported artist: " + type(artist).__name__)
            for line in ax.lines:
                item["lines"].append({**common(line), "xy": array(line.get_xydata()),
                                      "path": path(line.get_path()),
                                      "transform": transform(line.get_transform(), ax)})
            for collection in ax.collections:
                entry = {**common(collection), "type": type(collection).__name__,
                         "offsets": array(collection.get_offsets()),
                         "array": array(collection.get_array()), "norm": normalization(collection),
                         "paths": [path(p) for p in collection.get_paths()],
                         "transform": transform(collection.get_transform(), ax),
                         "offset_transform": transform(collection.get_offset_transform(), ax)}
                transforms = collection.get_transforms()
                if len(transforms):
                    if type(collection) is not PathCollection:
                        reject("collection per-element transforms")
                    else:
                        # Only isotropic origin-centered marker scaling is style.
                        expected = np.tile(np.eye(3), (len(transforms), 1, 1))
                        expected[:, 0, 0] = transforms[:, 0, 0]
                        expected[:, 1, 1] = transforms[:, 0, 0]
                        if not np.array_equal(transforms, expected):
                            reject("custom marker transforms")
                # PathCollection's per-marker size is a style property.
                if type(collection) is QuadMesh:
                    entry["coordinates"] = array(collection.get_coordinates())
                item["collections"].append(entry)
            for patch in ax.patches:
                item["patches"].append({**common(patch), "type": type(patch).__name__,
                    "path": path(patch.get_patch_transform().transform_path(patch.get_path())),
                    "transform": transform(patch.get_data_transform(), ax)})
            for image in ax.images:
                item["images"].append({**common(image), "array": array(image.get_array()),
                    "norm": normalization(image),
                    "extent": array(image.get_extent()), "origin": image.origin,
                    "transform": transform(image.get_transform(), ax)})
            payload.append(item)
        digest = typed_payload_sha256({"axes": payload}) if not unsupported else None
    except Exception as exc:
        reject("geometry inspection failed: " + type(exc).__name__)
        digest = None
    return {"geometry_sha256": digest,
            "geometry_status": "unsupported" if unsupported else "supported",
            "geometry_unsupported": sorted(unsupported)[:32]}


def render_receipt(source: str, payloads: dict, figure) -> dict:
    return {"version": 1,
            "renderer_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            "data_sha256": {name: typed_payload_sha256(value) for name, value in payloads.items()},
            **geometry_signature(figure)}
