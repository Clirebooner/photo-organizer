"""Pipeline — the thin glue between discovery and downstream stages."""

from photo_organizer.pipeline.loader import PhotoLoader

__all__ = ["PhotoLoader"]
