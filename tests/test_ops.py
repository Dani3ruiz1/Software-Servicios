"""Pruebas de OPERACIÓN: migraciones equivalentes a schema.sql y respaldo/restauración reales.

Necesitan un PostgreSQL con usuario administrador y las herramientas de cliente (psql, pg_dump, pg_restore) más `bash`.
Se configuran con TEST_ADMIN_URL (por defecto el servidor de pruebas local) y PG_BIN (carpeta de las herramientas). Si falta algo, se omiten.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql://postgres:postgres@127.0.0.1:55432/postgres")


def _pg_bin() -> Path | None:
    for cand in (os.environ.get("PG_BIN"), shutil.which("psql") and str(Path(shutil.which("psql") or "").parent), r"C:\Program Files\PostgreSQL\18\bin",
                 "/usr/lib/postgresql/16/bin"):
        if cand and (Path(cand) / ("psql.exe" if os.name == "nt" else "psql")).exists():
            return Path(cand)
    return None


def _bash() -> str | None:
    for cand in (shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe", "/bin/bash"):
        if cand and Path(cand).exists():
            try:
                if subprocess.run([cand, "-c", "exit 0"], capture_output=True, timeout=20).returncode == 0:   # noqa: S603
                    return cand
            except OSError:
                continue
    return None


PG = _pg_bin()
BASH = _bash()


def _env() -> dict[str, str]:
    e = dict(os.environ)
    if PG:
        e["PATH"] = str(PG) + os.pathsep + e.get("PATH", "")
    return e


def run(args: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", env=kw.pop("env", _env()), timeout=300, **kw)   # noqa: S603


def psql(db: str, sql: str | None = None, file: Path | None = None) -> str:
    u = urlparse(ADMIN_URL)
    cmd = [str(PG / "psql"), "-h", u.hostname or "127.0.0.1", "-p", str(u.port or 5432), "-U", u.username or "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-q", "-At"]
    r = run([*cmd, *(["-c", sql] if sql else ["-f", str(file)])], env={**_env(), "PGPASSWORD": u.password or ""})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def url_for(db: str, user: str | None = None, password: str | None = None, scheme: str = "postgresql") -> str:
    u = urlparse(ADMIN_URL)
    return f"{scheme}://{user or u.username}:{password or u.password}@{u.hostname}:{u.port}/{db}"


@pytest.fixture(scope="module", autouse=True)
def _tools():
    if not PG or not BASH:
        pytest.skip("Faltan psql/pg_dump o bash")
    try:
        psql("postgres", "SELECT 1")
    except AssertionError:
        pytest.skip("Sin servidor PostgreSQL administrativo para pruebas de operación")


@pytest.fixture
def dbs():
    made: list[str] = []

    def make() -> str:
        name = f"ops_{uuid4().hex[:10]}"
        psql("postgres", f'CREATE DATABASE "{name}"')
        made.append(name)
        return name
    yield make
    for n in made:
        psql("postgres", f'DROP DATABASE IF EXISTS "{n}" WITH (FORCE)')


def dump_schema(db: str) -> str:
    u = urlparse(ADMIN_URL)
    r = run([str(PG / "pg_dump"), "-h", u.hostname or "", "-p", str(u.port), "-U", u.username or "", "-s", "-T", "alembic_version", "--no-owner", "--no-privileges", db],
            env={**_env(), "PGPASSWORD": u.password or ""})
    assert r.returncode == 0, r.stderr
    return "\n".join(ln for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("--") and not ln.startswith("\\"))


def alembic(db: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**_env(), "MIGRATIONS_DATABASE_URL": url_for(db, scheme="postgresql+psycopg"), "PYTHONPATH": os.pathsep.join(p for p in sys.path if p and Path(p).resolve() != ROOT)}
    # la carpeta local `alembic/` (migraciones) taparía al paquete alembic si el proyecto estuviera en sys.path
    code = "import sys; sys.path[:] = [p for p in sys.path if p]; from alembic.config import main; main(argv=sys.argv[1:])"
    return run([sys.executable, "-c", code, *args], env=env, cwd=ROOT)


def test_alembic_builds_exactly_the_same_schema_as_schema_sql_and_is_reversible(dbs):
    via_alembic, via_sql = dbs(), dbs()
    r = alembic(via_alembic, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-1500:]
    psql(via_sql, file=ROOT / "sql" / "schema.sql")
    psql(via_sql, file=ROOT / "sql" / "rls.sql")
    a, b = dump_schema(via_alembic), dump_schema(via_sql)
    assert a == b, "alembic y sql/ producen esquemas distintos:\n" + "\n".join(
        ln for ln in (set(a.splitlines()) ^ set(b.splitlines())))[:1500]
    assert psql(via_alembic, "SELECT (SELECT count(*) FROM plans) || ',' || (SELECT count(*) FROM modules) || ',' || (SELECT count(*) FROM platform_users)") == "4,9,0"
    assert alembic(via_alembic, "upgrade", "head").returncode == 0                      # repetir no rompe ni duplica
    assert psql(via_alembic, "SELECT count(*) FROM plans") == "4"
    down = alembic(via_alembic, "downgrade", "base")
    assert down.returncode == 0, down.stderr[-800:]
    assert psql(via_alembic, "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name <> 'alembic_version'") == "0"
    assert alembic(via_alembic, "upgrade", "head").returncode == 0                      # y se puede volver a subir


def test_backup_then_restore_recovers_everything_and_keeps_tenant_isolation(dbs, tmp_path):
    src, dst = dbs(), dbs()
    for f in ("schema", "rls", "seed_catalog"):
        psql(src, file=ROOT / "sql" / f"{f}.sql")
    psql(src, """
        INSERT INTO tenants (id, nombre, slug, business_type) VALUES ('11111111-1111-1111-1111-111111111111', 'Negocio A', 'negocio-a', 'barberia'),
                                                                   ('22222222-2222-2222-2222-222222222222', 'Negocio B', 'negocio-b', 'spa');
        INSERT INTO customers (tenant_id, nombre) VALUES ('11111111-1111-1111-1111-111111111111', 'Cliente de A'), ('22222222-2222-2222-2222-222222222222', 'Cliente de B');
    """)
    out = tmp_path / "respaldos"
    env = {**_env(), "BACKUP_DATABASE_URL": url_for(src), "BACKUP_RETENTION_DAYS": "30"}
    b = run([BASH, (ROOT / "scripts" / "backup.sh").as_posix(), out.as_posix()], env=env)
    assert b.returncode == 0, b.stderr + b.stdout
    dumps = list(out.glob("agenda-*.dump"))
    assert len(dumps) == 1 and dumps[0].stat().st_size > 1000 and "respaldo OK" in b.stdout
    assert not list(out.glob("*.partial"))                                              # no queda basura de un respaldo a medias
    assert (out / (dumps[0].name + ".sha256")).exists()                                 # suma de verificación junto al respaldo

    r = run([BASH, (ROOT / "scripts" / "restore.sh").as_posix(), dumps[0].as_posix(), url_for(dst)], env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "negocios=2" in r.stdout
    assert psql(dst, "SELECT count(*) FROM customers") == "2" and psql(dst, "SELECT count(*) FROM plans") == "4"
    assert dump_schema(dst) == dump_schema(src)                                         # mismo esquema tras restaurar

    again = run([BASH, (ROOT / "scripts" / "restore.sh").as_posix(), dumps[0].as_posix(), url_for(dst)], env=env)
    assert again.returncode != 0 and "NO está vacía" in again.stdout + again.stderr     # jamás pisa una base con datos sin FORCE=1

    # La restauración conserva RLS y los permisos del rol de la API (si no, un respaldo restaurado dejaría de aislar a los negocios)
    api = url_for(dst, "agenda_app", "agenda_app_dev")
    u = urlparse(api)
    base = [str(PG / "psql"), "-h", u.hostname or "", "-p", str(u.port), "-U", "agenda_app", "-d", dst, "-At", "-q"]
    penv = {**_env(), "PGPASSWORD": "agenda_app_dev"}
    sin_contexto = run([*base, "-c", "SELECT count(*) FROM customers"], env=penv)
    assert sin_contexto.stdout.strip() == "0", sin_contexto.stderr                      # fail-closed
    solo_a = run([*base, "-c", "BEGIN; SELECT set_config('app.current_tenant','11111111-1111-1111-1111-111111111111', true); SELECT nombre FROM customers; COMMIT;"], env=penv)
    assert "Cliente de A" in solo_a.stdout and "Cliente de B" not in solo_a.stdout

    # Un archivo manipulado se detecta ANTES de restaurar
    bad = tmp_path / "manipulado" / dumps[0].name
    bad.parent.mkdir()
    shutil.copy(dumps[0], bad)
    shutil.copy(str(dumps[0]) + ".sha256", str(bad) + ".sha256")
    bad.write_bytes(bad.read_bytes()[:-50] + b"\x00" * 50)
    corrupt = run([BASH, (ROOT / "scripts" / "restore.sh").as_posix(), bad.as_posix(), url_for(dbs())], env=env)
    assert corrupt.returncode != 0 and "FAILED" in corrupt.stdout + corrupt.stderr       # sha256sum -c lo detecta antes de tocar la BD


def test_backup_script_refuses_to_leave_a_partial_file_when_the_database_is_unreachable(tmp_path):
    out = tmp_path / "r"
    env = {**_env(), "BACKUP_DATABASE_URL": "postgresql://nadie:x@127.0.0.1:1/no_existe"}
    r = run([BASH, (ROOT / "scripts" / "backup.sh").as_posix(), out.as_posix()], env=env)
    assert r.returncode != 0 and not list(out.glob("agenda-*.dump")) and "respaldo OK" not in r.stdout
    assert re.search(r"unreachable|connect|refused|could not", r.stderr, re.I)
