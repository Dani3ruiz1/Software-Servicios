-- =====================================================================
-- schema.sql  |  Agenda SaaS multi-inquilino  |  PostgreSQL 16
-- Ejecutable en pgAdmin (Query Tool) sobre una BD vacía. Orden: schema -> rls -> seed.
-- Convenciones: PK uuid, timestamptz UTC, dinero numeric(12,2), borrado lógico (deleted_at),
-- enums como CHECK (más fácil de migrar que CREATE TYPE).
-- Integridad cross-tenant: cada padre expone UNIQUE (tenant_id, id) y los hijos usan FK
-- compuesta (tenant_id, x_id): un hijo JAMÁS puede apuntar a un padre de otro tenant,
-- ni siquiera saltándose RLS (las FK no pasan por RLS).
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$;

-- ========================= PLATAFORMA (sin tenant_id) =================
CREATE TABLE platform_users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email         citext NOT NULL UNIQUE,
  password_hash text   NOT NULL,
  nombre        text   NOT NULL,
  activo        boolean NOT NULL DEFAULT true,
  totp_secret   text,                       -- cifrado Fernet
  totp_habilitado   boolean NOT NULL DEFAULT false,
  intentos_fallidos integer NOT NULL DEFAULT 0,
  bloqueado_hasta   timestamptz,
  ultimo_login  timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE plans (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre             text NOT NULL UNIQUE,
  precio_mensual     numeric(12,2) NOT NULL CHECK (precio_mensual >= 0),
  max_usuarios       integer NOT NULL CHECK (max_usuarios > 0),
  max_mensajes_ia_mes integer CHECK (max_mensajes_ia_mes >= 0),  -- NULL = sin IA
  es_independiente   boolean NOT NULL DEFAULT false,
  activo             boolean NOT NULL DEFAULT true,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CHECK (NOT es_independiente OR max_usuarios = 1)
);

CREATE TABLE modules (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo             text NOT NULL UNIQUE CHECK (codigo IN
    ('calendar','reception','inventory','cash','reports','booking','einvoice','whatsapp','ai')),
  nombre             text NOT NULL,
  precio_mensual_base numeric(12,2) NOT NULL CHECK (precio_mensual_base >= 0),
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE plan_modules (
  plan_id   uuid NOT NULL REFERENCES plans(id)   ON DELETE CASCADE,
  module_id uuid NOT NULL REFERENCES modules(id) ON DELETE RESTRICT,
  PRIMARY KEY (plan_id, module_id)
);
CREATE INDEX ix_plan_modules_module ON plan_modules (module_id);

CREATE TABLE business_types (
  codigo text PRIMARY KEY,
  nombre text NOT NULL
);
-- Servicios de ejemplo que se clonan al crear un tenant de ese tipo
CREATE TABLE business_type_templates (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_type   text NOT NULL REFERENCES business_types(codigo) ON DELETE CASCADE,
  categoria       text NOT NULL,
  nombre          text NOT NULL,
  duracion_min    integer NOT NULL CHECK (duracion_min > 0),
  precio_sugerido numeric(12,2) NOT NULL CHECK (precio_sugerido >= 0)
);
CREATE INDEX ix_bt_templates_type ON business_type_templates (business_type);

CREATE TABLE tenants (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nombre           text NOT NULL,
  nit              text,
  slug             citext NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$'),
  business_type    text NOT NULL REFERENCES business_types(codigo),
  modo             text NOT NULL DEFAULT 'empresa' CHECK (modo IN ('independiente','empresa')),
  zona_horaria     text NOT NULL DEFAULT 'America/Bogota',
  moneda           char(3) NOT NULL DEFAULT 'COP',
  estado           text NOT NULL DEFAULT 'activa'
                   CHECK (estado IN ('activa','por_vencer','vencida','suspendida','bloqueada')),
  requiere_sesion_caja boolean NOT NULL DEFAULT true,   -- regla 9 (configurable)
  bloqueo_manual   boolean NOT NULL DEFAULT false,      -- suspendida/bloqueada por el Super Admin: ni pagos ni el job la reactivan
  contacto_nombre  text,
  contacto_email   citext,
  contacto_telefono text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz
);
CREATE INDEX ix_tenants_estado ON tenants (estado) WHERE deleted_at IS NULL;

CREATE TABLE tenant_subscriptions (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants(id),
  plan_id             uuid NOT NULL REFERENCES plans(id),
  fecha_inicio        date NOT NULL,
  fecha_fin           date NOT NULL,
  estado              text NOT NULL DEFAULT 'vigente' CHECK (estado IN ('vigente','vencida','cancelada')),
  precio_plan_aplicado numeric(12,2) NOT NULL CHECK (precio_plan_aplicado >= 0),
  notas               text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (fecha_fin >= fecha_inicio)
);
CREATE UNIQUE INDEX ux_subscription_one_active ON tenant_subscriptions (tenant_id) WHERE estado = 'vigente';
CREATE INDEX ix_subscriptions_fin ON tenant_subscriptions (fecha_fin) WHERE estado = 'vigente';
CREATE INDEX ix_subscriptions_plan ON tenant_subscriptions (plan_id);

CREATE TABLE tenant_modules (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  module_id       uuid NOT NULL REFERENCES modules(id),
  origen          text NOT NULL CHECK (origen IN ('plan','adicional')),
  precio_override numeric(12,2) CHECK (precio_override >= 0),  -- lo escribe el Super Admin
  activo          boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, module_id)
);
CREATE INDEX ix_tenant_modules_module ON tenant_modules (module_id);

