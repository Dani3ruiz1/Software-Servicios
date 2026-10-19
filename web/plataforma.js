// Panel del dueño de la plataforma: resumen (MRR), negocios (plan, módulos, pagos, estado, soporte) y planes. Sesión propia (no mezcla con la de un negocio).
const $ = (id) => document.getElementById(id);
const API = "/api/v1";
const store = {
  get: (k) => { try { return sessionStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* sin almacenamiento */ } },
};
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (typeof v === "boolean" && k.startsWith("aria-")) el.setAttribute(k, String(v));   // aria-pressed="true" (no vacío): los estilos lo buscan así
    else if (v === true) el.setAttribute(k, "");
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}
const aviso = (el, msg, ok = false) => el.replaceChildren(msg ? h("div", { class: "aviso" + (ok ? " ok" : ""), role: "alert" }, msg) : "");
const campo = (etiqueta, input) => h("label", { class: "campo" }, h("span", {}, etiqueta), input);
const errTexto = (e) => { const c = e.data?.details?.campos?.[0]; return e.code === "VALIDATION_ERROR" && c ? `${c.campo}: ${c.mensaje}` : e.message; };
const money = (v) => new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 }).format(Number(v));
const num = (v) => Number(v) || 0;
const hoyLocal = () => new Intl.DateTimeFormat("en-CA").format(new Date());
const sumarDias = (f, n) => new Date(new Date(f + "T12:00:00Z").getTime() + n * 864e5).toISOString().slice(0, 10);
const fecha = (f) => (f ? new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" }).format(new Date(f + "T12:00:00Z")) : "—");

// ------------------------------------------------------------------ llamadas con renovación de sesión
let renovando = null;
async function renovar() {
  const r = await fetch(`${API}/auth/refresh`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refresh_token: store.get("prt") }) });
  if (!r.ok) throw new Error("sesión vencida");
  const t = await r.json(); store.set("pat", t.access_token); store.set("prt", t.refresh_token);
}
async function api(path, opts = {}, reintento = true) {
  const r = await fetch(API + path, { ...opts, headers: { "content-type": "application/json", Authorization: `Bearer ${store.get("pat")}`, ...(opts.headers || {}) } });
  if (r.status === 401 && reintento) {
    try { await (renovando ??= renovar().finally(() => { renovando = null; })); } catch { salir(); throw new Error("Tu sesión venció. Entra de nuevo."); }
    return api(path, opts, false);
  }
  const data = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw Object.assign(new Error(data?.message || "No se pudo completar la acción."), { code: data?.code, status: r.status, data });
  return data;
}

