// Tablero del Super Admin: métricas de TODA la plataforma (negocios, reservas, retención, horas pico). Gráficas SVG hechas a mano, sin librerías.
// Usa lo definido en plataforma.js (api, h, aviso, errTexto, money, $, vista, ng) y UI.
const TB = { dias: 30, datos: null };
const SVGNS = "http://www.w3.org/2000/svg";
const S = (tag, attrs = {}, ...kids) => { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); for (const kid of kids.flat()) if (kid != null) e.append(kid); return e; };
const fnum = (n) => new Intl.NumberFormat("es-CO").format(Math.round(Number(n) || 0));
const pct = (n) => `${Number(n).toLocaleString("es-CO", { maximumFractionDigits: 1 })} %`;
const abrev = (n) => (n >= 1e9 ? `${(n / 1e9).toLocaleString("es-CO", { maximumFractionDigits: 1 })} mil M` : n >= 1e6 ? `${(n / 1e6).toLocaleString("es-CO", { maximumFractionDigits: 1 })} M` : n >= 1e3 ? `${Math.round(n / 1e3)} mil` : String(Math.round(n)));
const fUTC = (f) => new Date(f + "T12:00:00Z");
const fCorta = (f) => new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", day: "numeric", month: "short" }).format(fUTC(f)).replace(".", "");
const fLarga = (f) => new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" }).format(fUTC(f));
const mesCorto = (f) => new Intl.DateTimeFormat("es-CO", { timeZone: "UTC", month: "short", year: "2-digit" }).format(fUTC(f)).replace(".", "");
const bonito = (m) => { const e = 10 ** Math.floor(Math.log10(Math.max(m, 1))), f = m / e; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e; };   // techo «redondo» para el eje
const COLOR = { atendida: "#12804f", confirmada: "#2457c5", pendiente: "#ffc93c", en_atencion: "#8e5db8", cancelada: "#d92f26", no_asistio: "#7a869f", reserva_web: "#2457c5", whatsapp: "#25a244", recepcion: "#ffc93c" };
const NOMBRE = { atendida: "Atendidas", confirmada: "Confirmadas", pendiente: "Pendientes", en_atencion: "En atención", cancelada: "Canceladas", no_asistio: "No vinieron", reserva_web: "Enlace de reservas", whatsapp: "WhatsApp", recepcion: "En recepción" };
const DIAS_SEM = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

// ------------------------------------------------------------------ agrupar la serie diaria según el periodo (diario / semanal / mensual)
function agrupar(serie, dias) {
  if (dias <= 45) return serie.map((d) => ({ etq: fCorta(d.fecha), larga: fLarga(d.fecha), reservas: d.reservas, atendidas: d.atendidas, ventas: Number(d.ventas) }));
  const clave = dias <= 120 ? (f) => { const d = fUTC(f), k = (d.getUTCDay() + 6) % 7; return new Date(d.getTime() - k * 864e5).toISOString().slice(0, 10); } : (f) => f.slice(0, 7) + "-01";
  const m = new Map();
  for (const d of serie) { const k = clave(d.fecha), b = m.get(k) || { k, reservas: 0, atendidas: 0, ventas: 0 }; b.reservas += d.reservas; b.atendidas += d.atendidas; b.ventas += Number(d.ventas); m.set(k, b); }
  return [...m.values()].map((b) => ({ etq: dias <= 120 ? fCorta(b.k) : mesCorto(b.k), larga: dias <= 120 ? `Semana del ${fCorta(b.k)}` : mesCorto(b.k), reservas: b.reservas, atendidas: b.atendidas, ventas: b.ventas }));
}

