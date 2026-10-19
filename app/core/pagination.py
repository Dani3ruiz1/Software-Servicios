"""Paginación y orden estándar: ?page=1&page_size=20&sort=-created_at → {items,page,page_size,total}."""
from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel

from app.core.errors import AppError


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int


@dataclass(frozen=True)
class PageParams:
    page: int
    page_size: int
    sort: str | None

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                sort: str | None = Query(None, description="campo o -campo (desc)")) -> PageParams:
    return PageParams(page, page_size, sort)


def parse_sort(sort: str | None, allowed: dict[str, object], default: str) -> tuple[object, bool]:
    """Devuelve (columna, descendente). Lista blanca: nunca se interpola texto del cliente."""
    key = (sort or default)
    desc = key.startswith("-")
    name = key.lstrip("-")
    if name not in allowed:
        raise AppError("VALIDATION_ERROR", "Orden no permitido", {"permitidos": sorted(allowed)})
    return allowed[name], desc
