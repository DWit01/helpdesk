from fastapi import APIRouter, Response, status

from app.api.dependencies import Categories, ResourceId
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate

router = APIRouter(
    prefix="/categories",
    tags=["Categories"],
)


@router.get("", summary="List categories")
def list_categories(
    service: Categories,
    include_inactive: bool = False,
) -> list[CategoryRead]:
    """List active categories; admins may also request inactive categories."""
    return service.list(include_inactive=include_inactive)


@router.get("/{category_id}", summary="Get a category")
def get_category(category_id: ResourceId, service: Categories) -> CategoryRead:
    """Return one existing category."""
    return service.get(category_id)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a category")
def create_category(data: CategoryCreate, service: Categories) -> CategoryRead:
    """Allow an admin to create an active category with a unique name."""
    return service.create(data)


@router.put("/{category_id}", summary="Replace category details")
def update_category(
    category_id: ResourceId,
    data: CategoryUpdate,
    service: Categories,
) -> CategoryRead:
    """Replace editable fields; an omitted description becomes null."""
    return service.update(category_id, data)


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unused category",
)
def delete_category(category_id: ResourceId, service: Categories) -> Response:
    """Allow an admin to delete an unused category; used categories return 409."""
    service.delete(category_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
