// Panel del negocio, parte 2: personal (alta, servicios, horario semanal con descanso, ausencias) y precios de servicios.
// Usa lo definido en panel.js (api, h, aviso, st, tz, desfase, money, $).
const DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
const vistas = { agenda: $("vista-agenda"), personal: $("vista-personal"), servicios: $("vista-servicios"), caja: $("vista-caja"), clientes: $("vista-clientes"), reportes: $("vista-reportes"), compartir: $("vista-compartir"), inventario: $("vista-inventario") };
const eq = { personal: [], servicios: [], abierto: null };   // abierto = { id, tipo: "horario" | "ausencias" | "servicios" }

function vista(v) {
  for (const [k, el] of Object.entries(vistas)) el.hidden = k !== v;
  document.querySelectorAll(".tabs button").forEach((b) => {
    if (b.dataset.vista === v) { b.setAttribute("aria-current", "page"); b.scrollIntoView?.({ inline: "center", block: "nearest", behavior: "smooth" }); }   // en celular, la pestaña activa queda a la vista
    else b.removeAttribute("aria-current");
  });
  if (v === "personal") cargarPersonal();
  if (v === "servicios") cargarServicios();
  if (v === "caja") cargarCaja();
  if (v === "clientes") cargarClientes();
  if (v === "reportes") cargarReportes();
  if (v === "compartir") cargarCompartir();
  if (v === "inventario") cargarInventario();
}
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => vista(b.dataset.vista)));
$("salir").addEventListener("click", () => vista("agenda"));

function errTexto(e) {
  const c = e.data?.details?.campos?.[0];
  return e.code === "VALIDATION_ERROR" && c ? `${c.campo}: ${c.mensaje}` : e.message;
}
const hhmm = (t) => String(t).slice(0, 5);
const campo = (etiqueta, input) => h("label", { class: "campo" }, h("span", {}, etiqueta), input);

// ================================================================== PERSONAL
async function cargarPersonal() {
  const cont = vistas.personal;
  if (typeof limpiarEquipo === "function") limpiarEquipo();      // la agenda por empleado vuelve a leer el equipo y sus horarios
  try {
    [eq.personal, eq.servicios] = await Promise.all([api("/staff"), api("/catalog/services?page_size=100&sort=nombre").then((r) => r.items)]);
  } catch (e) { return cont.replaceChildren(h("div", { class: "aviso" }, e.message)); }
  pintarPersonal();
}

function pintarPersonal(nuevaForm = false) {
  const cont = vistas.personal, nombreServ = (id) => eq.servicios.find((s) => s.id === id)?.nombre;
  const alta = h("div");
  const titulo = h("div", { class: "titulo" }, h("h2", {}, "Personal"),
    h("button", { class: "btn", type: "button", onclick: () => alta.replaceChildren(formAlta()) }, "Agregar persona"));
  cont.replaceChildren(titulo, alta, ...(eq.personal.length ? eq.personal.map((p) => tarjeta(p, nombreServ)) : [h("div", { class: "vacio" }, h("b", {}, "Aún no hay personal"), "Agrega a la primera persona para que pueda recibir reservas.")]));
  if (nuevaForm) alta.replaceChildren(formAlta());
}

