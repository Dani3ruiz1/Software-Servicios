// Paneles del Super Admin: Cobros, Usuarios, Auditoría, Salud del sistema y Módulos. Usa los ayudantes de plataforma.js y plataforma-tablero.js.
const fechaHora = (iso) => (iso ? new Intl.DateTimeFormat("es-CO", { timeZone: "America/Bogota", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }).format(new Date(iso)) : "—");
const chip = (txt, clase = "") => h("span", { class: "chip-p " + clase }, txt);

// Tabla con búsqueda/paginación: `pedir(page)` devuelve {items,total,page,page_size}; `cols` = [[titulo, (fila)=>nodo, clase?]]
function tablaPaginada(cont, cols, pedir, vacio) {
  let page = 1;
  const caja = h("div"), pie = h("div", { class: "paginacion" }), msg = h("div", { "aria-live": "polite" });
  cont.replaceChildren(msg, caja, pie);
  async function pintar() {
    let r; try { r = await pedir(page); } catch (e) { return aviso(msg, errTexto(e)); }
    aviso(msg, "");
    caja.replaceChildren(r.items.length
      ? h("div", { class: "tabla-ancha" }, h("table", { class: "reporte" }, h("thead", {}, h("tr", {}, cols.map(([t, , c]) => h("th", { class: c ?? "" }, t)))),
        h("tbody", {}, r.items.map((f) => h("tr", {}, cols.map(([, fn, c]) => h("td", { class: c ?? "" }, fn(f))))))))
      : h("div", { class: "vacio" }, h("b", {}, vacio[0]), vacio[1]));
    const d = r.total ? (r.page - 1) * r.page_size + 1 : 0, hasta = Math.min(r.page * r.page_size, r.total);
    pie.replaceChildren(h("span", { class: "sub" }, r.total ? `${d}–${hasta} de ${fnum(r.total)}` : ""),
      h("button", { class: "mini", type: "button", disabled: r.page <= 1, onclick: () => { page--; pintar(); } }, "Anterior"),
      h("button", { class: "mini", type: "button", disabled: hasta >= r.total, onclick: () => { page++; pintar(); } }, "Siguiente"));
  }
  pintar();
  return { recargar: () => { page = 1; pintar(); }, repintar: pintar };
}
const enlaceNegocio = (nombre) => h("a", { href: "#", class: "enlace-n", onclick: (e) => { e.preventDefault(); irANegocio(nombre); } }, nombre);
const debounce = (fn) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), 300); }; };

// ================================================================== COBROS
async function cargarCobros() {
  const cont = vistas.cobros, f = { desde: "", hasta: "", metodo: "" };
  const lista = h("div"), kpis = h("div", { class: "kpis" });
  const parametros = (page) => { const q = new URLSearchParams({ page: String(page), page_size: "15" }); for (const [k, v] of Object.entries(f)) if (v) q.set(k, v); return q; };
  let tabla;
  const inputs = {
    desde: h("input", { type: "date", "aria-label": "Desde" }), hasta: h("input", { type: "date", "aria-label": "Hasta" }),
    metodo: h("input", { type: "search", placeholder: "Método (efectivo, transferencia…)", "aria-label": "Método de pago", maxlength: 40 }),
  };
  const aplicar = () => { for (const k of Object.keys(f)) f[k] = inputs[k].value.trim(); tabla.recargar(); };
  Object.values(inputs).forEach((el) => el.addEventListener("input", debounce(aplicar)));
  cont.replaceChildren(h("div", { class: "titulo" }, h("h2", {}, "Cobros"), h("span", { class: "sub" }, "Pagos de suscripción de todos los negocios")), kpis,
    h("div", { class: "buscador" }, campo("Desde", inputs.desde), campo("Hasta", inputs.hasta), inputs.metodo), lista);
  let ultimo = null;
  tabla = tablaPaginada(lista, [
    ["Fecha", (p) => fecha(p.fecha_pago)], ["Negocio", (p) => enlaceNegocio(p.negocio)], ["Periodo cubierto", (p) => `${fecha(p.periodo_desde)} → ${fecha(p.periodo_hasta)}`],
    ["Método", (p) => p.metodo], ["Referencia", (p) => p.referencia ?? "—"], ["Monto", (p) => h("b", {}, money(p.monto)), "num"],
  ], async (page) => {
    const r = await api(`/platform/cobros?${parametros(page)}`); ultimo = r.resumen;
    const var_ = num(ultimo.mes_anterior) ? Math.round((100 * (num(ultimo.mes_actual) - num(ultimo.mes_anterior))) / num(ultimo.mes_anterior)) : null;
    kpis.replaceChildren(
      kpi("💰", "Cobrado este mes", num(ultimo.mes_actual), money, var_ == null ? "Sin cobros el mes pasado" : `${var_ >= 0 ? "▲" : "▼"} ${Math.abs(var_)} % frente al mes pasado (${money(ultimo.mes_anterior)})`),
      kpi("🧾", "Total del filtro", num(ultimo.total_periodo), money, `${fnum(ultimo.cantidad)} pagos`),
      kpi("⏰", "Suscripciones vencidas", ultimo.vencidos, fnum, ultimo.vencidos ? "Negocios por cobrar o suspender" : "Todos al día"));
    return r.pagos;
  }, ["Aún no hay pagos", "Se registran desde el detalle de cada negocio."]);
}

