-- =====================================================================
-- seed_catalog.sql | Catálogo de la plataforma (módulos, planes, tipos de negocio). ES EL QUE SE USA EN PRODUCCIÓN.
-- Ejecutar DESPUÉS de schema.sql y rls.sql, como superusuario. NO crea ningún super admin:
-- créalo con  python -m app.scripts.create_superadmin  (la contraseña se pide por teclado).
-- (seed.sql = este mismo catálogo + un super admin de DESARROLLO con contraseña conocida; nunca en producción.)
-- Precios en COP, de ejemplo: el Super Admin los ajusta desde la API.
-- =====================================================================

INSERT INTO modules (codigo, nombre, precio_mensual_base) VALUES
 ('calendar',  'Agenda / Calendario',          20000),
 ('reception', 'Recepción y ventas',           25000),
 ('inventory', 'Inventario',                   20000),
 ('cash',      'Caja',                         25000),
 ('reports',   'Reportes',                     20000),
 ('booking',   'Reservas en línea',            25000),
 ('einvoice',  'Factura electrónica DIAN',     40000),
 ('whatsapp',  'WhatsApp',                     30000),
 ('ai',        'Asistente IA',                 50000);

INSERT INTO plans (nombre, precio_mensual, max_usuarios, max_mensajes_ia_mes, es_independiente) VALUES
 ('Independiente', 29000,  1,  NULL, true),
 ('Básico',        79000,  5,  NULL, false),
 ('Profesional',  149000, 15,  500,  false),
 ('Premium',      249000, 50, 3000,  false);

INSERT INTO plan_modules (plan_id, module_id)
SELECT p.id, m.id FROM plans p JOIN modules m ON
  (p.nombre = 'Independiente' AND m.codigo IN ('calendar'))
  OR (p.nombre = 'Básico'      AND m.codigo IN ('calendar','reception','cash','booking'))
  OR (p.nombre = 'Profesional' AND m.codigo IN ('calendar','reception','cash','booking','reports','inventory','whatsapp'))
  OR (p.nombre = 'Premium');   -- todos, incl. einvoice y ai

INSERT INTO business_types (codigo, nombre) VALUES
 ('barberia', 'Barbería'), ('spa', 'Spa'), ('unas', 'Salón de uñas'),
 ('estetica', 'Centro de estética'), ('salon', 'Salón de belleza');

INSERT INTO business_type_templates (business_type, categoria, nombre, duracion_min, precio_sugerido) VALUES
 ('barberia','Cabello','Corte clásico',30,25000),
 ('barberia','Cabello','Corte + barba',45,35000),
 ('barberia','Barba','Perfilado de barba',20,15000),
 ('barberia','Cabello','Corte infantil',30,20000),
 ('spa','Masajes','Masaje relajante 60 min',60,120000),
 ('spa','Masajes','Masaje descontracturante',60,140000),
 ('spa','Faciales','Limpieza facial profunda',60,95000),
 ('spa','Cuerpo','Exfoliación corporal',45,110000),
 ('unas','Manos','Manicure tradicional',45,25000),
 ('unas','Manos','Manicure en gel',60,45000),
 ('unas','Pies','Pedicure spa',60,40000),
 ('unas','Manos','Uñas acrílicas',90,80000),
 ('estetica','Faciales','Limpieza facial',60,90000),
 ('estetica','Cejas','Diseño y depilación de cejas',30,30000),
 ('estetica','Corporal','Drenaje linfático',60,100000),
 ('estetica','Pestañas','Lifting de pestañas',60,110000),
 ('salon','Cabello','Corte dama',45,40000),
 ('salon','Color','Tinte completo',120,150000),
 ('salon','Cabello','Cepillado',45,35000),
 ('salon','Tratamientos','Keratina',150,220000);
