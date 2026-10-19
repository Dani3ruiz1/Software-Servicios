// "Nueva cita" desde el panel (recepción / administrador): cliente (buscar o crear), servicios, profesional, día y hora libre.
// Usa lo definido en panel.js (api, h, st, hora, money, aviso, campo, errTexto, ir, $) y panel-clientes.js.
async function nuevaCita(fechaInicial) {
  const dlg = $("nueva"), cuerpo = $("nuevaCuerpo");
  const s = { cliente: null, servicios: new Set(), staff: "", fecha: fechaInicial || st.fecha, slot: null, slots: [], catalogo: [], personal: [] };
  try {
    [s.catalogo, s.personal] = await Promise.all([api("/catalog/services?page_size=100&activo=true&sort=nombre").then((r) => r.items), api("/staff")]);
  } catch (e) { return aviso($("msg"), errTexto(e)); }
  const msg = h("div", { "aria-live": "polite" });

  // ---- 1. cliente
  const cliBox = h("div"), buscar = h("input", { type: "search", placeholder: "Buscar cliente por nombre o celular", "aria-label": "Buscar cliente", autocomplete: "off" }), sug = h("div", { class: "sugerencias" });
  let t; buscar.addEventListener("input", () => { clearTimeout(t); t = setTimeout(async () => {
    const q = buscar.value.trim(); if (q.length < 2) return sug.replaceChildren();
    try { const r = await api(`/customers?${new URLSearchParams({ q, page_size: "6" })}`); pintarSug(r.items, q); } catch { sug.replaceChildren(); }
  }, 250); });
  const elegir = (c) => { s.cliente = c; pintarCliente(); };
  function pintarSug(items, q) {
    sug.replaceChildren(...items.map((c) => h("button", { type: "button", class: "sug", onclick: () => elegir(c) }, h("b", {}, c.nombre), " ", h("small", {}, c.telefono ?? ""))),
      h("button", { type: "button", class: "sug nuevo", onclick: () => formNuevo(q) }, `＋ Crear cliente «${q}»`));
  }
  function formNuevo(q) {
    const nom = h("input", { value: /\d/.test(q) ? "" : q, maxlength: 120, "aria-label": "Nombre" }), tel = h("input", { type: "tel", value: /\d/.test(q) ? q : "", placeholder: "300 123 4567", "aria-label": "Celular" });
    const m = h("div", { "aria-live": "polite" });
    sug.replaceChildren(h("div", { class: "panelito" }, h("div", { class: "form-fila" }, campo("Nombre", nom), campo("Celular", tel)), m,
      h("button", { class: "btn", type: "button", onclick: async () => {
        if (nom.value.trim().length < 2) return aviso(m, "Escribe el nombre.");
        try { elegir(await api("/customers", { method: "POST", body: JSON.stringify({ nombre: nom.value.trim(), telefono: tel.value.trim() || null, origen: "recepcion" }) })); } catch (e) { aviso(m, errTexto(e)); }
      } }, "Guardar y usar")));
  }
  function pintarCliente() {
    cliBox.replaceChildren(s.cliente
      ? h("div", { class: "cliente-elegido" }, h("span", {}, h("b", {}, s.cliente.nombre), " ", h("small", {}, s.cliente.telefono ?? "")), h("button", { class: "mini", type: "button", onclick: () => { s.cliente = null; pintarCliente(); } }, "Cambiar"))
      : h("div", {}, buscar, sug));
  }

  // ---- 2. servicios y profesional
  const serviciosBox = h("div", { class: "chips", role: "group", "aria-label": "Servicios" });
  const pintarServicios = () => serviciosBox.replaceChildren(...s.catalogo.map((v) => h("button", { type: "button", class: "chip", "aria-pressed": s.servicios.has(v.id),
    onclick: () => { s.servicios.has(v.id) ? s.servicios.delete(v.id) : s.servicios.add(v.id); s.slot = null; pintarServicios(); cargarHoras(); } }, `${v.nombre} · ${v.duracion_min} min · ${money(v.precio)}`)));
  const selStaff = h("select", { "aria-label": "Profesional", onchange: (e) => { s.staff = e.target.value; s.slot = null; cargarHoras(); } },
    h("option", { value: "" }, "Cualquiera disponible"), s.personal.filter((p) => p.activo !== false).map((p) => h("option", { value: p.id }, p.nombre_publico)));

  // ---- 3. día y hora
  const fechaIn = h("input", { type: "date", value: s.fecha, "aria-label": "Día", onchange: (e) => { s.fecha = e.target.value; s.slot = null; cargarHoras(); } });
  const horasBox = h("div", { class: "chips horas", role: "group", "aria-label": "Horas libres" });
  async function cargarHoras() {
    if (!s.servicios.size || !s.fecha) return horasBox.replaceChildren(h("small", { class: "sub" }, "Elige servicios y un día para ver las horas libres."));
    horasBox.replaceChildren(h("small", { class: "sub" }, "Buscando horas…"));
    const q = new URLSearchParams({ fecha_desde: s.fecha }); s.servicios.forEach((id) => q.append("service_ids", id)); if (s.staff) q.set("staff_id", s.staff);
    try { const r = await api(`/appointments/availability?${q}`); s.slots = r.dias[0]?.slots ?? []; } catch (e) { return horasBox.replaceChildren(h("div", { class: "aviso" }, errTexto(e))); }
    pintarHoras();
  }
  const pintarHoras = () => horasBox.replaceChildren(...(s.slots.length ? s.slots.map((x) => h("button", { type: "button", class: "chip", "aria-pressed": s.slot === x.inicio,
    onclick: () => { s.slot = x.inicio; pintarHoras(); } }, hora(x.inicio))) : [h("small", { class: "sub" }, "No hay horas libres ese día con esa selección. Prueba otro día o profesional.")]));

  const notas = h("textarea", { rows: 2, maxlength: 1000, placeholder: "Notas (opcional)", "aria-label": "Notas" });
  const guardar = h("button", { class: "btn", type: "button", onclick: async (ev) => {
    if (!s.cliente) return aviso(msg, "Elige o crea el cliente.");
    if (!s.servicios.size) return aviso(msg, "Elige al menos un servicio.");
    if (!s.slot) return aviso(msg, "Elige una hora libre.");
    ev.target.disabled = true;
    try {
      await api("/appointments", { method: "POST", body: JSON.stringify({ customer_id: s.cliente.id, service_ids: [...s.servicios], inicio: s.slot, notas: notas.value.trim() || null,
        ...(s.staff ? { staff_id: s.staff } : { asignar_automatico: true }) }) });
      dlg.close(); await ir(s.fecha); aviso($("msg"), "Cita creada.", true);
    } catch (e) { ev.target.disabled = false; aviso(msg, errTexto(e)); if (e.code === "SLOT_TAKEN" || e.status === 409) cargarHoras(); }
  } }, "Crear cita");

  cuerpo.replaceChildren(h("button", { class: "cerrar", type: "button", "aria-label": "Cerrar", onclick: () => dlg.close() }, "✕"), h("h3", {}, "Nueva cita"),
    campo("Cliente", cliBox), campo("Servicios", serviciosBox), h("div", { class: "form-fila" }, campo("Profesional", selStaff), campo("Día", fechaIn)),
    campo("Hora libre", horasBox), campo("Notas", notas), msg, guardar);
  pintarCliente(); pintarServicios(); cargarHoras();
  dlg.showModal();
}
