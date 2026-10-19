// Panel del negocio, parte 3: cobrar una cita (pago dividido, propina, comprobante) y la caja (abrir, movimientos, cuadre, cierre).
// Usa lo definido en panel.js y panel-equipo.js (api, h, aviso, st, hora, money, campo, errTexto, vista, $).
const METODOS = { efectivo: "Efectivo", datafono: "Datáfono", transferencia: "Transferencia", otro: "Otro" };
const num = (v) => Number(v) || 0;
const pesos = (n) => money(n);

// ================================================================== COBRAR UNA CITA (desde la agenda)
async function abrirCobro(c, fila) {
  fila.querySelector(".panelito")?.remove();
  let caja;
  try { caja = await api("/cash/sessions/current"); } catch (e) { return aviso($("msg"), errTexto(e)); }
  if (!caja) {
    return $("msg").replaceChildren(h("div", { class: "aviso", role: "alert" }, "Para cobrar primero abre la caja. ",
      h("button", { class: "mini", type: "button", onclick: () => vista("caja") }, "Ir a Caja")));
  }
  aviso($("msg"), "");
  const base = num(c.total);
  const msg = h("div", { "aria-live": "polite" });
  const resumen = h("p", { class: "sub" });
  const lineas = h("div", { class: "dias-h" });
  const propina = h("input", { type: "number", min: 0, step: 500, value: 0, "aria-label": "Propina" });
  const filas = [];

  const total = () => base + num(propina.value);
  const pagado = () => filas.reduce((s, f) => s + num(f.monto.value), 0);
  const refrescar = () => {
    const falta = total() - pagado();
    resumen.replaceChildren(`Total a cobrar `, h("b", {}, pesos(total())), ` · Pagado ${pesos(pagado())} · `,
      h("b", { style: `color:${falta === 0 ? "var(--ok)" : "var(--error)"}` }, falta === 0 ? "Cuadra" : falta > 0 ? `Faltan ${pesos(falta)}` : `Sobran ${pesos(-falta)}`));
    cobrar.disabled = falta !== 0 || pagado() <= 0;
    cobrar.textContent = `Cobrar ${pesos(total())}`;
  };
  const nuevaFila = (monto) => {
    const metodo = h("select", { "aria-label": "Método de pago" }, Object.entries(METODOS).map(([k, v]) => h("option", { value: k }, v)));
    const montoI = h("input", { type: "number", min: 0, step: 500, value: monto, "aria-label": "Monto" });
    const banco = h("input", { placeholder: "Banco (obligatorio)", maxlength: 80, "aria-label": "Banco", hidden: true });
    const ref = h("input", { placeholder: "Referencia (opcional)", maxlength: 80, "aria-label": "Referencia", hidden: true });
    const quitar = h("button", { class: "mini", type: "button", onclick: () => { filas.splice(filas.indexOf(f), 1); f.el.remove(); refrescar(); } }, "Quitar");
    const f = { metodo, monto: montoI, banco, ref, el: h("div", { class: "pago-fila" }, metodo, h("span", { class: "envolver" }, h("span", { class: "sufijo" }, "$"), montoI), banco, ref, quitar) };
    metodo.addEventListener("change", () => { banco.hidden = ref.hidden = metodo.value !== "transferencia"; });
    montoI.addEventListener("input", refrescar);
    filas.push(f); lineas.append(f.el); quitar.hidden = filas.length === 1;
    filas.forEach((x) => (x.el.lastChild.hidden = filas.length === 1));
    return f;
  };
  propina.addEventListener("input", () => { if (filas.length === 1) filas[0].monto.value = total(); refrescar(); });

  const cobrar = h("button", { class: "btn", type: "button", onclick: async () => {
    const pagos = [];
    for (const f of filas) {
      if (num(f.monto.value) <= 0) continue;
      if (f.metodo.value === "transferencia" && !f.banco.value.trim()) return aviso(msg, "En una transferencia escribe el banco.");
      pagos.push({ metodo: f.metodo.value, monto: String(num(f.monto.value)), entidad_bancaria: f.banco.value.trim() || null, referencia: f.ref.value.trim() || null });
    }
    cobrar.disabled = true;
    try {
      const venta = await api("/sales", { method: "POST", body: JSON.stringify({ appointment_id: c.id, propina: String(num(propina.value)), pagos }) });
      await mostrarComprobante(venta.id, fila);
      await ir(st.fecha);
    } catch (err) {
      cobrar.disabled = false;
      if (err.code === "PAYMENT_MISMATCH" && err.data?.details?.total && filas.length === 1) {
        filas[0].monto.value = num(err.data.details.total); refrescar();
        aviso(msg, `El total real es ${pesos(err.data.details.total)} (incluye impuestos o propina). Ya lo ajusté; revisa y cobra de nuevo.`);
      } else if (err.code === "CASH_SESSION_REQUIRED") aviso(msg, "La caja está cerrada. Ábrela en la pestaña Caja.");
      else if (err.code === "CONFLICT") aviso(msg, "Esta cita ya tiene una venta registrada.");
      else aviso(msg, errTexto(err));
    }
  } }, "Cobrar");

  nuevaFila(base);
  const panel = h("div", { class: "panelito" },
    h("h3", {}, `Cobrar a ${c.cliente.nombre}`),
    h("div", {}, c.servicios.map((s) => h("div", { class: "linea-cobro" }, h("span", {}, s.nombre), h("span", { class: "precio" }, pesos(s.precio_pactado))))),
    h("div", { class: "form-fila" }, campo("Propina (opcional)", propina)),
    h("div", {}, h("div", { class: "campo" }, h("span", {}, "¿Cómo paga?")), lineas,
      h("button", { class: "mini", type: "button", style: "margin-top:.6rem", onclick: () => { nuevaFila(Math.max(total() - pagado(), 0)); refrescar(); } }, "Dividir en otro pago")),
    resumen, msg,
    h("div", { class: "envolver" }, cobrar, h("button", { class: "btn btn-suave", type: "button", onclick: () => panel.remove() }, "Cancelar")));
  fila.append(panel); refrescar();
}

