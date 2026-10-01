from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.api.routes import ai, auth, comparison, evaluation, experiments, health, intelligence, projects, prompts, testing

api_router = APIRouter()

# Public: /api/health* and /api/auth/* (auth declares its own dependencies —
# me uses get_current_user, signup/login use the rate-limit dependencies).
# Everything else requires a valid session; the dependency runs before body
# and path validation, so unauthenticated requests are 401, never 422/404.
requires_auth = [Depends(get_current_user)]

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(prompts.router, prefix="/prompts", tags=["prompts"], dependencies=requires_auth)
api_router.include_router(projects.router, prefix="/projects", tags=["projects"], dependencies=requires_auth)
api_router.include_router(ai.router, prefix="/ai", tags=["ai"], dependencies=requires_auth)
api_router.include_router(intelligence.router, prefix="/intelligence", tags=["intelligence"], dependencies=requires_auth)
api_router.include_router(testing.router, prefix="/testing", tags=["testing"], dependencies=requires_auth)
api_router.include_router(evaluation.router, prefix="/evaluations", tags=["evaluation"], dependencies=requires_auth)
api_router.include_router(comparison.router, prefix="/comparisons", tags=["comparison"], dependencies=requires_auth)
api_router.include_router(experiments.router, prefix="/experiments", tags=["experiment"], dependencies=requires_auth)
