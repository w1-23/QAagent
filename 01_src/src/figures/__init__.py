"""Research figure engine — pure computation kernel.

This package holds the figure pipeline that turns a validated figure request
into a real scientific figure: SVG (editable text), PNG and PDF, produced in a
single render pass so the on-screen preview and the downloaded file can never
disagree.

Design constraints
------------------
* **No service dependencies.** Nothing in this package may import ``src.config``,
  ``src.session``, ``src.api`` or ``langchain``. The sandbox image copies this
  directory verbatim and runs it with nothing else on the path. Service-side
  pieces that *do* need the application live in ``sandbox.py`` and ``store.py``
  and import the application lazily inside function bodies.
* **Only ``matplotlib``/``numpy`` are required.** ``pandas``/``scipy`` belong
  to the service layer that resolves data before calling in here.
* **One render pass, many formats.** ``exporter.export_all`` saves every
  requested format from the same ``Figure`` object. SVG is never rasterised
  (no converter is available in the deployment image), so PNG/PDF/TIFF are
  written by Matplotlib directly.
* **The figure is the product.** The user sees a rendered figure; the script
  that produced it is an implementation detail that happens to be archived
  alongside the figure so the result stays reproducible.

Public surface
--------------
``spec``        validated figure request (declarative form) and its renderer
``core``        script compilation, execution and figure capture
``manifest``    semantic object inventory (``objects.json``)
``overrides``   the editable-property allowlist and its applier
``exporter``    format export plus SVG flattening and sanitising
"""
from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
