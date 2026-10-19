import os

from sqlalchemy import create_engine

from alembic import context

# Sin autogenerate por ahora: el esquema vive en sql/*.sql (Fase 1). Desde la Fase 2 se
# añaden los modelos ORM y las migraciones nuevas se generan contra Base.metadata.
# Alembic corre síncrono (psycopg): permite scripts multi-sentencia; la API sigue siendo async.
engine = create_engine(os.environ["MIGRATIONS_DATABASE_URL"])
with engine.connect() as conn:
    context.configure(connection=conn, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