// ================================================================== USUARIOS
async function cargarUsuarios() {
  const cont = vistas.usuarios, f = { q: "", rol: "" };
  const q = h("input", { type: "search", placeholder: "Buscar por nombre, correo o negocio", "aria-label": "Buscar usuario" });
  const rol = h("select", { "aria-label": "Rol" }, h("option", { value: "" }, "Todos los roles"), [["admin", "Administrador"], ["recepcionista", "Recepción"], ["profesional", "Barbero / profesional"]].map(([v, t]) => h("option", { value: v }, t)));
  const lista = h("div"); let tabla;
  const aplicar = () => { f.q = q.value.trim(); f.rol = rol.value; tabla.recargar(); };
  q.addEventListener("input", debounce(aplicar)); rol.addEventListener("change", aplicar);
  cont.replaceChildren(h("div", { class: "titulo" }, h("h2", {}, "Usuarios"), h("span", { class: "sub" }, "Cuentas de todos los negocios · solo lectura")), h("div", { class: "buscador" }, q, rol), lista);
  const msg = h("div", { "aria-live": "polite", style: "margin-bottom:1rem" }); cont.prepend(msg);
  async function accion(u, ruta, metodo, cuerpo, pregunta, ok) {
    if (!confirm(pregunta)) return;
    try { await api(`/platform/usuarios/${u.id}/${ruta}`, { method: metodo, ...(cuerpo ? { body: JSON.stringify(cuerpo) } : {}) }); aviso(msg, ok, true); tabla.repintar(); } catch (e) { aviso(msg, errTexto(e)); }
  }
  async function claveNueva(u) {
    if (!confirm(`¿Generar una clave temporal para ${u.nombre}? Se cerrarán sus sesiones y la clave actual dejará de servir.`)) return;
    try {
      const r = await api(`/platform/usuarios/${u.id}/restablecer-clave`, { method: "POST" });
      msg.replaceChildren(h("div", { class: "aviso ok", role: "alert" }, h("div", {}, `Clave temporal de ${u.nombre} (${u.email}). Se muestra una sola vez; entrégala por un canal seguro:`),
        h("code", { class: "clave-t" }, r.clave_temporal), " ", h("button", { class: "mini", type: "button", onclick: (ev) => { navigator.clipboard?.writeText(r.clave_temporal); ev.target.textContent = "Copiada"; } }, "Copiar")));
      tabla.repintar();
    } catch (e) { aviso(msg, errTexto(e)); }
  }
  const ROL = { admin: "Administrador", recepcionista: "Recepción", profesional: "Profesional" };
  tabla = tablaPaginada(lista, [
    ["Nombre", (u) => h("b", {}, u.nombre)], ["Correo", (u) => u.email], ["Negocio", (u) => enlaceNegocio(u.negocio)], ["Rol", (u) => ROL[u.rol] ?? u.rol],
    ["Estado", (u) => h("span", {}, !u.activo ? chip("Inactivo", "mal") : u.bloqueado ? chip("Bloqueado", "mal") : chip("Activo", "ok"), " ", u.con_2fa ? chip("2FA", "ok") : u.rol === "admin" ? chip("sin 2FA", "ojo") : "")],
    ["Último ingreso", (u) => (u.ultimo_login ? fechaHora(u.ultimo_login) : "Nunca")],
    ["Acciones", (u) => h("div", { class: "acciones-u" },
      u.bloqueado ? h("button", { class: "mini", type: "button", onclick: () => accion(u, "desbloquear", "POST", null, `¿Desbloquear a ${u.nombre}?`, "Cuenta desbloqueada.") }, "Desbloquear") : null,
      h("button", { class: "mini", type: "button", onclick: () => accion(u, "activo", "PUT", { activo: !u.activo }, u.activo ? `¿Desactivar a ${u.nombre}? Se cerrarán sus sesiones.` : `¿Activar a ${u.nombre}?`, u.activo ? "Cuenta desactivada." : "Cuenta activada.") }, u.activo ? "Desactivar" : "Activar"),
      h("button", { class: "mini", type: "button", onclick: () => claveNueva(u) }, "Clave nueva"))],
  ], (page) => { const p = new URLSearchParams({ page: String(page), page_size: "15" }); if (f.q) p.set("q", f.q); if (f.rol) p.set("rol", f.rol); return api(`/platform/usuarios?${p}`); },
  ["Ningún usuario coincide", "Prueba con otro nombre o rol."]);
}