function tarjeta(p, nombreServ) {
  const abre = (tipo) => () => { eq.abierto = eq.abierto?.id === p.id && eq.abierto.tipo === tipo ? null : { id: p.id, tipo }; pintarPersonal(); };
  const es = (tipo) => eq.abierto?.id === p.id && eq.abierto.tipo === tipo;
  const serv = p.service_ids.map(nombreServ).filter(Boolean).join(", ") || "Sin servicios asignados";
  const card = h("article", { class: "persona" + (p.activo ? "" : " inactiva") },
    h("span", { class: "punto", style: `background:${/^#[0-9a-f]{6}$/i.test(p.color_calendario) ? p.color_calendario : "#888"}` }),
    h("div", {}, h("b", {}, p.nombre_publico), h("div", { class: "sub" }, [p.especialidad, serv].filter(Boolean).join(" · "))),
    h("div", { class: "envolver" },
      h("button", { class: "mini" + (es("horario") ? " principal" : ""), type: "button", onclick: abre("horario") }, "Horario"),
      h("button", { class: "mini" + (es("ausencias") ? " principal" : ""), type: "button", onclick: abre("ausencias") }, "Ausencias"),
      h("button", { class: "mini" + (es("servicios") ? " principal" : ""), type: "button", onclick: abre("servicios") }, "Servicios"),
      h("button", { class: "mini" + (es("acceso") ? " principal" : ""), type: "button", onclick: abre("acceso") }, p.user_id ? "🔑 Acceso" : "Acceso")));
  if (es("horario")) card.append(panelHorario(p));
  if (es("ausencias")) card.append(panelAusencias(p));
  if (es("servicios")) card.append(panelServiciosDe(p));
  if (es("acceso")) card.append(panelAcceso(p));
  return card;
}

// ---------------------------------------------------------------- alta de persona
function formAlta() {
  const msg = h("div", { "aria-live": "polite" });
  const checks = eq.servicios.filter((s) => s.activo).map((s) => h("label", {}, h("input", { type: "checkbox", value: s.id }), s.nombre));
  const f = h("form", { class: "panelito", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target), btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const p = await api("/staff", { method: "POST", body: JSON.stringify({
        nombre_publico: String(fd.get("nombre")).trim(), especialidad: String(fd.get("esp")).trim() || null, color_calendario: String(fd.get("color")),
        service_ids: [...e.target.querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value) }) });
      eq.personal = [...eq.personal, p]; eq.abierto = { id: p.id, tipo: "horario", nuevo: true };
      pintarPersonal();
    } catch (err) { btn.disabled = false; aviso(msg, errTexto(err)); }
  } },
    h("h3", {}, "Nueva persona"),
    h("div", { class: "form-fila" },
      campo("Nombre que verá el cliente", h("input", { name: "nombre", required: true, minlength: 2, maxlength: 80, placeholder: "Ej. Sofía" })),
      campo("Especialidad (opcional)", h("input", { name: "esp", placeholder: "Ej. Colorimetría" })),
      campo("Color en el calendario", h("input", { name: "color", type: "color", value: "#c9a45c" }))),
    h("div", {}, h("div", { class: "campo" }, h("span", {}, "¿Qué servicios hace?")), h("div", { class: "checks" }, checks)),
    msg,
    h("div", { class: "envolver" }, h("button", { class: "btn", type: "submit" }, "Agregar persona"),
      h("button", { class: "btn btn-suave", type: "button", onclick: () => pintarPersonal() }, "Cancelar")));
  return f;
}

// ---------------------------------------------------------------- servicios que hace una persona
function panelServiciosDe(p) {
  const msg = h("div", { "aria-live": "polite" });
  const checks = eq.servicios.filter((s) => s.activo || p.service_ids.includes(s.id)).map((s) => h("label", {}, h("input", { type: "checkbox", value: s.id, checked: p.service_ids.includes(s.id) }), s.nombre));
  return h("div", { class: "panelito" }, h("h3", {}, `Servicios de ${p.nombre_publico}`), h("div", { class: "checks" }, checks), msg,
    h("div", {}, h("button", { class: "btn", type: "button", onclick: async (ev) => {
      ev.target.disabled = true;
      try {
        const nuevo = await api(`/staff/${p.id}/services`, { method: "PUT", body: JSON.stringify({ service_ids: [...ev.target.closest(".panelito").querySelectorAll("input:checked")].map((c) => c.value) }) });
        eq.personal = eq.personal.map((x) => (x.id === p.id ? nuevo : x)); pintarPersonal();
      } catch (err) { ev.target.disabled = false; aviso(msg, errTexto(err)); }
    } }, "Guardar servicios")));
}

