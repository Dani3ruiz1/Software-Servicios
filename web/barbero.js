// Portal del barbero: SU agenda (el servidor solo le entrega lo suyo), el siguiente turno con cuenta regresiva y botones para empezar y terminar.
const { $, h, aviso, aviso_flotante: avisar, confeti } = UI;
const API = "/api/v1";
const store = {
  get: (k) => { try { return sessionStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* sin almacenamiento */ } },
};
const B = { me: null, fecha: null, semanaIni: null, citas: [], vistos: new Set(), abierto: null };

// ------------------------------------------------------------------ llamadas con renovación de sesión (misma sesión que el panel: "at" / "rt")
let renovando = null;
async function renovar() {
  const r = await fetch(`${API}/auth/refresh`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refresh_token: store.get("rt") }) });
  if (!r.ok) throw new Error("sesión vencida");
  const t = await r.json(); store.set("at", t.access_token); store.set("rt", t.refresh_token);
}
async function api(path, opts = {}, reintento = true) {
  const r = await fetch(API + path, { ...opts, headers: { "content-type": "application/json", Authorization: `Bearer ${store.get("at")}`, ...(opts.headers || {}) } });
  if (r.status === 401 && reintento) {
    try { await (renovando ??= renovar().finally(() => { renovando = null; })); } catch { salir(); throw new Error("Tu sesión venció. Entra de nuevo."); }
    return api(path, opts, false);
  }
  const data = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw Object.assign(new Error(data?.message || "No se pudo completar la acción."), { code: data?.code, status: r.status });
  return data;
}

// ------------------------------------------------------------------ fechas en la zona del negocio
const tz = () => B.me.tenant.zona_horaria;
const fechaLocal = (iso) => new Intl.DateTimeFormat("en-CA", { timeZone: tz() }).format(new Date(iso));
const hoy = () => fechaLocal(new Date());
const sumar = (f, n) => new Date(new Date(f + "T12:00:00Z").getTime() + n * 864e5).toISOString().slice(0, 10);
const hora = (iso) => new Intl.DateTimeFormat("en-US", { timeZone: tz(), hour: "numeric", minute: "2-digit", hour12: true }).format(new Date(iso)).toLowerCase();
const horaDe = (iso) => Number(new Intl.DateTimeFormat("en-GB", { timeZone: tz(), hour: "2-digit", hourCycle: "h23" }).format(new Date(iso)));
const largo = (f) => new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" }).format(new Date(f + "T12:00:00Z"));
const corto = (f) => new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", weekday: "short" }).format(new Date(f + "T12:00:00Z")).replace(".", "");
const min = (c) => (new Date(c.fin) - new Date(c.inicio)) / 60000;
const servicios = (c) => c.servicios.map((s) => `${UI.emojiDe(s.nombre)} ${s.nombre}`).join(" + ");
const activo = (c) => !["cancelada", "no_asistio"].includes(c.estado);
const ETIQUETA = { pendiente: "Pendiente", confirmada: "Confirmada", en_atencion: "En atención", atendida: "Atendida", cancelada: "Cancelada", no_asistio: "No vino" };

