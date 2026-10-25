// Мини-виджет: «затмение» на canvas — тёмный диск, раскалённое кольцо, тёплая корона, внутри текут световые
// ленты, глаза следят за мышкой. Состояния — от Python через window.onState, действия — window.pywebview.api.

"use strict";

const SIZE = 140;
const C = SIZE / 2;          // центр
const R = 44;                // радиус кольца
const PINCH = 1.2;           // запись: момент, когда перемычка капли рвётся
const RIPPLE_S = 1.5;        // сколько живёт одна волна
const SLEEP_AFTER = 90;      // секунд без движения мыши — и Ася засыпает
const CLICK_DELAY = 260;     // мс: столько ждём второго клика, чтобы отличить двойной

const canvas = document.getElementById("ring");
const ctx = canvas.getContext("2d");
const menu = document.getElementById("menu");

const view = {
  state: "idle",      // idle | speaking | recording | processing | done | error
  seq: -1,            // номер последней применённой смены состояния от Python
  since: performance.now() / 1000,
  level: 0,           // сглаженная громкость 0..1
  target: 0,          // последняя громкость с микрофона или голоса
  label: "",          // подпись «скопировано» на 1 с
  wake: false,        // фон слушается — точка на кольце
  blink: false,       // ждёт подтверждения — корона мигает жёлтым
  look: null,         // курсор относительно центра виджета (от Python), пиксели экрана
  hover: null,        // курсор над самим виджетом, пиксели canvas
  gaze: { x: 0, y: 0 },        // куда сейчас смотрят глаза, -1..1
  movedAt: performance.now() / 1000,
  nextBlink: 3,
  ripples: [],        // время рождения волн во время записи
  lastRipple: 0,
};

// ======== спрайты: рисуются один раз, в кадре только накладываются (бережём CPU) ========

const RATIO = window.devicePixelRatio || 1;

// ----------------------------------------
// Пустой холст размером с виджет в физических пикселях
// ----------------------------------------
function sprite(draw) {
  const c = document.createElement("canvas");
  c.width = c.height = SIZE * RATIO;
  const g = c.getContext("2d");
  g.setTransform(RATIO, 0, 0, RATIO, 0, 0);
  draw(g);
  return c;
}

// ----------------------------------------
// Корона затмения: тёплое свечение наружу от кольца, rgb — оттенок
// ----------------------------------------
function makeCorona(rgb) {
  return sprite((g) => {
    const grad = g.createRadialGradient(C, C, R - 3, C, C, C);
    grad.addColorStop(0, `rgba(${rgb}, 0)`);
    grad.addColorStop(0.08, `rgba(${rgb}, 0.95)`);
    grad.addColorStop(0.25, `rgba(${rgb}, 0.45)`);
    grad.addColorStop(0.55, `rgba(${rgb}, 0.14)`);
    grad.addColorStop(1, `rgba(${rgb}, 0)`);
    g.fillStyle = grad;
    g.fillRect(0, 0, SIZE, SIZE);
  });
}

const CORONA = { warm: makeCorona("255, 138, 40"), red: makeCorona("255, 64, 48"), yellow: makeCorona("255, 200, 40") };

// Тёмный диск луны: почти чёрный, у края тёплый отсвет короны изнутри
const DISK = sprite((g) => {
  const grad = g.createRadialGradient(C - 6, C - 8, 4, C, C, R);
  grad.addColorStop(0, "#0b0604");
  grad.addColorStop(0.75, "#080403");
  grad.addColorStop(0.93, "#1a0b04");
  grad.addColorStop(1, "#4a2008");
  g.fillStyle = grad;
  g.beginPath();
  g.arc(C, C, R, 0, Math.PI * 2);
  g.fill();
});

// Раскалённое кольцо: белое ядро с оранжевым ореолом, слева чуть толще — как у затмения на референсе
const RING = sprite((g) => {
  g.shadowColor = "rgba(255, 150, 50, 1)";
  g.shadowBlur = 10;
  g.strokeStyle = "#fff4e0";
  g.lineWidth = 2;
  g.beginPath();
  g.arc(C, C, R, 0, Math.PI * 2);
  g.stroke();
  g.shadowBlur = 4;
  g.lineWidth = 1.4;
  g.beginPath();
  g.arc(C + 0.9, C, R - 0.6, Math.PI * 0.55, Math.PI * 1.45);
  g.stroke();
});

