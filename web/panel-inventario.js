// Panel del negocio, parte 7: inventario (productos, entradas, salidas, ajuste por conteo, historial y alerta de bajo stock).
// Usa lo definido antes (api, h, aviso, campo, errTexto, money, hora, fechaHora, vistas, $).
const iv = { q: "", bajo: false, orden: "nombre", page: 1, abierto: null, timer: null };
const PAG_INV = 15;
const TIPO_TXT = { entrada: "Entrada", salida: "Salida", ajuste: "Ajuste" };

async function cargarInventario() {
  const cont = vistas.inventario;
  if (!cont.querySelector("#ivBuscar")) armarInventario(cont);
  await Promise.all([pintarResumenInv(), pintarListaInv()]);
}

function armarInventario(cont) {
  const buscar = h("input", { id: "ivBuscar", type: "search", placeholder: "Buscar por nombre o código", "aria-label": "Buscar productos", value: iv.q });
  buscar.addEventListener("input", () => { clearTimeout(iv.timer); iv.timer = setTimeout(() => { iv.q = buscar.value.trim(); iv.page = 1; iv.abierto = null; pintarListaInv(); }, 300); });
  const orden = h("select", { "aria-label": "Ordenar", onchange: (e) => { iv.orden = e.target.value; iv.page = 1; pintarListaInv(); } },
    h("option", { value: "nombre" }, "Nombre (A–Z)"), h("option", { value: "stock_actual" }, "Menos stock primero"), h("option", { value: "-created_at" }, "Más recientes primero"));
  cont.replaceChildren(
    h("div", { class: "titulo" }, h("h2", {}, "Inventario"), h("button", { class: "btn", type: "button", onclick: () => $("ivAlta").replaceChildren(formProducto()) }, "Agregar producto")),
    h("div", { id: "ivResumen", class: "conteo" }), h("div", { id: "ivAlta" }),
    h("div", { class: "buscador" }, buscar, orden, h("button", { class: "chip", id: "ivBajo", type: "button", "aria-pressed": false, onclick: (e) => { iv.bajo = !iv.bajo; e.currentTarget.setAttribute("aria-pressed", String(iv.bajo)); iv.page = 1; pintarListaInv(); } }, "Solo bajo stock")),
    h("div", { id: "ivMsg", "aria-live": "polite" }), h("div", { id: "ivLista" }), h("div", { id: "ivPie", class: "paginacion" }));
}

async function pintarResumenInv() {
  try {
    const s = await api("/inventory/summary");
    $("ivResumen").replaceChildren(h("span", {}, h("b", {}, String(s.productos)), s.productos === 1 ? "producto" : "productos"),
      h("span", {}, h("b", { style: s.bajo_stock ? "color:var(--error)" : "" }, String(s.bajo_stock)), "con bajo stock"), h("span", {}, h("b", {}, money(s.valor_inventario)), "en inventario (a costo)"));
  } catch (e) { aviso($("ivMsg"), e.code === "MODULE_NOT_ENABLED" ? "Tu plan no incluye inventario." : errTexto(e)); }
}

async function pintarListaInv() {
  const q = new URLSearchParams({ page: String(iv.page), page_size: String(PAG_INV), sort: iv.orden });
  if (iv.q) q.set("q", iv.q); if (iv.bajo) q.set("bajo_stock", "true");
  let r; try { r = await api(`/inventory/products?${q}`); } catch (e) { return aviso($("ivMsg"), errTexto(e)); }
  aviso($("ivMsg"), "");
  $("ivLista").replaceChildren(...(r.items.length ? r.items.map(filaProducto) : [h("div", { class: "vacio" },
    h("b", {}, iv.q || iv.bajo ? "Ningún producto coincide" : "Aún no hay productos"), iv.q || iv.bajo ? "Prueba con otra búsqueda o quita el filtro." : "Agrega tus insumos y artículos de venta para llevar el control del stock.")]));
  const desde = r.total ? (r.page - 1) * r.page_size + 1 : 0, hasta = Math.min(r.page * r.page_size, r.total);
  $("ivPie").replaceChildren(h("span", { class: "sub" }, r.total ? `${desde}–${hasta} de ${r.total}` : ""),
    h("button", { class: "mini", type: "button", disabled: r.page <= 1, onclick: () => { iv.page--; iv.abierto = null; pintarListaInv(); } }, "Anterior"),
    h("button", { class: "mini", type: "button", disabled: hasta >= r.total, onclick: () => { iv.page++; iv.abierto = null; pintarListaInv(); } }, "Siguiente"));
}

