"""Esquema inicial = sql/schema.sql + sql/rls.sql (única fuente de verdad en Fase 1).

La semilla (sql/seed.sql) NO va en la migración: es de datos, no de esquema.
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None

SQL = Path(__file__).resolve().parents[2] / "sql"


def upgrade() -> None:
    for name in ("schema.sql", "rls.sql"):
        # `%` -> `%%`: el driver (psycopg) lo tomaría por un parámetro (p. ej. format('%I') dentro de DO $$)
        op.get_bind().exec_driver_sql((SQL / name).read_text(encoding="utf-8").replace("%", "%%"))


def downgrade() -> None:
    # Alembic borra su fila de alembic_version DESPUÉS de este paso: la tabla debe sobrevivir al DROP SCHEMA
    op.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; "
               "CREATE TABLE alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY); INSERT INTO alembic_version VALUES ('0001');")
