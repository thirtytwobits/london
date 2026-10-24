"""Section kinds. Importing a kind's module registers it; add new kinds to the import below."""

from . import block, file, structured
from .base import Applied, Section, SectionError, Source, kind_of, kinds, register

__all__ = [
    "Applied",
    "Section",
    "SectionError",
    "Source",
    "block",
    "file",
    "kind_of",
    "kinds",
    "register",
    "structured",
]