const refrescarInv = async () => { await Promise.all([pintarResumenInv(), pintarListaInv()]); };

function filaProducto(pr) {
  const abierto = iv.abierto === pr.id;
  const art = h("article", { class: "cliente" + (abierto ? " abierto" : "") + (pr.activo ? "" : " inactiva") },
    h("button", { class: "cliente-cab", type: "button", "aria-expanded": abierto, onclick: () => { iv.abierto = abierto ? null : pr.id; pintarListaInv(); } },
      h("b", {}, pr.nombre), h("span", { class: "sub" }, [pr.sku, pr.activo ? null : "Inactivo", `Costo ${money(pr.costo)}`, `Venta ${money(pr.precio_venta)}`].filter(Boolean).join(" · ")),
      h("span", { class: "stock" }, h("b", { class: pr.bajo_stock ? "bajo" : "" }, String(pr.stock_actual)), pr.bajo_stock ? h("span", { class: "estado cancelada", style: "grid-column:auto;grid-row:auto" }, "Bajo stock") : h("span", { class: "sub" }, "en stock"))));
  if (abierto) art.append(fichaProducto(pr));
  return art;
}

// ---------------------------------------------------------------- ficha: mover stock, historial y edición
function fichaProducto(pr) {
  const cuerpo = h("div", { class: "panelito" });
  const msg = h("div", { "aria-live": "polite" });
  const mov = (tipo, titulo) => {
    const cant = h("input", { type: "number", min: tipo === "ajuste" ? 0 : 1, step: 1, "aria-label": "Cantidad", value: tipo === "ajuste" ? pr.stock_actual : 1 });
    const motivo = h("input", { maxlength: 200, "aria-label": "Motivo", placeholder: tipo === "ajuste" ? "Motivo (obligatorio)" : "Motivo (opcional)" });
    const costo = tipo === "entrada" ? h("input", { type: "number", min: 0, step: 500, "aria-label": "Costo por unidad", placeholder: "Costo por unidad (opcional)" }) : null;
    return h("form", { class: "mov-fila", novalidate: true, onsubmit: async (e) => {
      e.preventDefault();
      const n = Number(cant.value);
      if (!Number.isInteger(n) || n < (tipo === "ajuste" ? 0 : 1)) return aviso(msg, "Escribe una cantidad entera válida.");
      if (tipo === "ajuste" && motivo.value.trim().length < 3) return aviso(msg, "Un ajuste necesita un motivo, por ejemplo «conteo físico».");
      try {
        await api(`/inventory/products/${pr.id}/movements`, { method: "POST", body: JSON.stringify({ tipo, cantidad: n, motivo: motivo.value.trim() || null, costo_unitario: costo && costo.value !== "" ? String(Number(costo.value)) : null }) });
        await refrescarInv(); aviso($("ivMsg"), `${TIPO_TXT[tipo]} registrada en «${pr.nombre}».`, true);
      } catch (err) { aviso(msg, err.code === "STOCK_INSUFFICIENT" ? `Solo hay ${err.data?.details?.disponible ?? 0} en stock: no se pueden sacar ${n}.` : errTexto(err)); }
    } }, h("b", {}, titulo), cant, costo, motivo, h("button", { class: "btn btn-suave", type: "submit" }, tipo === "ajuste" ? "Fijar conteo" : "Registrar"));
  };
  const historial = h("div", {}, h("p", { class: "sub" }, "Cargando historial…"));
  api(`/inventory/products/${pr.id}/movements?page_size=10`).then((r) => {
    historial.replaceChildren(...(r.items.length ? r.items.map((m) => h("div", { class: "bloqueo" },
      h("span", {}, `${fechaHora(m.created_at)} · ${TIPO_TXT[m.tipo]}${m.motivo ? " — " + m.motivo : ""}`),
      h("b", { class: "precio", style: `color:${m.delta > 0 ? "var(--ok)" : "var(--error)"}` }, `${m.delta > 0 ? "+" : "−"}${Math.abs(m.delta)}`), h("span", { class: "sub" }, `quedan ${m.stock_resultante}`))) : [h("p", { class: "sub" }, "Sin movimientos.")]));
  }).catch((e) => historial.replaceChildren(h("div", { class: "aviso" }, errTexto(e))));

  const f = h("form", { class: "form-fila", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target), v = (k) => String(fd.get(k)).trim();
    if (v("nombre").length < 2) return aviso(msg, "El nombre necesita al menos 2 letras.");
    try {
      await api(`/inventory/products/${pr.id}`, { method: "PATCH", body: JSON.stringify({ nombre: v("nombre"), sku: v("sku") || null, costo: String(Number(v("costo")) || 0), precio_venta: String(Number(v("precio")) || 0), stock_minimo: Number(v("minimo")) || 0, activo: !!fd.get("activo") }) });
      await refrescarInv(); aviso($("ivMsg"), "Producto actualizado.", true);
    } catch (err) { aviso(msg, err.code === "CONFLICT" ? "Ya hay otro producto con ese código." : errTexto(err)); }
  } },
    campo("Nombre", h("input", { name: "nombre", value: pr.nombre, maxlength: 120 })), campo("Código (opcional)", h("input", { name: "sku", value: pr.sku ?? "", maxlength: 40 })),
    campo("Costo (COP)", h("input", { name: "costo", type: "number", min: 0, step: 500, value: Math.round(Number(pr.costo)) })), campo("Precio de venta (COP)", h("input", { name: "precio", type: "number", min: 0, step: 500, value: Math.round(Number(pr.precio_venta)) })),
    campo("Avisar cuando queden (0 = sin aviso)", h("input", { name: "minimo", type: "number", min: 0, step: 1, value: pr.stock_minimo })),
    h("label", { class: "envolver sufijo" }, h("input", { type: "checkbox", name: "activo", checked: pr.activo }), "Producto activo"), h("button", { class: "btn", type: "submit" }, "Guardar cambios"));

  cuerpo.append(h("h3", {}, `Hay ${pr.stock_actual} en stock`), mov("entrada", "Entró stock"), mov("salida", "Salió stock"), mov("ajuste", "Contar de nuevo"), msg,
    h("h3", { style: "margin-top:.6rem" }, "Historial"), historial, h("h3", { style: "margin-top:.6rem" }, "Datos del producto"), f,
    h("div", {}, h("button", { class: "mini", type: "button", onclick: async () => {
      if (!confirm(`¿Quitar «${pr.nombre}» de tu lista? El historial se conserva.`)) return;
      try { await api(`/inventory/products/${pr.id}`, { method: "DELETE" }); iv.abierto = null; await refrescarInv(); aviso($("ivMsg"), "Producto quitado.", true); } catch (err) { aviso(msg, errTexto(err)); }
    } }, "Quitar producto")));
  return cuerpo;
}