// Бусина — яркая точка на кольце («бриллиантовое кольцо» затмения)
const BEAD = sprite((g) => {
  const grad = g.createRadialGradient(C, C, 0, C, C, 11);
  grad.addColorStop(0, "rgba(255, 252, 240, 1)");
  grad.addColorStop(0.25, "rgba(255, 210, 140, 0.9)");
  grad.addColorStop(0.6, "rgba(255, 140, 40, 0.35)");
  grad.addColorStop(1, "rgba(255, 120, 30, 0)");
  g.fillStyle = grad;
  g.fillRect(C - 11, C - 11, 22, 22);
});

// ======== состояние ========

// ----------------------------------------
// API Python; до события pywebviewready — заглушка, которая ничего не делает
// ----------------------------------------
function api() {
  return (window.pywebview && window.pywebview.api) || new Proxy({}, { get: () => () => Promise.resolve(null) });
}

// ----------------------------------------
// Состояние от Python (~30 раз/с во время записи); look — где курсор относительно виджета
// ----------------------------------------
window.onState = function (data) {
  // Python шлёт состояние целиком (например, с каждым сдвигом курсора); новое — только при новом seq,
  // иначе «готово» и «ошибка», из которых виджет сам вернулся в покой, показывались бы снова и снова
  if (data.state && data.seq !== view.seq) {
    view.seq = data.seq;
    if (data.state !== view.state || data.state === "done" || data.state === "error") setState(data.state, data);
  }
  if (typeof data.level === "number") view.target = data.level;
  if (typeof data.wake === "boolean") {
    view.wake = data.wake;
    document.getElementById("wake-state").textContent = data.wake ? "вкл" : "выкл";
  }
  if (typeof data.blink === "boolean") view.blink = data.blink;
  if (Array.isArray(data.look)) {
    const old = view.look;
    view.look = data.look;
    if (!old || Math.abs(old[0] - data.look[0]) + Math.abs(old[1] - data.look[1]) > 2) view.movedAt = now();
  }
};

// ----------------------------------------
// Смена состояния: подсказка с ошибкой, подпись, отсчёт анимации заново
// ----------------------------------------
function setState(state, data) {
  view.state = state;
  view.since = now();
  view.ripples = [];
  if (state === "error") {
    canvas.title = data.text || "Ошибка";
  } else if (state === "done") {
    view.label = data.text || "готово";
    canvas.title = data.note || data.text || "";
  } else if (state === "recording") {
    canvas.title = "Запись: клик или 1,5 с тишины — стоп";
  } else if (state === "idle") {
    canvas.title = "Клик — запись · двойной клик — чат · правый клик — меню";
  }
}

// ----------------------------------------
// Секунды с загрузки страницы
// ----------------------------------------
function now() {
  return performance.now() / 1000;
}

// ----------------------------------------
// Плавный переход 0..1
// ----------------------------------------
function ease(t) {
  t = Math.min(1, Math.max(0, t));
  return t * t * (3 - 2 * t);
}

// ----------------------------------------
// Доля пути внутри отрезка [a, b) времени
// ----------------------------------------
function span(t, a, b) {
  return Math.min(1, Math.max(0, (t - a) / (b - a)));
}

// ======== слои: корона, диск, ленты, кольцо, бусина ========

// ----------------------------------------
// Корона: дышит, а при голосе — растёт и светлеет; ошибка — красная, ждём подтверждения — мигает жёлтым
// ----------------------------------------
function drawCorona(t, boost) {
  let kind = "warm";
  if (view.state === "error" && t - view.since < 0.9) kind = "red";
  else if (view.blink && Math.floor(t * 2.5) % 2 === 0) kind = "yellow";
  const breath = 0.5 + 0.5 * Math.sin(t * Math.PI * 2 / 4);
  const scale = 1 + breath * 0.025 + boost * 0.07;
  ctx.save();
  ctx.globalAlpha = Math.min(1, 0.72 + breath * 0.18 + boost * 0.35);
  const s = SIZE * scale;
  ctx.drawImage(CORONA[kind], C - s / 2, C - s / 2, s, s);
  ctx.restore();
}

