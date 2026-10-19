#!/bin/sh
# Prepara una base NUEVA: esquema + RLS + catálogo (migraciones de Alembic). Usa MIGRATIONS_DATABASE_URL (usuario administrador).
# Es seguro repetirlo: Alembic solo aplica lo que falta. NO crea ningún super admin (usa app.scripts.create_superadmin).
set -eu
: "${MIGRATIONS_DATABASE_URL:?Define MIGRATIONS_DATABASE_URL (usuario administrador de la BD)}"
alembic upgrade head
cat <<MSG

Base lista. Pasos que faltan (una sola vez):
  1) Contraseña del rol de la API (distinta a la de desarrollo; la misma que va en DATABASE_URL):
       psql "\$MIGRATIONS_DATABASE_URL" -c "ALTER ROLE agenda_app PASSWORD '<larga-y-aleatoria>'"
  2) Versión "solo reservas" (sin caja, inventario ni factura): psql "\$MIGRATIONS_DATABASE_URL" -f sql/perfil_solo_reservas.sql   (ANTES de crear negocios)
  3) Super admin:  python -m app.scripts.create_superadmin tu@correo.com "Tu Nombre"
MSG
