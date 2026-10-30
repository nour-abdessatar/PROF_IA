"""
registry — point d'entrée public du système de représentations visuelles.
Tout le reste du code (llm.py, prompt_builder.py) importe depuis ici,
jamais directement depuis representations.py/dispatch.py.
"""
from .dispatch import build_widget, UnknownRepresentation, InvalidParams
from .representations import build_menu, REPRESENTATIONS, get_variant_carrying_representations

__all__ = [
    "build_widget", "UnknownRepresentation", "InvalidParams",
    "build_menu", "REPRESENTATIONS", "get_variant_carrying_representations",
]