// ================================================================== AUDITORÍA
const ACTOR = { platform_user: "Súper admin", user: "Usuario del negocio", system: "Sistema" };
async function cargarAuditoria() {
  const cont = vistas.auditoria, f = { accion: "", actor_tipo: "" };
  const accion = h("input", { type: "search", placeholder: "Filtrar por acción (login, impersonate, pago…)", "aria-label": "Acción", maxlength: 60 });
  const actor = h("select", { "aria-label": "Quién" }, h("option", { value: "" }, "Todos"), Object.entries(ACTOR).map(([v, t]) => h("option", { value: v }, t)));
  const lista = h("div"); let tabla;
  const aplicar = () => { f.accion = accion.value.trim(); f.actor_tipo = actor.value; tabla.recargar(); };
  accion.addEventListener("input", debounce(aplicar)); actor.addEventListener("change", aplicar);
  cont.replaceChildren(h("div", { class: "titulo" }, h("h2", {}, "Auditoría"), h("span", { class: "sub" }, "Quién hizo qué, en toda la plataforma. No se puede editar ni borrar.")), h("div", { class: "buscador" }, accion, actor), lista);
  tabla = tablaPaginada(lista, [
    ["Cuándo", (a) => fechaHora(a.fecha)], ["Quién", (a) => chip(ACTOR[a.actor_tipo] ?? a.actor_tipo, a.actor_tipo === "platform_user" ? "ojo" : "")], ["Negocio", (a) => (a.negocio ? enlaceNegocio(a.negocio) : "—")],
    ["Acción", (a) => h("b", {}, a.accion)], ["Sobre", (a) => a.entidad], ["IP", (a) => a.ip ?? "—"],
  ], (page) => { const p = new URLSearchParams({ page: String(page), page_size: "20" }); for (const [k, v] of Object.entries(f)) if (v) p.set(k, v); return api(`/platform/auditoria?${p}`); },
  ["Sin registros", "Cambia los filtros."]);
}

