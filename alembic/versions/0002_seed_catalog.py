"""Catálogo de la plataforma (módulos, planes, tipos de negocio y plantillas de servicios).

Es DATOS de referencia, no del cliente: lo necesita cualquier instalación (sql/seed_catalog.sql). NO crea ningún super admin
(para eso: python -m app.scripts.create_superadmin). Los cambios futuros de precios/planes se hacen desde la API de plataforma,
no con migraciones.
"""
from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"

SQL = Path(__file__).resolve().parents[2] / "sql" / "seed_catalog.sql"


def upgrade() -> None:
    op.get_bind().exec_driver_sql(SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    pass        # bajar a 0001 elimina el esquema completo, catálogo incluido
