#!/bin/sh
# Respaldo lógico de PostgreSQL (formato comprimido de pg_dump) con verificación de integridad y retención.
#   BACKUP_DATABASE_URL=postgresql://usuario:clave@host:5432/agenda scripts/backup.sh [directorio]
# Programación (cron del servidor, todos los días 03:00):
#   0 3 * * * BACKUP_DATABASE_URL=... /srv/scripts/backup.sh /var/backups/agenda >> /var/log/agenda-backup.log 2>&1
# IMPORTANTE: copia el directorio FUERA de la máquina (otro proveedor/bucket) y PRUEBA la restauración periódicamente.
set -eu
: "${BACKUP_DATABASE_URL:?Define BACKUP_DATABASE_URL (postgresql://..., sin +asyncpg)}"
DIR="${1:-./backups}"
KEEP_DAYS="${BACKUP_RETENTION_DAYS:-30}"
umask 077
mkdir -p "$DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$DIR/agenda-$STAMP.dump"
pg_dump --format=custom --compress=6 --no-owner --file="$OUT.partial" "$BACKUP_DATABASE_URL"
pg_restore --list "$OUT.partial" > /dev/null          # si el archivo está corrupto, falla aquí y NO queda como respaldo válido
mv "$OUT.partial" "$OUT"
if command -v sha256sum > /dev/null 2>&1; then (cd "$DIR" && sha256sum "agenda-$STAMP.dump" > "agenda-$STAMP.dump.sha256"); fi
find "$DIR" -name 'agenda-*.dump*' -type f -mtime +"$KEEP_DAYS" -delete
echo "$(date -u +%FT%TZ) respaldo OK: $OUT ($(wc -c < "$OUT") bytes); conservando $KEEP_DAYS días"
