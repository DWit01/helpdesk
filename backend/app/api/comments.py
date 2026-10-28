from fastapi import APIRouter, Response, status

from app.api.dependencies import Comments, ResourceId
from app.schemas.comment import CommentCreate, CommentRead, CommentUpdate

router = APIRouter(tags=["Comments"])


@router.get(
    "/tickets/{ticket_id}/comments",
    summary="List visible ticket comments",
)
def list_comments(ticket_id: ResourceId, service: Comments) -> list[CommentRead]:
    """Check ticket visibility and hide internal notes from requesters."""
    return service.list(ticket_id)


@router.post(
    "/tickets/{ticket_id}/comments",
    status_code=status.HTTP_201_CREATED,
    summary="Add a ticket comment",
)
def create_comment(
    ticket_id: ResourceId,
    data: CommentCreate,
    service: Comments,
) -> CommentRead:
    """Add a public comment, or a staff-only internal note."""
    return service.create(ticket_id, data)


@router.put("/comments/{comment_id}", summary="Replace comment text")
def update_comment(
    comment_id: ResourceId,
    data: CommentUpdate,
    service: Comments,
) -> CommentRead:
    """Allow the author or an admin to replace text without changing visibility."""
    return service.update(comment_id, data)


@router.delete(
    "/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a comment",
)
def delete_comment(comment_id: ResourceId, service: Comments) -> Response:
    """Allow the author or an admin to delete a comment on an editable ticket."""
    service.delete(comment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
