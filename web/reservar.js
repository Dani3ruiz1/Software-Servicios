// Reserva pública: habla con /api/v1/public/{slug}/... (sin login). Pasos que se pliegan, «lo antes posible», ticket vivo y pantalla final con gracia.
const { $, h, aviso, aplicarMarca, confeti, aviso_flotante: avisar, ics, descargar, emojiDe, colorDe, iniciales } = UI;
const slug = new URLSearchParams(location.search).get("n") || "demo";
const API = `/api/v1/public/${encodeURIComponent(slug)}`;
const S = { cfg: null, servicio: null, staff: null, staffListo: false, dias: null, dia: null, inicio: null, abierto: "servicio", datosListos: false };
const REC = {};
const TITULOS = { servicio: "¿Qué te vas a hacer?", profesional: "¿Con quién?", dia: "¿Qué día?", hora: "¿A qué hora?", datos: "¿A nombre de quién?" };

const recuerdo = {   // la persona no vuelve a escribir sus datos la próxima vez (solo en su propio celular)
  leer() { try { return JSON.parse(localStorage.getItem("reservas.cliente") || "null"); } catch { return null; } },
  poner(v) { try { localStorage.setItem("reservas.cliente", JSON.stringify(v)); } catch { /* sin almacenamiento: no pasa nada */ } },
};

async function api(path, opts = {}) {
  const r = await fetch(API + path, { headers: { "content-type": "application/json" }, ...opts });
  const data = r.status === 204 ? null : await r.json().catch(() => null);
  if (!r.ok) throw Object.assign(new Error(data?.message || "No se pudo completar la acción."), { code: data?.code, status: r.status });
  return data;
}

// ------------------------------------------------------------------ formatos (siempre en la zona horaria del negocio)
const tz = () => S.cfg.zona_horaria;
const money = (v) => new Intl.NumberFormat("es-CO", { style: "currency", currency: S.cfg.moneda, maximumFractionDigits: 0 }).format(Number(v));
const fmtHora = (iso) => new Intl.DateTimeFormat("en-US", { timeZone: tz(), hour: "numeric", minute: "2-digit", hour12: true }).format(new Date(iso)).toLowerCase();
const hora24 = (iso) => Number(new Intl.DateTimeFormat("en-GB", { timeZone: tz(), hour: "2-digit", hourCycle: "h23" }).format(new Date(iso)));
const hoyNegocio = () => new Intl.DateTimeFormat("en-CA", { timeZone: tz() }).format(new Date());
const sumar = (f, n) => new Date(new Date(f + "T12:00:00Z").getTime() + n * 864e5).toISOString().slice(0, 10);
const fechaUTC = (f) => new Date(f + "T12:00:00Z");
const diaLargo = (f) => new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" }).format(fechaUTC(f));
const etiquetaDia = (f) => (f === hoyNegocio() ? "Hoy" : f === sumar(hoyNegocio(), 1) ? "Mañana" : diaLargo(f));
const pasosActivos = () => ["servicio", ...(S.cfg.escoger_profesional ? ["profesional"] : []), "dia", "hora", "datos"];
const prosDelServicio = () => S.cfg.profesionales.filter((p) => S.servicio && p.service_ids.includes(S.servicio.id));

// ------------------------------------------------------------------ estado de cada paso
const hecho = (id) => ({ servicio: !!S.servicio, profesional: S.staffListo, dia: !!S.dia, hora: !!S.inicio, datos: false })[id];
const bloqueado = (id) => { const l = pasosActivos(); return l.slice(0, l.indexOf(id)).some((p) => !hecho(p)); };
function resumen(id) {
  if (id === "servicio") return S.servicio ? `${emojiDe(S.servicio.nombre)} ${S.servicio.nombre} · ${money(S.servicio.precio)}` : "Elige un servicio";
  if (id === "profesional") return S.staffListo ? (S.staff ? S.staff.nombre_publico : "Quien esté libre") : "";
  if (id === "dia") return S.dia ? etiquetaDia(S.dia) : "";
  if (id === "hora") return S.inicio ? fmtHora(S.inicio) : "";
  return "";
}

