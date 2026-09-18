"""路由包。"""

from fastapi import APIRouter

from backend.routers import agents, auth, analysis, dashboards, datasources, explore, insights, misc, sessions, settings, skills, usage, utils

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(misc.router)
api_router.include_router(sessions.router)
api_router.include_router(analysis.router)
api_router.include_router(datasources.router)
api_router.include_router(skills.router)
api_router.include_router(agents.router)
api_router.include_router(insights.router)
api_router.include_router(dashboards.router)
api_router.include_router(explore.router)
api_router.include_router(settings.router)
api_router.include_router(usage.router)
api_router.include_router(utils.router)