// ---------------------------------------------------------------- acceso propio: el barbero entra a ver SU agenda
function panelAcceso(p) {
  const cuerpo = h("div", { class: "panelito" }, h("p", { class: "sub" }, "Cargando…")), msg = h("div", { "aria-live": "polite" });
  const enlace = `${location.origin}/app/barbero.html`;
  const recargar = async (texto) => { eq.personal = await api("/staff"); pintarPersonal(); if (texto) aviso($("msgEquipo") ?? document.createElement("div"), texto, true); };
  (p.user_id ? api("/users?rol=profesional&page_size=100").then((r) => r.items.find((u) => u.id === p.user_id)) : Promise.resolve(null)).then((u) => {
    if (p.user_id) {
      cuerpo.replaceChildren(h("h3", {}, `${p.nombre_publico} ya puede entrar a su agenda`),
        h("p", {}, "Su usuario es ", h("b", {}, u?.email ?? "(correo no disponible)"), ". Entra desde esta dirección y ve solo sus propias reservas:"),
        h("div", { class: "enlace-fila" }, h("input", { class: "enlace", readonly: true, value: enlace, "aria-label": "Dirección de acceso", onfocus: (e) => e.target.select() }),
          h("button", { class: "btn btn-suave", type: "button", onclick: async (ev) => { try { await navigator.clipboard.writeText(enlace); ev.target.textContent = "¡Copiado!"; setTimeout(() => (ev.target.textContent = "Copiar"), 1600); } catch { /* sin portapapeles */ } } }, "Copiar")),
        h("p", { class: "sub" }, "Si olvidó su contraseña, quítale el acceso y créale uno nuevo."), msg,
        h("div", {}, h("button", { class: "mini", type: "button", onclick: async (ev) => {
          if (!confirm(`¿Quitarle el acceso a ${p.nombre_publico}? Sus reservas y su agenda se conservan.`)) return;
          ev.target.disabled = true;
          try { await api(`/staff/${p.id}/access`, { method: "DELETE" }); await recargar(); } catch (err) { ev.target.disabled = false; aviso(msg, errTexto(err)); }
        } }, "Quitar acceso")));
      return;
    }
    const f = h("form", { class: "form-fila", novalidate: true, onsubmit: async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target), v = (k) => String(fd.get(k)).trim();
      if (!/^\S+@\S+\.\S+$/.test(v("email"))) return aviso(msg, "Escribe un correo válido: será su usuario.");
      if (String(fd.get("pass")).length < 10) return aviso(msg, "La contraseña necesita al menos 10 caracteres.");
      try { await api(`/staff/${p.id}/access`, { method: "POST", body: JSON.stringify({ email: v("email"), password: String(fd.get("pass")) }) }); await recargar(); }
      catch (err) { aviso(msg, err.code === "USER_LIMIT_REACHED" ? "Tu plan ya no tiene cupo para más usuarios. Mejora tu plan para dar este acceso." : err.code === "CONFLICT" ? "Ya existe un usuario con ese correo." : errTexto(err)); }
    } },
      campo("Correo de " + p.nombre_publico + " (será su usuario)", h("input", { name: "email", type: "email", autocomplete: "off" })),
      campo("Contraseña inicial (mín. 10 caracteres)", h("input", { name: "pass", type: "text", autocomplete: "off", placeholder: "Ej. Corte.Fino.2026" })), h("button", { class: "btn", type: "submit" }, "Darle acceso"));
    cuerpo.replaceChildren(h("h3", {}, `Dale acceso a ${p.nombre_publico}`), h("p", { class: "sub" }, "Con su propio usuario entra desde el celular, ve solo SUS reservas del día, avisa cuándo empieza y termina cada turno y llama o escribe al cliente. No ve precios, caja ni reportes."), f, msg);
  }).catch((e) => cuerpo.replaceChildren(h("div", { class: "aviso" }, errTexto(e))));
  return cuerpo;
}