// Ленты рисуются в маленький буфер и растягиваются на диск — края сами становятся мягкими, как дымка в шаре
const WISP_SCALE = 0.22;
const wispBuffer = document.createElement("canvas");
wispBuffer.width = wispBuffer.height = Math.round(SIZE * WISP_SCALE);
const wispCtx = wispBuffer.getContext("2d");

// ----------------------------------------
// Световые вуали внутри диска, как в стеклянном шаре: медленно текут, меняют форму, светятся сильнее от голоса
// ----------------------------------------
function drawWisps(t, power = 1) {
  // Дуги вдоль стенок шара, как отражения в стекле: плывут по кругу, дышат шириной и длиной
  const wisps = [
    { color: "255, 175, 95", speed: 0.17, phase: 0.6, radius: 33, span: 1.7, width: 7 },
    { color: "255, 205, 150", speed: -0.12, phase: 3.4, radius: 27, span: 1.3, width: 6 },
    { color: "255, 145, 70", speed: 0.08, phase: 5.0, radius: 37, span: 1.0, width: 5 },
  ];
  const g = wispCtx;
  g.setTransform(WISP_SCALE, 0, 0, WISP_SCALE, 0, 0);
  g.clearRect(0, 0, SIZE, SIZE);
  g.globalCompositeOperation = "lighter";
  g.lineCap = "round";
  for (const w of wisps) {
    const start = w.phase + t * w.speed;
    const span = w.span + Math.sin(t * 0.33 + w.phase) * 0.4;
    const radius = w.radius + Math.sin(t * 0.45 + w.phase * 2) * 3;
    const alpha = Math.min(0.95, (0.42 + 0.15 * Math.sin(t * 0.7 + w.phase)) * power);
    const grad = g.createConicGradient(start, C, C);  // ярче к середине дуги, гаснет к концам
    const mid = span / 2 / (Math.PI * 2), end = span / (Math.PI * 2);
    grad.addColorStop(0, `rgba(${w.color}, 0)`);
    grad.addColorStop(mid, `rgba(${w.color}, ${alpha})`);
    grad.addColorStop(end, `rgba(${w.color}, 0)`);
    grad.addColorStop(1, `rgba(${w.color}, 0)`);
    g.strokeStyle = grad;
    g.lineWidth = w.width * (0.8 + 0.3 * Math.sin(t * 0.6 + w.phase));
    g.beginPath();
    g.arc(C, C, radius, start, start + span);
    g.stroke();
  }
  ctx.save();
  ctx.beginPath();
  ctx.arc(C, C, R - 2, 0, Math.PI * 2);
  ctx.clip();
  ctx.globalCompositeOperation = "lighter";
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(wispBuffer, 0, 0, SIZE, SIZE);
  ctx.restore();
}

// ----------------------------------------
// Бусина на кольце: медленно ползёт по кругу; fast — бежит с хвостом (Ася думает)
// ----------------------------------------
function drawBead(t, fast = false) {
  const bead = (angle, alpha, scale) => {
    const x = C + Math.cos(angle) * R, y = C + Math.sin(angle) * R, s = SIZE * scale;
    ctx.globalAlpha = alpha;
    ctx.drawImage(BEAD, x - s / 2, y - s / 2, s, s);
  };
  ctx.save();
  if (fast) {
    const head = (t - view.since) * Math.PI * 2 / 1.1 - Math.PI / 2;
    for (let i = 7; i >= 0; i--) bead(head - i * 0.13, 1 - i * 0.12, 1 - i * 0.08);
  } else {
    const angle = -0.15 + t * 0.06;
    bead(angle, 0.75 + 0.25 * Math.sin(t * 1.3), 1);
    bead(angle + Math.PI * 0.96, 0.35, 0.55);  // слабый отблеск напротив
  }
  ctx.restore();
}

