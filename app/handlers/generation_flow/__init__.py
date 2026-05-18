"""
Подпакет для handlers генерации изображений
"""
from .preflight import router as preflight_router
from .video import router as video_router

__all__ = [
    "preflight_router",
    "video_router",
]