// ================================================================== SALUD
async function cargarSalud() {
  const cont = vistas.salud; let s;
  try { s = await api("/platform/salud"); } catch (e) { return cont.replaceChildren(h("div", { class: "aviso" }, errTexto(e))); }
  const alerta = (ok, bien, mal) => h("li", { class: "salud-fila " + (ok ? "ok" : "mal") }, h("span", { "aria-hidden": "true" }, ok ? "✅" : "⚠️"), h("span", {}, ok ? bien : mal));
  const total7 = s.notificaciones_7d.reduce((a, x) => a + x.n, 0), fall = s.notificaciones_7d.find((x) => x.clave === "fallida")?.n ?? 0;
  const ESTADO_N = { enviada: "var(--ok)", pendiente: "var(--sol)", fallida: "var(--acento)", cancelada: "var(--linea)" };
  cont.replaceChildren(
    h("div", { class: "titulo" }, h("h2", {}, "Salud del sistema"), h("button", { class: "mini", type: "button", onclick: cargarSalud }, "Actualizar")),
    h("div", { class: "kpis" },
      kpi("📨", "Mensajes (7 días)", total7, fnum, fall ? `${fnum(fall)} fallidos` : "Ninguno fallido"),
      kpi("⏳", "Cola atrasada", s.notificaciones_pendientes_viejas, fnum, "Pendientes con más de 1 h de retraso"),
      kpi("🔒", "Cuentas bloqueadas", s.usuarios_bloqueados, fnum, "Por intentos fallidos de ingreso"),
      kpi("🗄️", "Base de datos", s.base_de_datos_mb, (v) => `${new Intl.NumberFormat("es-CO", { maximumFractionDigits: 1 }).format(v)} MB`, "Tamaño actual")),
    h("div", { class: "rejilla-g" },
      h("section", { class: "card-g ancho-2" }, h("header", {}, h("h3", {}, "Revisión rápida"), h("small", {}, "Lo que conviene mirar hoy")),
        h("ul", { class: "salud-lista" },
          alerta(!s.notificaciones_pendientes_viejas, "La cola de mensajes está al día.", `${fnum(s.notificaciones_pendientes_viejas)} mensajes llevan más de 1 h esperando: revisa el worker de Celery y Redis.`),
          alerta(!s.negocios_con_fallos.length, "Ningún negocio con mensajes fallidos esta semana.", `${s.negocios_con_fallos.length} negocio(s) con mensajes fallidos (abajo): suele ser WhatsApp mal conectado.`),
          alerta(!s.negocios_sin_admin_activo, "Todos los negocios tienen un administrador activo.", `${s.negocios_sin_admin_activo} negocio(s) sin administrador activo: nadie puede entrar a configurarlos.`),
          alerta(!s.suscripciones_vencidas, "No hay suscripciones vencidas.", `${s.suscripciones_vencidas} suscripción(es) vencida(s): contáctalos desde Negocios o Cobros.`),
          alerta(!s.usuarios_sin_2fa_admin, "Todos los administradores tienen 2FA.", `${s.usuarios_sin_2fa_admin} administrador(es) sin verificación en dos pasos (recomendable activarla).`))),
      h("section", { class: "card-g" }, h("header", {}, h("h3", {}, "Mensajes por estado"), h("small", {}, "Últimos 7 días")),
        s.notificaciones_7d.length ? barrasH(s.notificaciones_7d.map((x) => ({ etq: x.clave, v: x.n, color: ESTADO_N[x.clave] })), fnum) : h("div", { class: "vacio" }, "Sin mensajes en la semana.")),
      h("section", { class: "card-g" }, h("header", {}, h("h3", {}, "Integraciones"), h("small", {}, "Por tipo y estado")),
        s.integraciones.length ? barrasH(s.integraciones.map((x) => ({ etq: x.clave, v: x.n })), fnum) : h("div", { class: "vacio" }, "Ningún negocio ha conectado integraciones.")),
      h("section", { class: "card-g ancho-2" }, h("header", {}, h("h3", {}, "Negocios con mensajes fallidos"), h("small", {}, "Últimos 7 días")),
        s.negocios_con_fallos.length ? h("div", { class: "tabla-ancha" }, h("table", { class: "reporte" }, h("tbody", {}, s.negocios_con_fallos.map((n) => h("tr", {}, h("td", {}, enlaceNegocio(n.negocio)), h("td", { class: "num" }, `${fnum(n.fallidas)} fallidos`)))))) : h("div", { class: "vacio" }, "Ninguno 🎉"))));
}

// ================================================================== MÓDULOS (precio base de cada módulo)
async function cargarModulos() {
  const cont = vistas.modulos; let mods;
  try { mods = await api("/platform/modules"); } catch (e) { return cont.replaceChildren(h("div", { class: "aviso" }, errTexto(e))); }
  const msg = h("div", { "aria-live": "polite" });
  cont.replaceChildren(h("div", { class: "titulo" }, h("h2", {}, "Módulos")), h("p", { class: "sub", style: "margin-bottom:1rem" }, "Funciones que se pueden activar por negocio. Cambiar el precio base aplica a nuevas asignaciones; lo ya pactado no cambia."), msg,
    ...mods.map((m) => {
      const nombre = h("input", { value: m.nombre, maxlength: 60, "aria-label": "Nombre" }), precio = h("input", { type: "number", min: 0, step: 1000, value: Math.round(num(m.precio_mensual_base)), "aria-label": "Precio mensual base" });
      return h("section", { class: "panelito", style: "margin-top:1rem" }, h("div", { class: "form-fila" }, campo(`Nombre (${m.codigo})`, nombre), campo("Precio mensual base (COP)", precio),
        h("button", { class: "btn", type: "button", onclick: async (ev) => {
          ev.target.disabled = true;
          try { await api(`/platform/modules/${m.codigo}`, { method: "PATCH", body: JSON.stringify({ nombre: nombre.value.trim(), precio_mensual_base: String(num(precio.value)) }) }); aviso(msg, `Módulo "${nombre.value.trim()}" guardado.`, true); }
          catch (err) { aviso(msg, errTexto(err)); }
          ev.target.disabled = false;
        } }, "Guardar")));
    }));
}