// ----------------------------------------
// Точка «фон слушается» на кольце — холодная, чтобы не путать с короной
// ----------------------------------------
function drawWakeDot(t) {
  const a = -Math.PI / 4;
  ctx.save();
  ctx.globalAlpha = 0.6 + 0.4 * Math.sin(t * 2);
  ctx.fillStyle = "#7fd8ff";
  ctx.beginPath();
  ctx.arc(C + Math.cos(a) * (R + 9), C + Math.sin(a) * (R + 9), 2.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.restore();
}

// ======== глаза ========

// ----------------------------------------
// Куда смотреть: курсор над виджетом важнее, иначе — позиция курсора на экране от Python
// ----------------------------------------
function gazeTarget() {
  if (view.hover) {
    return { x: Math.max(-1, Math.min(1, (view.hover[0] - C) / 45)), y: Math.max(-1, Math.min(1, (view.hover[1] - C) / 45)) };
  }
  if (!view.look) return { x: 0, y: 0 };
  const [dx, dy] = view.look;
  const dist = Math.hypot(dx, dy);
  if (dist < 1) return { x: 0, y: 0 };
  const reach = Math.min(1, dist / 260);  // дальше ~260 px — смотрит в край
  return { x: dx / dist * reach, y: dy / dist * reach * 0.8 };
}

// ----------------------------------------
// Плавный поворот глаз к цели; true — ещё двигаются (нужны частые кадры)
// ----------------------------------------
function updateGaze() {
  const target = gazeTarget();
  view.gaze.x += (target.x - view.gaze.x) * 0.3;
  view.gaze.y += (target.y - view.gaze.y) * 0.3;
  return Math.abs(target.x - view.gaze.x) + Math.abs(target.y - view.gaze.y) > 0.01;
}

// ----------------------------------------
// Моргание: раз в 2.5–6 с, иногда двойное; 1 — открыты, 0.1 — закрыты
// ----------------------------------------
function blinkOpen(t) {
  if (t > view.nextBlink + 0.14) view.nextBlink = t + 2.5 + Math.random() * 3.5 - (Math.random() < 0.2 ? 2.2 : 0);
  return t > view.nextBlink && t < view.nextBlink + 0.14 ? 0.1 : 1;
}

// ----------------------------------------
// Глаза по таблице эмоций: normal | happy (^ ^) | closed (‿ ‿) | sleep (◡ ◡) | surprised | busy (●—●) | error (× ×)
// ----------------------------------------
function drawEyes(t, kind, open = 1, bounce = 0) {
  const gx = view.gaze.x, gy = view.gaze.y;
  const spread = 11 * (1 - Math.abs(gx) * 0.12);            // голова повернулась — глаза ближе
  const ox = gx * 8, oy = gy * 6 - bounce;
  ctx.save();
  ctx.shadowColor = "rgba(255, 150, 50, 0.95)";
  ctx.shadowBlur = 8;
  ctx.fillStyle = ctx.strokeStyle = "#fff1dc";
  ctx.lineWidth = 2.6;
  ctx.lineCap = ctx.lineJoin = "round";
  if (kind === "busy") {                                    // ●—●
    ctx.beginPath();
    ctx.moveTo(C - spread + ox, C + oy); ctx.lineTo(C + spread + ox, C + oy);
    ctx.lineWidth = 1.6; ctx.stroke();
    for (const side of [-1, 1]) { ctx.beginPath(); ctx.arc(C + side * spread + ox, C + oy, 3.4, 0, Math.PI * 2); ctx.fill(); }
    ctx.restore();
    return;
  }
  for (const side of [-1, 1]) {
    const x = C + side * spread + ox, y = C - 1 + oy;
    const near = 1 - side * gx * 0.15;                      // дальний от взгляда глаз чуть уже
    ctx.beginPath();
    if (kind === "happy") {                                 // ^ ^
      ctx.arc(x, y + 4, 5.5, Math.PI * 1.15, Math.PI * 1.85); ctx.stroke();
    } else if (kind === "closed") {                         // ‿ ‿
      ctx.arc(x, y - 4, 5.5, Math.PI * 0.15, Math.PI * 0.85); ctx.stroke();
    } else if (kind === "sleep") {                          // ◡ ◡ ниже и плоше
      ctx.ellipse(x, y + 2, 5, 2.4, 0, Math.PI * 0.1, Math.PI * 0.9); ctx.stroke();
    } else if (kind === "error") {                          // × ×
      ctx.moveTo(x - 4, y - 4); ctx.lineTo(x + 4, y + 4); ctx.moveTo(x + 4, y - 4); ctx.lineTo(x - 4, y + 4); ctx.stroke();
    } else {                                                // | | — обычные; surprised — с бровками
      const h = Math.max(2, 14 * open), w = 5.6 * near;
      ctx.roundRect(x - w / 2, y - h / 2, w, h, w / 2); ctx.fill();
      if (kind === "surprised") {
        ctx.beginPath(); ctx.lineWidth = 1.8;
        ctx.arc(x, y - 7, 5, Math.PI * 1.2, Math.PI * 1.8); ctx.stroke();
      }
    }
  }
  if (kind === "sleep") {                                   // «z» уплывает вверх
    const k = (t % 3) / 3;
    ctx.globalAlpha = 1 - k;
    ctx.shadowBlur = 0;
    ctx.font = `${7 + k * 4}px 'JetBrains Mono', Consolas, monospace`;
    ctx.fillText("z", C + 17 + k * 6, C - 10 - k * 12);
  }
  ctx.restore();
}

// ======== запись: капля из кольца становится шариком, круги — в такт голосу ========

// ----------------------------------------
// Жидкий свет: все части одним путём, чтобы свечение было общим
// ----------------------------------------
function fillLiquid(build, alpha = 1, glow = 12) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.shadowColor = "rgba(255, 150, 50, 1)";
  ctx.shadowBlur = glow;
  ctx.fillStyle = "#fff1dc";
  ctx.beginPath();
  build();
  ctx.fill();
  ctx.restore();
}

