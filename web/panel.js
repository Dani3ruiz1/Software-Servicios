// Panel del negocio: login + agenda del día. Habla con /api/v1 usando el token del negocio (el negocio sale del token).
const $ = (id) => document.getElementById(id);
const API = "/api/v1";
const st = { me: null, fecha: null, citas: [], bloqueos: [], modo: (() => { try { return localStorage.getItem("agenda.modo"); } catch { return null; } })() || (innerWidth < 760 ? "lista" : "equipo") };
const store = {
  get: (k) => { try { return sessionStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* sin almacenamiento: la sesión dura lo que dure la página */ } },
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

// ------------------------------------------------------------------ llamadas con renovación de sesión
let renovando = null;
async function renovar() {
  const rt = store.get("rt");
  if (!rt) throw new Error("sin sesión");
  const r = await fetch(`${API}/auth/refresh`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refresh_token: rt }) });
  if (!r.ok) throw new Error("sesión vencida");
  const t = await r.json();
  store.set("at", t.access_token); store.set("rt", t.refresh_token);   // el refresh rota: se guarda siempre el último
}
async function api(path, opts = {}, reintento = true) {
  const r = await fetch(API + path, { ...opts, headers: { "content-type": "application/json", Authorization: `Bearer ${store.get("at")}`, ...(opts.headers || {}) } });
  if (r.status === 401 && reintento) {
    try { await (renovando ??= renovar().finally(() => { renovando = null; })); } catch { salir(); throw new Error("Tu sesión venció. Entra de nuevo."); }
    return api(path, opts, false);
  }
  const data = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw Object.assign(new Error(data?.message || "No se pudo completar la acción."), { code: data?.code, status: r.status, data });
  return data;
}

// ------------------------------------------------------------------ fechas en la zona del negocio
const tz = () => st.me.tenant.zona_horaria;
const hoy = () => new Intl.DateTimeFormat("en-CA", { timeZone: tz() }).format(new Date());
const sumar = (f, n) => new Date(new Date(f + "T12:00:00Z").getTime() + n * 864e5).toISOString().slice(0, 10);
function desfase(f) {   // "+00:00" / "-05:00" de la zona del negocio en esa fecha
  const o = new Intl.DateTimeFormat("en", { timeZone: tz(), timeZoneName: "longOffset" }).formatToParts(new Date(f + "T12:00:00Z")).find((p) => p.type === "timeZoneName").value;
  return o === "GMT" ? "+00:00" : o.slice(3);
}
const hora = (iso) => new Intl.DateTimeFormat("en-US", { timeZone: tz(), hour: "numeric", minute: "2-digit", hour12: true }).format(new Date(iso)).toLowerCase();
const largo = (f) => new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" }).format(new Date(f + "T12:00:00Z"));
const money = (v) => new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 0 }).format(Number(v));

