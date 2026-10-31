"""Request-scoped image state for the active multimodal main model.

The former dedicated citrus disease classifier has been retired. Images are
handled by the configured main model, while ``context`` keeps attachment data
available to the request without adding another inference engine.
"""

__all__: list[str] = []