// ----------------------------------------
// Шарик с упругой деформацией: sx, sy — сплющивание и вытягивание
// ----------------------------------------
function ballPath(x, y, r, sx = 1, sy = 1) {
  ctx.moveTo(x + r * sx, y);
  ctx.ellipse(x, y, r * sx, r * sy, 0, 0, Math.PI * 2);
}

// ----------------------------------------
// Перемычка от кольца к шарику: бока прогибаются внутрь, waist — толщина в самом узком месте
// ----------------------------------------
function neckPath(baseY, base, topY, top, waist) {
  const mid = (baseY + topY) / 2;
  ctx.moveTo(C - base, baseY);
  ctx.quadraticCurveTo(C - waist, mid, C - top, topY);
  ctx.lineTo(C + top, topY);
  ctx.quadraticCurveTo(C + waist, mid, C + base, baseY);
  ctx.closePath();
}

// ----------------------------------------
// Волны по воде от шарика: рождаются на пиках голоса, расходятся и тают
// ----------------------------------------
function drawRipples(t) {
  view.ripples = view.ripples.filter((born) => t - born < RIPPLE_S);
  if (!view.ripples.length) return;
  ctx.save();
  ctx.beginPath();
  ctx.arc(C, C, R - 3, 0, Math.PI * 2);
  ctx.clip();
  ctx.strokeStyle = "#ffc27a";
  for (const born of view.ripples) {
    const a = (t - born) / RIPPLE_S;
    ctx.globalAlpha = (1 - a) * (1 - a) * 0.75;
    ctx.lineWidth = 1.8 * (1 - a) + 0.4;
    ctx.beginPath();
    ctx.arc(C, C, 10 + a * (R - 14), 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.restore();
}

// ----------------------------------------
// Запись: на кольце снизу набухает капля, тянется, рвётся — шарик в центре качается к зрителю в такт голосу
// ----------------------------------------
function drawDrop(t, lv) {
  const k = t - view.since;
  const ring = C + R - 1.5;
  if (k < 0.45) {                                  // набухает на кольце
    const g = ease(span(k, 0, 0.45));
    fillLiquid(() => ballPath(C, ring - g * 5, 2 + g * 5));
  } else if (k < PINCH) {                          // тянется вверх, перемычка истончается
    const g = ease(span(k, 0.45, PINCH));
    const y = ring - 5 - g * 15;
    fillLiquid(() => {
      ballPath(C, y, 7 + g, 1 - g * 0.12, 1 + g * 0.2);
      neckPath(ring + 1, 6 - g * 2, y + 3, 5 - g * 1.5, 4.5 * (1 - g) + 0.6);
      ballPath(C, ring, 4 - g * 1.5);
    });
  } else {                                         // оторвался: летит в центр и качается в такт голосу
    const free = k - PINCH;
    const fly = 1 - Math.pow(1 - span(free, 0, 0.5), 3);
    const y = ring - 20 - fly * (ring - 20 - C);
    const z = Math.exp(-free * 2.6) * Math.sin(free * 11) * 0.8 + lv * 1.4 + Math.sin(t * 2.2) * 0.05;
    if (free > 0.5 && lv > 0.18 && t - view.lastRipple > 0.28) {   // пик голоса — новая волна
      view.ripples.push(t);
      view.lastRipple = t;
    }
    if (free > 0 && free < 0.05 && !view.ripples.length) view.ripples.push(t);  // всплеск при отрыве
    fillLiquid(() => {
      ballPath(C, y, 8.5 * (1 + z * 0.2));
      if (free < 0.3) ballPath(C, ring - 1, 2.5 * (1 - free / 0.3));
    }, Math.min(1, 0.85 + z * 0.15), 12 + z * 8);
  }
}

// ======== кадр ========

// ----------------------------------------
// Один кадр по текущему состоянию; возвращает, сколько кадров в секунду нужно дальше
// ----------------------------------------
function draw() {
  const t = now();
  const k = t - view.since;
  const voice = view.state === "recording" || view.state === "speaking";
  view.level += ((voice ? view.target : 0) - view.level) * 0.35;
  const lv = Math.min(1, view.level * 1.6);
  const moving = updateGaze();
  const asleep = view.state === "idle" && !view.hover && t - view.movedAt > SLEEP_AFTER;
  let flash = 0;
  if (view.state === "done") flash = 1 - span(k, 0, 0.6);

  ctx.clearRect(0, 0, SIZE, SIZE);
  drawCorona(t, Math.max(lv, flash));
  ctx.drawImage(DISK, 0, 0, SIZE, SIZE);
  drawWisps(t, (asleep ? 0.5 : 1) + lv * 1.5);
  if (view.state === "recording") drawRipples(t);
  ctx.drawImage(RING, 0, 0, SIZE, SIZE);
  drawBead(t, view.state === "processing");

  let fps = 8;  // покой: отражения медленные, хватает 8 кадров
  if (view.state === "recording") {
    drawDrop(t, lv);
    fps = 30;
  } else if (view.state === "speaking") {
    drawEyes(t, "happy", 1, lv * 3);
    fps = 30;
  } else if (view.state === "processing") {
    drawEyes(t, "busy");
    fps = 30;
  } else if (view.state === "done") {
    drawEyes(t, "closed");
    if (k < 1.1 && view.label) drawLabel(view.label, 1 - span(k, 0.85, 1.1));
    if (k > 1.15) setState("idle", {});
    fps = 24;
  } else if (view.state === "error") {
    drawEyes(t, "error");
    if (k > 0.9) { const title = canvas.title; setState("idle", {}); canvas.title = title; }
    fps = 24;
  } else if (view.blink) {
    drawEyes(t, "surprised", blinkOpen(t));
    fps = 20;
  } else if (asleep) {
    drawEyes(t, "sleep");
    fps = 6;
  } else {
    const open = blinkOpen(t);
    drawEyes(t, view.hover ? "happy" : "normal", open);
    if (moving || open < 1 || t > view.nextBlink - 0.05) fps = 24;
  }
  if (view.wake && !voice) drawWakeDot(t);
  return fps;
}

// ----------------------------------------
// Короткая подпись под глазами («скопировано»)
// ----------------------------------------
function drawLabel(text, alpha) {
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = "#ffd9a8";
  ctx.font = "9px 'JetBrains Mono', Consolas, monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, C, C + 18);
  ctx.restore();
}

// ----------------------------------------
// Цикл кадров по таймеру, а не requestAnimationFrame: частота — по тому, что сейчас на экране
// ----------------------------------------
function loop() {
  const fps = draw();
  setTimeout(loop, 1000 / fps);
}

// ----------------------------------------
// Чёткость на экранах с масштабом: canvas в физических пикселях
// ----------------------------------------
function setupCanvas() {
  canvas.width = SIZE * RATIO;
  canvas.height = SIZE * RATIO;
  ctx.setTransform(RATIO, 0, 0, RATIO, 0, 0);
}

// ======== мышь: клик, двойной клик, перетаскивание, правое меню ========

let press = null;        // {x, y, id, moved}
let clickTimer = null;

canvas.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  if (!menu.hidden) { closeMenu(); return; }
  press = { x: e.screenX, y: e.screenY, moved: false, pending: false };
  canvas.setPointerCapture(e.pointerId);
});

