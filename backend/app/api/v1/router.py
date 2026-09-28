from fastapi import APIRouter

from app.api.v1 import me, securities

api_router = APIRouter()
api_router.include_router(me.router)
api_router.include_router(securities.router)

# Phase 2/3 routers (transactions, portfolio, imports, reports, tax) get
# included here as they're built, per CLAUDE.md's build order.