// ---------------------------------------------------------------- horario semanal con descanso
function aFilas(tramos) {   // tramos de la API -> una fila por día (primer inicio, último fin, y el hueco entre los dos primeros = descanso)
  return DIAS.map((_, d) => {
    const t = tramos.filter((x) => x.dia_semana === d).sort((a, b) => a.hora_inicio.localeCompare(b.hora_inicio));
    if (!t.length) return { on: false, ini: "09:00", fin: "19:00", desde: "", hasta: "" };
    return { on: true, ini: hhmm(t[0].hora_inicio), fin: hhmm(t[t.length - 1].hora_fin), desde: t.length > 1 ? hhmm(t[0].hora_fin) : "", hasta: t.length > 1 ? hhmm(t[1].hora_inicio) : "" };
  });
}
const FILAS_NUEVAS = DIAS.map((_, d) => ({ on: d < 6, ini: "09:00", fin: "19:00", desde: d < 6 ? "13:00" : "", hasta: d < 6 ? "14:00" : "" }));

function panelHorario(p) {
  const cuerpo = h("div", { class: "panelito" }, h("p", { class: "sub" }, "Cargando horario…"));
  const nuevo = eq.abierto?.nuevo;
  (nuevo ? Promise.resolve(FILAS_NUEVAS.map((f) => ({ ...f }))) : api(`/staff/${p.id}/schedule`).then(aFilas)).then((filas) => {
    const msg = h("div", { "aria-live": "polite" });
    const ctl = filas.map((f, d) => {
      const i = { ini: h("input", { type: "time", value: f.ini }), fin: h("input", { type: "time", value: f.fin }), desde: h("input", { type: "time", value: f.desde }), hasta: h("input", { type: "time", value: f.hasta }) };
      const on = h("input", { type: "checkbox", checked: f.on });
      const horas = h("div", { class: "horas" + (f.on ? "" : " off") }, h("span", { class: "grupo" }, h("span", { class: "etq" }, "Trabaja de"), i.ini, h("span", {}, "a"), i.fin), h("span", { class: "grupo" }, h("span", { class: "etq" }, "Descanso de"), i.desde, h("span", {}, "a"), i.hasta));
      on.addEventListener("change", () => horas.classList.toggle("off", !on.checked));
      return { on, i, fila: h("div", { class: "dia-h" }, h("label", { class: "chk" }, on, DIAS[d]), horas) };
    });
    cuerpo.replaceChildren(
      h("h3", {}, nuevo ? `Horario de ${p.nombre_publico}` : `Horario semanal de ${p.nombre_publico}`),
      nuevo ? h("div", { class: "aviso ok" }, "Persona agregada. Define su horario: sin horario no aparece para reservar.") : null,
      h("div", { class: "dias-h" }, ctl.map((c) => c.fila)),
      h("p", { class: "sub" }, "El descanso es opcional: deja las dos horas vacías si no tiene."), msg,
      h("div", {}, h("button", { class: "btn", type: "button", onclick: async (ev) => {
        const tramos = [];
        for (const [d, c] of ctl.entries()) {
          if (!c.on.checked) continue;
          const [ini, fin, desde, hasta] = [c.i.ini.value, c.i.fin.value, c.i.desde.value, c.i.hasta.value];
          if (!ini || !fin || fin <= ini) return aviso(msg, `${DIAS[d]}: la hora de salida debe ser después de la de entrada.`);
          if (desde || hasta) {
            if (!desde || !hasta || hasta <= desde) return aviso(msg, `${DIAS[d]}: el descanso necesita hora de inicio y de fin, en orden.`);
            if (desde <= ini || hasta >= fin) return aviso(msg, `${DIAS[d]}: el descanso debe quedar dentro del horario de trabajo.`);
            tramos.push({ dia_semana: d, hora_inicio: ini, hora_fin: desde }, { dia_semana: d, hora_inicio: hasta, hora_fin: fin });
          } else tramos.push({ dia_semana: d, hora_inicio: ini, hora_fin: fin });
        }
        ev.target.disabled = true;
        try {
          await api(`/staff/${p.id}/schedule`, { method: "PUT", body: JSON.stringify({ tramos }) });
          aviso(msg, "Horario guardado. Ya se puede reservar con esta persona en esas horas.", true);
          if (eq.abierto) delete eq.abierto.nuevo;
        } catch (err) { aviso(msg, errTexto(err)); }
        ev.target.disabled = false;
      } }, "Guardar horario")));
  }).catch((e) => cuerpo.replaceChildren(h("div", { class: "aviso" }, errTexto(e))));
  return cuerpo;
}