// ------------------------------------------------------------------ cursor con detalle (sirve con mouse y con el dedo)
function conTip(caja, svg, n, xDe, lineaH, contenido) {
  const tip = h("div", { class: "tip", hidden: true }), guia = S("line", { class: "guia", y1: 0, y2: lineaH, opacity: 0 }), ancho = Number(svg.getAttribute("viewBox").split(" ")[2]);
  svg.append(guia); caja.append(tip);
  const mover = (e) => {
    const r = svg.getBoundingClientRect(), x = ((e.clientX - r.left) / r.width) * ancho;
    let mejor = 0, dist = Infinity; for (let i = 0; i < n; i++) { const d = Math.abs(xDe(i) - x); if (d < dist) { dist = d; mejor = i; } }
    guia.setAttribute("x1", xDe(mejor)); guia.setAttribute("x2", xDe(mejor)); guia.setAttribute("opacity", 1);
    tip.hidden = false; tip.replaceChildren(contenido(mejor));
    const px = (xDe(mejor) / ancho) * r.width, w = tip.offsetWidth;
    tip.style.left = `${Math.min(Math.max(px - w / 2, 0), r.width - w)}px`;
  };
  svg.addEventListener("pointermove", mover); svg.addEventListener("pointerdown", mover);
  svg.addEventListener("pointerleave", () => { tip.hidden = true; guia.setAttribute("opacity", 0); });
}

function ejes(svg, W, H, m, max, fmt, etqs, xDe) {
  for (let i = 0; i <= 4; i++) {
    const y = m.t + (1 - i / 4) * (H - m.t - m.b);
    svg.append(S("line", { class: "rejilla-g", x1: m.l, x2: W - m.r, y1: y, y2: y }), S("text", { class: "eje", x: m.l - 6, y: y + 4, "text-anchor": "end" }, fmt((max * i) / 4)));
  }
  const paso = Math.max(1, Math.ceil(etqs.length / (W < 400 ? 3 : 6)));
  etqs.forEach((t, i) => { if (i % paso === 0) svg.append(S("text", { class: "eje", x: xDe(i), y: H - 8, "text-anchor": i === 0 ? "start" : "middle" }, t)); });
}

function graficaLineas(datos, series) {
  const W = 640, H = 250, m = { l: 46, r: 14, t: 14, b: 30 }, caja = h("div", { class: "grafica" }), n = datos.length;
  const max = bonito(Math.max(1, ...datos.flatMap((d) => series.map((s) => d[s.k])))), xDe = (i) => m.l + (n === 1 ? (W - m.l - m.r) / 2 : (i * (W - m.l - m.r)) / (n - 1)), yDe = (v) => m.t + (1 - v / max) * (H - m.t - m.b);
  const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": series.map((s) => s.etq).join(" y ") });
  ejes(svg, W, H, m, max, abrev, datos.map((d) => d.etq), xDe);
  for (const s of series) {
    const pts = datos.map((d, i) => `${xDe(i)},${yDe(d[s.k])}`), base = H - m.b;
    svg.append(S("path", { class: "area", d: `M${xDe(0)},${base} L${pts.join(" L")} L${xDe(n - 1)},${base} Z`, fill: s.color }), S("polyline", { class: "linea-g", points: pts.join(" "), stroke: s.color }),
      S("circle", { cx: xDe(n - 1), cy: yDe(datos[n - 1][s.k]), r: 5, fill: s.color, stroke: "var(--tinta)", "stroke-width": 2 }));
  }
  caja.append(svg);
  conTip(caja, svg, n, xDe, H - m.b, (i) => h("div", {}, h("b", {}, datos[i].larga), ...series.map((s) => h("div", { class: "tip-f" }, h("i", { style: `background:${s.color}` }), `${s.etq}: `, h("b", {}, fnum(datos[i][s.k]))))));
  return caja;
}

