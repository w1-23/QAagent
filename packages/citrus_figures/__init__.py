"""Independent scientific figure rendering and byte-bound QA.

No model, network, backend or platform initialization occurs on import.
"""
__version__ = "2.0.0"
from .verification import verify_current_figure

__all__ = ["verify_current_figure", "render_figure", "revise_figure", "write_bundle", "load_spec_bundle", "FigureSession", "register_semantic_identity"]


def __getattr__(name):
    if name == "register_semantic_identity":
        from .manifest import register_semantic_identity
        return register_semantic_identity
    if name in {"render_figure", "revise_figure", "write_bundle", "load_spec_bundle", "FigureSession"}:
        from . import bundle
        return getattr(bundle, name)
    raise AttributeError(name)
