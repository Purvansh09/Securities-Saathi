from fastapi import APIRouter

from app.api.v1 import analysis, imports, internal, me, portfolio, reports, securities, tax, transactions

api_router = APIRouter()
api_router.include_router(me.router)
api_router.include_router(securities.router)
api_router.include_router(transactions.router)
api_router.include_router(portfolio.router)
api_router.include_router(analysis.router)
api_router.include_router(imports.router)
api_router.include_router(tax.router)
api_router.include_router(reports.router)
api_router.include_router(internal.router)

# Phase 2/3 routers (imports, reports, tax) get included here
# as they're built, per CLAUDE.md's build order.
