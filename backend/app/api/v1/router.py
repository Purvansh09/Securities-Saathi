from fastapi import APIRouter

from app.api.v1 import me

api_router = APIRouter()
api_router.include_router(me.router)

# Phase 2/3 routers (securities, transactions, portfolio, imports, reports, tax)
# get included here as they're built, per CLAUDE.md's build order.