// ------------------------------------------------------------------ sesión
$("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target), btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/auth/platform/login`, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: String(fd.get("email")).trim(), password: String(fd.get("password")), totp_code: String(fd.get("totp") || "").trim() || null }) });
    const d = await r.json().catch(() => null);
    if (!r.ok) {
      if (d?.code === "TOTP_REQUIRED") { $("totpCampo").hidden = false; throw new Error("Escribe el código de tu app de verificación."); }
      throw new Error(d?.code === "ACCOUNT_LOCKED" ? "Cuenta bloqueada por intentos fallidos. Intenta más tarde." : d?.message || "No pudimos entrar.");
    }
    store.set("pat", d.access_token); store.set("prt", d.refresh_token); e.target.reset(); aviso($("loginMsg"), ""); await abrir();
  } catch (err) { aviso($("loginMsg"), err.message); }
  btn.disabled = false;
});
$("salir").addEventListener("click", async () => { try { await api("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: store.get("prt") }) }, false); } catch { /* ya vencida */ } salir(); });
function salir() { store.set("pat", null); store.set("prt", null); $("app").hidden = true; $("entrada").hidden = false; }
async function abrir() {
  $("quien").textContent = "Dueño de la plataforma";
  $("entrada").hidden = true; $("app").hidden = false; vista("resumen");
}

const vistas = Object.fromEntries([...document.querySelectorAll("main[id^=vista-]")].map((el) => [el.id.slice(6), el]));
function vista(v) {
  for (const [k, el] of Object.entries(vistas)) el.hidden = k !== v;
  document.querySelectorAll(".tabs button").forEach((b) => (b.dataset.vista === v ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
  ({ resumen: cargarResumen, negocios: cargarNegocios, planes: cargarPlanes, cobros: cargarCobros, usuarios: cargarUsuarios, auditoria: cargarAuditoria, salud: cargarSalud, modulos: cargarModulos })[v]();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => vista(b.dataset.vista)));

const ESTADO_CLASE = { activa: "confirmada", por_vencer: "pendiente", vencida: "cancelada", suspendida: "cancelada", bloqueada: "cancelada" };
const ESTADO_TXT = { activa: "Activa", por_vencer: "Por vencer", vencida: "Vencida", suspendida: "Suspendida", bloqueada: "Bloqueada" };
const chipEstado = (e) => h("span", { class: `estado ${ESTADO_CLASE[e] ?? ""}`, style: "grid-column:auto;grid-row:auto" }, ESTADO_TXT[e] ?? e);

// ================================================================== NEGOCIOS
const ng = { q: "", estado: "", page: 1, abierto: null, planes: [], modulos: [] };
async function cargarNegocios() {
  const cont = vistas.negocios;
  try { [ng.planes, ng.modulos] = await Promise.all([api("/platform/plans"), api("/platform/modules")]); } catch (e) { return cont.replaceChildren(h("div", { class: "aviso" }, errTexto(e))); }
  const buscar = h("input", { id: "ngBuscar", type: "search", placeholder: "Buscar negocio por nombre o código", value: ng.q, "aria-label": "Buscar negocio" });
  let t; buscar.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { ng.q = buscar.value.trim(); ng.page = 1; ng.abierto = null; pintarNegocios(); }, 300); });
  const filtro = h("select", { "aria-label": "Estado", onchange: (e) => { ng.estado = e.target.value; ng.page = 1; pintarNegocios(); } },
    h("option", { value: "" }, "Todos los estados"), Object.entries(ESTADO_TXT).map(([k, v]) => h("option", { value: k, selected: ng.estado === k }, v)));
  cont.replaceChildren(h("div", { class: "titulo" }, h("h2", {}, "Negocios"), h("button", { class: "btn", type: "button", onclick: () => $("ngAlta").replaceChildren(formNegocio()) }, "Nuevo negocio")),
    h("div", { id: "ngAlta" }), h("div", { class: "buscador" }, buscar, filtro), h("div", { id: "ngMsg", "aria-live": "polite" }), h("div", { id: "ngLista" }), h("div", { id: "ngPie", class: "paginacion" }));
  pintarNegocios();
}

async function pintarNegocios() {
  const q = new URLSearchParams({ page: String(ng.page), page_size: "12" });
  if (ng.q) q.set("q", ng.q); if (ng.estado) q.set("estado", ng.estado);
  let r; try { r = await api(`/platform/tenants?${q}`); } catch (e) { return aviso($("ngMsg"), errTexto(e)); }
  $("ngLista").replaceChildren(...(r.items.length ? r.items.map(filaNegocio) : [h("div", { class: "vacio" }, h("b", {}, "Ningún negocio coincide"), "Prueba con otro nombre o estado.")]));
  const desde = r.total ? (r.page - 1) * r.page_size + 1 : 0, hasta = Math.min(r.page * r.page_size, r.total);
  $("ngPie").replaceChildren(h("span", { class: "sub" }, r.total ? `${desde}–${hasta} de ${r.total}` : ""),
    h("button", { class: "mini", type: "button", disabled: r.page <= 1, onclick: () => { ng.page--; ng.abierto = null; pintarNegocios(); } }, "Anterior"),
    h("button", { class: "mini", type: "button", disabled: hasta >= r.total, onclick: () => { ng.page++; ng.abierto = null; pintarNegocios(); } }, "Siguiente"));
}

function filaNegocio(t) {
  const abierto = ng.abierto === t.id;
  const vence = t.dias_restantes == null ? "" : t.dias_restantes < 0 ? `venció hace ${-t.dias_restantes} d` : t.dias_restantes === 0 ? "vence hoy" : `vence en ${t.dias_restantes} d`;
  const art = h("article", { class: "cliente" + (abierto ? " abierto" : "") },
    h("button", { class: "cliente-cab", type: "button", "aria-expanded": abierto, onclick: () => { ng.abierto = abierto ? null : t.id; pintarNegocios(); } },
      h("b", {}, t.nombre), h("span", { class: "sub" }, [t.plan ?? "Sin plan", t.fecha_fin ? `hasta ${fecha(t.fecha_fin)}` : null, vence || null].filter(Boolean).join(" · ")), h("span", {}, chipEstado(t.estado))));
  if (abierto) art.append(detalleNegocio(t.id));
  return art;
}

const refrescar = () => pintarNegocios();

function detalleNegocio(id) {
  const cuerpo = h("div", { class: "panelito" }, h("p", { class: "sub" }, "Cargando…"));
  const msg = h("div", { "aria-live": "polite" });
  Promise.all([api(`/platform/tenants/${id}`), api(`/platform/tenants/${id}/payments?page_size=8&sort=-fecha_pago`)]).then(([d, pagos]) => {
    const sus = d.suscripcion;
    // --- plan
    const selPlan = h("select", { "aria-label": "Plan" }, ng.planes.filter((p) => p.activo).map((p) => h("option", { value: p.id, selected: p.id === sus?.plan_id }, `${p.nombre} · ${money(p.precio_mensual)}`)));
    const precioManual = h("input", { type: "number", min: 0, step: 1000, placeholder: "Dejar vacío = precio del plan", "aria-label": "Precio manual" });
    const cambiarPlan = h("button", { class: "btn", type: "button", onclick: async (ev) => {
      if (!confirm("¿Cambiar el plan de este negocio? Los módulos incluidos se ajustan al plan nuevo.")) return;
      ev.target.disabled = true;
      try { await api(`/platform/tenants/${id}/plan`, { method: "PUT", body: JSON.stringify({ plan_id: selPlan.value, precio_plan_aplicado: precioManual.value === "" ? null : String(num(precioManual.value)) }) }); await refrescar(); aviso($("ngMsg"), "Plan actualizado.", true); }
      catch (err) { ev.target.disabled = false; aviso(msg, errTexto(err)); }
    } }, "Cambiar plan");
    // --- módulos
    const mods = h("div", { class: "checks" }, d.modulos.map((m) => h("label", {}, h("input", { type: "checkbox", checked: m.activo, onchange: async (e) => {
      e.target.disabled = true;
      try { await api(`/platform/tenants/${id}/modules/${m.codigo}`, { method: "PUT", body: JSON.stringify({ activo: e.target.checked }) }); await refrescar(); aviso($("ngMsg"), `Módulo "${m.nombre}" ${e.target.checked ? "activado" : "desactivado"}.`, true); }
      catch (err) { e.target.checked = !e.target.checked; e.target.disabled = false; aviso(msg, errTexto(err)); }
    } }), `${m.nombre}${m.origen === "manual" ? " (manual)" : ""}`)));
    // --- pago
    const hoy = hoyLocal(), base = sus && sus.fecha_fin >= hoy ? sus.fecha_fin : hoy;
    const fp = h("form", { class: "form-fila", novalidate: true, onsubmit: async (e) => {
      e.preventDefault();
      const f = new FormData(e.target), v = (k) => String(f.get(k)).trim();
      if (num(v("monto")) <= 0 || !v("desde") || !v("hasta") || v("hasta") < v("desde")) return aviso(msg, "Revisa el monto y que el periodo termine después de empezar.");
      try {
        const r = await api(`/platform/tenants/${id}/payments`, { method: "POST", body: JSON.stringify({ monto: String(num(v("monto"))), fecha_pago: v("fecha"), periodo_desde: v("desde"), periodo_hasta: v("hasta"), metodo: v("metodo"), referencia: v("ref") || null }) });
        await refrescar(); aviso($("ngMsg"), `Pago registrado. El negocio queda ${ESTADO_TXT[r.estado_negocio] ?? r.estado_negocio}, con acceso hasta ${fecha(r.fecha_fin)}.`, true);
      } catch (err) { aviso(msg, errTexto(err)); }
    } },
      campo("Monto (COP)", h("input", { name: "monto", type: "number", min: 0, step: 1000, value: num(d.facturacion.total_mensual) })), campo("Fecha del pago", h("input", { name: "fecha", type: "date", value: hoy })),
      campo("Periodo desde", h("input", { name: "desde", type: "date", value: base })), campo("Periodo hasta", h("input", { name: "hasta", type: "date", value: sumarDias(base, 30) })),
      campo("Método", h("input", { name: "metodo", value: "transferencia" })), campo("Referencia (opcional)", h("input", { name: "ref", maxlength: 80 })), h("button", { class: "btn", type: "submit" }, "Registrar pago"));
    // --- estado y soporte
    const accion = (a, texto, aviso2) => h("button", { class: "mini", type: "button", onclick: async () => {
      if (!confirm(`¿${texto} este negocio?`)) return;
      try { await api(`/platform/tenants/${id}/estado/${a}`, { method: "POST" }); await refrescar(); aviso($("ngMsg"), aviso2, true); } catch (err) { aviso(msg, errTexto(err)); }
    } }, texto);
    cuerpo.replaceChildren(
      h("div", { class: "conteo" }, h("span", {}, h("b", {}, chipEstado(d.estado)), ""), h("span", {}, h("b", {}, `${d.usuarios_activos}`), "usuarios activos"),
        h("span", {}, h("b", {}, sus ? fecha(sus.fecha_fin) : "—"), "acceso hasta"), h("span", {}, h("b", {}, money(d.facturacion.total_mensual)), "al mes")),
      h("p", { class: "sub" }, [d.contacto_nombre, d.contacto_email, d.contacto_telefono, d.nit ? `NIT ${d.nit}` : null].filter(Boolean).join(" · ") || "Sin datos de contacto"),
      h("h3", {}, "Plan"), h("div", { class: "form-fila" }, campo("Plan", selPlan), campo("Precio acordado (opcional)", precioManual), cambiarPlan),
      h("h3", { style: "margin-top:.4rem" }, "Módulos"), mods,
      h("h3", { style: "margin-top:.4rem" }, "Cobro mensual"), ...d.facturacion.items.map((i) => h("div", { class: "linea-cobro" }, h("span", {}, `${i.concepto}${i.origen_precio === "manual" ? " (precio manual)" : ""}`), h("span", { class: "precio" }, money(i.precio)))),
      h("div", { class: "linea-cobro total" }, h("span", {}, "Total mensual"), h("span", { class: "precio" }, money(d.facturacion.total_mensual))),
      h("h3", { style: "margin-top:.4rem" }, "Registrar un pago"), fp,
      h("h3", { style: "margin-top:.4rem" }, "Pagos recientes"), ...(pagos.items.length ? pagos.items.map((p) => h("div", { class: "linea-cobro" }, h("span", {}, `${fecha(p.fecha_pago)} · ${p.metodo}${p.referencia ? " · " + p.referencia : ""} · cubre ${fecha(p.periodo_desde)} a ${fecha(p.periodo_hasta)}`), h("span", { class: "precio" }, money(p.monto)))) : [h("p", { class: "sub" }, "Sin pagos registrados.")]),
      msg,
      h("div", { class: "envolver", style: "flex-wrap:wrap; margin-top:.4rem" }, accion("activar", "Activar", "Negocio activado."), accion("suspender", "Suspender", "Negocio suspendido."), accion("bloquear", "Bloquear", "Negocio bloqueado."),
        h("button", { class: "mini principal", type: "button", onclick: () => entrarComoSoporte(id, msg) }, "Entrar como soporte")));
  }).catch((e) => cuerpo.replaceChildren(h("div", { class: "aviso" }, errTexto(e))));
  return cuerpo;
}

// Abre el panel del negocio en otra pestaña y le entrega el token de soporte por mensaje entre ventanas (el token nunca viaja en la URL).
async function entrarComoSoporte(id, msg) {
  try {
    const t = await api(`/platform/tenants/${id}/impersonate`, { method: "POST" });
    const w = window.open("panel.html#soporte", "_blank");
    if (!w) return aviso(msg, "El navegador bloqueó la ventana nueva. Permite ventanas emergentes para este sitio.");
    const oir = (ev) => { if (ev.origin === location.origin && ev.source === w && ev.data?.tipo === "listo") { w.postMessage({ tipo: "imp", token: t.access_token }, location.origin); removeEventListener("message", oir); } };
    addEventListener("message", oir); setTimeout(() => removeEventListener("message", oir), 30000);
    aviso(msg, "Sesión de soporte abierta en otra pestaña (queda auditada y dura poco).", true);
  } catch (err) { aviso(msg, errTexto(err)); }
}

// ---------------------------------------------------------------- alta de negocio
const TIPOS = [["barberia", "Barbería"], ["spa", "Spa"], ["unas", "Salón de uñas"], ["estetica", "Centro de estética"], ["salon", "Salón de belleza"]];
const slugDe = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
function formNegocio() {
  const msg = h("div", { "aria-live": "polite" });
  const nombre = h("input", { name: "nombre", maxlength: 120, required: true }), slug = h("input", { name: "slug", maxlength: 50, placeholder: "se llena solo" });
  nombre.addEventListener("input", () => { if (!slug.dataset.manual) slug.value = slugDe(nombre.value); }); slug.addEventListener("input", () => (slug.dataset.manual = "1"));
  return h("form", { class: "panelito", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const f = new FormData(e.target), v = (k) => String(f.get(k)).trim(), btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const t = await api("/platform/tenants", { method: "POST", body: JSON.stringify({ nombre: v("nombre"), slug: v("slug"), business_type: v("tipo"), plan_id: v("plan"), contacto_email: v("cemail") || null, contacto_telefono: v("ctel") || null,
        admin: { nombre: v("anombre"), email: v("aemail"), password: v("apass") } }) });
      $("ngAlta").replaceChildren(); ng.q = t.nombre; $("ngBuscar").value = t.nombre; ng.page = 1; ng.abierto = t.id; await pintarNegocios();
      aviso($("ngMsg"), `Negocio creado. Su dueño entra con el código "${t.slug}" y el correo que escribiste. Su enlace de reservas será ${location.origin}/r/${t.slug}; lo activa desde su panel, en Compartir.`, true);
    } catch (err) { btn.disabled = false; aviso(msg, errTexto(err)); }
  } },
    h("h3", {}, "Nuevo negocio"),
    h("div", { class: "form-fila" }, campo("Nombre del negocio", nombre), campo("Código en la URL (minúsculas y guiones)", slug),
      campo("Tipo de negocio", h("select", { name: "tipo" }, TIPOS.map(([k, t]) => h("option", { value: k }, t)))), campo("Plan", h("select", { name: "plan" }, ng.planes.filter((p) => p.activo).map((p) => h("option", { value: p.id }, `${p.nombre} · ${money(p.precio_mensual)}`))))),
    h("h3", { style: "font-size:1.05rem" }, "Quién administra el negocio"),
    h("div", { class: "form-fila" }, campo("Nombre", h("input", { name: "anombre", maxlength: 80 })), campo("Correo (será su usuario)", h("input", { name: "aemail", type: "email" })), campo("Contraseña inicial (mín. 10)", h("input", { name: "apass", type: "password", minlength: 10, autocomplete: "new-password" }))),
    h("div", { class: "form-fila" }, campo("Correo de contacto (opcional)", h("input", { name: "cemail", type: "email" })), campo("Teléfono de contacto (opcional)", h("input", { name: "ctel", type: "tel" }))),
    msg, h("div", { class: "envolver" }, h("button", { class: "btn", type: "submit" }, "Crear negocio"), h("button", { class: "btn btn-suave", type: "button", onclick: () => $("ngAlta").replaceChildren() }, "Cancelar")));
}

