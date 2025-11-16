from aiogram import Router

from .creation import router as creation_router
from .details import router as details_router
from .edit import router as edit_router
from .listing import router as listing_router

router = Router()
router.include_router(listing_router)
router.include_router(details_router)
router.include_router(creation_router)
router.include_router(edit_router)

__all__ = ["router"]