function graficaBarras(datos, k, color, formato, W = 640, H = 250) {
  const m = { l: W < 400 ? 34 : 52, r: 10, t: 14, b: 30 }, caja = h("div", { class: "grafica" }), n = datos.length;
  const max = bonito(Math.max(1, ...datos.map((d) => d[k]))), paso = (W - m.l - m.r) / n, xDe = (i) => m.l + paso * (i + 0.5);
  const svg = S("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Barras" });
  ejes(svg, W, H, m, max, abrev, datos.map((d) => d.etq), xDe);
  datos.forEach((d, i) => { const alto = (d[k] / max) * (H - m.t - m.b), ancho = Math.max(2, paso * 0.68);
    svg.append(S("rect", { class: "barra-g", x: xDe(i) - ancho / 2, y: H - m.b - alto, width: ancho, height: Math.max(alto, d[k] ? 2 : 0), rx: Math.min(4, ancho / 2), fill: color, style: `--d:${Math.min(i * 12, 500)}ms` })); });
  caja.append(svg);
  conTip(caja, svg, n, xDe, H - m.b, (i) => h("div", {}, h("b", {}, datos[i].larga), h("div", {}, formato(datos[i][k]))));
  return caja;
}

function dona(items, centroTxt, centroSub) {
  const total = items.reduce((s, i) => s + i.n, 0) || 1, svg = S("svg", { viewBox: "0 0 160 160", class: "dona", role: "img", "aria-label": centroSub });
  svg.append(S("circle", { cx: 80, cy: 80, r: 58, fill: "none", stroke: "var(--superficie-2)", "stroke-width": 26 }));
  let acum = 0;
  items.filter((i) => i.n > 0).forEach((i, idx) => {
    const p = (i.n / total) * 100, c = S("circle", { cx: 80, cy: 80, r: 58, fill: "none", stroke: i.color, "stroke-width": 26, pathLength: 100, "stroke-dasharray": `0 100`, "stroke-dashoffset": -acum, transform: "rotate(-90 80 80)", class: "seg" });
    svg.append(c); acum += p; setTimeout(() => c.setAttribute("stroke-dasharray", `${Math.max(p - 0.6, 0.2)} ${100 - Math.max(p - 0.6, 0.2)}`), UI.reducido() ? 0 : 80 + idx * 120);
  });
  svg.append(S("text", { x: 80, y: 82, "text-anchor": "middle", class: "dona-n" }, centroTxt), S("text", { x: 80, y: 100, "text-anchor": "middle", class: "dona-s" }, centroSub));
  return h("div", { class: "dona-caja" }, svg, h("ul", { class: "leyenda" }, ...items.map((i) => h("li", {}, h("i", { style: `background:${i.color}` }), h("span", {}, i.etq), h("b", {}, fnum(i.n)), h("small", {}, pct((100 * i.n) / total))))));
}

function barrasH(items, fmt, color = "var(--marca)") {
  const max = Math.max(1, ...items.map((i) => i.v));
  return h("div", { class: "barras-h" }, ...items.map((i) => h("div", { class: "bh" }, h("div", { class: "bh-t" }, h("span", {}, i.etq), h("b", {}, fmt(i.v))), h("div", { class: "bh-p" }, h("i", { style: `--w:${(100 * i.v) / max}%;background:${i.color || color}` })), i.sub ? h("small", {}, i.sub) : null)));
}

function mapaCalor(celdas) {
  if (!celdas.length) return h("div", { class: "vacio" }, "Aún no hay reservas en este periodo.");
  const horas = [...new Set(celdas.map((c) => c.hora))].sort((a, b) => a - b), h0 = Math.min(...horas), h1 = Math.max(...horas), max = Math.max(...celdas.map((c) => c.citas));
  const mapa = new Map(celdas.map((c) => [`${c.dia}-${c.hora}`, c.citas])), cols = Array.from({ length: h1 - h0 + 1 }, (_, i) => h0 + i);
  const fmtH = (x) => `${x % 12 || 12}${x < 12 ? "a" : "p"}`;
  const grid = h("div", { class: "calor", style: `--cols:${cols.length}` }, h("span"), ...cols.map((x) => h("span", { class: "calor-h" }, (x - h0) % 2 === 0 ? fmtH(x) : "")),
    ...DIAS_SEM.flatMap((d, di) => [h("span", { class: "calor-d" }, d), ...cols.map((x) => { const v = mapa.get(`${di}-${x}`) || 0, k = v / max;
      return h("span", { class: "celda" + (k > 0.55 ? " fuerte" : ""), style: `--k:${Math.round(k * 100)}%`, title: `${DIAS_SEM[di]} ${fmtH(x)}: ${fnum(v)} citas`, "data-v": v ? String(v) : "" }); })]));
  return h("div", {}, grid, h("div", { class: "calor-leyenda" }, h("small", {}, "Pocas"), h("i"), h("small", {}, `Muchas (hasta ${fnum(max)} por hora)`)));
}

