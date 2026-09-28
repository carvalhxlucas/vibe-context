// VibeContext dashboard behaviour. Loaded as a static file: the page's CSP allows no
// inline script, no eval. Data arrives in data-* attributes rendered by the server.
(() => {
  "use strict";

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------------------------------------------------------------- widths
  // Inline style attributes are blocked by the CSP; setting them from script is not.
  function applyWidths(root) {
    root.querySelectorAll("[data-w]").forEach((el) => { el.style.width = el.dataset.w; });
  }

  // ---------------------------------------------------------------- toast
  let toastTimer = null;
  function toast(text) {
    const el = document.getElementById("toast");
    if (!el || !text) return;
    el.querySelector(".toast-text").textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
  }

  // ---------------------------------------------------------------- brain
  // Ported from the design's canvas engine: a point cloud shaped like two hemispheres,
  // lit up in proportion to the indexed tokens, colored by where they come from.
  class Brain {
    constructor(canvas) {
      this.canvas = canvas;
      this.eng = null;
      this.feedUntil = 0;
      this.spec = null;
      this.density = 1100;
      this.autoRotate = !reducedMotion;
      this.particles = !reducedMotion;
      this.tick = this.tick.bind(this);
      this.readSpec();
      this.raf = requestAnimationFrame(this.tick);
    }

    readSpec() {
      const overlay = document.getElementById("ctx-overlay");
      if (!overlay) return;
      try { this.spec = JSON.parse(overlay.dataset.spec); } catch (_) { this.spec = null; }
    }

    feed() {
      this.feedUntil = performance.now() + 2600;
      if (this.eng) this.eng.swell = 1;
    }

    build(N) {
      let seed = 1337;
      const rnd = () => ((seed = (seed * 16807) % 2147483647), (seed - 1) / 2147483646);
      const pts = [];
      let tries = 0;
      while (pts.length < N && tries < N * 120) {
        tries++;
        const side = rnd() < 0.5 ? -1 : 1;
        let x = rnd() * 2 - 1, y = rnd() * 2 - 1, z = rnd() * 2 - 1;
        const r2 = x * x + y * y + z * z;
        if (r2 > 1 || r2 < 1e-4) continue;
        const shell = rnd() < 0.72;
        if (shell) {
          const k = (0.93 + rnd() * 0.07) / Math.sqrt(r2);
          x *= k; y *= k; z *= k;
          const a = Math.atan2(y, z), b = Math.asin(Math.max(-1, Math.min(1, x * side)));
          if (Math.sin(a * 6 + Math.sin(b * 4 + a * 1.5) * 2.4 + b * 3) < -0.05) continue;
        }
        const X = side * 0.5 + x * 0.47;
        if (X * side < 0.045) continue;
        const Y = y * 0.62;
        if (Y < -0.36) continue;
        const Z = z * 0.92 * (1 - 0.07 * y);
        const rad = Math.sqrt(X * X + Y * Y * 1.8 + Z * Z) / 1.05;
        pts.push({ x: X, y: Y, z: Z, shell, key: rnd() * 0.55 + rad * 0.45, a: 0, vis: 0, born: 0,
                   r: 163, g: 147, b: 255, sx: 0, sy: 0, d: 0 });
      }
      pts.sort((p, q) => p.key - q.key);
      const n = pts.length, nb = pts.map(() => []), edges = [], seen = new Set();
      for (let i = 0; i < n; i++) {
        const best = [];
        for (let j = 0; j < n; j++) {
          if (i === j) continue;
          const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y, dz = pts[i].z - pts[j].z;
          const d = dx * dx + dy * dy + dz * dz;
          if (d < 0.028) best.push([d, j]);
        }
        best.sort((p, q) => p[0] - q[0]);
        for (let q = 0; q < Math.min(3, best.length); q++) {
          const j = best[q][1], key = i < j ? i * n + j : j * n + i;
          if (seen.has(key)) continue;
          seen.add(key);
          edges.push([i, j]); nb[i].push(j); nb[j].push(i);
        }
      }
      this.eng = { N, pts, nb, edges, shown: 0, scale: 0.4, swell: 0, parts: [], pulses: [] };
    }

    tick(t) {
      this.raf = requestAnimationFrame(this.tick);
      const c = this.canvas, spec = this.spec;
      if (!c.isConnected) { cancelAnimationFrame(this.raf); return; }
      if (!spec || document.hidden) return;
      if (!this.eng || this.eng.N !== this.density) this.build(this.density);
      const E = this.eng, P = E.pts;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const w = c.clientWidth, h = c.clientHeight;
      if (!w || !h) return;
      if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
        c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
      }
      const ctx = c.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const fill = Math.min(1, spec.tokens / spec.budget);
      const target = Math.min(P.length, Math.round(P.length * (0.08 + 0.92 * Math.pow(fill, 0.6))));
      const feeding = t < this.feedUntil;
      if (E.shown < target) {
        const k = Math.max(1, Math.ceil((target - E.shown) * (feeding ? 0.035 : 0.08)));
        for (let q = 0; q < k && E.shown < target; q++) {
          const p = P[E.shown++]; p.vis = 1;
          if (feeding && this.particles && E.parts.length < 420) {
            const dur = 900 + Math.random() * 900;
            E.parts.push({ i: E.shown - 1, ang: Math.random() * Math.PI * 2, t0: t, dur, bend: (Math.random() - 0.5) * 0.9 });
            p.born = t + dur;
          } else p.born = t - 700;
        }
      } else if (E.shown > target) {
        const k = Math.max(1, Math.ceil((E.shown - target) * 0.08));
        for (let q = 0; q < k && E.shown > target; q++) P[--E.shown].vis = 0;
      }

      const segs = spec.segs.length ? spec.segs : [{ tokens: 1, rgb: [163, 147, 255] }];
      const total = segs.reduce((a, s) => a + s.tokens, 0) || 1;
      const bounds = [];
      let acc = 0;
      for (const s of segs) { acc += s.tokens / total; bounds.push(acc); }
      const tScale = (0.45 + 0.55 * Math.sqrt(fill)) * (1 + E.swell * 0.08);
      E.scale += (tScale - E.scale) * 0.035; E.swell *= 0.975;

      const yaw = this.autoRotate ? t * 0.00009 : 0.5;
      const pitch = 0.62, cy0 = Math.cos(yaw), sy0 = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
      const cx = w / 2, cy = h * 0.52, R = Math.min(w, h * 1.3) * 0.33 * E.scale;

      ctx.globalCompositeOperation = "lighter";
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.5);
      g.addColorStop(0, `rgba(120,108,255,${0.10 + 0.08 * fill})`);
      g.addColorStop(0.5, "rgba(80,120,255,0.04)");
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);

      const rings = [[1.32, 0.00022, [163, 147, 255]], [1.62, -0.00015, [92, 208, 230]]];
      for (const [rr, spd, col] of rings) {
        const rx = rr * R, ry = rr * R * sp * 0.62;
        ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2); ctx.stroke();
        for (let k = 0; k < 3; k++) {
          const an = t * spd + (k * Math.PI * 2) / 3, ox = cx + Math.cos(an) * rx, oy = cy + Math.sin(an) * ry;
          const front = 0.35 + 0.65 * (Math.sin(an) * 0.5 + 0.5);
          ctx.fillStyle = `rgba(${col[0]},${col[1]},${col[2]},${0.8 * front})`;
          ctx.beginPath(); ctx.arc(ox, oy, 1.6 + front, 0, Math.PI * 2); ctx.fill();
        }
      }

      let si = 0;
      for (let i = 0; i < P.length; i++) {
        const p = P[i];
        if (p.vis) {
          const f = i / Math.max(1, target);
          while (si < bounds.length - 1 && f > bounds[si]) si++;
          const col = segs[si] ? segs[si].rgb : [163, 147, 255];
          p.r += (col[0] - p.r) * 0.06; p.g += (col[1] - p.g) * 0.06; p.b += (col[2] - p.b) * 0.06;
        }
        const ta = p.vis && t >= p.born ? 1 : 0;
        p.a += (ta - p.a) * 0.07;
        if (!p.vis && p.a < 0.01) continue;
        const x1 = p.x * cy0 - p.z * sy0, z1 = p.x * sy0 + p.z * cy0;
        const y2 = p.y * cp - z1 * sp, z2 = p.y * sp + z1 * cp;
        const persp = 2.8 / (2.8 + z2);
        p.sx = cx + x1 * R * persp; p.sy = cy - y2 * R * persp; p.d = Math.max(0, Math.min(1, 0.5 - z2 * 0.5));
      }

      ctx.lineWidth = 0.6;
      for (const layer of [0, 1]) {
        ctx.strokeStyle = layer ? "rgba(175,170,255,0.16)" : "rgba(150,150,255,0.06)";
        ctx.beginPath();
        for (const [i, j] of E.edges) {
          const a = P[i], b = P[j];
          if (a.a < 0.5 || b.a < 0.5) continue;
          if ((a.d + b.d > 1 ? 1 : 0) !== layer) continue;
          ctx.moveTo(a.sx, a.sy); ctx.lineTo(b.sx, b.sy);
        }
        ctx.stroke();
      }

      const maxPulses = reducedMotion ? 0 : 4 + Math.round(fill * 22);
      while (E.pulses.length < maxPulses && E.shown > 10) {
        const i = Math.floor(Math.random() * E.shown), nbs = E.nb[i].filter((j) => P[j].vis);
        if (!nbs.length) break;
        E.pulses.push({ i, j: nbs[Math.floor(Math.random() * nbs.length)], t0: t, dur: 380 + Math.random() * 300, hops: 0 });
      }
      E.pulses = E.pulses.filter((pl) => {
        const u = (t - pl.t0) / pl.dur, a = P[pl.i], b = P[pl.j];
        if (!a.vis || !b.vis) return false;
        if (u >= 1) {
          const nbs = E.nb[pl.j].filter((k) => P[k].vis && k !== pl.i);
          if (!nbs.length || pl.hops > 7 || Math.random() < 0.12) return false;
          pl.i = pl.j; pl.j = nbs[Math.floor(Math.random() * nbs.length)]; pl.t0 = t; pl.hops++;
          return true;
        }
        const x = a.sx + (b.sx - a.sx) * u, y = a.sy + (b.sy - a.sy) * u;
        ctx.strokeStyle = `rgba(${b.r | 0},${b.g | 0},${b.b | 0},0.55)`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(x, y); ctx.stroke();
        ctx.fillStyle = "rgba(235,238,255,0.9)"; ctx.beginPath(); ctx.arc(x, y, 1.4, 0, Math.PI * 2); ctx.fill();
        return true;
      });

      for (let i = 0; i < P.length; i++) {
        const p = P[i];
        if (p.a < 0.02) continue;
        const fl = p.vis ? Math.max(0, 1 - (t - p.born) / 700) : 0;
        const size = (0.55 + p.d * 1.25) * (p.shell ? 1 : 0.75) + fl * 2.6;
        const al = p.a * (0.28 + 0.72 * p.d);
        const wr = p.r + (255 - p.r) * fl, wg = p.g + (255 - p.g) * fl, wb = p.b + (255 - p.b) * fl;
        ctx.fillStyle = `rgba(${wr | 0},${wg | 0},${wb | 0},${al})`;
        ctx.beginPath(); ctx.arc(p.sx, p.sy, size, 0, Math.PI * 2); ctx.fill();
        if (fl > 0) {
          ctx.fillStyle = `rgba(${p.r | 0},${p.g | 0},${p.b | 0},${fl * 0.25})`;
          ctx.beginPath(); ctx.arc(p.sx, p.sy, size * 3.5, 0, Math.PI * 2); ctx.fill();
        }
      }

      const Rmax = Math.max(w, h) * 0.62;
      E.parts = E.parts.filter((pt) => {
        const u = (t - pt.t0) / pt.dur;
        if (u >= 1) return false;
        const p = P[pt.i];
        if (!p.vis) return false;
        const pos = (uu) => {
          const e = uu * uu * (3 - 2 * uu), e2 = Math.pow(e, 1.4);
          const ox = cx + Math.cos(pt.ang) * Rmax, oy = cy + Math.sin(pt.ang) * Rmax * 0.75;
          const bx = ox + (p.sx - ox) * e2, by = oy + (p.sy - oy) * e2;
          const off = Math.sin(e * Math.PI) * pt.bend * R * 0.6;
          const nx = -(p.sy - oy), ny = p.sx - ox, nl = Math.hypot(nx, ny) || 1;
          return [bx + (nx / nl) * off, by + (ny / nl) * off];
        };
        const [x, y] = pos(u), [x0, y0] = pos(Math.max(0, u - 0.07));
        const al = Math.min(1, u * 4) * 0.85;
        ctx.strokeStyle = `rgba(${p.r | 0},${p.g | 0},${p.b | 0},${al * 0.6})`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x, y); ctx.stroke();
        ctx.fillStyle = `rgba(240,242,255,${al})`; ctx.beginPath(); ctx.arc(x, y, 1.3, 0, Math.PI * 2); ctx.fill();
        return true;
      });
      ctx.globalCompositeOperation = "source-over";
    }
  }

  let brain = null;
  let absorbingTimer = null;

  function showAbsorbing() {
    const status = document.querySelector("#ctx-overlay [data-status]");
    if (!status) return;
    status.classList.add("busy");
    status.querySelector("[data-status-label]").textContent = "ABSORVENDO CONTEXTO";
    clearTimeout(absorbingTimer);
    absorbingTimer = setTimeout(() => {
      const current = document.querySelector("#ctx-overlay [data-status]");
      if (!current) return;
      const busy = document.getElementById("ctx-overlay").dataset.busy === "true";
      current.classList.toggle("busy", busy);
      current.querySelector("[data-status-label]").textContent = busy ? "INDEXANDO" : "SINCRONIZADO";
    }, 3000);
  }

  // ---------------------------------------------------------------- uploads
  let uploadTarget = null;

  async function upload(files, target) {
    const list = [...(files || [])];
    if (!list.length || !target) return;
    const form = new FormData();
    list.forEach((file) => form.append("files", file));
    form.append("target", target);
    toast(`Enviando ${list.length} arquivo${list.length > 1 ? "s" : ""}…`);
    let response;
    try {
      // HX-Request marks the request as coming from the dashboard (the server requires it).
      response = await fetch("/ui/upload", { method: "POST", body: form, headers: { "HX-Request": "true" } });
    } catch (_) {
      toast("Falha de conexão com o backend local.");
      return;
    }
    if (response.status === 401) { window.location.reload(); return; }
    if (!response.ok) {
      let detail = `Envio recusado (HTTP ${response.status})`;
      try { detail = (await response.json()).detail || detail; } catch (_) { /* not JSON */ }
      toast(detail);
      return;
    }
    const body = await response.json();
    const errors = body.messages.filter((m) => m.kind === "error").map((m) => m.text);
    toast(errors.length ? `${body.toast} — ${errors.join(" · ")}` : body.toast);
    let events = {};
    try { events = JSON.parse(response.headers.get("HX-Trigger") || "{}"); } catch (_) { /* none */ }
    if (events["vc-fed"] && brain) { brain.feed(); showAbsorbing(); }
    htmx.trigger(document.body, "vc-changed");
  }

  // ---------------------------------------------------------------- wiring
  function closeModal() {
    const modal = document.getElementById("modal");
    if (modal) modal.innerHTML = "";
  }

  document.addEventListener("click", (event) => {
    const uploadButton = event.target.closest("[data-upload-target]");
    if (uploadButton) {
      uploadTarget = uploadButton.dataset.uploadTarget;
      const input = document.getElementById("file-input");
      input.value = "";
      input.click();
      return;
    }
    if (event.target.closest("[data-close-modal]") || event.target.matches("[data-modal]")) {
      closeModal();
      return;
    }
    // Close open menus on any click outside them, and after choosing an item.
    document.querySelectorAll("details.menu[open]").forEach((menu) => {
      if (!menu.contains(event.target) || event.target.closest(".menu-item")) menu.open = false;
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeModal();
    document.querySelectorAll("details.menu[open]").forEach((menu) => { menu.open = false; });
  });

  document.addEventListener("change", (event) => {
    if (event.target.id === "file-input") upload(event.target.files, uploadTarget);
  });

  // Drag and drop onto a session or the global view.
  let dragDepth = 0;
  document.addEventListener("dragenter", (event) => {
    const zone = event.target.closest && event.target.closest("[data-drop-target]");
    if (!zone || !event.dataTransfer || ![...event.dataTransfer.types].includes("Files")) return;
    dragDepth++;
    zone.classList.add("dragging");
  });
  document.addEventListener("dragover", (event) => {
    if (event.target.closest && event.target.closest("[data-drop-target]")) event.preventDefault();
  });
  document.addEventListener("dragleave", (event) => {
    const zone = event.target.closest && event.target.closest("[data-drop-target]");
    if (!zone) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) zone.classList.remove("dragging");
  });
  document.addEventListener("drop", (event) => {
    const zone = event.target.closest && event.target.closest("[data-drop-target]");
    if (!zone) return;
    event.preventDefault();
    dragDepth = 0;
    zone.classList.remove("dragging");
    upload(event.dataTransfer.files, zone.dataset.dropTarget);
  });

  document.body.addEventListener("vc-toast", (event) => toast(event.detail.value));
  document.body.addEventListener("vc-fed", () => { if (brain) { brain.feed(); showAbsorbing(); } });

  document.body.addEventListener("htmx:afterSettle", (event) => {
    applyWidths(event.detail.elt.ownerDocument || document);
    if (brain) brain.readSpec();
    const input = document.querySelector("#modal [autofocus]");
    if (input && event.detail.elt.closest && event.detail.elt.closest("#modal")) input.focus();
  });

  document.body.addEventListener("htmx:responseError", (event) => {
    const xhr = event.detail.xhr;
    let detail = `Erro ${xhr.status}`;
    try { detail = JSON.parse(xhr.responseText).detail || detail; } catch (_) { /* not JSON */ }
    toast(detail);
  });

  applyWidths(document);
  const canvas = document.querySelector("canvas[data-brain]");
  if (canvas) brain = new Brain(canvas);
})();