// ------------------------------------------------------------------ sesión
$("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target), btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/auth/login`, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ tenant_slug: String(fd.get("tenant")).trim(), email: String(fd.get("email")).trim(), password: String(fd.get("password")) }) });
    const d = await r.json().catch(() => null);
    if (!r.ok) throw new Error(d?.code === "ACCOUNT_LOCKED" ? "Tu cuenta se bloqueó por intentos fallidos. Prueba en unos minutos." : d?.code === "INVALID_CREDENTIALS" ? "El código, el correo o la contraseña no coinciden." : d?.message || "No pudimos entrar.");
    store.set("at", d.access_token); store.set("rt", d.refresh_token); e.target.reset(); aviso($("loginMsg"), "");
    await abrir();
  } catch (err) { aviso($("loginMsg"), err.message); }
  btn.disabled = false;
});
$("salir").addEventListener("click", async () => { try { await api("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: store.get("rt") }) }, false); } catch { /* ya vencida */ } salir(); });
function salir() { store.set("at", null); store.set("rt", null); B.me = null; $("app").hidden = true; $("entrada").hidden = false; }

async function abrir() {
  B.me = await api("/auth/me");
  UI.aplicarTipo(B.me.tenant?.tipo, { colores: true });
  if (B.me.rol !== "profesional") { location.replace("panel.html"); return; }      // el administrador tiene su propio panel
  if (B.me.tenant?.bloqueado) { salir(); return aviso($("loginMsg"), "La suscripción de tu negocio está vencida. Avísale a tu administrador."); }
  $("entrada").hidden = true; $("app").hidden = false;
  $("negocio").textContent = B.me.tenant?.nombre ?? "";
  B.fecha = hoy(); B.vistos = new Set();
  await cargar();
}

// ------------------------------------------------------------------ datos
async function cargar(silencioso = false) {
  try {
    const cal = await api(`/appointments/calendar?vista=semana&fecha=${B.fecha}`);
    const previos = B.vistos, ids = new Set(cal.citas.map((c) => c.id));
    if (silencioso && previos.size) for (const c of cal.citas) if (!previos.has(c.id) && ["pendiente", "confirmada"].includes(c.estado)) avisar(`🆕 Nueva reserva: ${c.cliente.nombre} a las ${hora(c.inicio)}`);
    B.vistos = ids; B.citas = cal.citas; B.semanaIni = cal.desde.slice(0, 10);
    aviso($("msg"), ""); pintar();
  } catch (e) { if (!silencioso) aviso($("msg"), e.message); }
}
const delDia = (f) => B.citas.filter((c) => fechaLocal(c.inicio) === f).sort((a, b) => a.inicio.localeCompare(b.inicio));

// ------------------------------------------------------------------ pintar
function saludo() {
  const hh = horaDe(new Date().toISOString()), nombre = (B.me.nombre || "").split(" ")[0];
  $("saludo").textContent = `${hh < 12 ? "Buenos días" : hh < 19 ? "Buenas tardes" : "Buenas noches"}, ${nombre}`;
}

function pintarSemana() {
  const dias = Array.from({ length: 7 }, (_, i) => sumar(B.semanaIni, i));
  $("semana").replaceChildren(...dias.map((f) => {
    const n = delDia(f).filter(activo).length;
    return h("button", { class: "sem-dia" + (f === hoy() ? " hoy" : ""), type: "button", role: "tab", "aria-selected": f === B.fecha, onclick: () => { B.fecha = f; B.abierto = null; pintar(); } },
      h("small", {}, corto(f)), h("b", {}, String(Number(f.slice(8)))), h("i", { class: n ? "" : "cero", title: `${n} turnos` }, String(n)));
  }));
}

function pintarSiguiente() {
  const cont = $("siguiente"), dia = delDia(B.fecha), vivos = dia.filter(activo);
  if (!vivos.length) {
    return cont.replaceChildren(h("div", { class: "hero-vacio" }, h("div", { class: "gran" }, "🎉"), h("h2", {}, B.fecha === hoy() ? "Hoy no tienes turnos" : "Día libre"),
      h("p", { class: "sub" }, B.fecha === hoy() ? "Disfruta el descanso. Si alguien reserva contigo, te avisamos aquí mismo." : `No tienes reservas el ${largo(B.fecha)}.`)));
  }
  if (B.fecha !== hoy()) {
    const primero = vivos[0], pasado = B.fecha < hoy(), hechos = vivos.filter((c) => c.estado === "atendida").length;
    return cont.replaceChildren(h("div", { class: "hero-vacio" }, h("div", { class: "gran" }, pasado ? "🗓️" : "📅"), h("h2", {}, `${vivos.length} ${vivos.length === 1 ? "turno" : "turnos"}`),
      h("p", { class: "sub" }, pasado ? `${largo(B.fecha)}: atendiste ${hechos} de ${vivos.length}.` : `${largo(B.fecha)}. El primero es a las ${hora(primero.inicio)} con ${primero.cliente.nombre}.`)));
  }
  const ahora = Date.now();
  const actual = vivos.find((c) => c.estado === "en_atencion") || vivos.find((c) => !["atendida"].includes(c.estado) && new Date(c.fin) > ahora);
  if (!actual) return cont.replaceChildren(h("div", { class: "hero-vacio" }, h("div", { class: "gran" }, "🏁"), h("h2", {}, "¡Terminaste tu día!"), h("p", { class: "sub" }, "Ya atendiste todos tus turnos de hoy. Buen trabajo.")));
  const m = Math.round((new Date(actual.inicio) - ahora) / 60000), enCurso = actual.estado === "en_atencion";
  const faltan = enCurso ? "En atención" : m > 90 ? `en ${Math.floor(m / 60)} h ${m % 60} min` : m > 1 ? `en ${m} min` : m >= -2 ? "¡ahora!" : `hace ${-m} min`;
  const tel = (actual.cliente.telefono || "").replace(/\D/g, "");
  cont.replaceChildren(h("div", { class: "hero-turno" },
    h("p", { class: "etq" }, enCurso ? "Estás atendiendo a" : "Tu siguiente turno"),
    h("div", { class: "cuando" }, h("span", { class: "hora-grande" }, hora(actual.inicio)), h("span", { class: "faltan" + (!enCurso && m <= 1 ? " ya" : "") }, faltan)),
    h("h2", {}, actual.cliente.nombre), h("p", { class: "serv" }, `${servicios(actual)} · ${min(actual)} min`),
    h("div", { class: "acc" },
      h("button", { class: "btn btn-grande", type: "button", onclick: (e) => mover(actual, enCurso ? "atendida" : "en_atencion", e.currentTarget) }, enCurso ? "✅ Terminar turno" : "▶ Empezar turno"),
      tel ? h("a", { class: "mini", href: `tel:${tel}` }, "📞 Llamar") : null,
      tel ? h("a", { class: "mini", target: "_blank", rel: "noopener", href: `https://wa.me/57${tel}?text=${encodeURIComponent(`Hola ${actual.cliente.nombre.split(" ")[0]}, soy ${B.me.nombre.split(" ")[0]} de ${B.me.tenant.nombre}. Te espero a las ${hora(actual.inicio)}.`)}` }, "💬 WhatsApp") : null)));
}

function pintarProgreso() {
  const c = $("progreso"), vivos = delDia(B.fecha).filter(activo), hechos = vivos.filter((x) => x.estado === "atendida").length;
  c.hidden = !vivos.length;
  if (!vivos.length) return;
  c.replaceChildren(h("span", {}, `Tu día: ${hechos} de ${vivos.length} ${vivos.length === 1 ? "turno atendido" : "turnos atendidos"}`),
    h("div", { class: "barra-p" }, ...vivos.map((x) => h("i", { class: x.estado === "atendida" ? "ok" : x.estado === "en_atencion" ? "en" : "" }))));
}

function pintarLista() {
  const dia = delDia(B.fecha), ahora = Date.now();
  if (!dia.length) return $("lista").replaceChildren();
  const nodos = [];
  let franja = null;
  dia.forEach((c, i) => {
    const hh = horaDe(c.inicio), f = hh < 12 ? "🌅 Mañana" : hh < 18 ? "☀️ Tarde" : "🌙 Noche";
    if (f !== franja) { franja = f; nodos.push(h("p", { class: "hora-sep" }, f)); }
    const abierto = B.abierto === c.id, tel = (c.cliente.telefono || "").replace(/\D/g, ""), enCurso = c.estado === "en_atencion";
    const t = h("div", { class: `turno ${c.estado === "atendida" ? "listo" : !activo(c) ? "cancelado" : enCurso ? "ahora-t" : ""}`, style: `animation-delay:${i * 40}ms`, role: "button", tabindex: "0", "aria-expanded": abierto,
      onclick: (e) => { if (e.target.closest("a,button")) return; B.abierto = abierto ? null : c.id; pintarLista(); }, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); B.abierto = abierto ? null : c.id; pintarLista(); } } },
      h("div", { class: "hr" }, hora(c.inicio), h("small", {}, `${min(c)} min`)),
      h("div", { class: "cu" }, h("b", {}, c.cliente.nombre), h("span", {}, servicios(c)), h("span", { class: `estado ${c.estado}` }, ETIQUETA[c.estado] ?? c.estado)));
    if (abierto) {
      const puede = ["pendiente", "confirmada", "en_atencion"].includes(c.estado);
      t.append(h("div", { class: "panelito" },
        c.notas ? h("p", { class: "sub" }, `📝 ${c.notas}`) : null,
        h("div", { class: "envolver" },
          puede ? h("button", { class: "mini principal", type: "button", onclick: (e) => mover(c, enCurso ? "atendida" : "en_atencion", e.currentTarget) }, enCurso ? "✅ Terminar" : "▶ Empezar") : null,
          tel ? h("a", { class: "mini", href: `tel:${tel}` }, `📞 ${c.cliente.telefono}`) : null,
          tel ? h("a", { class: "mini", target: "_blank", rel: "noopener", href: `https://wa.me/57${tel}` }, "💬 WhatsApp") : null),
        !puede ? h("p", { class: "sub" }, c.estado === "atendida" ? "Turno terminado." : "Este turno ya no está activo. Cancelar o reprogramar lo hace tu administrador.") : h("p", { class: "sub" }, "Cancelar, mover o confirmar turnos lo hace tu administrador.")));
    }
    nodos.push(t);
  });
  $("lista").replaceChildren(...nodos);
}

function pintar() { saludo(); pintarSemana(); pintarSiguiente(); pintarProgreso(); pintarLista(); }

async function mover(c, estado, btn) {
  btn.disabled = true;
  try {
    const nueva = await api(`/appointments/${c.id}/status`, { method: "POST", body: JSON.stringify({ estado }) });
    B.citas = B.citas.map((x) => (x.id === c.id ? nueva : x));
    pintar();
    if (estado === "atendida") {
      avisar("¡Turno terminado! 👏");
      const vivos = delDia(B.fecha).filter(activo);
      if (vivos.length && vivos.every((x) => x.estado === "atendida")) confeti();
    } else avisar("Turno en marcha ✂️");
  } catch (e) { btn.disabled = false; avisar(e.code === "INVALID_STATE_TRANSITION" ? "Ese turno ya cambió de estado. Actualizo tu agenda." : e.message, false); if (e.code === "INVALID_STATE_TRANSITION") cargar(); }
}

// ------------------------------------------------------------------ navegación y arranque
$("semAnt").onclick = () => { B.fecha = sumar(B.fecha, -7); B.abierto = null; cargar(); };
$("semSig").onclick = () => { B.fecha = sumar(B.fecha, 7); B.abierto = null; cargar(); };
$("actualizar").onclick = async (e) => { e.currentTarget.disabled = true; await cargar(); e.currentTarget.disabled = false; avisar("Agenda actualizada"); };
setInterval(() => { if (B.me && !document.hidden) { cargar(true); } }, 45000);                // entra una reserva nueva y te enteras
setInterval(() => { if (B.me && !document.hidden && B.fecha === hoy()) pintarSiguiente(); }, 30000);   // la cuenta regresiva avanza sola
(async () => {
  if (store.get("at")) { try { await abrir(); return; } catch { salir(); return; } }
  $("entrada").hidden = false;
})();