// ---------------------------------------------------------------- ausencias y bloqueos puntuales (almuerzo de hoy, cita médica…)
function panelAusencias(p) {
  const cuerpo = h("div", { class: "panelito" }, h("p", { class: "sub" }, "Cargando…"));
  const fmt = (iso) => new Intl.DateTimeFormat("es-CO", { timeZone: tz(), weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true }).format(new Date(iso));
  const hoyStr = new Intl.DateTimeFormat("en-CA", { timeZone: tz() }).format(new Date());
  async function pintar() {
    const msg = h("div", { "aria-live": "polite" });
    let bloques = [];
    try { bloques = await api(`/staff/${p.id}/blocks`); } catch (e) { return cuerpo.replaceChildren(h("div", { class: "aviso" }, errTexto(e))); }
    const futuros = bloques.filter((b) => new Date(b.fin) > new Date()).sort((a, b) => a.inicio.localeCompare(b.inicio));
    const f = h("form", { novalidate: true, class: "form-fila", onsubmit: async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target), [dia, d, hs] = [fd.get("dia"), fd.get("desde"), fd.get("hasta")];
      if (!dia || !d || !hs || hs <= d) return aviso(msg, "Elige el día y una hora de fin posterior a la de inicio.");
      try {
        const b = await api(`/staff/${p.id}/blocks`, { method: "POST", body: JSON.stringify({ inicio: `${dia}T${d}:00${desfase(dia)}`, fin: `${dia}T${hs}:00${desfase(dia)}`, motivo: String(fd.get("motivo")).trim() || null }) });
        await pintar();
        if (b.citas_afectadas > 0) aviso(cuerpo.querySelector("[data-msg]"), `Bloqueado. Ojo: ya hay ${b.citas_afectadas} ${b.citas_afectadas === 1 ? "cita agendada" : "citas agendadas"} en ese rango; no se cancelan solas, revísalas en la agenda.`);
        else aviso(cuerpo.querySelector("[data-msg]"), "Bloqueado. Ese rato ya no se ofrece para reservar.", true);
      } catch (err) { aviso(msg, errTexto(err)); }
    } },
      campo("Día", h("input", { name: "dia", type: "date", value: hoyStr, min: hoyStr })), campo("Desde", h("input", { name: "desde", type: "time" })), campo("Hasta", h("input", { name: "hasta", type: "time" })),
      campo("Motivo (opcional)", h("input", { name: "motivo", maxlength: 200, placeholder: "Almuerzo, trámite…" })), h("button", { class: "btn", type: "submit" }, "Bloquear"));
    cuerpo.replaceChildren(h("h3", {}, `Ausencias de ${p.nombre_publico}`), h("p", { class: "sub" }, "Para un descanso de un día o una salida puntual. Los descansos de todas las semanas van en el horario."),
      f, msg, h("div", { "data-msg": "", "aria-live": "polite" }),
      ...(futuros.length ? futuros.map((b) => h("div", { class: "bloqueo" }, h("span", {}, `${fmt(b.inicio)} → ${fmt(b.fin).split(", ").pop()}${b.motivo ? " · " + b.motivo : ""}`),
        h("button", { class: "mini", type: "button", onclick: async (ev) => { ev.target.disabled = true; try { await api(`/staff/${p.id}/blocks/${b.id}`, { method: "DELETE" }); pintar(); } catch (err) { ev.target.disabled = false; aviso(msg, errTexto(err)); } } }, "Quitar")))
        : [h("p", { class: "sub" }, "No hay ausencias próximas.")]));
  }
  pintar();
  return cuerpo;
}

// ================================================================== SERVICIOS Y PRECIOS
async function cargarServicios() {
  try { eq.servicios = (await api("/catalog/services?page_size=100&sort=nombre")).items; } catch (e) { return vistas.servicios.replaceChildren(h("div", { class: "aviso" }, e.message)); }
  pintarServicios();
}

