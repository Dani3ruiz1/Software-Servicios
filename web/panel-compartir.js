// Panel del negocio, parte 6: el enlace para compartir (copiar, WhatsApp, activar o pausar la página de reservas) y qué falta antes de compartirlo.
// Usa lo definido antes (api, h, aviso, campo, errTexto, st, vistas, $).
const cp = { cfg: null };
const enlaceDe = (slug) => `${location.origin}/r/${slug}`;

async function cargarCompartir() {
  const cont = vistas.compartir;
  try { cp.cfg = await api("/booking/settings"); } catch (e) { return cont.replaceChildren(h("div", { class: "aviso" }, e.code === "MODULE_NOT_ENABLED" ? "Tu plan no incluye reservas en línea." : errTexto(e))); }
  pintarCompartir();
}

// Guarda activo/slug sin perder el resto de la configuración (la API reemplaza el conjunto completo).
async function guardarReservas(cambios) {
  const { configurado, ...actual } = cp.cfg;
  cp.cfg = await api("/booking/settings", { method: "PUT", body: JSON.stringify({ ...actual, ...cambios }) });
}

function pintarCompartir() {
  const cont = vistas.compartir, c = cp.cfg, link = enlaceDe(c.slug), msg = h("div", { "aria-live": "polite" });
  const caja = h("input", { class: "enlace", readonly: true, value: link, "aria-label": "Enlace de reservas", onfocus: (e) => e.target.select() });
  const copiar = h("button", { class: "btn", type: "button", onclick: async (ev) => {
    try { await navigator.clipboard.writeText(link); } catch { caja.select(); document.execCommand?.("copy"); }
    ev.target.textContent = "¡Copiado!"; setTimeout(() => (ev.target.textContent = "Copiar enlace"), 1800);
  } }, "Copiar enlace");
  const texto = `¡Reserva tu turno en ${st.me.tenant?.nombre ?? "nuestro negocio"} cuando quieras, sin llamadas! ${link}`;

  const estado = c.activo
    ? h("div", { class: "aviso ok" }, "Tu página de reservas está activa: tus clientes ya pueden reservar con este enlace.")
    : h("div", { class: "aviso" }, "Tu página de reservas está apagada: quien abra el enlace no podrá reservar. Actívala cuando estés listo para compartirlo.");
  const interruptor = h("button", { class: c.activo ? "btn btn-suave" : "btn", type: "button", onclick: async (ev) => {
    if (c.activo && !confirm("¿Pausar las reservas en línea? El enlace dejará de funcionar hasta que lo actives otra vez.")) return;
    ev.target.disabled = true;
    try { await guardarReservas({ activo: !c.activo }); pintarCompartir(); aviso($("cpMsg"), cp.cfg.activo ? "Reservas en línea activadas." : "Reservas en línea pausadas.", true); }
    catch (e) { ev.target.disabled = false; aviso(msg, errTexto(e)); }
  } }, c.activo ? "Pausar reservas en línea" : "Activar reservas en línea");

  const lista = h("div", { id: "cpChecks" }, h("p", { class: "sub" }, "Revisando…"));
  revisar(lista);

  const slugI = h("input", { value: c.slug, maxlength: 50, "aria-label": "Dirección" });
  const dir = h("form", { class: "form-fila", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const nuevo = slugI.value.trim().toLowerCase();
    if (nuevo === c.slug) return aviso(msg, "Esa ya es tu dirección.");
    if (!confirm(`Los enlaces que ya compartiste con "${c.slug}" dejarán de funcionar. ¿Cambiar la dirección?`)) return;
    try { await guardarReservas({ slug: nuevo }); pintarCompartir(); aviso($("cpMsg"), "Dirección actualizada. Comparte el enlace nuevo.", true); }
    catch (err) {
      aviso(msg, err.code === "CONFLICT" ? "Esa dirección ya la usa otro negocio. Prueba con otra."
        : err.code === "VALIDATION_ERROR" ? (/reservada/.test(errTexto(err)) ? "Esa dirección está reservada. Prueba con otra." : "Usa de 3 a 50 caracteres: letras minúsculas sin tildes, números y guiones (sin espacios).")
        : errTexto(err));
    }
  } }, campo("Dirección (letras minúsculas, números y guiones)", slugI), h("button", { class: "btn btn-suave", type: "submit" }, "Guardar dirección"));

  cont.replaceChildren(
    h("div", { class: "titulo" }, h("h2", {}, "Compartir mi página de reservas")),
    h("p", { class: "sub", style: "margin-bottom:1rem" }, "Este es el enlace de tu negocio. Ponlo en tu WhatsApp, en la biografía de Instagram o imprímelo en el local."),
    h("div", { id: "cpMsg", "aria-live": "polite" }), estado,
    h("div", { class: "enlace-fila" }, caja, copiar),
    h("div", { class: "envolver", style: "flex-wrap:wrap; margin:1rem 0 1.4rem" },
      h("a", { class: "btn btn-suave", href: `https://wa.me/?text=${encodeURIComponent(texto)}`, target: "_blank", rel: "noopener" }, "Compartir por WhatsApp"),
      h("a", { class: "btn btn-suave", href: `/r/${c.slug}`, target: "_blank", rel: "noopener" }, "Ver como la ve el cliente"), interruptor),
    h("section", { class: "bloque" }, h("h3", {}, "Código QR para el local"),
      h("p", { class: "sub" }, "Imprímelo y ponlo en la entrada o en el espejo: el cliente apunta con la cámara del celular y reserva."),
      h("div", { class: "qr-fila" }, qrSvg(link), h("div", { class: "envolver", style: "flex-direction:column; align-items:flex-start; gap:.6rem" },
        h("button", { class: "btn", type: "button", onclick: () => imprimirCartel(link) }, "Imprimir cartel"),
        h("button", { class: "btn btn-suave", type: "button", onclick: () => descargarQr(link) }, "Descargar QR (imagen SVG)")))),
    h("section", { class: "bloque" }, h("h3", {}, "Antes de compartirlo"), lista),
    h("section", { class: "bloque" }, h("h3", {}, "Cómo se ve y cómo reserva el cliente"), formPersonalizar(c)),
    h("section", { class: "bloque" }, h("h3", {}, "Dirección del enlace"), dir, msg));
}

