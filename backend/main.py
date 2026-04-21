from fastapi import FastAPI

from backend.api import (
    admin,
    audit,
    borrowers,
    config,
    genie,
    health,
    leads,
    offers,
    outreach,
    portfolio,
    segments,
)

app = FastAPI(title="Mortgage Intelligence Platform API")

for router in [
    health.router,
    config.router,
    admin.router,
    portfolio.router,
    segments.router,
    leads.router,
    borrowers.router,
    offers.router,
    outreach.router,
    genie.router,
    audit.router,
]:
    app.include_router(router)
