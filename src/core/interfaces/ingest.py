from __future__ import annotations

from typing import Protocol

from core.models.ingest import IngestRequest, IngestResult


class ManualIngester(Protocol):
    """Find, download, and process an IKEA assembly manual into catalog ground truth."""

    def ingest(self, request: IngestRequest) -> IngestResult: ...
