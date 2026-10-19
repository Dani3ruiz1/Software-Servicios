-- Perfil "motor de reservas" (primera versión comercial): el negocio NO cobra, factura ni maneja inventario aquí.
-- Los módulos de dinero (caja, inventario, factura DIAN) siguen existiendo en el código para una versión futura, pero ningún plan los incluye.
-- Aplicar ANTES de crear negocios (los módulos de cada negocio se copian de su plan al crearlo):
--   psql "$MIGRATIONS_DATABASE_URL" -f sql/perfil_solo_reservas.sql        -- es seguro repetirlo
DELETE FROM plan_modules WHERE module_id IN (SELECT id FROM modules WHERE codigo IN ('cash', 'inventory', 'einvoice'));
DELETE FROM tenant_modules WHERE module_id IN (SELECT id FROM modules WHERE codigo IN ('cash', 'inventory', 'einvoice'));

-- Cada plan = lo necesario para reservar; se distinguen por usuarios, reportes, WhatsApp e IA.
INSERT INTO plan_modules (plan_id, module_id)
SELECT p.id, m.id FROM plans p JOIN modules m ON
  (p.nombre IN ('Independiente', 'Básico') AND m.codigo IN ('calendar', 'reception', 'booking'))
  OR (p.nombre = 'Profesional' AND m.codigo IN ('calendar', 'reception', 'booking', 'reports', 'whatsapp'))
  OR (p.nombre = 'Premium'     AND m.codigo IN ('calendar', 'reception', 'booking', 'reports', 'whatsapp', 'ai'))
ON CONFLICT DO NOTHING;

UPDATE modules SET nombre = 'Clientes y recepción' WHERE codigo = 'reception';
UPDATE modules SET nombre = 'Reportes de reservas' WHERE codigo = 'reports';
