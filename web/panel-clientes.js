// Panel del negocio, parte 4: clientes (lista paginada, búsqueda, ficha con historial y edición, alta).
// Usa lo definido antes (api, h, aviso, campo, errTexto, fechaHora, ETIQUETA, vistas, $).
const cl = { q: "", orden: "nombre", page: 1, abierto: null, timer: null };
const PAGINA = 15;
const ORIGEN = { reserva_web: "Reserva en línea", whatsapp: "WhatsApp", recepcion: "Recepción" };
const fechaCorta = (iso) => new Intl.DateTimeFormat("es-CO", { timeZone: tz(), day: "numeric", month: "short", year: "numeric" }).format(new Date(iso));

async function cargarClientes() {
  const cont = vistas.clientes;
  if (!cont.querySelector("#clBuscar")) armarClientes(cont);
  await pintarLista();
}

function armarClientes(cont) {
  const buscar = h("input", { id: "clBuscar", type: "search", placeholder: "Buscar por nombre, celular, correo o documento", "aria-label": "Buscar clientes", value: cl.q });
  buscar.addEventListener("input", () => { clearTimeout(cl.timer); cl.timer = setTimeout(() => { cl.q = buscar.value.trim(); cl.page = 1; cl.abierto = null; pintarLista(); }, 300); });
  const orden = h("select", { "aria-label": "Ordenar", onchange: (e) => { cl.orden = e.target.value; cl.page = 1; pintarLista(); } },
    h("option", { value: "nombre" }, "Nombre (A–Z)"), h("option", { value: "-created_at" }, "Más recientes primero"));
  cont.replaceChildren(
    h("div", { class: "titulo" }, h("h2", {}, "Clientes"), h("button", { class: "btn", type: "button", onclick: () => $("clAlta").replaceChildren(formCliente()) }, "Agregar cliente")),
    h("div", { id: "clAlta" }), h("div", { class: "buscador" }, buscar, orden), h("div", { id: "clMsg", "aria-live": "polite" }), h("div", { id: "clLista" }), h("div", { id: "clPie", class: "paginacion" }));
}

async function pintarLista() {
  const q = new URLSearchParams({ page: String(cl.page), page_size: String(PAGINA), sort: cl.orden });
  if (cl.q) q.set("q", cl.q);
  let r;
  try { r = await api(`/customers?${q}`); } catch (e) { return aviso($("clMsg"), errTexto(e)); }
  aviso($("clMsg"), "");
  const lista = $("clLista");
  lista.replaceChildren(...(r.items.length ? r.items.map(filaCliente) : [h("div", { class: "vacio" },
    h("b", {}, cl.q ? "Nadie coincide con esa búsqueda" : "Aún no hay clientes"), cl.q ? "Prueba con otra parte del nombre o del celular." : "Aparecen solos cuando alguien reserva, o agrégalos tú.")]));
  const desde = r.total ? (r.page - 1) * r.page_size + 1 : 0, hasta = Math.min(r.page * r.page_size, r.total);
  $("clPie").replaceChildren(h("span", { class: "sub" }, r.total ? `${desde}–${hasta} de ${r.total}` : ""),
    h("button", { class: "mini", type: "button", disabled: r.page <= 1, onclick: () => { cl.page--; cl.abierto = null; pintarLista(); } }, "Anterior"),
    h("button", { class: "mini", type: "button", disabled: hasta >= r.total, onclick: () => { cl.page++; cl.abierto = null; pintarLista(); } }, "Siguiente"));
}

function filaCliente(c) {
  const abierto = cl.abierto === c.id;
  const art = h("article", { class: "cliente" + (abierto ? " abierto" : "") },
    h("button", { class: "cliente-cab", type: "button", "aria-expanded": abierto, onclick: () => { cl.abierto = abierto ? null : c.id; pintarLista(); } },
      h("b", {}, c.nombre), h("span", { class: "sub" }, [c.telefono, c.email].filter(Boolean).join(" · ") || "Sin datos de contacto"),
      h("span", { class: "sub" }, `${ORIGEN[c.origen] ?? c.origen} · desde ${fechaCorta(c.created_at)}`)));
  if (abierto) art.append(ficha(c));
  return art;
}

