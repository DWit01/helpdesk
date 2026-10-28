from fastapi import APIRouter

from app.api import categories, comments, tickets, users

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(users.router)
api_router.include_router(categories.router)
api_router.include_router(tickets.router)
api_router.include_router(comments.router)
