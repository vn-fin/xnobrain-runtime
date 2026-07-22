"""Persistence ports implemented by atomic local files."""

from .files import FileRepository, StoreError

__all__ = ["FileRepository", "StoreError"]