const sparkline = (vals, color) => { const W = 96, H = 30, max = Math.max(...vals, 1), n = vals.length; if (n < 2) return h("span");
  const pts = vals.map((v, i) => `${(i * W) / (n - 1)},${H - 3 - (v / max) * (H - 6)}`).join(" ");
  return S("svg", { viewBox: `0 0 ${W} ${H}`, class: "spark", "aria-hidden": "true" }, S("polyline", { points: pts, fill: "none", stroke: color, "stroke-width": 2.5, "stroke-linejoin": "round", "stroke-linecap": "round" })); };

const kpi = (icono, titulo, valor, formato, sub, spark, destacado = false) => {
  const num0 = h("b", { class: "kpi-n" }, formato(0));
  const t = h("article", { class: "kpi" + (destacado ? " destacado" : "") }, h("div", { class: "kpi-t" }, h("span", { "aria-hidden": "true" }, icono), titulo), num0, h("small", {}, sub), spark || null);
  requestAnimationFrame(() => UI.contar(num0, valor, formato)); return t;
};

// ------------------------------------------------------------------ el tablero
async function cargarResumen() {
  const cont = vistas.resumen;
  if (!TB.datos) cont.replaceChildren(h("div", { class: "esqueleto-t" }), h("div", { class: "esqueleto-t g" }));
  try { TB.datos = await api(`/platform/analytics?dias=${TB.dias}`); } catch (e) { return cont.replaceChildren(h("div", { class: "aviso" }, errTexto(e))); }
  pintarTablero();
}