function pintarServicios(abrirAlta = false) {
  const cont = vistas.servicios, alta = h("div");
  cont.replaceChildren(
    h("div", { class: "titulo" }, h("h2", {}, "Servicios y precios"), h("button", { class: "btn", type: "button", onclick: () => alta.replaceChildren(formServicio()) }, "Agregar servicio")),
    alta, h("div", { "aria-live": "polite", id: "msgServ" }),
    ...(eq.servicios.length ? eq.servicios.map(filaServicio) : [h("div", { class: "vacio" }, h("b", {}, "Sin servicios"), "Agrega el primero para poder reservar.")]));
  if (abrirAlta) alta.replaceChildren(formServicio());
}

function formServicio() {
  const msg = h("div", { "aria-live": "polite" });
  return h("form", { class: "panelito", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target), btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      await api("/catalog/services", { method: "POST", body: JSON.stringify({ nombre: String(fd.get("nombre")).trim(), duracion_min: Number(fd.get("dur")), precio: String(Number(fd.get("precio"))) }) });
      await cargarServicios();
      aviso($("msgServ"), "Servicio agregado. Para que se pueda reservar, asígnalo a alguien en Personal → Servicios.", true);
    } catch (err) { btn.disabled = false; aviso(msg, errTexto(err)); }
  } },
    h("h3", {}, "Nuevo servicio"),
    h("div", { class: "form-fila" },
      campo("Nombre", h("input", { name: "nombre", required: true, minlength: 2, maxlength: 120, placeholder: "Ej. Corte + barba" })),
      campo("Duración (minutos)", h("input", { name: "dur", type: "number", min: 5, max: 720, step: 5, value: 30 })),
      campo("Precio (COP)", h("input", { name: "precio", type: "number", min: 0, step: 500, value: 25000 }))),
    msg, h("div", { class: "envolver" }, h("button", { class: "btn", type: "submit" }, "Agregar servicio"), h("button", { class: "btn btn-suave", type: "button", onclick: () => pintarServicios() }, "Cancelar")));
}

function filaServicio(s) {
  const i = { nombre: h("input", { type: "text", value: s.nombre, maxlength: 120, "aria-label": "Nombre" }), dur: h("input", { type: "number", value: s.duracion_min, min: 5, max: 720, step: 5, "aria-label": "Duración en minutos" }),
    precio: h("input", { type: "number", value: Math.round(Number(s.precio)), min: 0, step: 500, "aria-label": "Precio" }), activo: h("input", { type: "checkbox", checked: s.activo }) };
  const guardar = h("button", { class: "mini principal", type: "button", disabled: true }, "Guardar");
  const fila = h("div", { class: "servicio-fila" + (s.activo ? "" : " inactivo") },
    i.nombre, h("div", { class: "envolver" }, i.dur, h("span", { class: "sufijo" }, "min")), h("div", { class: "envolver" }, h("span", { class: "sufijo" }, "$"), i.precio),
    h("label", { class: "envolver sufijo" }, i.activo, "Activo"), guardar);
  const sucio = () => (guardar.disabled = !(i.nombre.value.trim() !== s.nombre || Number(i.dur.value) !== s.duracion_min || Number(i.precio.value) !== Math.round(Number(s.precio)) || i.activo.checked !== s.activo));
  Object.values(i).forEach((el) => el.addEventListener("input", sucio));
  guardar.addEventListener("click", async () => {
    guardar.disabled = true;
    try {
      const nuevo = await api(`/catalog/services/${s.id}`, { method: "PATCH", body: JSON.stringify({ nombre: i.nombre.value.trim(), duracion_min: Number(i.dur.value), precio: String(Number(i.precio.value)), activo: i.activo.checked }) });
      eq.servicios = eq.servicios.map((x) => (x.id === s.id ? nuevo : x)); pintarServicios();
      aviso($("msgServ"), `"${nuevo.nombre}" guardado. Las reservas nuevas ya usan $${Math.round(Number(nuevo.precio)).toLocaleString("es-CO")}; las citas ya hechas conservan su precio.`, true);
    } catch (err) { guardar.disabled = false; aviso($("msgServ"), errTexto(err)); }
  });
  return fila;
}
