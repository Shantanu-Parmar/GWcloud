# core/__init__.py
# This file makes Python treat the 'core' directory as a package
# We expose the main functions so app.py can do: from core.gravfetch import ...

from .gravfetch import download_osdf, download_nds
from .omicron import run_omicron, generate_fin_ffl

__all__ = [
    "download_osdf",
    "download_nds",
    "run_omicron",
    "generate_fin_ffl",
]