function pintarTablero() {
  const d = TB.datos, r = d.resumen, cont = vistas.resumen, serie = agrupar(d.serie, d.dias), ESTADOS = ["atendida", "confirmada", "pendiente", "en_atencion", "cancelada", "no_asistio"];
  const periodos = [[7, "7 días"], [30, "30 días"], [90, "90 días"], [365, "12 meses"]];
  const encabezado = h("div", { class: "tb-cab" }, h("div", {}, h("h2", {}, "Tablero de la plataforma"), h("p", { class: "sub" }, `Del ${fCorta(d.desde)} al ${fCorta(d.hasta)} · hora de Colombia · solo cifras agregadas, sin datos de clientes`)),
    h("div", { class: "tb-ctl" }, h("div", { class: "chips", role: "group", "aria-label": "Periodo" }, ...periodos.map(([v, t]) => h("button", { class: "chip", type: "button", "aria-pressed": TB.dias === v, onclick: () => { TB.dias = v; TB.datos = null; cargarResumen(); } }, t))),
      h("button", { class: "mini", type: "button", onclick: () => cargarResumen() }, "↻ Actualizar"), h("button", { class: "mini", type: "button", onclick: () => csv(d) }, "⬇ Datos (CSV)")));

  const tarjetas = h("section", { class: "kpis", "aria-label": "Indicadores" },
    kpi("💰", "Ingreso mensual recurrente", Number(r.mrr), money, `${r.negocios_activos} negocios activos pagando`, null, true),
    kpi("🏪", "Negocios activos", r.negocios_activos, fnum, `de ${r.negocios_total} en total · ${r.negocios_nuevos} nuevos en el periodo`),
    kpi("📅", "Reservas creadas", r.reservas_creadas, fnum, `${pct(r.reservas_online_pct)} llegaron por el enlace de reservas`, sparkline(serie.map((x) => x.reservas), "#2457c5")),
    kpi("✅", "Citas atendidas", r.citas_atendidas, fnum, `${pct(r.tasa_no_show)} no vinieron · ${pct(r.tasa_cancelacion)} canceladas`, sparkline(serie.map((x) => x.atendidas), "#12804f")),
    kpi("📈", "Reservas por negocio", r.reservas_creadas / Math.max(1, r.negocios_activos), fnum, "Promedio por negocio activo en el periodo", sparkline(serie.map((x) => x.reservas), "#d92f26")),
    kpi("🙋", "Clientes nuevos", r.clientes_nuevos, fnum, "registrados en todos los negocios"),
    kpi("🚫", "Cancelaciones", r.tasa_cancelacion, pct, `${fnum(d.citas_por_estado.cancelada)} citas · ${fnum(d.citas_por_estado.no_asistio)} sin asistir`),
    kpi("👤", "Usuarios activos", r.usuarios_activos, fnum, "administradores, recepción y barberos"));

  const tarjeta = (titulo, sub, cuerpo, ancho = "") => h("section", { class: `card-g ${ancho}` }, h("header", {}, h("h3", {}, titulo), sub ? h("small", {}, sub) : null), cuerpo);
  const leyendaLineas = h("div", { class: "leyenda-g" }, h("span", {}, h("i", { style: "background:#2457c5" }), "Reservas creadas"), h("span", {}, h("i", { style: "background:#12804f" }), "Citas atendidas"));
  const porEstado = dona(ESTADOS.map((e) => ({ etq: NOMBRE[e], n: d.citas_por_estado[e], color: COLOR[e] })), fnum(r.citas_periodo), "citas");
  const porOrigen = barrasH(d.por_origen.map((o) => ({ etq: NOMBRE[o.origen] || o.origen, v: o.reservas, color: COLOR[o.origen] })), fnum);
  const porPlan = barrasH(d.por_plan.map((p) => ({ etq: p.plan, v: Number(p.mrr), sub: `${p.negocios} ${p.negocios === 1 ? "negocio" : "negocios"} · ${money(p.mrr)} al mes` })), (v) => money(v), "var(--acento)");
  const porTipo = dona(d.por_tipo.map((t, i) => ({ etq: t.tipo, n: t.negocios, color: ["#2457c5", "#d92f26", "#ffc93c", "#12804f", "#8e5db8"][i % 5] })), fnum(r.negocios_total), "negocios");
  const crec = graficaBarras(d.crecimiento.map((c) => ({ etq: mesCorto(c.mes), larga: `${mesCorto(c.mes)} · ${c.acumulado} negocios en total`, v: c.nuevos })), "v", "#2457c5", (v) => `${v} nuevos`, 330, 230);

  const maxV = Math.max(1, ...d.top_negocios.map((t) => t.citas));
  const ranking = d.top_negocios.length ? h("div", { class: "tabla-ancha" }, h("table", { class: "reporte" }, h("thead", {}, h("tr", {}, ["#", "Negocio", "Citas", "Atendidas", "No vinieron"].map((t, i) => h("th", { class: i > 1 ? "num" : "" }, t)))),
    h("tbody", {}, ...d.top_negocios.map((t, i) => h("tr", {}, h("td", {}, ["🥇", "🥈", "🥉"][i] || String(i + 1)), h("td", {}, h("b", {}, t.nombre)), h("td", { class: "num con-barra", style: `--barra:${Math.round((100 * t.citas) / maxV)}%` }, fnum(t.citas)), h("td", { class: "num" }, fnum(t.atendidas)),
      h("td", { class: "num" }, t.atendidas + t.no_asistio ? pct((100 * t.no_asistio) / (t.atendidas + t.no_asistio)) : "—")))))) : h("div", { class: "vacio" }, "Aún no hay actividad en este periodo.");

  const urg = (dias) => (dias < 0 ? { t: `venció hace ${-dias} d`, c: "mal" } : dias === 0 ? { t: "vence hoy", c: "mal" } : dias <= 7 ? { t: `vence en ${dias} d`, c: "ojo" } : { t: `vence en ${dias} d`, c: "" });
  const llamar = h("div", { class: "llamar" },
    h("h4", {}, "💳 Suscripciones por vencer"), ...(d.vencimientos.length ? d.vencimientos.map((v) => h("div", { class: "ll-fila" }, h("div", {}, h("b", {}, v.nombre), h("small", {}, `${v.plan} · ${fCorta(v.fecha_fin)}`)), h("span", { class: `ll-badge ${urg(v.dias).c}` }, urg(v.dias).t), h("button", { class: "mini", type: "button", onclick: () => irANegocio(v.nombre) }, "Ver"))) : [h("p", { class: "sub" }, "Nadie vence en los próximos 14 días. 🎉")]),
    h("h4", { style: "margin-top:1.2rem" }, `😴 Sin reservas nuevas en 14 días${d.sin_actividad_total ? ` (${d.sin_actividad_total})` : ""}`), ...(d.sin_actividad.length ? d.sin_actividad.map((v) => h("div", { class: "ll-fila" }, h("div", {}, h("b", {}, v.nombre), h("small", {}, "Puede necesitar ayuda para arrancar o está por irse")), h("span", { class: "ll-badge ojo" }, "inactivo"), h("button", { class: "mini", type: "button", onclick: () => irANegocio(v.nombre) }, "Ver"))) : [h("p", { class: "sub" }, "Todos los negocios activos recibieron reservas. 👏")]));

  cont.replaceChildren(encabezado, tarjetas, h("div", { class: "rejilla-g" },
    tarjeta("Reservas y citas atendidas", "Cada punto es un día (o semana / mes en periodos largos). Pasa el cursor o el dedo por encima.", h("div", {}, leyendaLineas, graficaLineas(serie, [{ k: "reservas", color: "#2457c5", etq: "Reservas creadas" }, { k: "atendidas", color: "#12804f", etq: "Citas atendidas" }])), "ancho-2"),
    tarjeta("Cómo terminaron las citas", `${fnum(r.citas_periodo)} citas del periodo`, porEstado),
    tarjeta("Citas atendidas", `Total ${fnum(r.citas_atendidas)} en el periodo`, graficaBarras(serie, "atendidas", "#12804f", fnum), "ancho-2"),
    tarjeta("¿Por dónde reservan?", "Reservas creadas según su origen", porOrigen),
    tarjeta("Ingreso por plan", "Ingreso mensual recurrente de cada plan", porPlan),
    tarjeta("Negocios por tipo", "Barberías, spas, uñas…", porTipo),
    tarjeta("Crecimiento", "Negocios nuevos por mes (últimos 6 meses)", crec),
    tarjeta("Horas pico", "Cuándo se agendan más citas (hora local de cada negocio)", mapaCalor(d.mapa_calor), "ancho-3"),
    tarjeta("Ranking de negocios", "Los más activos del periodo, por citas atendidas", ranking, "ancho-2"),
    tarjeta("A quién llamar hoy", "Renovaciones cercanas y negocios que se enfriaron", llamar)));
}

function irANegocio(nombre) { ng.q = nombre; ng.page = 1; ng.abierto = null; vista("negocios"); setTimeout(() => { const b = $("ngBuscar"); if (b) b.value = nombre; }, 300); }

function csv(d) {
  const filas = [["fecha", "reservas_creadas", "citas_atendidas"], ...d.serie.map((x) => [x.fecha, x.reservas, x.atendidas])];
  UI.descargar(new Blob(["﻿" + filas.map((f) => f.join(",")).join("\n")], { type: "text/csv;charset=utf-8" }), `metricas-${d.desde}-a-${d.hasta}.csv`);
}
