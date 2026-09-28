from fastapi import APIRouter

from app.api.v1 import me, securities, transactions

api_router = APIRouter()
api_router.include_router(me.router)
api_router.include_router(securities.router)
api_router.include_router(transactions.router)

# Phase 2/3 routers (portfolio, imports, reports, tax) get included here
# as they're built, per CLAUDE.md's build order.