canvas.addEventListener("pointermove", (e) => {
  view.hover = [e.offsetX, e.offsetY];
  view.movedAt = now();
  if (!press) return;
  const dx = e.screenX - press.x, dy = e.screenY - press.y;
  if (!press.moved && Math.hypot(dx, dy) < 4) return;
  const start = !press.moved;
  press.moved = true;
  if (press.pending && !start) { press.next = [dx, dy]; return; }
  press.pending = true;
  api().drag(dx, dy, start).then(() => {
    if (!press) return;
    press.pending = false;
    if (press.next) { const [nx, ny] = press.next; press.next = null; press.pending = true;
      api().drag(nx, ny, false).then(() => { if (press) press.pending = false; }); }
  });
});

canvas.addEventListener("pointerleave", () => { view.hover = null; });

canvas.addEventListener("pointerup", (e) => {
  if (!press) return;
  const moved = press.moved;
  if (moved) {
    const dx = e.screenX - press.x, dy = e.screenY - press.y;
    api().drag(dx, dy, false).then(() => api().drag_end());
  }
  press = null;
  if (moved) return;
  if (clickTimer) {                // второй клик — двойной: открыть или спрятать чат
    clearTimeout(clickTimer);
    clickTimer = null;
    api().toggle_chat();
    return;
  }
  clickTimer = setTimeout(() => { clickTimer = null; api().toggle_record(); }, CLICK_DELAY);
});

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  openMenu();
});

