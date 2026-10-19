-- =====================================================================
-- rls.sql | Row Level Security. Ejecutar DESPUÉS de schema.sql.
-- La API se conecta como agenda_app (NO superusuario, sin BYPASSRLS), y por request hace:
--   SET LOCAL app.current_tenant = '<uuid del JWT>';            -- request de tenant
--   SET LOCAL app.is_platform    = 'on';                         -- solo Super Admin / jobs / webhooks
-- Sin ninguna de las dos variables, NO se ve ninguna fila (fail-closed).
-- =====================================================================
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agenda_app') THEN
    -- CAMBIAR la contraseña en cualquier entorno que no sea desarrollo local
    CREATE ROLE agenda_app LOGIN PASSWORD 'agenda_app_dev' NOSUPERUSER NOBYPASSRLS;
  END IF;
END $$;

CREATE FUNCTION app_current_tenant() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.current_tenant', true), '')::uuid $$;

CREATE FUNCTION app_is_platform() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT coalesce(current_setting('app.is_platform', true), '') = 'on' $$;

-- Toda tabla con columna tenant_id: aislamiento total (lectura y escritura)
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT c.table_name FROM information_schema.columns c
           JOIN information_schema.tables tb USING (table_schema, table_name)
           WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' AND tb.table_type = 'BASE TABLE'
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);   -- aplica incluso al dueño de la tabla
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I
                      USING (tenant_id = app_current_tenant() OR app_is_platform())
                      WITH CHECK (tenant_id = app_current_tenant() OR app_is_platform())$p$, t);
  END LOOP;
END $$;

-- tenants: un tenant solo ve su propia fila (la clave es id, no tenant_id)
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_self ON tenants
  USING (id = app_current_tenant() OR app_is_platform())
  WITH CHECK (app_is_platform());

-- Tablas solo de plataforma: el tenant no puede ver super admins
ALTER TABLE platform_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_users FORCE ROW LEVEL SECURITY;
CREATE POLICY platform_only ON platform_users USING (app_is_platform()) WITH CHECK (app_is_platform());

-- Privilegios: el rol de la API opera DML; nunca DDL
GRANT USAGE ON SCHEMA public TO agenda_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agenda_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agenda_app;
-- Los logs de auditoría son append-only
REVOKE UPDATE, DELETE ON audit_logs FROM agenda_app;
-- El libro de movimientos de inventario también: se corrige con un ajuste nuevo, no editando el historial
REVOKE UPDATE, DELETE ON stock_movements FROM agenda_app;