function crearPaso(id, n) {
  const cuerpo = h("div", { class: "pad" }), res = h("span", { class: "paso-res" }), num = h("span", { class: "paso-num" }, String(n));
  const cab = h("button", { class: "paso-cab", type: "button", "aria-expanded": false, onclick: () => { if (!bloqueado(id)) abrir(id); } },
    num, h("span", { class: "paso-tit" }, h("h2", {}, TITULOS[id]), res), h("span", { class: "paso-cambiar" }, "Cambiar"));
  const sec = h("section", { class: "paso", "data-paso": id }, cab, h("div", { class: "paso-cuerpo" }, h("div", { class: "in" }, cuerpo)));
  REC[id] = { sec, cab, cuerpo, res, num };
  return sec;
}

function actualizar() {
  const lista = pasosActivos();
  lista.forEach((id, i) => {
    const r = REC[id], est = id === S.abierto ? "abierto" : hecho(id) ? "hecho" : bloqueado(id) ? "bloqueado" : "pendiente";
    r.sec.dataset.estado = est; r.cab.setAttribute("aria-expanded", String(est === "abierto")); r.cab.disabled = est === "bloqueado";
    r.num.textContent = est === "hecho" ? "✓" : String(i + 1); r.res.textContent = resumen(id);
  });
  const hechos = lista.filter(hecho).length;
  document.querySelectorAll("#progreso i").forEach((el, i) => el.classList.toggle("on", i < hechos));
  $("progreso").setAttribute("aria-valuenow", String(hechos));
  pintarTicket(hechos, lista.length);
}

function abrir(id) {
  S.abierto = id; actualizar();
  setTimeout(() => REC[id].sec.scrollIntoView({ behavior: UI.reducido() ? "auto" : "smooth", block: "start" }), 120);
}

// ------------------------------------------------------------------ pintar cada paso
const ofrecidos = () => S.cfg.servicios.filter((s) => S.cfg.profesionales.some((p) => p.service_ids.includes(s.id)));   // un servicio que nadie atiende no se ofrece
function pintarServicios() {
  const porCat = new Map(), lista0 = ofrecidos();
  if (!lista0.length) return REC.servicio.cuerpo.replaceChildren(h("div", { class: "vacio" }, h("b", {}, "Aún no hay turnos para reservar"), "Este negocio todavía está preparando su agenda. Vuelve pronto."));
  lista0.forEach((s) => porCat.set(s.categoria || "", [...(porCat.get(s.categoria || "") || []), s]));
  const nodos = [];
  for (const [cat, lista] of porCat) {
    if (cat && porCat.size > 1) nodos.push(h("p", { class: "categoria" }, cat));
    nodos.push(h("div", { class: "rejilla" }, lista.map((s) => h("button", { class: "opcion", type: "button", "aria-pressed": S.servicio?.id === s.id, onclick: () => elegirServicio(s) },
      h("span", { class: "ico", "aria-hidden": "true" }, emojiDe(s.nombre)), h("span", { class: "t" }, h("b", {}, s.nombre), h("small", {}, `${s.duracion_min} min`)), h("span", { class: "p" }, money(s.precio))))));
  }
  REC.servicio.cuerpo.replaceChildren(...nodos);
}

function pintarPros() {
  if (!REC.profesional) return;
  const opcion = (p) => h("button", { class: "opcion", type: "button", "aria-pressed": S.staffListo && (S.staff?.id ?? null) === (p?.id ?? null), onclick: () => elegirStaff(p) },
    p ? h("span", { class: "avatar", style: `background:${colorDe(p.id)}`, "aria-hidden": "true" }, iniciales(p.nombre_publico)) : h("span", { class: "ico", "aria-hidden": "true" }, "🎲"),
    h("span", { class: "t" }, h("b", {}, p ? p.nombre_publico : "Quien esté libre"), h("small", {}, p ? p.especialidad || "Profesional" : "Te asignamos el primero disponible")));
  REC.profesional.cuerpo.replaceChildren(h("div", { class: "rejilla" }, [opcion(null), ...prosDelServicio().map(opcion)]));
}