async function mostrarComprobante(saleId, contenedor) {
  const r = await api(`/sales/${saleId}/receipt`);
  contenedor.querySelector(".panelito")?.remove();
  const cuerpo = h("div", { class: "panelito comprobante" },
    h("div", { class: "aviso ok" }, `Cobrado. Comprobante ${r.numero}`),
    h("div", {}, r.items.map((i) => h("div", { class: "linea-cobro" }, h("span", {}, i.descripcion), h("span", { class: "precio" }, pesos(i.precio_cobrado)))),
      num(r.propina) ? h("div", { class: "linea-cobro" }, h("span", {}, "Propina"), h("span", { class: "precio" }, pesos(r.propina))) : null,
      h("div", { class: "linea-cobro total" }, h("span", {}, "Total"), h("span", { class: "precio" }, pesos(r.total)))),
    h("div", { class: "sub" }, r.pagos.map((p) => `${METODOS[p.metodo] ?? p.metodo} ${pesos(p.monto)}${p.entidad_bancaria ? " (" + p.entidad_bancaria + ")" : ""}`).join(" · ")),
    h("div", {}, h("button", { class: "btn btn-suave", type: "button", onclick: (ev) => ev.target.closest(".panelito").remove() }, "Cerrar")));
  contenedor.append(cuerpo);
}

// ================================================================== CAJA
async function cargarCaja() {
  const cont = vistas.caja;
  let actual, historial;
  try { [actual, historial] = await Promise.all([api("/cash/sessions/current"), api("/cash/sessions?page_size=6").then((r) => r.items)]); }
  catch (e) { return cont.replaceChildren(h("div", { class: "aviso" }, errTexto(e))); }
  if (!actual) return pintarCajaCerrada(historial);
  try { pintarCajaAbierta(await api(`/cash/sessions/${actual.id}/cuadre`), historial); }
  catch (e) { cont.replaceChildren(h("div", { class: "aviso" }, errTexto(e))); }
}

const fechaHora = (iso) => new Intl.DateTimeFormat("es-CO", { timeZone: tz(), day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true }).format(new Date(iso));

