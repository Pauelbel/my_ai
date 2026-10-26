// Граф вики: точка — заметка, линия — ссылка [[…]]. Точки таскаются мышкой, фон — панорама, колесо — масштаб,
// наведение подсвечивает соседей, щелчок открывает заметку. SVG: цвета — из переменных темы (wiki.css).

"use strict";

window.WikiGraph = (function () {
  const SVG = "http://www.w3.org/2000/svg";
  const LINK = 130;          // длина пружины-связи
  const REPULSE = 24000;     // отталкивание точек
  const GRAVITY = 0.006;     // притяжение к центру: несвязанные группы не разлетаются
  const DAMP = 0.85;         // затухание скорости за шаг
  const MAX_TICKS = 500;     // шагов раскладки после изменения
  const LABELS_FROM = 0.7;   // с этого масштаба подписи видны у всех точек
  const CLICK_PX = 4;        // сдвиг меньше — это щелчок, а не перетаскивание
  const COLORS = 8;

  // ----------------------------------------
  // SVG-элемент с классом
  // ----------------------------------------
  function make(tag, cls) {
    const node = document.createElementNS(SVG, tag);
    if (cls) node.setAttribute("class", cls);
    return node;
  }

  // ----------------------------------------
  // Стабильное «случайное» место новой точки: по имени, чтобы граф не прыгал между запусками
  // ----------------------------------------
  function seed(id) {
    let h = 2166136261;
    for (const ch of id) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    const angle = ((h >>> 0) % 3600) / 3600 * Math.PI * 2;
    const radius = 120 + ((h >>> 12) % 260);
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  }

  // ----------------------------------------
  // Граф в контейнере root; onOpen(id) — открыть заметку
  // ----------------------------------------
  function create(root, { onOpen }) {
    const svg = make("svg", "wg-svg");
    const scene = make("g");
    const edgeLayer = make("g", "wg-edges");
    const nodeLayer = make("g", "wg-nodes");
    scene.append(edgeLayer, nodeLayer);
    svg.append(scene);
    root.append(svg);

    const pos = new Map();   // id → {x, y, vx, vy, fx?, fy?} — переживает обновления данных
    let nodes = [], edges = [], byId = new Map(), neighbours = new Map();
    let view = { x: 0, y: 0, k: 1 }, ticks = 0, frame = 0, current = "";
    let drag = null, pan = null;

    // ----------------------------------------
    // Новые данные: старые точки остаются на местах, новые встают рядом с соседями
    // ----------------------------------------
    function update(data, cur = current) {
      nodes = data.nodes || [];
      edges = (data.edges || []).filter((e) => e.source !== e.target);
      byId = new Map(nodes.map((n) => [n.id, n]));
      neighbours = new Map(nodes.map((n) => [n.id, new Set()]));
      for (const e of edges) {
        if (!byId.has(e.source) || !byId.has(e.target)) continue;
        neighbours.get(e.source).add(e.target);
        neighbours.get(e.target).add(e.source);
      }
      for (const id of [...pos.keys()]) if (!byId.has(id)) pos.delete(id);
      for (const n of nodes) {
        if (pos.has(n.id)) continue;
        const near = [...neighbours.get(n.id)].map((id) => pos.get(id)).find(Boolean);
        const s = seed(n.id);
        pos.set(n.id, near ? { x: near.x + s.x / 4, y: near.y + s.y / 4, vx: 0, vy: 0 } : { ...s, vx: 0, vy: 0 });
      }
      build();
      setCurrent(cur);
      reheat();
    }

    // ----------------------------------------
    // Элементы SVG заново: линии, точки с подписями
    // ----------------------------------------
    function build() {
      edgeLayer.replaceChildren(...edges.filter((e) => byId.has(e.source) && byId.has(e.target)).map((e) => {
        const line = make("line", "wg-edge");
        line.dataset.a = e.source;
        line.dataset.b = e.target;
        return line;
      }));
      nodeLayer.replaceChildren(...nodes.map((n) => {
        const g = make("g", `wg-node wg-c${n.group % COLORS}`);
        g.dataset.id = n.id;
        const circle = make("circle");
        circle.setAttribute("r", String(radius(n)));
        const label = make("text");
        label.textContent = n.title;
        label.setAttribute("dy", String(radius(n) + 13));
        const tip = make("title");
        tip.textContent = `${n.title}\n${n.folder}`;
        g.append(circle, label, tip);
        g.addEventListener("pointerenter", () => focus(n.id));
        g.addEventListener("pointerleave", () => { if (!drag) focus(null); });
        g.addEventListener("pointerdown", (e) => startDrag(e, n.id));
        return g;
      }));
      draw();
    }

    // ----------------------------------------
    // Радиус точки: больше связей — крупнее
    // ----------------------------------------
    function radius(n) {
      return 7 + Math.sqrt(n.degree || 0) * 3.5;
    }

    // ----------------------------------------
    // Открытая заметка — с кольцом
    // ----------------------------------------
    function setCurrent(id) {
      current = id || "";
      for (const g of nodeLayer.children) g.classList.toggle("current", g.dataset.id === current);
    }

    // ----------------------------------------
    // Подсветка точки и её соседей; null — снять
    // ----------------------------------------
    function focus(id) {
      svg.classList.toggle("focus", Boolean(id));
      const near = id ? neighbours.get(id) || new Set() : new Set();
      for (const g of nodeLayer.children) g.classList.toggle("hl", g.dataset.id === id || near.has(g.dataset.id));
      for (const line of edgeLayer.children) line.classList.toggle("hl", line.dataset.a === id || line.dataset.b === id);
    }

    // ----------------------------------------
    // Раскладка снова движется
    // ----------------------------------------
    function reheat(steps = MAX_TICKS) {
      ticks = Math.max(ticks, steps);
      if (!frame) frame = requestAnimationFrame(tick);
    }

    // ----------------------------------------
    // Шаг физики: отталкивание, пружины-связи, притяжение к центру, затухание
    // ----------------------------------------
    function tick() {
      frame = 0;
      const list = nodes.map((n) => pos.get(n.id));
      for (let i = 0; i < list.length; i++) {
        const a = list[i];
        for (let j = i + 1; j < list.length; j++) {
          const b = list[j];
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1; }
          const f = REPULSE / d2 / Math.sqrt(d2);
          a.vx += dx * f; a.vy += dy * f;
          b.vx -= dx * f; b.vy -= dy * f;
        }
      }
      for (const e of edges) {
        const a = pos.get(e.source), b = pos.get(e.target);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - LINK) / d * 0.03;
        a.vx += dx * f; a.vy += dy * f;
        b.vx -= dx * f; b.vy -= dy * f;
      }
      let energy = 0;
      for (const p of list) {
        if (p.fx !== undefined) { p.x = p.fx; p.y = p.fy; p.vx = p.vy = 0; continue; }
        p.vx = (p.vx - p.x * GRAVITY) * DAMP;
        p.vy = (p.vy - p.y * GRAVITY) * DAMP;
        const speed = Math.hypot(p.vx, p.vy);
        if (speed > 30) { p.vx *= 30 / speed; p.vy *= 30 / speed; }
        p.x += p.vx; p.y += p.vy;
        energy += p.vx * p.vx + p.vy * p.vy;
      }
      draw();
      ticks--;
      if (ticks > 0 && (energy > 0.02 * list.length || drag)) frame = requestAnimationFrame(tick);
      else ticks = 0;
    }

    // ----------------------------------------
    // Координаты на экран: центр контейнера + сдвиг и масштаб вида
    // ----------------------------------------
    function draw() {
      const w = svg.clientWidth || root.clientWidth, h = svg.clientHeight || root.clientHeight;
      scene.setAttribute("transform", `translate(${w / 2 + view.x} ${h / 2 + view.y}) scale(${view.k})`);
      svg.classList.toggle("labels", view.k >= LABELS_FROM);
      for (const line of edgeLayer.children) {
        const a = pos.get(line.dataset.a), b = pos.get(line.dataset.b);
        line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
        line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      }
      for (const g of nodeLayer.children) {
        const p = pos.get(g.dataset.id);
        g.setAttribute("transform", `translate(${p.x} ${p.y})`);
      }
    }

    // ----------------------------------------
    // Точка экрана → координаты графа
    // ----------------------------------------
    function toWorld(e) {
      const box = svg.getBoundingClientRect();
      return { x: (e.clientX - box.left - box.width / 2 - view.x) / view.k,
               y: (e.clientY - box.top - box.height / 2 - view.y) / view.k };
    }

    // ----------------------------------------
    // Перетаскивание точки; без сдвига — щелчок (открыть заметку)
    // ----------------------------------------
    function startDrag(e, id) {
      if (e.button !== 0) return;
      e.stopPropagation();
      svg.setPointerCapture(e.pointerId);
      drag = { id, x: e.clientX, y: e.clientY, moved: false };
      focus(id);
    }

    svg.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || drag) return;
      svg.setPointerCapture(e.pointerId);
      pan = { x: e.clientX - view.x, y: e.clientY - view.y };
      svg.classList.add("panning");
    });

    svg.addEventListener("pointermove", (e) => {
      if (drag) {
        if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < CLICK_PX) return;
        drag.moved = true;
        const p = pos.get(drag.id), w = toWorld(e);
        p.fx = w.x; p.fy = w.y;
        reheat(120);
      } else if (pan) {
        view.x = e.clientX - pan.x;
        view.y = e.clientY - pan.y;
        draw();
      }
    });

    // ----------------------------------------
    // Отпустили: точка снова свободна, раскладка дорабатывает; щелчок — открыть
    // ----------------------------------------
    function release() {
      if (drag) {
        const p = pos.get(drag.id);
        if (p) { delete p.fx; delete p.fy; }
        if (!drag.moved) onOpen(drag.id);
        drag = null;
        focus(null);
        reheat(200);
      }
      pan = null;
      svg.classList.remove("panning");
    }

    svg.addEventListener("pointerup", release);
    svg.addEventListener("pointercancel", release);

    // ----------------------------------------
    // Колесо: масштаб вокруг курсора
    // ----------------------------------------
    svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const before = toWorld(e);
      view.k = Math.min(4, Math.max(0.2, view.k * Math.exp(-e.deltaY * 0.0015)));
      const box = svg.getBoundingClientRect();
      view.x = e.clientX - box.left - box.width / 2 - before.x * view.k;
      view.y = e.clientY - box.top - box.height / 2 - before.y * view.k;
      draw();
    }, { passive: false });

    // ----------------------------------------
    // Двойной клик по фону — вид по центру, исходный масштаб
    // ----------------------------------------
    svg.addEventListener("dblclick", (e) => {
      if (e.target !== svg) return;
      view = { x: 0, y: 0, k: 1 };
      draw();
    });

    return { update, setCurrent, draw };
  }

  return { create };
})();
