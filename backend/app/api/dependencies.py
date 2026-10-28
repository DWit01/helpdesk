from typing import Annotated

from fastapi import Depends, Path
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.category_service import CategoryService
from app.services.comment_service import CommentService
from app.services.ticket_service import TicketService
from app.services.user_service import UserService

DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
ResourceId = Annotated[int, Path(gt=0)]


def get_ticket_service(db: DbSession, actor: CurrentUser) -> TicketService:
    return TicketService(db, actor.id)


def get_comment_service(db: DbSession, actor: CurrentUser) -> CommentService:
    return CommentService(db, actor.id)


def get_category_service(db: DbSession, actor: CurrentUser) -> CategoryService:
    return CategoryService(db, actor.id)


def get_user_service(db: DbSession, actor: CurrentUser) -> UserService:
    return UserService(db, actor.id)


Tickets = Annotated[TicketService, Depends(get_ticket_service)]
Comments = Annotated[CommentService, Depends(get_comment_service)]
Categories = Annotated[CategoryService, Depends(get_category_service)]
Users = Annotated[UserService, Depends(get_user_service)]