// ---------------------------------------------------------------- alta
function formProducto() {
  const msg = h("div", { "aria-live": "polite" });
  return h("form", { class: "panelito", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target), v = (k) => String(fd.get(k)).trim();
    if (v("nombre").length < 2) return aviso(msg, "Escribe el nombre del producto.");
    try {
      const pr = await api("/inventory/products", { method: "POST", body: JSON.stringify({ nombre: v("nombre"), sku: v("sku") || null, costo: String(Number(v("costo")) || 0), precio_venta: String(Number(v("precio")) || 0), stock_inicial: Number(v("stock")) || 0, stock_minimo: Number(v("minimo")) || 0 }) });
      $("ivAlta").replaceChildren(); iv.q = pr.nombre; $("ivBuscar").value = pr.nombre; iv.page = 1; iv.abierto = pr.id; await refrescarInv(); aviso($("ivMsg"), "Producto agregado.", true);
    } catch (err) { aviso(msg, err.code === "CONFLICT" ? "Ya hay otro producto con ese código." : errTexto(err)); }
  } },
    h("h3", {}, "Nuevo producto"),
    h("div", { class: "form-fila" }, campo("Nombre", h("input", { name: "nombre", maxlength: 120, required: true })), campo("Código (opcional)", h("input", { name: "sku", maxlength: 40 })),
      campo("Costo (COP)", h("input", { name: "costo", type: "number", min: 0, step: 500, value: 0 })), campo("Precio de venta (COP)", h("input", { name: "precio", type: "number", min: 0, step: 500, value: 0 })),
      campo("Cuántos hay hoy", h("input", { name: "stock", type: "number", min: 0, step: 1, value: 0 })), campo("Avisar cuando queden (0 = sin aviso)", h("input", { name: "minimo", type: "number", min: 0, step: 1, value: 0 }))),
    msg, h("div", { class: "envolver" }, h("button", { class: "btn", type: "submit" }, "Agregar producto"), h("button", { class: "btn btn-suave", type: "button", onclick: () => $("ivAlta").replaceChildren() }, "Cancelar")));
}