CREATE TABLE subscription_payments (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  monto         numeric(12,2) NOT NULL CHECK (monto > 0),
  fecha_pago    date NOT NULL,
  periodo_desde date NOT NULL,
  periodo_hasta date NOT NULL,
  metodo        text NOT NULL,
  referencia    text,
  registrado_por uuid REFERENCES platform_users(id),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  CHECK (periodo_hasta >= periodo_desde)
);
CREATE INDEX ix_sub_payments_tenant ON subscription_payments (tenant_id, fecha_pago DESC);

CREATE TABLE audit_logs (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_tipo  text NOT NULL CHECK (actor_tipo IN ('platform_user','user','system')),
  actor_id    uuid,
  tenant_id   uuid REFERENCES tenants(id),   -- NULL = acción de plataforma
  accion      text NOT NULL,
  entidad     text NOT NULL,
  entidad_id  uuid,
  antes       jsonb,
  despues     jsonb,
  ip          inet,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_tenant_fecha ON audit_logs (tenant_id, created_at DESC);
CREATE INDEX ix_audit_entidad ON audit_logs (entidad, entidad_id);

-- Auth: refresh tokens rotativos (hash, nunca el token) y recuperación de contraseña
CREATE TABLE refresh_tokens (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sujeto_tipo  text NOT NULL CHECK (sujeto_tipo IN ('platform_user','user')),
  sujeto_id    uuid NOT NULL,
  tenant_id    uuid REFERENCES tenants(id),
  token_hash   text NOT NULL UNIQUE,
  familia_id   uuid NOT NULL,               -- detección de reutilización
  expira_en    timestamptz NOT NULL,
  revocado_en  timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_refresh_sujeto ON refresh_tokens (sujeto_tipo, sujeto_id);

CREATE TABLE password_reset_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sujeto_tipo text NOT NULL CHECK (sujeto_tipo IN ('platform_user','user')),
  sujeto_id   uuid NOT NULL,
  tenant_id   uuid REFERENCES tenants(id),
  token_hash  text NOT NULL UNIQUE,
  expira_en   timestamptz NOT NULL,
  usado_en    timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- ========================= NEGOCIO (con tenant_id) ====================
CREATE TABLE users (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id         uuid NOT NULL REFERENCES tenants(id),
  email             citext NOT NULL,
  password_hash     text NOT NULL,
  nombre            text NOT NULL,
  rol               text NOT NULL CHECK (rol IN ('admin','recepcionista','profesional')),
  activo            boolean NOT NULL DEFAULT true,
  totp_secret       text,
  totp_habilitado   boolean NOT NULL DEFAULT false,
  intentos_fallidos integer NOT NULL DEFAULT 0,
  bloqueado_hasta   timestamptz,
  ultimo_login      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  UNIQUE (tenant_id, id)
);
CREATE UNIQUE INDEX ux_users_email ON users (tenant_id, email) WHERE deleted_at IS NULL;

CREATE TABLE staff_profiles (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  user_id          uuid,
  nombre_publico   text NOT NULL,
  especialidad     text,
  color_calendario char(7) NOT NULL DEFAULT '#6B8F71' CHECK (color_calendario ~ '^#[0-9A-Fa-f]{6}$'),
  foto_url         text,
  activo           boolean NOT NULL DEFAULT true,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz,
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id)
);
CREATE INDEX ix_staff_user ON staff_profiles (tenant_id, user_id);

CREATE TABLE staff_schedules (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  staff_id    uuid NOT NULL,
  dia_semana  smallint NOT NULL CHECK (dia_semana BETWEEN 0 AND 6),   -- 0 = lunes
  hora_inicio time NOT NULL,
  hora_fin    time NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  CHECK (hora_fin > hora_inicio),
  FOREIGN KEY (tenant_id, staff_id) REFERENCES staff_profiles (tenant_id, id)
);
CREATE INDEX ix_staff_schedules ON staff_schedules (tenant_id, staff_id, dia_semana);

CREATE TABLE schedule_blocks (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  staff_id   uuid NOT NULL,
  inicio     timestamptz NOT NULL,
  fin        timestamptz NOT NULL,
  motivo     text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (fin > inicio),
  FOREIGN KEY (tenant_id, staff_id) REFERENCES staff_profiles (tenant_id, id)
);
CREATE INDEX ix_schedule_blocks ON schedule_blocks (tenant_id, staff_id, inicio);

CREATE TABLE customers (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id              uuid NOT NULL REFERENCES tenants(id),
  nombre                 text NOT NULL,
  telefono               text,
  email                  citext,
  documento              text,
  notas                  text,
  consentimiento_datos   boolean NOT NULL DEFAULT false,   -- Ley 1581
  consentimiento_fecha   timestamptz,
  consentimiento_version text,
  origen                 text NOT NULL DEFAULT 'recepcion' CHECK (origen IN ('reserva_web','whatsapp','recepcion')),
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  deleted_at             timestamptz,
  UNIQUE (tenant_id, id),
  CHECK (NOT consentimiento_datos OR consentimiento_fecha IS NOT NULL)
);
CREATE INDEX ix_customers_tel ON customers (tenant_id, telefono);
CREATE INDEX ix_customers_email ON customers (tenant_id, email);
CREATE INDEX ix_customers_doc ON customers (tenant_id, documento);
CREATE INDEX ix_customers_nombre ON customers (tenant_id, lower(nombre));

CREATE TABLE service_categories (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  nombre     text NOT NULL,
  orden      integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deleted_at timestamptz,
  UNIQUE (tenant_id, id)
);
CREATE UNIQUE INDEX ux_service_categories ON service_categories (tenant_id, lower(nombre)) WHERE deleted_at IS NULL;

CREATE TABLE services (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id),
  category_id    uuid,
  nombre         text NOT NULL,
  duracion_min   integer NOT NULL CHECK (duracion_min > 0),
  precio         numeric(12,2) NOT NULL CHECK (precio >= 0),
  precio_editable boolean NOT NULL DEFAULT false,
  aplica_iva     boolean NOT NULL DEFAULT false,
  porcentaje_iva numeric(5,2) NOT NULL DEFAULT 0 CHECK (porcentaje_iva BETWEEN 0 AND 100),
  activo         boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz,
  UNIQUE (tenant_id, id),
  CHECK (aplica_iva OR porcentaje_iva = 0),
  FOREIGN KEY (tenant_id, category_id) REFERENCES service_categories (tenant_id, id)
);
CREATE INDEX ix_services_cat ON services (tenant_id, category_id);
CREATE INDEX ix_services_nombre ON services (tenant_id, lower(nombre));