// ----------------------------------------
// Правое меню: виджет меряет его, Python растит окно вокруг неподвижной головы — меню справа сверху
// ----------------------------------------
const MENU_PAD = 12;  // запас вокруг меню под тень

function openMenu() {
  menu.style.visibility = "hidden";   // меряем меню, пока оно невидимо
  menu.hidden = false;
  const width = Math.ceil(menu.offsetWidth) + MENU_PAD * 2, height = Math.ceil(menu.offsetHeight) + MENU_PAD * 2;
  menu.hidden = true;
  menu.style.visibility = "";
  api().menu(true, width, height).then((layout) => {
    if (!layout || !layout.menu) return;
    place(canvas, layout.ring);
    place(menu, [layout.menu[0] + MENU_PAD, layout.menu[1] + MENU_PAD]);
    menu.hidden = false;
  });
}

// ----------------------------------------
// Ставит элемент в точку окна [left, top]
// ----------------------------------------
function place(node, [left, top]) {
  node.style.left = left + "px";
  node.style.top = top + "px";
}

// ----------------------------------------
// Окно виджета не берёт фокус (не отнимает его у вашего окна), поэтому Esc и клик мимо до него не доходят:
// меню закрывается само, когда курсор уходит из виджета
// ----------------------------------------
let leaveTimer = null;
document.documentElement.addEventListener("mouseleave", () => {
  if (menu.hidden) return;
  clearTimeout(leaveTimer);
  leaveTimer = setTimeout(closeMenu, 350);
});
document.documentElement.addEventListener("mouseenter", () => clearTimeout(leaveTimer));

// ----------------------------------------
// Закрывает меню и возвращает окну размер кольца
// ----------------------------------------
function closeMenu() {
  clearTimeout(leaveTimer);
  if (menu.hidden) return;
  menu.hidden = true;
  api().menu(false).then((layout) => place(canvas, (layout && layout.ring) || [0, 0]));
}

menu.addEventListener("click", (e) => {
  const button = e.target.closest("button");
  if (!button) return;
  const action = button.dataset.action;
  closeMenu();
  if (action === "open_chat") api().open_chat();
  else if (action === "send_last") api().send_last_to_chat();
  else if (action === "wake") api().set_wake(!view.wake).then((r) => { if (r && r.ok) window.onState({ wake: r.wake }); });
  else if (action === "quit") api().quit();
});

document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) closeMenu(); });  // если фокус всё же есть
window.addEventListener("blur", () => { if (!menu.hidden) closeMenu(); });
document.addEventListener("pointerdown", (e) => { if (!menu.hidden && !menu.contains(e.target) && e.target !== canvas) closeMenu(); });

// ----------------------------------------
// Старт: canvas, состояние из Python, цикл кадров
// ----------------------------------------
window.addEventListener("pywebviewready", () => {
  api().ready().then((state) => {
    if (!state) return;
    if (state.ring) place(canvas, state.ring);  // на Qt голова в середине большого прозрачного окна
    window.onState(state);
  });
});
setupCanvas();
setState("idle", {});
loop();
