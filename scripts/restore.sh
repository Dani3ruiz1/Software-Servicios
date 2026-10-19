#!/bin/sh
# Restaura un respaldo en una base VACÍA (nunca sobre la de producción en uso).
#   scripts/restore.sh agenda-20261015T030000Z.dump postgresql://admin:clave@host:5432/agenda_restaurada
# Antes: crea la base destino vacía (CREATE DATABASE agenda_restaurada) y verifica que existe el rol `agenda_app`.
set -eu
FILE="${1:?Uso: restore.sh archivo.dump postgresql://admin@host/base_destino}"
TARGET="${2:?Falta la URL de la base destino}"
[ -f "$FILE" ] || { echo "No existe $FILE"; exit 1; }
if [ -f "$FILE.sha256" ] && command -v sha256sum > /dev/null 2>&1; then (cd "$(dirname "$FILE")" && sha256sum -c "$(basename "$FILE").sha256"); fi
TABLES=$(psql "$TARGET" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
if [ "$TABLES" != "0" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "La base destino NO está vacía ($TABLES tablas). Usa una base nueva, o FORCE=1 si de verdad quieres sobrescribirla."; exit 1
fi
pg_restore --no-owner --exit-on-error --dbname="$TARGET" "$FILE"
echo "Restauración terminada. Verificación rápida:"
psql "$TARGET" -Atc "SELECT 'negocios=' || (SELECT count(*) FROM tenants) || ' usuarios=' || (SELECT count(*) FROM users) || ' citas=' || (SELECT count(*) FROM appointments) || ' ventas=' || (SELECT count(*) FROM sales)"