CREATE TABLE staff_services (
  tenant_id  uuid NOT NULL REFERENCES tenants(id),
  staff_id   uuid NOT NULL,
  service_id uuid NOT NULL,
  PRIMARY KEY (staff_id, service_id),
  FOREIGN KEY (tenant_id, staff_id)   REFERENCES staff_profiles (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_staff_services_service ON staff_services (tenant_id, service_id);

CREATE TABLE appointments (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  customer_id uuid NOT NULL,
  staff_id    uuid,                                  -- NULL = "asignar en recepción"
  inicio      timestamptz NOT NULL,
  fin         timestamptz NOT NULL,
  estado      text NOT NULL DEFAULT 'pendiente'
              CHECK (estado IN ('pendiente','confirmada','en_atencion','atendida','cancelada','no_asistio')),
  origen      text NOT NULL DEFAULT 'recepcion' CHECK (origen IN ('reserva_web','whatsapp','recepcion')),
  notas       text,
  created_by  uuid,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  deleted_at  timestamptz,
  UNIQUE (tenant_id, id),
  CHECK (fin > inicio),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id),
  FOREIGN KEY (tenant_id, staff_id)    REFERENCES staff_profiles (tenant_id, id),
  -- Anti doble reserva a nivel de BD (regla 8). Citas canceladas/no_asistio liberan el hueco.
  CONSTRAINT ex_appointments_no_overlap EXCLUDE USING gist (
    staff_id WITH =, tstzrange(inicio, fin, '[)') WITH &&
  ) WHERE (staff_id IS NOT NULL AND deleted_at IS NULL AND estado NOT IN ('cancelada','no_asistio'))
);
CREATE INDEX ix_appt_staff_inicio ON appointments (tenant_id, staff_id, inicio);
CREATE INDEX ix_appt_inicio ON appointments (tenant_id, inicio);
CREATE INDEX ix_appt_customer ON appointments (tenant_id, customer_id, inicio DESC);
CREATE INDEX ix_appt_estado ON appointments (tenant_id, estado, inicio);

CREATE TABLE appointment_services (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id),
  appointment_id uuid NOT NULL,
  service_id     uuid NOT NULL,
  precio_pactado numeric(12,2) NOT NULL CHECK (precio_pactado >= 0),
  duracion_min   integer NOT NULL CHECK (duracion_min > 0),
  FOREIGN KEY (tenant_id, appointment_id) REFERENCES appointments (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, service_id)     REFERENCES services (tenant_id, id)
);
CREATE INDEX ix_appt_services_appt ON appointment_services (tenant_id, appointment_id);
CREATE INDEX ix_appt_services_svc  ON appointment_services (tenant_id, service_id);

