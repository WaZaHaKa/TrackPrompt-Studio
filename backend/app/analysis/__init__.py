"""Leaf analysis modules; orchestration lives in :mod:`app.analysis.pipeline`.

This initializer intentionally has no re-exports. Importing ``app.analysis.core``
must not initialize the orchestration pipeline, optional adapters, or FastAPI
application. Callers that need orchestration symbols import them directly from
``app.analysis.pipeline``.
"""
