// Panel del negocio: agenda por empleado (línea de tiempo del día con TODAS las reservas de cada persona) y ventana de detalle del turno.
// Usa lo definido en panel.js (api, h, st, tz, hora, largo, money, cambiar, SIGUIENTES, ETIQUETA, hoy, $).
const EQ = { staff: null, horarios: new Map(), filtro: null };
const PX = 92;   // alto en pantalla de una hora

const minutosDelDia = (iso) => {
  const p = new Intl.DateTimeFormat("en-GB", { timeZone: tz(), hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(iso));
  const g = (t) => Number(p.find((x) => x.type === t).value);
  return g("hour") * 60 + g("minute");
};
const minutos = (hhmmss) => { const [a, b] = String(hhmmss).split(":"); return Number(a) * 60 + Number(b); };
const duracion = (c) => Math.max(15, (new Date(c.fin) - new Date(c.inicio)) / 60000);
const nombresServicios = (c) => c.servicios.map((s) => s.nombre).join(" + ");
const hhmm12 = (m) => { const h24 = Math.floor(m / 60) % 24; return `${h24 % 12 || 12} ${h24 < 12 ? "am" : "pm"}`; };

function limpiarEquipo() { EQ.staff = null; EQ.horarios.clear(); }   // se llama al cambiar al equipo o sus horarios

async function baseEquipo() {
  if (!EQ.staff) EQ.staff = await api("/staff?solo_activos=true");
  await Promise.all(EQ.staff.filter((s) => !EQ.horarios.has(s.id)).map(async (s) => EQ.horarios.set(s.id, await api(`/staff/${s.id}/schedule`).catch(() => []))));
}

function columnas() {
  const dow = (new Date(st.fecha + "T12:00:00Z").getUTCDay() + 6) % 7;      // 0 = lunes
  const cols = EQ.staff.map((s) => ({ id: s.id, nombre: s.nombre_publico, color: /^#[0-9a-f]{6}$/i.test(s.color_calendario) ? s.color_calendario : "#8b97ae",
    tramos: (EQ.horarios.get(s.id) || []).filter((t) => t.dia_semana === dow).map((t) => [minutos(t.hora_inicio), minutos(t.hora_fin)]) }));
  const conocidos = new Set(cols.map((c) => c.id));
  for (const c of st.citas) {
    const id = c.profesional?.id ?? null;
    if (!conocidos.has(id)) { conocidos.add(id); cols.push({ id, nombre: c.profesional?.nombre_publico ?? "Sin asignar", color: "#8b97ae", tramos: [], extra: true }); }
  }
  return cols;
}

async function pintarEquipo() {
  const cont = $("equipoVista");
  try { await baseEquipo(); } catch (e) { return aviso($("msg"), e.message); }
  const cols = columnas();
  if (!cols.length) return cont.replaceChildren(h("div", { class: "vacio" }, h("b", {}, "Aún no hay equipo"), "Agrega a tu primera persona en la pestaña Equipo y aquí verás la agenda de cada una."));
  if (EQ.filtro && !cols.find((c) => c.id === EQ.filtro)) EQ.filtro = null;
  const visibles = EQ.filtro ? cols.filter((c) => c.id === EQ.filtro) : cols;
  const activas = (c) => c.estado !== "cancelada" && c.estado !== "no_asistio";
  const citasDe = (id) => st.citas.filter((c) => (c.profesional?.id ?? null) === id);

  // horizonte del día: al menos 8 am – 7 pm, ampliado a lo que haya (horarios, citas, ausencias)
  const extremos = [8 * 60, 19 * 60];
  cols.forEach((c) => c.tramos.forEach(([a, b]) => extremos.push(a, b)));
  st.citas.forEach((c) => { const m = minutosDelDia(c.inicio); extremos.push(m, m + duracion(c)); });
  const ini = Math.floor(Math.min(...extremos) / 60) * 60, fin = Math.min(24 * 60, Math.ceil(Math.max(...extremos) / 60) * 60), alto = ((fin - ini) / 60) * PX;
  const y = (m) => ((m - ini) / 60) * PX;
  const esHoy = st.fecha === hoy(), ahoraMin = minutosDelDia(new Date().toISOString());

  const chips = h("div", { class: "chips equipo-ctl", role: "group", "aria-label": "Mostrar" },
    h("button", { class: "chip", type: "button", "aria-pressed": !EQ.filtro, onclick: () => { EQ.filtro = null; pintarEquipo(); } }, "Todos"),
    ...cols.map((c) => h("button", { class: "chip", type: "button", "aria-pressed": EQ.filtro === c.id, onclick: () => { EQ.filtro = c.id; pintarEquipo(); } },
      h("span", { class: "punto-c", style: `background:${c.color}` }), c.nombre)));

  const cabecera = visibles.map((c) => {
    const mias = citasDe(c.id).filter(activas), reservado = mias.reduce((s, x) => s + duracion(x), 0), laboral = c.tramos.reduce((s, [a, b]) => s + (b - a), 0);
    const pct = laboral ? Math.min(100, Math.round((100 * reservado) / laboral)) : null;
    return h("div", { class: "eq-cab" }, h("span", { class: "avatar-eq", style: `background:${c.color}` }, UI.iniciales(c.nombre)),
      h("div", {}, h("b", {}, c.nombre), h("small", {}, `${mias.length} ${mias.length === 1 ? "turno" : "turnos"}${pct != null ? ` · ${pct}% ocupado` : laboral === 0 && !c.extra ? " · no trabaja hoy" : ""}`)),
      pct != null ? h("i", { class: "ocupacion", style: `--p:${pct}%` }) : null);
  });

  const horas = h("div", { class: "eq-horas", style: `height:${alto}px` }, ...Array.from({ length: (fin - ini) / 60 }, (_, i) => h("span", { style: `top:${i * PX}px` }, hhmm12(ini + i * 60))),
    esHoy && ahoraMin >= ini && ahoraMin <= fin ? h("span", { class: "ahora-et", style: `top:${y(ahoraMin)}px` }, hora(new Date().toISOString())) : null);

  const columnasDom = visibles.map((c) => h("div", { class: "eq-col", style: `height:${alto}px` },
    ...c.tramos.map(([a, b]) => h("div", { class: "labor", style: `top:${y(a)}px;height:${y(b) - y(a)}px` })),
    ...st.bloqueos.filter((b) => b.staff_id === c.id).map((b) => { const a = minutosDelDia(b.inicio), z = a + (new Date(b.fin) - new Date(b.inicio)) / 60000;
      return h("div", { class: "tbloque", style: `top:${y(Math.max(a, ini))}px;height:${y(Math.min(z, fin)) - y(Math.max(a, ini))}px`, title: b.motivo || "No disponible" }, h("span", {}, `🚫 ${b.motivo || "No disponible"}`)); }),
    ...citasDe(c.id).map((x) => {
      const a = minutosDelDia(x.inicio), d = duracion(x), h0 = (d / 60) * PX - 3;
      return h("button", { class: `tcita ${x.estado}${h0 < 34 ? " corta" : ""}`, type: "button", style: `top:${y(a)}px;height:${h0}px;--col:${c.color}`, onclick: () => abrirDetalle(x),
        title: `${hora(x.inicio)} · ${x.cliente.nombre} · ${nombresServicios(x)} · ${ETIQUETA[x.estado]}` },
        h("b", {}, `${hora(x.inicio)} · ${x.cliente.nombre}`), h0 > 46 ? h("span", {}, nombresServicios(x)) : null, h0 > 72 ? h("em", {}, ETIQUETA[x.estado]) : null);
    }),
    esHoy && ahoraMin >= ini && ahoraMin <= fin ? h("div", { class: "ahora", style: `top:${y(ahoraMin)}px` }) : null));

  const grilla = h("div", { class: "equipo", style: `--cols:${visibles.length};--px:${PX}px` }, h("div", { class: "eq-esquina" }), ...cabecera, horas, ...columnasDom);
  const caja = h("div", { class: "equipo-caja" }, grilla);
  cont.replaceChildren(cols.length > 1 ? chips : "", caja,
    h("p", { class: "sub equipo-nota" }, "Toca un turno para confirmarlo, cobrarlo o llamar al cliente. Las franjas rayadas son horas en las que esa persona no trabaja."));
  const primera = st.citas.filter(activas).map((c) => minutosDelDia(c.inicio)).sort((a, b) => a - b)[0];
  const foco = esHoy && ahoraMin >= ini ? ahoraMin : primera ?? 9 * 60;
  caja.scrollTop = Math.max(0, y(foco) - PX * 1.2);
}

// ------------------------------------------------------------------ ventana de detalle de un turno
function abrirDetalle(c) {
  const d = $("detalle"), cuerpo = $("detalleCuerpo"), tel = (c.cliente.telefono || "").replace(/\D/g, "");
  const acciones = (SIGUIENTES[c.estado] || []).map(([estado, texto, principal]) => h("button", { class: "mini" + (principal ? " principal" : ""), type: "button", onclick: (ev) => cambiar(c, estado, texto, ev.currentTarget) }, texto));
  cuerpo.replaceChildren(...[
    h("button", { class: "cerrar", type: "button", "aria-label": "Cerrar", onclick: () => d.close() }, "✕"),
    h("div", { class: "detalle-cab" }, h("span", { class: `estado ${c.estado}` }, ETIQUETA[c.estado] ?? c.estado), h("span", { class: "sub" }, `${largo(st.fecha)} · ${hora(c.inicio)} a ${hora(c.fin)}`)),
    h("h2", {}, c.cliente.nombre),
    tel ? h("div", { class: "envolver", style: "flex-wrap:wrap" }, h("a", { class: "mini", href: `tel:${tel}` }, `📞 ${c.cliente.telefono}`),
      h("a", { class: "mini", target: "_blank", rel: "noopener", href: `https://wa.me/57${tel}?text=${encodeURIComponent(`Hola ${c.cliente.nombre.split(" ")[0]}, te escribimos de ${st.me.tenant.nombre} sobre tu turno de las ${hora(c.inicio)}.`)}` }, "💬 WhatsApp")) : null,
    h("div", { class: "detalle-lista" }, ...c.servicios.map((s) => h("div", { class: "linea-cobro" }, h("span", {}, `${UI.emojiDe(s.nombre)} ${s.nombre} · ${s.duracion_min} min`), h("span", { class: "precio" }, money(s.precio_pactado)))),
      h("div", { class: "linea-cobro total" }, h("span", {}, "Total"), h("span", { class: "precio" }, money(c.total)))),
    c.profesional ? h("p", { class: "sub" }, `🙋 Con ${c.profesional.nombre_publico}`) : h("p", { class: "sub" }, "⚠️ Sin profesional asignado"),
    c.notas ? h("p", { class: "sub" }, `📝 ${c.notas}`) : null,
    acciones.length ? h("div", { class: "envolver", style: "flex-wrap:wrap" }, acciones) : h("p", { class: "sub" }, "Este turno ya no admite más cambios.")].filter(Boolean));
  if (!d.open) d.showModal();
}
$("detalle").addEventListener("click", (e) => { if (e.target === e.currentTarget) e.currentTarget.close(); });   // tocar fuera cierra

function fijarModo(m) { st.modo = m; try { localStorage.setItem("agenda.modo", m); } catch { /* sin almacenamiento */ } pintar(); }
$("modoEquipo").onclick = () => fijarModo("equipo");
$("modoLista").onclick = () => fijarModo("lista");
