// Utilidades compartidas del frente (todo cuelga de `UI` para no chocar con los nombres de cada pantalla).

// El DOM escribe la palabra «null» si le pasas null; aquí los valores vacíos (null, undefined, false) simplemente se omiten, en todas las pantallas.
for (const m of ["replaceChildren", "append", "prepend"]) {
  const original = Element.prototype[m];
  Element.prototype[m] = function (...nodos) { return original.apply(this, nodos.filter((n) => n != null && n !== false)); };
}
const UI = (() => {
  const $ = (id) => document.getElementById(id);
  const reducido = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Crea elementos sin innerHTML: todo texto que venga del servidor entra como texto, nunca como HTML.
  function h(tag, attrs = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (typeof v === "boolean" && k.startsWith("aria-")) el.setAttribute(k, String(v));
      else if (v === true) el.setAttribute(k, "");
      else if (v !== false && v != null) el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null) el.append(kid);
    return el;
  }
  const aviso = (el, msg, ok = false) => el.replaceChildren(msg ? h("div", { class: "aviso" + (ok ? " ok" : ""), role: "alert" }, msg) : "");

  // ---------------------------------------------------------------- color: cada negocio pinta su página con su marca, con contraste garantizado
  const rgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const hex = (c) => "#" + c.map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, "0")).join("");
  const mezcla = (a, b, t) => { const [x, y] = [rgb(a), rgb(b)]; return hex(x.map((v, i) => v + (y[i] - v) * t)); };
  const luz = (h0) => { const [r, g, b] = rgb(h0).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const contraste = (a, b) => { const [x, y] = [luz(a), luz(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const sobre = (fondo) => (contraste(fondo, "#14213d") >= contraste(fondo, "#ffffff") ? "#14213d" : "#ffffff");
  const esHex = (v) => typeof v === "string" && /^#[0-9a-f]{6}$/i.test(v);

  function aplicarMarca({ primario, secundario, fondo } = {}) {
    const raiz = document.documentElement.style, p = esHex(primario) ? primario : "#2457c5", s = esHex(secundario) ? secundario : "#d92f26", f = esHex(fondo) ? fondo : "#fff8eb";
    const claro = luz(f) > 0.4;
    const tinta = claro ? mezcla(p, "#000000", 0.62) : "#f5f7ff";
    const set = { "--marca": p, "--on-marca": sobre(p), "--acento": s, "--on-acento": sobre(s), "--fondo": f, "--tinta": tinta,
      "--tinta-suave": mezcla(tinta, f, 0.36), "--superficie": claro ? "#ffffff" : mezcla(f, "#ffffff", 0.08), "--superficie-2": mezcla(f, s, claro ? 0.13 : 0.2),
      "--linea": claro ? "rgba(0,0,0,.14)" : "rgba(255,255,255,.2)" };
    for (const [k, v] of Object.entries(set)) raiz.setProperty(k, v);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", f);
  }


  // Tema por tipo de negocio: pone data-tipo en <html> (tema.css cambia el adorno y la tipografía) y, si se pide, los colores propios del tipo.
  const TIPOS = { barberia: ["#1D3557", "#E63946", "#FFF9F0"], spa: ["#2F6F5E", "#C9A66B", "#F4FAF6"], unas: ["#C2185B", "#7E57C2", "#FFF5F9"],
    estetica: ["#6D4C9F", "#F2A65A", "#FBF8FF"], salon: ["#D1495B", "#2E86AB", "#FFF8F6"] };
  function aplicarTipo(tipo, { colores = false, recordar = true } = {}) {
    const t = tipo in TIPOS ? tipo : "barberia";
    document.documentElement.dataset.tipo = t;
    if (colores) { const [primario, secundario, fondo] = TIPOS[t]; aplicarMarca({ primario, secundario, fondo }); }
    if (recordar) try { localStorage.setItem("agenda.tipo", t); } catch { /* sin almacenamiento */ }
  }
  // Pantallas de entrada (antes de saber el negocio): el último tipo usado en este navegador; la de la plataforma fija el suyo en el HTML.
  // La página pública no sabe el tipo hasta recibir la configuración: mientras tanto el adorno va oculto (tema.css) para que no parpadee el poste.
  document.addEventListener("DOMContentLoaded", () => { if (!document.documentElement.dataset.tipo && !document.querySelector('script[src="reservar.js"]')) aplicarTipo("barberia", { colores: true, recordar: false }); });
  if (!document.documentElement.dataset.tipo) { try { const t = localStorage.getItem("agenda.tipo"); if (t in TIPOS) aplicarTipo(t, { colores: true, recordar: false }); } catch { /* sin almacenamiento */ } }

  // ---------------------------------------------------------------- pequeños gustos
  function confeti(colores = ["#ffc93c", "#d92f26", "#2457c5", "#ffffff", "#12804f"]) {
    if (reducido()) return;
    const c = h("canvas", { style: "position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:99" }), g = c.getContext("2d");
    c.width = innerWidth; c.height = innerHeight; document.body.append(c);
    const p = Array.from({ length: 110 }, () => ({ x: innerWidth / 2 + (Math.random() - 0.5) * 120, y: innerHeight * 0.55, vx: (Math.random() - 0.5) * 16, vy: -Math.random() * 15 - 4,
      w: 6 + Math.random() * 7, h: 4 + Math.random() * 6, a: Math.random() * 6, va: (Math.random() - 0.5) * 0.4, col: colores[(Math.random() * colores.length) | 0] }));
    const t0 = performance.now();
    (function cuadro(t) {
      const dt = t - t0; g.clearRect(0, 0, c.width, c.height);
      for (const q of p) { q.vy += 0.38; q.vx *= 0.992; q.x += q.vx; q.y += q.vy; q.a += q.va; g.save(); g.globalAlpha = Math.max(0, 1 - dt / 2600); g.translate(q.x, q.y); g.rotate(q.a); g.fillStyle = q.col; g.fillRect(-q.w / 2, -q.h / 2, q.w, q.h); g.restore(); }
      dt < 2600 ? requestAnimationFrame(cuadro) : c.remove();
    })(t0);
  }

  function aviso_flotante(texto, ok = true) {
    const t = h("div", { role: "status", class: "toast" + (ok ? "" : " mal") }, texto);
    document.body.append(t); requestAnimationFrame(() => t.classList.add("ve"));
    setTimeout(() => { t.classList.remove("ve"); setTimeout(() => t.remove(), 300); }, 2600);
  }

  // Cuenta hasta el número con una animación corta (sin animación si la persona pidió menos movimiento).
  function contar(el, hasta, formato = (v) => String(Math.round(v)), ms = 900) {
    if (reducido() || !isFinite(hasta)) { el.textContent = formato(hasta); return; }
    const t0 = performance.now();
    (function paso(t) {
      const k = Math.min(1, (t - t0) / ms), e = 1 - (1 - k) ** 3;
      el.textContent = formato(hasta * e);
      if (k < 1) requestAnimationFrame(paso);
    })(t0);
    setTimeout(() => { el.textContent = formato(hasta); }, ms + 200);   // seguro: si la pestaña estaba oculta y el navegador pausó la animación, igual termina en el valor correcto
  }

  // Archivo .ics para «Agregar a mi calendario» (cualquier celular lo abre).
  function ics({ titulo, inicio, fin, lugar = "", detalle = "" }) {
    const f = (d) => new Date(d).toISOString().replace(/[-:]|\.\d{3}/g, "");
    const esc = (s) => String(s).replace(/([,;\\])/g, "\\$1").replace(/\n/g, "\\n");
    const txt = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Reservas//ES", "BEGIN:VEVENT", `UID:${crypto.randomUUID()}@reservas`, `DTSTAMP:${f(new Date())}`, `DTSTART:${f(inicio)}`, `DTEND:${f(fin)}`,
      `SUMMARY:${esc(titulo)}`, lugar && `LOCATION:${esc(lugar)}`, detalle && `DESCRIPTION:${esc(detalle)}`, "BEGIN:VALARM", "TRIGGER:-PT60M", "ACTION:DISPLAY", `DESCRIPTION:${esc(titulo)}`, "END:VALARM", "END:VEVENT", "END:VCALENDAR"].filter(Boolean).join("\r\n");
    return new Blob([txt], { type: "text/calendar;charset=utf-8" });
  }
  function descargar(blob, nombre) {
    const a = h("a", { href: URL.createObjectURL(blob), download: nombre });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  const EMOJIS = [[/uñ|unas|manicur|pedicur|acril|gel|esmalt/i, "💅"], [/barba|afeit|perfil|navaja/i, "🪒"], [/masaje|relaj|descontract|piedras|espalda/i, "💆"], [/facial|limpieza|rostro|hidrat|piel/i, "🧖"],
    [/depil|cera|laser|láser/i, "🌸"], [/tinte|color|mecha|balayage|reflejo/i, "🎨"], [/ceja|pestañ|lifting/i, "👁️"], [/niñ|infantil|kids/i, "🧒"], [/corte|cabello|pelo|peinad|brushing|keratina|alisado/i, "✂️"]];
  const emojiDe = (nombre) => (EMOJIS.find(([re]) => re.test(nombre)) || [null, "✨"])[1];
  const PALETA = ["#e4372e", "#1d6fb8", "#f2a541", "#2e9e73", "#8e5db8", "#d1495b", "#2a9d8f", "#e07a5f"];
  const colorDe = (txt) => PALETA[[...String(txt)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7) % PALETA.length];
  const iniciales = (n) => String(n).trim().split(/\s+/).slice(0, 2).map((x) => x[0]?.toUpperCase() ?? "").join("");

  return { $, h, aviso, aplicarMarca, aplicarTipo, confeti, aviso_flotante, contar, ics, descargar, emojiDe, colorDe, iniciales, reducido, mezcla, luz, sobre };
})();