function historialCaja(historial) {
  const cerradas = historial.filter((s) => s.estado !== "abierta");
  if (!cerradas.length) return null;
  return h("section", { class: "bloque" }, h("h3", {}, "Cierres anteriores"), ...cerradas.map((s) => {
    const d = num(s.diferencia);
    return h("div", { class: "bloqueo" }, h("span", {}, `${fechaHora(s.abierta_en)} → ${s.cerrada_en ? fechaHora(s.cerrada_en) : ""}`),
      h("span", { class: "sub" }, `Contado ${pesos(s.monto_contado ?? 0)}`), h("b", { style: `color:${d === 0 ? "var(--ok)" : "var(--error)"}` }, d === 0 ? "Cuadró" : d < 0 ? `Faltaron ${pesos(-d)}` : `Sobraron ${pesos(d)}`));
  }));
}

function pintarCajaCerrada(historial, ultimoCierre = null) {
  const msg = h("div", { "aria-live": "polite" });
  const f = h("form", { class: "panelito", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try { await api("/cash/sessions/open", { method: "POST", body: JSON.stringify({ monto_inicial: String(num(fd.get("monto"))), notas: String(fd.get("notas")).trim() || null }) }); await cargarCaja(); }
    catch (err) { aviso(msg, errTexto(err)); }
  } },
    h("h3", {}, "Abrir caja"), h("p", { class: "sub" }, "Escribe el efectivo con el que empiezas. Mientras la caja esté cerrada no se puede cobrar."),
    h("div", { class: "form-fila" }, campo("Efectivo inicial (COP)", h("input", { name: "monto", type: "number", min: 0, step: 500, value: 0 })), campo("Notas (opcional)", h("input", { name: "notas", maxlength: 500 }))),
    msg, h("div", {}, h("button", { class: "btn", type: "submit" }, "Abrir caja")));
  vistas.caja.replaceChildren(h("div", { class: "titulo" }, h("h2", {}, "Caja"), h("span", { class: "estado atendida" }, "Cerrada")), ...[ultimoCierre, f, historialCaja(historial)].filter(Boolean));
}

function dato(valor, etiqueta) { return h("span", {}, h("b", {}, valor), etiqueta); }