// ================================================================== PLANES
async function cargarPlanes() {
  const cont = vistas.planes; let planes, mods;
  try { [planes, mods] = await Promise.all([api("/platform/plans"), api("/platform/modules")]); } catch (e) { return cont.replaceChildren(h("div", { class: "aviso" }, errTexto(e))); }
  ng.planes = planes; ng.modulos = mods;
  cont.replaceChildren(h("div", { class: "titulo" }, h("h2", {}, "Planes")), h("p", { class: "sub", style: "margin-bottom:1rem" }, "Cambiar el precio o los módulos de un plan no altera el precio ya pactado de las suscripciones que existen."),
    h("div", { id: "plMsg", "aria-live": "polite" }), ...planes.map((p) => filaPlan(p, mods)));
}

function filaPlan(p, mods) {
  const i = { nombre: h("input", { value: p.nombre, maxlength: 60, "aria-label": "Nombre" }), precio: h("input", { type: "number", min: 0, step: 1000, value: Math.round(num(p.precio_mensual)), "aria-label": "Precio mensual" }),
    users: h("input", { type: "number", min: 1, value: p.max_usuarios, "aria-label": "Usuarios máximos" }), ia: h("input", { type: "number", min: 0, value: p.max_mensajes_ia_mes ?? "", placeholder: "sin IA", "aria-label": "Mensajes de IA al mes" }),
    activo: h("input", { type: "checkbox", checked: p.activo }) };
  const checks = mods.map((m) => h("label", {}, h("input", { type: "checkbox", value: m.codigo, checked: p.modulos.includes(m.codigo) }), m.nombre));
  const guardar = h("button", { class: "btn", type: "button", onclick: async (ev) => {
    ev.target.disabled = true;
    try {
      await api(`/platform/plans/${p.id}`, { method: "PATCH", body: JSON.stringify({ nombre: i.nombre.value.trim(), precio_mensual: String(num(i.precio.value)), max_usuarios: num(i.users.value), max_mensajes_ia_mes: i.ia.value === "" ? null : num(i.ia.value), activo: i.activo.checked,
        modulos: checks.map((c) => c.querySelector("input")).filter((x) => x.checked).map((x) => x.value) }) });
      await cargarPlanes(); aviso($("plMsg"), `Plan "${i.nombre.value.trim()}" guardado.`, true);
    } catch (err) { ev.target.disabled = false; aviso($("plMsg"), errTexto(err)); }
  } }, "Guardar plan");
  return h("section", { class: "panelito", style: "margin-top:1rem" },
    h("div", { class: "form-fila" }, campo("Nombre", i.nombre), campo("Precio mensual (COP)", i.precio), campo("Usuarios máximos", i.users), campo("Mensajes de IA al mes", i.ia), h("label", { class: "envolver sufijo" }, i.activo, "Plan activo")),
    h("div", {}, h("div", { class: "campo" }, h("span", {}, "Módulos incluidos")), h("div", { class: "checks" }, checks)), h("div", {}, guardar));
}

(async () => { if (store.get("pat")) { try { await api("/platform/dashboard"); await abrir(); return; } catch { salir(); return; } } $("entrada").hidden = false; })();