// El color de acento se usa sobre fondo oscuro: si es muy oscuro no se leería, así que se exige una luminosidad mínima.
const luminosidad = (hex) => { const [r, g, b] = [1, 3, 5].map((i) => { const v = parseInt(hex.slice(i, i + 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const MODOS = { cliente_elige: "El cliente elige al profesional", automatica: "Se asigna automáticamente a quien esté libre", en_recepcion: "Yo asigno al profesional en recepción" };

function formPersonalizar(c) {
  const msg = h("div", { "aria-live": "polite" }), t = c.textos || {};
  const area = (name, valor, ph) => h("textarea", { name, rows: 2, maxlength: 600, placeholder: ph }, valor ?? "");
  const num = (name, valor, min, max) => h("input", { name, type: "number", min, max, value: valor });
  return h("form", { class: "panelito", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const f = new FormData(e.target), v = (k) => String(f.get(k)).trim();
    if (luminosidad(v("acento")) < 0.2) return aviso(msg, "Ese color es muy oscuro y no se leería sobre el fondo oscuro. Elige uno más claro.");
    if (v("logo") && !/^https:\/\//.test(v("logo"))) return aviso(msg, "La dirección del logo debe empezar por https://");
    const textos = {}; for (const k of ["titulo", "bienvenida", "confirmacion"]) if (v(k)) textos[k] = v(k);
    try {
      await guardarReservas({ textos: { ...(c.textos || {}), ...Object.fromEntries(["titulo", "bienvenida", "confirmacion"].map((k) => [k, ""])), ...textos }, color_secundario: v("acento"), logo_url: v("logo") || null,
        anticipacion_min_horas: Number(v("amin")), anticipacion_max_dias: Number(v("amax")), buffer_min: Number(v("buffer")), cancelacion_min_horas: Number(v("canc")), modo_asignacion: v("modo") });
      pintarCompartir(); aviso($("cpMsg"), "Cambios guardados. Tu página de reservas ya los usa.", true);
    } catch (err) { aviso(msg, errTexto(err)); }
  } },
    h("div", { class: "form-fila" }, campo("Título (reemplaza el nombre arriba, opcional)", h("input", { name: "titulo", maxlength: 600, value: t.titulo ?? "", placeholder: st.me.tenant?.nombre ?? "" })),
      campo("Color de acento", h("input", { name: "acento", type: "color", value: c.color_secundario })), campo("Logo (dirección https, opcional)", h("input", { name: "logo", value: c.logo_url ?? "", placeholder: "https://…/logo.png" }))),
    campo("Mensaje de bienvenida", area("bienvenida", t.bienvenida, "Ej. Reserva tu turno en un minuto. ¡Te esperamos!")),
    campo("Mensaje al confirmar la reserva", area("confirmacion", t.confirmacion, "Ej. Llega 5 minutos antes. Si no puedes venir, cancela con tu enlace.")),
    h("div", { class: "form-fila" }, campo("Reservar con al menos (horas de anticipación)", num("amin", c.anticipacion_min_horas, 0, 720)), campo("Reservar hasta (días hacia adelante)", num("amax", c.anticipacion_max_dias, 1, 365)),
      campo("Descanso entre citas (minutos)", num("buffer", c.buffer_min, 0, 240)), campo("Cancelar hasta (horas antes)", num("canc", c.cancelacion_min_horas, 0, 168))),
    campo("¿Quién atiende?", h("select", { name: "modo" }, Object.entries(MODOS).map(([k, tx]) => h("option", { value: k, selected: c.modo_asignacion === k }, tx)))),
    msg, h("div", {}, h("button", { class: "btn", type: "submit" }, "Guardar cambios")));
}

// Revisa con datos reales si ya hay algo que reservar: servicios activos y personas con servicios y horario.
async function revisar(contenedor) {
  try {
    const [serv, personal] = await Promise.all([api("/catalog/services?page_size=100&activo=true").then((r) => r.items), api("/staff?solo_activos=true")]);
    const conHorario = [];
    for (const p of personal) {
      if (!p.service_ids.length) continue;
      const tramos = await api(`/staff/${p.id}/schedule`).catch(() => []);
      if (tramos.length) conHorario.push(p);
    }
    const fila = (ok, texto, ayuda) => h("div", { class: "linea-cobro" }, h("span", {}, texto, ok ? null : h("span", { class: "sub" }, ` — ${ayuda}`)),
      h("b", { style: `color:${ok ? "var(--ok)" : "var(--error)"}` }, ok ? "Listo" : "Falta"));
    contenedor.replaceChildren(
      fila(serv.length > 0, `Servicios para reservar: ${serv.length}`, "agrégalos en Precios"),
      fila(conHorario.length > 0, `Personas con servicios y horario: ${conHorario.length}`, "agrega personal y define su horario en Personal"));
  } catch (e) { contenedor.replaceChildren(h("p", { class: "sub" }, `No se pudo revisar: ${errTexto(e)}`)); }
}

// ---------------------------------------------------------------- código QR (librería web/vendor/qrcode.js, se genera en el navegador)
function qrSvgTexto(link, celda = 8) {
  const q = qrcode(0, "M");                                       // tamaño automático, corrección media: se lee aunque el cartel se ensucie
  q.addData(link); q.make();
  return q.createSvgTag({ cellSize: celda, margin: 2, scalable: true });
}
function qrSvg(link) {
  const doc = new DOMParser().parseFromString(qrSvgTexto(link), "image/svg+xml");        // SVG generado por la librería a partir de nuestro enlace
  const svg = document.importNode(doc.documentElement, true);
  svg.setAttribute("class", "qr"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `Código QR de ${link}`);
  return svg;
}
function descargarQr(link) {
  const url = URL.createObjectURL(new Blob([qrSvgTexto(link, 12)], { type: "image/svg+xml" }));
  const a = h("a", { href: url, download: `qr-reservas-${cp.cfg.slug}.svg` });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 4000);
}
// Cartel en blanco y negro (ahorra tinta y el QR se lee mejor): solo se ve al imprimir.
function imprimirCartel(link) {
  document.getElementById("cartel")?.remove();
  const cartel = h("div", { id: "cartel" }, h("h1", {}, st.me.tenant?.nombre ?? ""), h("p", { class: "cartel-sub" }, "Reserva tu turno desde el celular"),
    qrSvg(link), h("p", { class: "cartel-paso" }, "Apunta la cámara al código"), h("p", { class: "cartel-link" }, link.replace(/^https?:\/\//, "")));
  document.body.append(cartel);
  window.print();
}