function pintarCajaAbierta(cu, historial) {
  const s = cu.sesion, msg = h("div", { "aria-live": "polite", id: "msgCaja" });
  const stats = h("div", { class: "conteo" }, dato(pesos(cu.total_vendido), "vendido"), dato(String(cu.ventas_pagadas), cu.ventas_pagadas === 1 ? "venta" : "ventas"),
    dato(pesos(cu.propinas), "en propinas"), dato(pesos(cu.esperado_efectivo), "efectivo que debe haber"));

  const porMetodo = h("section", { class: "bloque" }, h("h3", {}, "Cobrado por método"),
    ...(cu.por_metodo.length ? cu.por_metodo.map((m) => h("div", { class: "linea-cobro" }, h("span", {}, `${METODOS[m.metodo] ?? m.metodo} · ${m.pagos} ${m.pagos === 1 ? "pago" : "pagos"}`), h("span", { class: "precio" }, pesos(m.monto)))) : [h("p", { class: "sub" }, "Todavía no hay cobros.")]),
    h("p", { class: "sub", style: "margin-top:.6rem" }, `Efectivo: ${pesos(cu.monto_inicial)} iniciales + ${pesos(cu.ventas_efectivo)} en ventas + ${pesos(cu.ingresos)} de ingresos − ${pesos(cu.egresos)} de egresos.`));

  const mov = h("section", { class: "bloque" }, h("h3", {}, "Ingresos y egresos"),
    h("form", { class: "form-fila", novalidate: true, onsubmit: async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      if (num(fd.get("monto")) <= 0 || String(fd.get("cat")).trim().length < 2) return aviso(msg, "Escribe una categoría y un monto mayor que cero.");
      try { await api("/cash/movements", { method: "POST", body: JSON.stringify({ tipo: fd.get("tipo"), categoria: String(fd.get("cat")).trim(), monto: String(num(fd.get("monto"))), descripcion: String(fd.get("desc")).trim() || null }) }); await cargarCaja(); }
      catch (err) { aviso(msg, errTexto(err)); }
    } },
      campo("Tipo", h("select", { name: "tipo" }, h("option", { value: "egreso" }, "Sale dinero (egreso)"), h("option", { value: "ingreso" }, "Entra dinero (ingreso)"))),
      campo("Categoría", h("input", { name: "cat", maxlength: 60, placeholder: "Insumos, propinas…" })), campo("Monto", h("input", { name: "monto", type: "number", min: 0, step: 500 })),
      campo("Detalle (opcional)", h("input", { name: "desc", maxlength: 300 })), h("button", { class: "btn", type: "submit" }, "Registrar")),
    ...cu.movimientos.map((m) => h("div", { class: "linea-cobro" }, h("span", {}, `${hora(m.created_at)} · ${m.categoria}${m.descripcion ? " — " + m.descripcion : ""}`),
      h("span", { class: "precio", style: `color:${m.tipo === "ingreso" ? "var(--ok)" : "var(--error)"}` }, `${m.tipo === "ingreso" ? "+" : "−"}${pesos(m.monto)}`))));

  const ventas = h("section", { class: "bloque" }, h("h3", {}, "Ventas de esta caja"), h("p", { class: "sub" }, "Cargando…"));
  api(`/sales?cash_session_id=${s.id}&page_size=50&sort=-pagada_en`).then((r) => {
    ventas.replaceChildren(h("h3", {}, "Ventas de esta caja"), ...(r.items.length ? r.items.map((v) => h("div", { class: "bloqueo" },
      h("span", {}, `${v.pagada_en ? hora(v.pagada_en) : "—"} · ${v.cliente ?? "Sin cliente"}${v.profesional ? " · " + v.profesional : ""} · ${v.comprobante_numero ?? ""}`),
      h("b", { class: "precio" }, v.estado === "anulada" ? "Anulada" : pesos(v.total)),
      v.estado === "pagada" ? h("button", { class: "mini", type: "button", onclick: async (ev) => {
        const motivo = prompt("¿Por qué se anula esta venta? (mínimo 3 letras)");
        if (!motivo) return;
        ev.target.disabled = true;
        try { await api(`/sales/${v.id}/void`, { method: "POST", body: JSON.stringify({ motivo }) }); await cargarCaja(); }
        catch (err) { ev.target.disabled = false; aviso(msg, errTexto(err)); }
      } }, "Anular") : null)) : [h("p", { class: "sub" }, "Aún no hay ventas en esta caja.")]));
  }).catch((e) => ventas.replaceChildren(h("div", { class: "aviso" }, errTexto(e))));

  const cierre = h("form", { class: "panelito", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    if (fd.get("contado") === "") return aviso(msg, "Escribe cuánto efectivo contaste en la caja.");
    if (!confirm("¿Cerrar la caja? Después de cerrar no se pueden anular las ventas de esta caja.")) return;
    try {
      const r = await api(`/cash/sessions/${s.id}/close`, { method: "POST", body: JSON.stringify({ monto_contado: String(num(fd.get("contado"))), notas: String(fd.get("notas")).trim() || null }) });
      const d = num(r.diferencia);
      const resultado = h("div", { class: d === 0 ? "aviso ok" : "aviso" }, d === 0 ? `Caja cerrada y cuadrada. Contaste ${pesos(r.contado)}.` : `Caja cerrada. Esperabas ${pesos(r.esperado_efectivo)} y contaste ${pesos(r.contado)}: ${d < 0 ? "faltan" : "sobran"} ${pesos(Math.abs(d))}.`);
      pintarCajaCerrada(await api("/cash/sessions?page_size=6").then((x) => x.items), resultado);
    } catch (err) { aviso(msg, errTexto(err)); }
  } },
    h("h3", {}, "Cerrar caja"), h("p", { class: "sub" }, `Cuenta el efectivo que hay en la caja. Debería haber ${pesos(cu.esperado_efectivo)}.`),
    h("div", { class: "form-fila" }, campo("Efectivo contado (COP)", h("input", { name: "contado", type: "number", min: 0, step: 500 })), campo("Notas (opcional)", h("input", { name: "notas", maxlength: 500 }))),
    h("div", {}, h("button", { class: "btn", type: "submit" }, "Cerrar caja")));

  vistas.caja.replaceChildren(h("div", { class: "titulo" }, h("h2", {}, "Caja"), h("span", { class: "estado confirmada" }, `Abierta desde ${fechaHora(s.abierta_en)}`)),
    ...[stats, msg, porMetodo, ventas, mov, cierre, historialCaja(historial)].filter(Boolean));
}