function pintarDias() {
  const c = REC.dia.cuerpo;
  if (!S.servicio) return c.replaceChildren();
  if (S.dias === null) return c.replaceChildren(h("div", { class: "esqueleto", style: "height:5.5rem" }));
  const conCupo = S.dias.filter((d) => d.horarios.length);
  if (!conCupo.length) return c.replaceChildren(h("div", { class: "aviso" }, S.cfg.escoger_profesional && S.staff ? `${S.staff.nombre_publico} no tiene horarios libres en estos días. Prueba con otra persona o con «quien esté libre».` : "No hay horarios libres en los próximos días. Vuelve más tarde."));
  const primero = conCupo[0], slot = primero.horarios[0];
  const rapido = h("div", { class: "rapido" }, h("span", { class: "rayo", "aria-hidden": "true" }, "⚡"),
    h("span", { class: "tx" }, h("b", {}, "¿Lo antes posible?"), h("small", {}, `${etiquetaDia(primero.fecha)} a las ${fmtHora(slot)}`)),
    h("button", { class: "btn", type: "button", onclick: () => { S.dia = primero.fecha; S.inicio = slot; pintarDias(); pintarHoras(); pintarDatos(); abrir("datos"); } }, "Reservar este"));
  const dias = h("div", { class: "dias", role: "group", "aria-label": "Días disponibles" }, S.dias.map((d) => {
    const f = fechaUTC(d.fecha), n = d.horarios.length;
    return h("button", { class: "dia", type: "button", disabled: !n, "aria-pressed": S.dia === d.fecha, onclick: () => elegirDia(d.fecha) },
      d.fecha === hoyNegocio() ? h("span", { class: "hoy" }, "Hoy") : h("small", {}, new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", weekday: "short" }).format(f).replace(".", "")),
      h("b", {}, String(f.getUTCDate())), h("small", {}, new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", month: "short" }).format(f).replace(".", "")),
      h("em", { class: n && n <= 3 ? "poco" : "" }, n ? (n <= 3 ? `¡Quedan ${n}!` : `${n} cupos`) : "Lleno"));
  }));
  c.replaceChildren(rapido, dias);
}

function pintarHoras() {
  const c = REC.hora.cuerpo, dia = S.dias?.find((d) => d.fecha === S.dia);
  if (!dia) return c.replaceChildren();
  const franjas = [["🌅 Mañana", (x) => x < 12], ["☀️ Tarde", (x) => x >= 12 && x < 18], ["🌙 Noche", (x) => x >= 18]];
  c.replaceChildren(...franjas.flatMap(([t, f]) => {
    const hs = dia.horarios.filter((iso) => f(hora24(iso)));
    return hs.length ? [h("p", { class: "franja" }, t), h("div", { class: "horas" }, hs.map((iso) => h("button", { class: "hora", type: "button", "aria-pressed": S.inicio === iso, onclick: () => elegirHora(iso) }, fmtHora(iso))))] : [];
  }));
}

let formDatos = null;
function pintarDatos() {
  if (!formDatos) {
    const guardado = recuerdo.leer() || {}, msg = h("div", { "aria-live": "polite" });
    const campo = (et, input) => h("label", { class: "campo" }, h("span", {}, et), input);
    formDatos = {
      msg,
      form: h("form", { class: "form", novalidate: true, onsubmit: enviar },
        h("div", { class: "resumen-reserva", "data-resumen": "" }),
        campo("Tu nombre", h("input", { name: "nombre", autocomplete: "name", required: true, minlength: 2, maxlength: 120, value: guardado.nombre ?? "", placeholder: "Ej. Laura Gómez" })),
        campo("Tu celular (te avisamos por WhatsApp)", h("input", { name: "telefono", type: "tel", autocomplete: "tel", inputmode: "tel", required: true, value: guardado.telefono ?? "", placeholder: "300 123 4567" })),
        campo("Correo (opcional)", h("input", { name: "email", type: "email", autocomplete: "email", value: guardado.email ?? "", placeholder: "tucorreo@ejemplo.com" })),
        h("label", { class: "consent" }, h("input", { type: "checkbox", name: "consent" }), h("span", {}, S.cfg.consentimiento_texto)),
        h("input", { class: "trampa", name: "website", tabindex: "-1", autocomplete: "off", "aria-hidden": "true" }), msg,
        h("button", { class: "btn", type: "submit" }, "Reservar mi turno")),
    };
    REC.datos.cuerpo.replaceChildren(formDatos.form);
  }
  const r = formDatos.form.querySelector("[data-resumen]");
  r.textContent = S.servicio && S.inicio ? `${emojiDe(S.servicio.nombre)} ${S.servicio.nombre} · ${diaLargo(S.dia)} a las ${fmtHora(S.inicio)}${S.staff ? " · con " + S.staff.nombre_publico : ""}` : "";
}

function pintarTicket(hechos, total) {
  const fila = (k, v) => [h("dt", {}, k), h("dd", { class: v ? "" : "vacio-t" }, v || "—")];
  const precio = S.servicio ? money(S.servicio.precio) : "—";
  $("recibo").replaceChildren(...fila("Servicio", S.servicio?.nombre), ...(S.cfg.escoger_profesional ? fila("Con", S.staffListo ? (S.staff?.nombre_publico ?? "Quien esté libre") : "") : []),
    ...fila("Día", S.dia ? etiquetaDia(S.dia) : ""), ...fila("Hora", S.inicio ? fmtHora(S.inicio) : ""), h("div", { class: "total" }, h("span", {}, "Total"), h("span", { class: "precio" }, precio)));
  $("ticketNota").textContent = `Pagas en el local. Puedes cancelar sin costo hasta ${S.cfg.cancelacion_min_horas} h antes.`;
  $("barraMovil").hidden = !S.servicio;
  $("bmTitulo").textContent = S.servicio ? `${emojiDe(S.servicio.nombre)} ${S.servicio.nombre}${S.inicio ? " · " + fmtHora(S.inicio) : ""}` : "";
  $("bmSub").textContent = hechos >= total ? "Completa tus datos para reservar" : `Paso ${hechos + 1} de ${total}`;
  $("bmTotal").textContent = S.servicio ? precio : "";
}

// ------------------------------------------------------------------ acciones de la persona
function elegirServicio(s) {
  S.servicio = s; S.staff = null; S.dia = null; S.inicio = null; S.dias = null; S.staffListo = !S.cfg.escoger_profesional || prosDelServicio().length === 0;
  pintarServicios(); pintarPros(); pintarDias(); pintarHoras(); pintarDatos(); cargarDias();
  abrir(S.staffListo ? "dia" : "profesional");
}
function elegirStaff(p) { S.staff = p; S.staffListo = true; S.dia = null; S.inicio = null; S.dias = null; pintarPros(); pintarDias(); pintarHoras(); pintarDatos(); cargarDias(); abrir("dia"); }
function elegirDia(f) { S.dia = f; S.inicio = null; pintarDias(); pintarHoras(); pintarDatos(); abrir("hora"); }
function elegirHora(iso) { S.inicio = iso; pintarHoras(); pintarDatos(); abrir("datos"); }

async function cargarDias() {
  if (!S.servicio) return;
  S.dias = null; pintarDias();
  const clave = `${S.servicio.id}|${S.staff?.id ?? ""}`, desde = hoyNegocio();
  const q = new URLSearchParams({ fecha_desde: desde, fecha_hasta: sumar(desde, 13), service_ids: S.servicio.id });
  if (S.staff) q.set("staff_id", S.staff.id);
  try {
    const r = await api(`/availability?${q}`);
    if (clave === `${S.servicio?.id}|${S.staff?.id ?? ""}`) { S.dias = r.dias; if (S.dia && !S.dias.find((d) => d.fecha === S.dia && d.horarios.length)) { S.dia = null; S.inicio = null; } pintarDias(); pintarHoras(); actualizar(); }
  } catch (e) { S.dias = []; pintarDias(); avisar(e.message, false); }
}

async function enviar(ev) {
  ev.preventDefault();
  const fd = new FormData(ev.target), btn = ev.target.querySelector(".btn"), v = (k) => String(fd.get(k) ?? "").trim();
  if (v("nombre").length < 2) return aviso(formDatos.msg, "Escribe tu nombre para que sepamos quién llega.");
  if (v("telefono").replace(/\D/g, "").length < 7) return aviso(formDatos.msg, "Escribe un celular válido: ahí te recordamos tu turno.");
  if (!fd.get("consent")) return aviso(formDatos.msg, "Necesitamos tu autorización de datos para guardar el turno.");
  aviso(formDatos.msg, ""); btn.classList.add("cargando"); btn.textContent = "Reservando…";
  try {
    const b = await api("/bookings", { method: "POST", body: JSON.stringify({ service_ids: [S.servicio.id], staff_id: S.staff?.id ?? null, inicio: S.inicio,
      cliente: { nombre: v("nombre"), telefono: v("telefono"), email: v("email") || null }, consentimiento_datos: true, respuestas: {}, website: v("website") }) });
    recuerdo.poner({ nombre: v("nombre"), telefono: v("telefono"), email: v("email") });
    history.replaceState(null, "", `#t=${encodeURIComponent(b.token)}`);
    mostrarTurno(b, b.token, true, v("nombre"));
  } catch (err) {
    btn.classList.remove("cargando"); btn.textContent = "Reservar mi turno";
    if (err.code === "SLOT_UNAVAILABLE" || err.code === "SLOT_TAKEN") {
      aviso(formDatos.msg, "¡Ups! Alguien reservó ese horario justo antes que tú. Ya actualizamos los horarios: elige otro.");
      S.inicio = null; await cargarDias(); abrir("hora");
    } else if (err.code === "RATE_LIMITED" || err.status === 429) aviso(formDatos.msg, "Hiciste muchos intentos seguidos. Espera un momento y vuelve a intentar.");
    else aviso(formDatos.msg, err.message);
  }
}

// ------------------------------------------------------------------ turno confirmado / gestión
function mostrarTurno(b, token, nuevo, nombre = "") {
  const cancelado = b.estado === "cancelada";
  const fecha = new Intl.DateTimeFormat("es-CO", { timeZone: tz(), weekday: "long", day: "numeric", month: "long" }).format(new Date(b.inicio));
  const msg = h("div", { "aria-live": "polite" });
  const base = location.origin + location.pathname + location.search;
  const cancelar = h("button", { class: "btn btn-suave", type: "button", onclick: async (e) => {
    if (!confirm("¿Seguro que quieres cancelar este turno?")) return;
    e.currentTarget.disabled = true;
    try { mostrarTurno(await api(`/bookings/${encodeURIComponent(token)}/cancel`, { method: "POST", body: "{}" }), token, false); }
    catch (err) { e.currentTarget.disabled = false; aviso(msg, err.code === "CANCELLATION_WINDOW_CLOSED" ? "Ya no se puede cancelar por aquí porque falta muy poco. Escríbele al negocio y lo resuelven." : err.message); }
  } }, "Cancelar turno");
  const titulo = (b.servicios || []).map((s) => s.nombre).join(" + ") || "Turno";
  const acciones = cancelado ? [h("a", { class: "btn", href: base }, "Reservar otro turno")] : [
    h("button", { class: "btn", type: "button", onclick: () => descargar(ics({ titulo: `${titulo} en ${b.negocio}`, inicio: b.inicio, fin: b.fin, lugar: b.negocio, detalle: b.profesional ? `Te atiende ${b.profesional}` : "" }), "mi-turno.ics") }, "📅 Agregar a mi calendario"),
    h("a", { class: "btn btn-suave", target: "_blank", rel: "noopener", href: `https://wa.me/?text=${encodeURIComponent(`Reservé en ${b.negocio}: ${fecha} a las ${fmtHora(b.inicio)}. Reserva tú también aquí: ${base}`)}` }, "💬 Contarle a un amigo"), cancelar];
  document.querySelector(".reserva").style.gridTemplateColumns = "minmax(0, 1fr)";
  $("ticket").hidden = true; $("barraMovil").hidden = true; $("progreso").classList.add("listo"); $("progreso").hidden = true; $("saludo").hidden = true;
  $("pasos").replaceChildren(h("section", { class: "listo" },
    cancelado ? h("div", { class: "ico", style: "margin:0 auto 1rem;width:5rem;height:5rem;font-size:2.4rem" }, "👋") : h("span", { class: "sello" }),
    h("h2", {}, cancelado ? "Turno cancelado" : nuevo ? `¡Listo${nombre ? ", " + nombre.split(" ")[0] : ""}!` : "Tu turno"),
    h("p", { class: "cuando" }, cancelado ? "Liberamos el horario. Cuando quieras, reserva otro." : `${fecha[0].toUpperCase() + fecha.slice(1)}, a las ${fmtHora(b.inicio)}`),
    h("div", { class: "tarjeta" }, h("p", {}, h("b", {}, b.negocio)), ...(b.servicios || []).map((s) => h("p", {}, `${emojiDe(s.nombre)} ${s.nombre} · ${money(s.precio)}`)),
      b.profesional ? h("p", {}, `🙋 Te atiende ${b.profesional}`) : null, h("p", {}, h("b", {}, `Total ${money(b.total)} · pagas en el local`))),
    !cancelado && S.cfg.marca.textos?.confirmacion ? h("p", { class: "msg-negocio" }, S.cfg.marca.textos.confirmacion) : null,
    h("div", { class: "acciones" }, acciones), msg,
    !cancelado ? h("p", { class: "guarda" }, nuevo ? "Guarda esta página: con ella puedes ver o cancelar tu turno." : "") : null));
  if (!cancelado) {   // el sello con el palomita animado se arma como SVG de verdad (h() crea elementos HTML, no SVG)
    const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg"), c = document.createElementNS(ns, "circle"), p = document.createElementNS(ns, "path");
    svg.setAttribute("class", "sello"); svg.setAttribute("viewBox", "0 0 100 100"); svg.setAttribute("aria-hidden", "true");
    c.setAttribute("cx", "50"); c.setAttribute("cy", "50"); c.setAttribute("r", "44"); p.setAttribute("d", "M30 52 L44 66 L71 36");
    svg.append(c, p); document.querySelector(".listo .sello")?.replaceWith(svg);
    window.scrollTo({ top: 0, behavior: UI.reducido() ? "auto" : "smooth" });
    if (nuevo) confeti();
  }
}

// ------------------------------------------------------------------ arranque
(async () => {
  try {
    S.cfg = await api("/config");
    const marca = S.cfg.marca, textos = marca.textos || {}, g = recuerdo.leer();
    UI.aplicarTipo(marca.tipo, { recordar: false });
    aplicarMarca({ primario: marca.color_primario, secundario: marca.color_secundario, fondo: marca.color_fondo });
    document.title = `Reservar turno · ${marca.nombre}`;
    $("nombre").textContent = textos.titulo || marca.nombre;
    if (textos.bienvenida) $("lema").textContent = textos.bienvenida;
    if (marca.logo_url && /^https:\/\//.test(marca.logo_url)) { $("logo").src = marca.logo_url; $("logo").alt = `Logo de ${marca.nombre}`; $("logo").hidden = false; }
    if (g?.nombre) { $("saludo").textContent = `¡Hola de nuevo, ${g.nombre.split(" ")[0]}!`; $("saludo").hidden = false; }
    const lista = pasosActivos();
    $("progreso").replaceChildren(...lista.map(() => h("i")));
    $("progreso").setAttribute("aria-valuemax", String(lista.length));
    $("pasos").replaceChildren(...lista.map((id, i) => crearPaso(id, i + 1)));
    const t = new URLSearchParams(location.hash.slice(1)).get("t");
    if (t) {
      try { mostrarTurno(await api(`/bookings/${encodeURIComponent(t)}`), t, false); return; } catch { history.replaceState(null, "", location.pathname + location.search); }
    }
    pintarServicios(); pintarPros(); pintarDatos(); actualizar();
  } catch (e) {
    $("pasos").replaceChildren(h("div", { class: "vacio" }, h("b", {}, e.status === 404 ? "No encontramos este negocio" : "Algo salió mal"),
      e.status === 404 ? "Revisa que el enlace esté completo o pídele uno nuevo al negocio." : e.message));
    $("ticket").hidden = true; $("progreso").hidden = true;
  }
})();