// ------------------------------------------------------------------ sesión
$("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target), btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/auth/login`, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ tenant_slug: String(fd.get("tenant")).trim(), email: String(fd.get("email")).trim(), password: String(fd.get("password")) }) });
    const d = await r.json().catch(() => null);
    if (!r.ok) throw new Error(d?.code === "ACCOUNT_LOCKED" ? "Cuenta bloqueada por intentos fallidos. Intenta más tarde." : d?.message || "No pudimos entrar.");
    store.set("at", d.access_token); store.set("rt", d.refresh_token);
    e.target.reset(); aviso($("loginMsg"), "");
    await abrir();
  } catch (err) { aviso($("loginMsg"), err.message); }
  btn.disabled = false;
});
$("salir").addEventListener("click", async () => {
  try { await api("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: store.get("rt") }) }, false); } catch { /* ya vencida */ }
  salir();
});
function salir() { store.set("at", null); store.set("rt", null); st.me = null; $("app").hidden = true; $("entrada").hidden = false; }

async function abrir() {
  st.me = await api("/auth/me");
  UI.aplicarTipo(st.me.tenant?.tipo, { colores: true });
  if (st.me.tenant?.bloqueado) {
    salir(); return aviso($("loginMsg"), `La suscripción de este negocio está vencida${st.me.tenant.motivo_bloqueo ? " (" + st.me.tenant.motivo_bloqueo + ")" : ""}. Contacta a soporte para reactivarla.`);
  }
  if (st.me.rol === "profesional") { location.replace("barbero.html"); return; }   // su agenda propia (misma sesión)
  if (typeof aplicarModulos === "function") aplicarModulos();   // oculta pestañas de módulos que el plan no incluye
  $("negocio").textContent = st.me.tenant?.nombre ?? "Panel";
  $("quien").textContent = st.me.impersonado ? `Soporte viendo como ${st.me.nombre}` : st.me.nombre;
  $("bannerSoporte").hidden = !st.me.impersonado;
  if (st.me.impersonado) $("bannerSoporte").textContent = "🛟 Sesión de soporte: estás viendo este negocio como su administrador. Todo queda registrado.";
  $("entrada").hidden = true; $("app").hidden = false;
  await ir(hoy());
}

// ------------------------------------------------------------------ agenda
const SIGUIENTES = {
  pendiente: [["confirmada", "Confirmar", true], ["en_atencion", "Empezar"], ["cobrar", "Cobrar"], ["no_asistio", "No vino"], ["cancelada", "Cancelar"]],
  confirmada: [["en_atencion", "Empezar", true], ["cobrar", "Cobrar"], ["no_asistio", "No vino"], ["cancelada", "Cancelar"]],
  en_atencion: [["cobrar", "Cobrar", true], ["atendida", "Terminar"], ["cancelada", "Cancelar"]],
};
const ETIQUETA = { pendiente: "Pendiente", confirmada: "Confirmada", en_atencion: "En atención", atendida: "Atendida", cancelada: "Cancelada", no_asistio: "No vino" };

async function ir(fecha) {
  st.fecha = fecha;
  $("fecha").value = fecha; $("fechaLarga").textContent = largo(fecha);
  aviso($("msg"), "");
  try { const cal = await api(`/appointments/calendar?vista=dia&fecha=${fecha}`); st.citas = cal.citas; st.bloqueos = cal.bloqueos; await pintar(); }
  catch (err) { aviso($("msg"), err.message); }
}

async function pintar() {
  const n = (e) => st.citas.filter((c) => c.estado === e).length;
  const vivas = st.citas.filter((c) => !["cancelada", "no_asistio"].includes(c.estado));
  $("conteo").replaceChildren(
    h("span", {}, h("b", {}, String(vivas.length)), vivas.length === 1 ? "turno" : "turnos"), h("span", {}, h("b", {}, String(n("pendiente"))), "por confirmar"),
    h("span", {}, h("b", {}, String(n("atendida"))), n("atendida") === 1 ? "atendido" : "atendidos"),
    (st.me.modulos_activos || []).includes("cash") ? h("span", {}, h("b", {}, money(vivas.reduce((s, c) => s + Number(c.total), 0))), "en servicios") : h("span", {}, h("b", {}, String(n("cancelada") + n("no_asistio"))), "canceladas / no vinieron"));   // sin caja no se muestra dinero
  $("modoEquipo").setAttribute("aria-pressed", String(st.modo === "equipo")); $("modoLista").setAttribute("aria-pressed", String(st.modo !== "equipo"));
  $("equipoVista").hidden = st.modo !== "equipo"; $("libreta").hidden = st.modo === "equipo";
  if (st.modo === "equipo") { $("libreta").replaceChildren(); return pintarEquipo(); }   // línea de tiempo por empleado: panel-agenda.js
  $("libreta").replaceChildren(...(st.citas.length ? st.citas.map(fila) : [h("div", { class: "vacio" }, h("b", {}, "Sin turnos este día"), "Cuando alguien reserve, aparecerá aquí.")]));
}

function fila(c) {
  const acciones = (SIGUIENTES[c.estado] || []).map(([estado, texto, principal]) => h("button", {
    class: "mini" + (principal ? " principal" : ""), type: "button", onclick: (ev) => cambiar(c, estado, texto, ev.currentTarget),
  }, texto));
  return h("article", { class: "cita" },
    h("div", { class: "h" }, hora(c.inicio), h("small", {}, `hasta ${hora(c.fin)}`)),
    h("div", { class: "quien" }, h("b", {}, c.cliente.nombre), h("span", {}, [c.servicios.map((s) => s.nombre).join(" + "), c.profesional?.nombre_publico, c.cliente.telefono].filter(Boolean).join(" · "))),
    h("span", { class: `estado ${c.estado}` }, ETIQUETA[c.estado] ?? c.estado),
    acciones.length ? h("div", { class: "acciones" }, acciones) : null);
}

async function cambiar(c, estado, texto, btn) {
  if (estado === "cobrar") return abrirCobro(c, btn.closest("article, .detalle-in"));   // pantalla de cobro: panel-caja.js
  if (["cancelada", "no_asistio"].includes(estado) && !confirm(`¿${texto} el turno de ${c.cliente.nombre}?`)) return;
  btn.disabled = true;
  try {
    const nueva = await api(`/appointments/${c.id}/status`, { method: "POST", body: JSON.stringify({ estado }) });
    st.citas = st.citas.map((x) => (x.id === c.id ? nueva : x)); document.getElementById("detalle")?.open && document.getElementById("detalle").close(); await pintar(); aviso($("msg"), "");
  } catch (err) { btn.disabled = false; aviso($("msg"), err.message); }
}

$("ayer").onclick = () => ir(sumar(st.fecha, -1));
$("manana").onclick = () => ir(sumar(st.fecha, 1));
$("hoy").onclick = () => ir(hoy());
$("nuevaCita").onclick = () => nuevaCita(st.fecha);
$("fecha").onchange = (e) => e.target.value && ir(e.target.value);

// ------------------------------------------------------------------ arranque
(async () => {
  if (location.hash === "#soporte" && window.opener) {   // sesión de soporte: la plataforma entrega el token por mensaje entre ventanas, no por la URL
    addEventListener("message", async (ev) => {
      if (ev.origin !== location.origin || ev.data?.tipo !== "imp") return;
      store.set("at", ev.data.token); store.set("rt", null); history.replaceState(null, "", location.pathname);
      try { await abrir(); } catch { salir(); }
    }, { once: true });
    window.opener.postMessage({ tipo: "listo" }, location.origin);
    return;
  }
  if (store.get("at")) { try { await abrir(); return; } catch { salir(); return; } }
  $("entrada").hidden = false;
})();
