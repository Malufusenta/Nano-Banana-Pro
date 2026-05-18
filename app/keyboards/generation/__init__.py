"""
Клавиатуры для generation handlers
"""
from .preflight import get_preflight_kb, get_ratio_kb
from .result import get_result_kb, get_cancel_kb

__all__ = [
    "get_preflight_kb",
    "get_ratio_kb",
    "get_result_kb",
    "get_cancel_kb",
]