// ---------------------------------------------------------------- ficha: contadores, historial y edición
function ficha(c) {
  const cuerpo = h("div", { class: "panelito" }, h("p", { class: "sub" }, "Cargando historial…"));
  api(`/customers/${c.id}/history?page_size=20&sort=-inicio`).then((hi) => {
    const msg = h("div", { "aria-live": "polite" });
    const f = h("form", { novalidate: true, class: "form-fila", onsubmit: async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target), v = (k) => String(fd.get(k)).trim();
      if (v("nombre").length < 2) return aviso(msg, "El nombre necesita al menos 2 letras.");
      try {
        await api(`/customers/${c.id}`, { method: "PATCH", body: JSON.stringify({ nombre: v("nombre"), telefono: v("telefono") || null, email: v("email") || null, documento: v("documento") || null, notas: v("notas") || null }) });
        await pintarLista(); aviso($("clMsg"), "Cliente actualizado.", true);
      } catch (err) { aviso(msg, errTexto(err)); }
    } },
      campo("Nombre", h("input", { name: "nombre", value: hi.cliente.nombre, maxlength: 120 })), campo("Celular", h("input", { name: "telefono", value: hi.cliente.telefono ?? "", type: "tel" })),
      campo("Correo", h("input", { name: "email", value: hi.cliente.email ?? "", type: "email" })), campo("Documento", h("input", { name: "documento", value: hi.cliente.documento ?? "", maxlength: 30 })),
      Object.assign(campo("Notas (solo las ve el negocio)", h("input", { name: "notas", value: hi.cliente.notas ?? "", placeholder: "Alergias, preferencias…", maxlength: 1000 })), { style: "grid-column: 1 / -1" }),
      h("button", { class: "btn", type: "submit" }, "Guardar cambios"));
    const consent = hi.cliente.consentimiento_datos
      ? `Autorizó el tratamiento de sus datos${hi.cliente.consentimiento_fecha ? " el " + fechaCorta(hi.cliente.consentimiento_fecha) : ""}.`
      : "Todavía no hay autorización de tratamiento de datos registrada.";
    cuerpo.replaceChildren(
      h("div", { class: "conteo" }, ...[[hi.total_citas, "citas"], [hi.atendidas, "atendidas"], [hi.canceladas, "canceladas"], [hi.no_asistio, "no vino"]].map(([n, t]) => h("span", {}, h("b", {}, String(n)), t)),
        h("span", {}, h("b", {}, hi.ultima_visita ? fechaCorta(hi.ultima_visita) : "—"), "última visita")),
      h("h3", {}, "Historial"),
      ...(hi.citas.length ? hi.citas.map((x) => h("div", { class: "bloqueo" },
        h("span", {}, `${fechaHoraCorta(x.inicio)} · ${x.servicios.map((s) => s.nombre).join(" + ") || "Sin servicio"}${x.profesional ? " · " + x.profesional : ""}`),
        h("span", { class: `estado ${x.estado}`, style: "grid-column:auto;grid-row:auto;flex:none" }, ETIQUETA[x.estado] ?? x.estado))) : [h("p", { class: "sub" }, "Todavía no tiene citas.")]),
      h("h3", { style: "margin-top:1rem" }, "Datos"), f, msg, h("p", { class: "sub" }, consent));
  }).catch((e) => cuerpo.replaceChildren(h("div", { class: "aviso" }, errTexto(e))));
  return cuerpo;
}
const fechaHoraCorta = (iso) => new Intl.DateTimeFormat("es-CO", { timeZone: tz(), weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true }).format(new Date(iso));

// ---------------------------------------------------------------- alta manual
function formCliente() {
  const msg = h("div", { "aria-live": "polite" });
  return h("form", { class: "panelito", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target), v = (k) => String(fd.get(k)).trim();
    if (v("nombre").length < 2) return aviso(msg, "Escribe el nombre del cliente.");
    try {
      const c = await api("/customers", { method: "POST", body: JSON.stringify({ nombre: v("nombre"), telefono: v("telefono") || null, email: v("email") || null, documento: v("documento") || null, consentimiento_datos: !!fd.get("consent"), origen: "recepcion" }) });
      $("clAlta").replaceChildren(); cl.q = c.nombre; $("clBuscar").value = c.nombre; cl.page = 1; cl.abierto = c.id;
      await pintarLista(); aviso($("clMsg"), "Cliente agregado.", true);
    } catch (err) { aviso(msg, errTexto(err)); }
  } },
    h("h3", {}, "Nuevo cliente"),
    h("div", { class: "form-fila" }, campo("Nombre", h("input", { name: "nombre", maxlength: 120, required: true })), campo("Celular", h("input", { name: "telefono", type: "tel", placeholder: "300 123 4567" })),
      campo("Correo (opcional)", h("input", { name: "email", type: "email" })), campo("Documento (opcional)", h("input", { name: "documento", maxlength: 30 }))),
    h("label", { class: "checks" }, h("span", { class: "envolver" }, h("input", { type: "checkbox", name: "consent" }), "El cliente autorizó el tratamiento de sus datos personales (Ley 1581)")),
    msg, h("div", { class: "envolver" }, h("button", { class: "btn", type: "submit" }, "Agregar cliente"), h("button", { class: "btn btn-suave", type: "button", onclick: () => $("clAlta").replaceChildren() }, "Cancelar")));
}
