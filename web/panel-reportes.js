// Panel del negocio, parte 5: reportes (7 reportes con la misma tabla), filtros y descarga en Excel/PDF.
// Usa lo definido antes (api, h, aviso, campo, errTexto, st, hoy, sumar, money, store, vistas, $).
// [clave, título, ¿maneja dinero?]. Los de dinero solo aparecen si el plan incluye caja; sin ella, el negocio solo ve reportes de reservas.
const REPORTES = [["ranking-clientes", "Clientes que más reservan"], ["clientes-reservas", "Nuevos vs recurrentes"], ["reservas-profesional", "Por profesional"], ["reservas-servicio", "Por servicio"],
  ["citas", "Cancelaciones y no-shows"], ["ocupacion", "Ocupación"], ["ventas", "Ventas", true], ["por-profesional", "Ventas por profesional", true], ["por-servicio", "Ventas por servicio", true], ["por-metodo", "Por método de pago", true]];
const reportesVisibles = () => REPORTES.filter(([, , dinero]) => !dinero || (st.me.modulos_activos || []).includes("cash"));
const CON_AGRUPAR = new Set(["ventas", "ocupacion", "clientes", "citas", "clientes-reservas"]);
const rp = { nombre: "ranking-clientes", desde: null, hasta: null, agrupar: "dia", staff: "" };

// Muestra solo las pestañas que el plan del negocio incluye (el servidor valida igual: esto es solo para no ofrecer lo que no se puede usar).
const MODULO_DE = { caja: "cash", reportes: "reports", clientes: "reception", compartir: "booking", inventario: "inventory" };
const PERMISO_DE = { caja: "cash:manage", reportes: "reports:read", compartir: "booking:manage", inventario: "inventory:manage" };   // quien no tiene el permiso no ve la pestaña
function aplicarModulos() {
  const activos = new Set(st.me.modulos_activos || []);
  const permisos = new Set(st.me.permisos || []);
  document.querySelectorAll(".tabs button").forEach((b) => { const m = MODULO_DE[b.dataset.vista], perm = PERMISO_DE[b.dataset.vista]; b.hidden = (!!m && !activos.has(m)) || (!!perm && !permisos.has(perm)); });
}

const inicioMes = (f) => f.slice(0, 8) + "01";
const finMesPasado = (f) => sumar(inicioMes(f), -1);

async function cargarReportes() {
  const cont = vistas.reportes;
  if (!rp.desde) { rp.hasta = hoy(); rp.desde = inicioMes(rp.hasta); }
  if (!cont.querySelector("#rpTabla")) await armarReportes(cont);
  await pintarReporte();
}

async function armarReportes(cont) {
  let personal = [];
  try { personal = await api("/staff"); } catch { /* sin filtro por profesional */ }
  const preset = (txt, f) => h("button", { class: "mini", type: "button", onclick: () => { [rp.desde, rp.hasta] = f(); sincronizar(); pintarReporte(); } }, txt);
  const desde = h("input", { type: "date", id: "rpDesde", value: rp.desde, onchange: (e) => { rp.desde = e.target.value; pintarReporte(); } });
  const hasta = h("input", { type: "date", id: "rpHasta", value: rp.hasta, onchange: (e) => { rp.hasta = e.target.value; pintarReporte(); } });
  const agr = h("select", { id: "rpAgrupar", "aria-label": "Agrupar por", onchange: (e) => { rp.agrupar = e.target.value; pintarReporte(); } },
    h("option", { value: "dia" }, "Por día"), h("option", { value: "mes" }, "Por mes"), h("option", { value: "anio" }, "Por año"));
  const pro = h("select", { id: "rpStaff", "aria-label": "Profesional", onchange: (e) => { rp.staff = e.target.value; pintarReporte(); } },
    h("option", { value: "" }, "Todo el equipo"), personal.map((p) => h("option", { value: p.id }, p.nombre_publico)));
  const sincronizar = () => { desde.value = rp.desde; hasta.value = rp.hasta; };
  cont.replaceChildren(
    h("div", { class: "titulo" }, h("h2", {}, "Reportes"),
      h("button", { class: "btn btn-suave", type: "button", onclick: () => descargar("xlsx") }, "Descargar Excel"),
      h("button", { class: "btn btn-suave", type: "button", onclick: () => descargar("pdf") }, "Descargar PDF")),
    h("div", { class: "chips", id: "rpChips", style: "margin-bottom:1.2rem" }),
    h("div", { class: "form-fila filtros", style: "margin-bottom:.8rem" }, campo("Desde", desde), campo("Hasta", hasta), campo("Agrupar", agr), campo("Profesional", pro)),
    h("div", { class: "envolver", style: "margin-bottom:1.4rem; flex-wrap:wrap" },
      preset("Hoy", () => [hoy(), hoy()]), preset("Últimos 7 días", () => [sumar(hoy(), -6), hoy()]),
      preset("Este mes", () => [inicioMes(hoy()), hoy()]), preset("Mes pasado", () => [inicioMes(finMesPasado(hoy())), finMesPasado(hoy())])),
    h("div", { id: "rpMsg", "aria-live": "polite" }), h("div", { id: "rpTabla" }));
}

