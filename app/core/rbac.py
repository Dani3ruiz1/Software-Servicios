from collections.abc import Callable

from fastapi import Depends

from app.core.errors import AppError
from app.core.tenancy import Principal, get_principal

PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({
        "users:manage", "staff:manage", "customers:write", "catalog:manage",
        "appointments:read_all", "appointments:write", "sales:read", "sales:write", "sales:void",
        "cash:manage", "reports:read", "booking:manage", "integrations:manage", "conversations:manage", "invoices:manage", "inventory:manage",
    }),
    "recepcionista": frozenset({
        "customers:write", "appointments:read_all", "appointments:write", "sales:read", "sales:write", "cash:manage",
        "conversations:manage", "invoices:manage", "inventory:manage",
    }),
    "profesional": frozenset({"appointments:read_own", "appointments:status_own"}),
    "super_admin": frozenset({"platform:*"}),
}


def permissions_for(rol: str) -> list[str]:
    return sorted(PERMISSIONS.get(rol, frozenset()))


def require_permission(perm: str) -> Callable[..., Principal]:
    def dep(p: Principal = Depends(get_principal)) -> Principal:
        granted = PERMISSIONS.get(p.rol or "", frozenset())
        if perm not in granted and "platform:*" not in granted:
            raise AppError("FORBIDDEN", "No tienes permiso para esta acción", {"permiso": perm})
        return p
    return dep


def require_platform(p: Principal = Depends(get_principal)) -> Principal:
    if p.kind != "platform_user":
        raise AppError("FORBIDDEN", "Solo el Super Admin puede hacer esto")
    return p


def require_any_permission(*perms: str) -> Callable[..., Principal]:
    def dep(p: Principal = Depends(get_principal)) -> Principal:
        granted = PERMISSIONS.get(p.rol or "", frozenset())
        if not (granted & set(perms)) and "platform:*" not in granted:
            raise AppError("FORBIDDEN", "No tienes permiso para esta acción", {"permisos": list(perms)})
        return p
    return dep
