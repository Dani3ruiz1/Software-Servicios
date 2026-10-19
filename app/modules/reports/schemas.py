from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

ReportName = Literal["ventas", "por-profesional", "por-servicio", "por-metodo", "ocupacion", "clientes", "citas",
                    "ranking-clientes", "clientes-reservas", "reservas-profesional", "reservas-servicio"]
Agrupar = Literal["dia", "mes", "anio"]
Formato = Literal["json", "xlsx", "pdf"]


class Col(BaseModel):
    key: str
    label: str
    tipo: Literal["texto", "numero", "dinero", "porcentaje"]


class ReportOut(BaseModel):
    """Todos los reportes comparten forma: columnas + filas + totales (sirve para tabla, gráfica y exportación)."""
    reporte: str
    titulo: str
    desde: date
    hasta: date
    zona_horaria: str
    columnas: list[Col]
    filas: list[dict[str, Any]]
    totales: dict[str, Any]