-- ---- Caja y ventas ----
CREATE TABLE cash_sessions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants(id),
  abierta_en     timestamptz NOT NULL DEFAULT now(),
  cerrada_en     timestamptz,
  responsable_id uuid NOT NULL,
  monto_inicial  numeric(12,2) NOT NULL DEFAULT 0 CHECK (monto_inicial >= 0),
  monto_esperado numeric(12,2),                       -- se congela al cerrar
  monto_contado  numeric(12,2) CHECK (monto_contado >= 0),
  diferencia     numeric(12,2),                       -- contado - esperado
  estado         text NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta','cerrada')),
  notas          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  CHECK ((estado = 'abierta' AND cerrada_en IS NULL) OR (estado = 'cerrada' AND cerrada_en IS NOT NULL AND monto_contado IS NOT NULL)),
  FOREIGN KEY (tenant_id, responsable_id) REFERENCES users (tenant_id, id)
);
-- Supuesto: una sola caja abierta a la vez por tenant (ver docs/ARQUITECTURA.md §Decisiones)
CREATE UNIQUE INDEX ux_cash_one_open ON cash_sessions (tenant_id) WHERE estado = 'abierta';
CREATE INDEX ix_cash_sessions_fecha ON cash_sessions (tenant_id, abierta_en DESC);