function chips() {
  $("rpChips").replaceChildren(...reportesVisibles().map(([k, t]) => h("button", { class: "chip", type: "button", "aria-pressed": rp.nombre === k,
    onclick: () => { rp.nombre = k; pintarReporte(); } }, t)));
  $("rpAgrupar").closest("label").hidden = !CON_AGRUPAR.has(rp.nombre);
}

const consulta = (extra = {}) => new URLSearchParams({ desde: rp.desde, hasta: rp.hasta, agrupar: rp.agrupar, ...(rp.staff ? { staff_id: rp.staff } : {}), ...extra });

function formato(valor, tipo) {
  if (valor == null || valor === "") return "—";
  if (tipo === "dinero") return money(valor);
  if (tipo === "porcentaje") return `${Number(valor).toLocaleString("es-CO", { maximumFractionDigits: 1 })} %`;
  if (tipo === "numero") return Number(valor).toLocaleString("es-CO", { maximumFractionDigits: 2 });
  return typeof METODOS !== "undefined" && METODOS[valor] ? METODOS[valor] : String(valor);   // "datafono" -> "Datáfono"
}

async function pintarReporte() {
  chips();
  const cont = $("rpTabla");
  if (!rp.desde || !rp.hasta || rp.hasta < rp.desde) return aviso($("rpMsg"), "La fecha final no puede ser anterior a la inicial.");
  aviso($("rpMsg"), "");
  let r;
  try { r = await api(`/reports/${rp.nombre}?${consulta()}`); }
  catch (e) {
    cont.replaceChildren();
    return aviso($("rpMsg"), e.code === "MODULE_NOT_ENABLED" ? "Tu plan no incluye reportes. Pídele a soporte que lo active." : errTexto(e));
  }
  const cols = r.columnas, principal = cols.find((c) => c.tipo === "dinero");
  const max = principal ? Math.max(...r.filas.map((f) => Math.abs(Number(f[principal.key]) || 0)), 1) : 1;
  const celda = (c, f) => h("td", { class: c.tipo === "texto" ? "" : "num" }, formato(f[c.key], c.tipo));
  cont.replaceChildren(
    h("h3", { style: "margin-bottom:.2rem" }, r.titulo), h("p", { class: "sub", style: "margin-bottom:1rem" }, `Del ${r.desde} al ${r.hasta} · hora de ${r.zona_horaria}`),
    r.filas.length ? h("div", { class: "tabla-ancha" }, h("table", { class: "reporte" },
      h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.tipo === "texto" ? "" : "num" }, c.label)))),
      h("tbody", {}, r.filas.map((f) => h("tr", {}, cols.map((c) => {
        const td = celda(c, f);
        if (principal && c.key === principal.key) td.style.setProperty("--barra", `${Math.round(Math.abs(Number(f[c.key]) || 0) / max * 100)}%`), td.classList.add("con-barra");
        return td;
      })))),
      Object.keys(r.totales || {}).length ? h("tfoot", {}, h("tr", {}, cols.map((c) => h("td", { class: c.tipo === "texto" ? "" : "num" }, formato(r.totales[c.key], c.tipo))))) : null))
      : h("div", { class: "vacio" }, h("b", {}, "Sin datos en este periodo"), "Prueba con otro rango de fechas."));
}

async function descargar(fmt) {
  try {
    await api("/auth/me");                                       // renueva la sesión si hace falta antes de pedir el archivo
    const r = await fetch(`${API}/reports/${rp.nombre}?${consulta({ formato: fmt })}`, { headers: { Authorization: `Bearer ${store.get("at")}` } });
    if (!r.ok) throw new Error((await r.json().catch(() => null))?.message || "No se pudo generar el archivo.");
    const a = h("a", { href: URL.createObjectURL(await r.blob()), download: `reporte-${rp.nombre}-${rp.desde}-${rp.hasta}.${fmt}` });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    aviso($("rpMsg"), `Archivo ${fmt.toUpperCase()} descargado.`, true);
  } catch (e) { aviso($("rpMsg"), errTexto(e)); }
}
