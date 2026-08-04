"""Metadata loading — turn discovered files into normalized PhotoRecords.

The bridge between :mod:`photo_organizer.discover` (file-level scan) and
the pipeline's :class:`PhotoRecord`: every discovered file is handed to a
:class:`MetadataReader`, and the resulting records feed downstream stages.
A single file failing to parse must not abort the batch.
"""

from __future__ import annotations

import logging

from photo_organizer.discover.models import DiscoveredFile
from photo_organizer.domain.models import PhotoRecord
from photo_organizer.metadata.reader import ExifReader, MetadataError, MetadataReader

logger = logging.getLogger(__name__)


class PhotoLoader:
    """Converts discovered files into PhotoRecords via a MetadataReader."""

    def __init__(self, reader: MetadataReader | None = None) -> None:
        """Create a loader bound to *reader* (default: the exifread backend).

        Injecting a reader keeps the loader testable and lets the
        metadata engine be swapped without touching this module.
        """
        self._reader = reader or ExifReader()

    def load(self, files: list[DiscoveredFile]) -> list[PhotoRecord]:
        """Read metadata for every *file*, returning the successful records.

        A file whose metadata cannot be read is logged as a warning and
        skipped; the order of the returned records matches the input
        order (failures are simply omitted).
        """
        records: list[PhotoRecord] = []
        for file in files:
            try:
                records.append(self._reader.read(file.path))
            except MetadataError as exc:
                logger.warning("skipping %s: %s", file.path, exc)
                continue
        return records