CREATE TABLE cash_movements (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  cash_session_id uuid NOT NULL,
  tipo            text NOT NULL CHECK (tipo IN ('ingreso','egreso')),
  categoria       text NOT NULL,
  monto           numeric(12,2) NOT NULL CHECK (monto > 0),
  descripcion     text,
  soporte_url     text,
  created_by      uuid,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, cash_session_id) REFERENCES cash_sessions (tenant_id, id)
);
CREATE INDEX ix_cash_mov_session ON cash_movements (tenant_id, cash_session_id);

CREATE TABLE document_sequences (          -- numeración de comprobantes sin huecos por tenant
  tenant_id     uuid NOT NULL REFERENCES tenants(id),
  serie         text NOT NULL,
  ultimo_numero bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_id, serie)
);

CREATE TABLE sales (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants(id),
  customer_id        uuid,
  staff_id           uuid,
  appointment_id     uuid,
  cash_session_id    uuid,
  subtotal           numeric(12,2) NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
  iva                numeric(12,2) NOT NULL DEFAULT 0 CHECK (iva >= 0),
  propina            numeric(12,2) NOT NULL DEFAULT 0 CHECK (propina >= 0),
  total              numeric(12,2) NOT NULL DEFAULT 0 CHECK (total >= 0),
  estado             text NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta','pagada','anulada')),
  comprobante_numero text,
  pagada_en          timestamptz,                 -- fecha de cobro: base de los reportes de ventas
  anulada_en         timestamptz,
  anulada_motivo     text,
  created_by         uuid,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz,
  UNIQUE (tenant_id, id),
  CHECK (total = subtotal + iva + propina),
  CHECK (estado <> 'anulada' OR anulada_en IS NOT NULL),
  FOREIGN KEY (tenant_id, customer_id)     REFERENCES customers (tenant_id, id),
  FOREIGN KEY (tenant_id, staff_id)        REFERENCES staff_profiles (tenant_id, id),
  FOREIGN KEY (tenant_id, appointment_id)  REFERENCES appointments (tenant_id, id),
  FOREIGN KEY (tenant_id, cash_session_id) REFERENCES cash_sessions (tenant_id, id)
);
CREATE UNIQUE INDEX ux_sales_comprobante ON sales (tenant_id, comprobante_numero) WHERE comprobante_numero IS NOT NULL;
CREATE UNIQUE INDEX ux_sales_one_per_appt ON sales (tenant_id, appointment_id) WHERE appointment_id IS NOT NULL AND estado <> 'anulada';
CREATE INDEX ix_sales_fecha ON sales (tenant_id, created_at DESC);
CREATE INDEX ix_sales_pagada ON sales (tenant_id, pagada_en) WHERE estado = 'pagada';
CREATE INDEX ix_sales_staff ON sales (tenant_id, staff_id, created_at);
CREATE INDEX ix_sales_customer ON sales (tenant_id, customer_id);
CREATE INDEX ix_sales_cash ON sales (tenant_id, cash_session_id);

CREATE TABLE sale_items (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  sale_id         uuid NOT NULL,
  service_id      uuid,
  descripcion     text NOT NULL,
  precio_original numeric(12,2) NOT NULL CHECK (precio_original >= 0),  -- regla 6: siempre se guarda
  precio_cobrado  numeric(12,2) NOT NULL CHECK (precio_cobrado >= 0),
  iva             numeric(12,2) NOT NULL DEFAULT 0 CHECK (iva >= 0),
  FOREIGN KEY (tenant_id, sale_id)    REFERENCES sales (tenant_id, id) ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id)
);
CREATE INDEX ix_sale_items_sale ON sale_items (tenant_id, sale_id);
CREATE INDEX ix_sale_items_service ON sale_items (tenant_id, service_id);

