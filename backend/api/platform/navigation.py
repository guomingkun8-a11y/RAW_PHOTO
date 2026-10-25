"""Shared navigation preferences managed by the initial administrator."""

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from api.support import require_identity, require_super_admin
from services.platform.navigation_settings import navigation_settings_store


class NavigationItemUpdate(BaseModel):
    id: str
    visible: bool


class NavigationUpdate(BaseModel):
    items: list[NavigationItemUpdate]


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api/navigation", tags=["navigation"])

    @router.get("")
    async def get_navigation(authorization: str | None = Header(default=None)):
        require_identity(authorization)
        return await run_in_threadpool(navigation_settings_store.get)

    @router.put("")
    async def update_navigation(body: NavigationUpdate, authorization: str | None = Header(default=None)):
        identity = require_super_admin(authorization)
        try:
            return await run_in_threadpool(
                navigation_settings_store.update,
                [item.model_dump() for item in body.items],
                updated_by=str(identity.get("id") or "admin"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    return router

