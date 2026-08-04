"""Pipeline — the thin glue between discovery and downstream stages."""

from photo_organizer.pipeline.loader import PhotoLoader
from photo_organizer.pipeline.preview import (
    PipelineComponents,
    PreviewReport,
    default_components,
    render_report,
    run_preview,
)

__all__ = [
    "PhotoLoader",
    "PipelineComponents",
    "PreviewReport",
    "default_components",
    "render_report",
    "run_preview",
]