CREATE TABLE payments (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  sale_id          uuid NOT NULL,
  metodo           text NOT NULL CHECK (metodo IN ('efectivo','datafono','transferencia','otro')),
  monto            numeric(12,2) NOT NULL CHECK (monto > 0),
  entidad_bancaria text,
  referencia       text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (metodo <> 'transferencia' OR entidad_bancaria IS NOT NULL),  -- regla 9
  FOREIGN KEY (tenant_id, sale_id) REFERENCES sales (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_payments_sale ON payments (tenant_id, sale_id);
CREATE INDEX ix_payments_metodo ON payments (tenant_id, metodo, created_at);

-- ---- Motor de reservas público ----
CREATE TABLE booking_settings (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL UNIQUE REFERENCES tenants(id),
  slug               citext NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$'),
  activo             boolean NOT NULL DEFAULT false,
  color_primario     char(7) NOT NULL DEFAULT '#2F5D50',
  color_secundario   char(7) NOT NULL DEFAULT '#E8B86D',
  color_fondo        char(7) NOT NULL DEFAULT '#FBF7F0',
  logo_url           text,
  textos             jsonb NOT NULL DEFAULT '{}',          -- título, bienvenida, mensaje de confirmación
  anticipacion_min_horas integer NOT NULL DEFAULT 1 CHECK (anticipacion_min_horas >= 0),
  anticipacion_max_dias  integer NOT NULL DEFAULT 60 CHECK (anticipacion_max_dias > 0),
  buffer_min         integer NOT NULL DEFAULT 0 CHECK (buffer_min >= 0),
  cancelacion_min_horas integer NOT NULL DEFAULT 2 CHECK (cancelacion_min_horas >= 0),  -- límite para cancelar/reprogramar en línea
  modo_asignacion    text NOT NULL DEFAULT 'cliente_elige'
                     CHECK (modo_asignacion IN ('cliente_elige','automatica','en_recepcion')),
  pedir_servicio     boolean NOT NULL DEFAULT true,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE booking_form_fields (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id   uuid NOT NULL REFERENCES tenants(id),
  etiqueta    text NOT NULL,
  tipo        text NOT NULL CHECK (tipo IN ('texto','telefono','email','numero','fecha','seleccion','casilla','area_texto')),
  obligatorio boolean NOT NULL DEFAULT false,
  orden       integer NOT NULL DEFAULT 0,
  opciones    jsonb,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_form_fields ON booking_form_fields (tenant_id, orden);

CREATE TABLE availability_rules (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  service_id      uuid,                      -- NULL = aplica a todos
  staff_id        uuid,                      -- NULL = aplica a todos
  cupos_simultaneos integer NOT NULL DEFAULT 1 CHECK (cupos_simultaneos > 0),
  duracion_turno_min integer CHECK (duracion_turno_min > 0),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, service_id) REFERENCES services (tenant_id, id),
  FOREIGN KEY (tenant_id, staff_id)   REFERENCES staff_profiles (tenant_id, id)
);
CREATE INDEX ix_avail_rules ON availability_rules (tenant_id, service_id, staff_id);

-- ---- Integraciones, mensajería, facturación ----
CREATE TABLE integrations (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id              uuid NOT NULL REFERENCES tenants(id),
  tipo                   text NOT NULL CHECK (tipo IN ('meta_whatsapp','ai','einvoice')),
  credenciales_cifradas  text,               -- Fernet; JAMÁS se devuelve por API
  identificador_externo  text,               -- p.ej. phone_number_id: enruta el webhook al tenant
  config                 jsonb NOT NULL DEFAULT '{}',   -- ajustes NO secretos (plantillas aprobadas, nombre del asistente...)
  estado                 text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','activa','error','desactivada')),
  ultima_verificacion    timestamptz,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, tipo)
);
CREATE UNIQUE INDEX ux_integrations_ext ON integrations (tipo, identificador_externo) WHERE identificador_externo IS NOT NULL;

CREATE TABLE conversations (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants(id),
  customer_id        uuid,
  canal              text NOT NULL DEFAULT 'whatsapp' CHECK (canal IN ('whatsapp','web')),
  contacto_externo   text NOT NULL,           -- teléfono E.164
  nombre_contacto    text,
  escalado_a_humano  boolean NOT NULL DEFAULT false,
  escalado_motivo    text,
  ultimo_mensaje_en  timestamptz,
  ultimo_mensaje_cliente_en timestamptz,   -- ventana de 24 h de WhatsApp: fuera de ella solo plantillas aprobadas
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES customers (tenant_id, id)
);
CREATE UNIQUE INDEX ux_conversations_contacto ON conversations (tenant_id, canal, contacto_externo);

CREATE TABLE messages (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  conversation_id uuid NOT NULL,
  rol             text NOT NULL CHECK (rol IN ('cliente','ia','humano','sistema')),
  contenido       text NOT NULL,
  estado          text NOT NULL DEFAULT 'recibido' CHECK (estado IN ('recibido','pendiente','enviado','entregado','leido','fallido')),
  id_externo      text,                       -- wamid: idempotencia del webhook
  meta            jsonb NOT NULL DEFAULT '{}',   -- herramientas usadas por la IA, motivo de fallo de envío
  created_at      timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_messages_conv ON messages (tenant_id, conversation_id, created_at);
CREATE UNIQUE INDEX ux_messages_externo ON messages (tenant_id, id_externo) WHERE id_externo IS NOT NULL;
CREATE INDEX ix_messages_ia_mes ON messages (tenant_id, created_at) WHERE rol = 'ia';   -- límite por plan

CREATE TABLE invoice_settings (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL UNIQUE REFERENCES tenants(id),
  nit          text NOT NULL,
  dv           smallint CHECK (dv BETWEEN 0 AND 9),     -- dígito de verificación del NIT
  razon_social text NOT NULL,
  resolucion   text,                                     -- número de la resolución de numeración de la DIAN
  vigencia_hasta date,                                   -- las resoluciones vencen (normalmente a los 24 meses)
  prefijo      text,
  prefijo_nota_credito text NOT NULL DEFAULT 'NC',
  rango_desde  bigint,
  rango_hasta  bigint,
  ambiente     text NOT NULL DEFAULT 'habilitacion' CHECK (ambiente IN ('habilitacion','produccion')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CHECK (rango_hasta IS NULL OR rango_hasta >= rango_desde)
);

CREATE TABLE invoices (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL REFERENCES tenants(id),
  sale_id              uuid NOT NULL,
  tipo                 text NOT NULL CHECK (tipo IN ('interna','electronica','nota_credito')),
  factura_origen_id    uuid,                 -- nota crédito -> factura que corrige
  prefijo              text,
  numero               bigint NOT NULL,
  cufe                 text,
  estado               text NOT NULL DEFAULT 'emitida' CHECK (estado IN ('emitida','enviada','aceptada','rechazada','error')),
  fecha_emision        timestamptz NOT NULL DEFAULT now(),
  datos                jsonb NOT NULL DEFAULT '{}',   -- fotografía de lo enviado (emisor, adquirente, líneas, impuestos): base de reintentos y notas crédito
  motivo               text,                          -- nota crédito: motivo
  referencia_proveedor text,                          -- id del documento en el proveedor tecnológico
  proximo_intento      timestamptz,
  ultimo_error         text,
  xml_url              text,
  pdf_url              text,
  respuesta_proveedor  jsonb,
  intentos             integer NOT NULL DEFAULT 0 CHECK (intentos >= 0),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  CHECK (tipo <> 'nota_credito' OR factura_origen_id IS NOT NULL),
  FOREIGN KEY (tenant_id, sale_id) REFERENCES sales (tenant_id, id),
  FOREIGN KEY (tenant_id, factura_origen_id) REFERENCES invoices (tenant_id, id)
);
CREATE UNIQUE INDEX ux_invoices_numero ON invoices (tenant_id, tipo, coalesce(prefijo,''), numero);
CREATE INDEX ix_invoices_sale ON invoices (tenant_id, sale_id);
CREATE INDEX ix_invoices_estado ON invoices (tenant_id, estado) WHERE estado IN ('emitida','enviada','rechazada','error');
CREATE INDEX ix_invoices_pendientes ON invoices (proximo_intento) WHERE estado IN ('emitida','enviada') AND tipo <> 'interna';
-- Una sola factura electrónica VIVA por venta (si una se rechaza, se corrige o se emite otra). Frena duplicados concurrentes.
CREATE UNIQUE INDEX ux_invoice_one_electronic ON invoices (tenant_id, sale_id) WHERE tipo = 'electronica' AND estado IN ('emitida','enviada','aceptada');

CREATE TABLE notifications (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants(id),
  canal           text NOT NULL CHECK (canal IN ('whatsapp','email','sms','interna')),
  destinatario    text NOT NULL,
  plantilla       text NOT NULL,
  payload         jsonb NOT NULL DEFAULT '{}',
  estado          text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','enviada','fallida','cancelada')),
  programada_para timestamptz NOT NULL DEFAULT now(),
  enviada_en      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_notifications_cola ON notifications (programada_para) WHERE estado = 'pendiente';
CREATE INDEX ix_notifications_tenant ON notifications (tenant_id, created_at DESC);

-- ---------------------------------------------------------------- Inventario (módulo `inventory`)
-- Productos del negocio (insumos y artículos de venta). El stock solo cambia por movimientos: el libro de movimientos es de solo agregar
-- (la API no puede modificarlo ni borrarlo, ver rls.sql); un error se corrige con un ajuste nuevo, nunca editando el pasado.
CREATE TABLE products (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants(id),
  nombre       text NOT NULL,
  sku          text,
  costo        numeric(12,2) NOT NULL DEFAULT 0 CHECK (costo >= 0),
  precio_venta numeric(12,2) NOT NULL DEFAULT 0 CHECK (precio_venta >= 0),
  stock_actual integer NOT NULL DEFAULT 0 CHECK (stock_actual >= 0),
  stock_minimo integer NOT NULL DEFAULT 0 CHECK (stock_minimo >= 0),
  activo       boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  deleted_at   timestamptz,
  UNIQUE (tenant_id, id)
);
CREATE UNIQUE INDEX ux_products_sku ON products (tenant_id, lower(sku)) WHERE sku IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX ix_products_nombre ON products (tenant_id, lower(nombre));

CREATE TABLE stock_movements (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id        uuid NOT NULL REFERENCES tenants(id),
  product_id       uuid NOT NULL,
  tipo             text NOT NULL CHECK (tipo IN ('entrada','salida','ajuste')),
  delta            integer NOT NULL CHECK (delta <> 0),
  stock_resultante integer NOT NULL CHECK (stock_resultante >= 0),
  costo_unitario   numeric(12,2) CHECK (costo_unitario >= 0),
  motivo           text,
  user_id          uuid,
  created_at       timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, product_id) REFERENCES products (tenant_id, id)
);
CREATE INDEX ix_stock_mov_producto ON stock_movements (tenant_id, product_id, created_at DESC);

-- ---- Trigger updated_at en toda tabla que tenga la columna ----
DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT c.table_name FROM information_schema.columns c
           JOIN information_schema.tables tb ON tb.table_name = c.table_name AND tb.table_schema = c.table_schema
           WHERE c.table_schema = 'public' AND c.column_name = 'updated_at' AND tb.table_type = 'BASE TABLE'
  LOOP
    EXECUTE format('CREATE TRIGGER trg_%I_updated_at BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
  END LOOP;
END $$;
