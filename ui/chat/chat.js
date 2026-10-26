// Окно чата: лента агента по событиям ядра (window.onEvent), вызовы Python — window.pywebview.api.

"use strict";

const FOLD_LINES = 20;          // блоки кода длиннее — свёрнуты
const STATUS_EVERY = 1000;      // строка статуса обновляется раз в секунду
const GIT_EVERY = 3000;         // счётчик изменений git — раз в 3 секунды
const FONT_DEFAULT = 14;
const IMAGE_FILE = /\.(png|jpe?g|webp|gif|svg|bmp|ico)$/i;  // открываются в редакторе как картинка

const $ = (sel, root = document) => root.querySelector(sel);
const feeds = new Map();        // id сессии → Feed
const app = {
  session: "",                  // активная сессия
  spaces: [],
  sessions: [],
  skills: [],
  tools: [],
  memory: { facts: [], path: "", limit: 0, size: 0 },
  trash: [],
  commands: [],
  status: null,
  ui: { font_size: FONT_DEFAULT, theme: "dark", sidebar: true },
};
const confirmQueue = [];        // ждущие подтверждения: {session, id, type, text, data}

// ----------------------------------------
// API Python (появляется после pywebviewready)
// ----------------------------------------
function api() {
  return window.pywebview.api;
}

// ----------------------------------------
// Элемент с классом и текстом
// ----------------------------------------
function el(tag, cls = "", text = "") {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text) node.textContent = text;
  return node;
}

// ----------------------------------------
// Русское число: 1 шаг, 2 шага, 5 шагов
// ----------------------------------------
function plural(n, one, few, many) {
  const d = n % 10, h = n % 100;
  if (d === 1 && h !== 11) return `${n} ${one}`;
  if (d >= 2 && d <= 4 && (h < 12 || h > 14)) return `${n} ${few}`;
  return `${n} ${many}`;
}

// ----------------------------------------
// 12400 → 12.4k
// ----------------------------------------
function short(n) {
  if (n >= 10000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n || 0);
}

// ----------------------------------------
// Секунды → 00:12:03
// ----------------------------------------
function clock(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
}

// ----------------------------------------
// Русское название инструмента или скилла («Чтение файла», «Статус git»); неизвестные — как есть
// ----------------------------------------
function toolTitle(name) {
  const item = app.tools.find((t) => t.name === name) || app.skills.find((s) => s.name === name);
  return item && item.title ? item.title : name;
}

// ----------------------------------------
// Значок инструмента для строки шага
// ----------------------------------------
function toolIcon(name) {
  const icons = { read_file: "▤", write_file: "✎", edit_file: "✎", list_dir: "▦", search: "⌕",
                  delete: "✕", run: "❯", browser: "◍", remember: "✦", new_skill: "✚" };
  return icons[name] || "⚙";
}

// ----------------------------------------
// Короткие аргументы: путь, команда, адрес или первые значения
// ----------------------------------------
function shortArgs(args) {
  if (!args || typeof args !== "object") return "";
  for (const key of ["path", "cmd", "url", "pattern", "fact"]) {
    if (typeof args[key] === "string" && args[key]) {
      const extra = key === "path" && args.start ? `:${args.start}${args.end ? "-" + args.end : ""}` : "";
      return (args[key] + extra).replace(/\s+/g, " ").slice(0, 90);
    }
  }
  const text = Object.values(args).map((v) => (typeof v === "string" ? v : JSON.stringify(v))).join(" ");
  return text.replace(/\s+/g, " ").slice(0, 90);
}

// ======== Markdown ========

marked.setOptions({ gfm: true, breaks: true });
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener"); }
});

// ----------------------------------------
// Markdown → безопасный HTML с подсветкой, кнопкой «копировать» и сворачиванием длинного кода
// ----------------------------------------
function renderMarkdown(target, text) {
  target.innerHTML = DOMPurify.sanitize(marked.parse(text || ""));
  target.querySelectorAll("pre > code").forEach((code) => decorateCode(code));
}

// ----------------------------------------
// Картинки в отрисованном Markdown: относительный путь (от папки файла base) грузит load(path) → data URL,
// потому что окно отдаёт только ui/; щелчок — крупно. Внешние (http, data) остаются как есть
// ----------------------------------------
function showImages(target, base, load) {
  target.querySelectorAll("img").forEach((img) => {
    img.classList.add("md-image");
    img.onclick = () => { if (img.src) showImage(img.src); };
    const src = img.getAttribute("src") || "";
    if (!src || /^(data|https?|blob):/i.test(src)) return;
    const path = joinPath(base, decodeURIComponent(src.split(/[?#]/)[0]));
    img.removeAttribute("src");
    img.title = path;
    load(path).then((url) => { if (url) img.src = url; else img.alt = `нет картинки ${path}`; });
  });
}

// ----------------------------------------
// Путь rel от папки base: «..» поднимается, «.» пропускается
// ----------------------------------------
function joinPath(base, rel) {
  const parts = base ? base.split("/") : [];
  for (const part of rel.split("/")) {
    if (part === "..") parts.pop();
    else if (part && part !== ".") parts.push(part);
  }
  return parts.join("/");
}

// ----------------------------------------
// Блок кода: рамка с языком на линии, «копировать», подсветка, «… ещё N строк»
// ----------------------------------------
function decorateCode(code, lang = "") {
  const pre = code.parentElement;
  const box = el("div", "code");
  pre.replaceWith(box);
  box.append(pre);
  lang = lang || (code.className.match(/language-(\S+)/) || [])[1] || "";
  if (lang) box.append(el("span", "lang", lang));
  const copy = el("button", "copy", "копировать");
  copy.onclick = () => copyText(code.textContent, copy);
  box.append(copy);
  if (lang && hljs.getLanguage(lang)) hljs.highlightElement(code);
  else if (!lang && code.textContent.length < 20000) hljs.highlightElement(code);
  const lines = code.textContent.replace(/\n$/, "").split("\n").length;
  if (lines > FOLD_LINES) {
    box.classList.add("folded");
    const more = el("button", "more", `… ещё ${lines - FOLD_LINES} строк`);
    more.onclick = () => {
      const folded = box.classList.toggle("folded");
      more.textContent = folded ? `… ещё ${lines - FOLD_LINES} строк` : "свернуть";
    };
    box.append(more);
  }
  return box;
}

// ----------------------------------------
// Блок кода из обычного текста (для панели скилла и подтверждения нового скилла)
// ----------------------------------------
function codeBlock(text, lang) {
  const holder = el("div");
  const pre = el("pre");
  const code = el("code", lang ? `language-${lang}` : "", text);
  pre.append(code);
  holder.append(pre);
  return decorateCode(code, lang);
}

// ----------------------------------------
// Копирование в буфер: браузерный буфер, при отказе — через Python
// ----------------------------------------
function copyText(text, button) {
  const done = () => { if (button) { button.textContent = "скопировано"; setTimeout(() => (button.textContent = "копировать"), 1200); } };
  const fallback = () => api().copy(text).then((r) => r && r.ok && done());
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fallback);
  else fallback();
}

// ======== лента ========

// ----------------------------------------
// Лента одной сессии: свой контейнер, своя автопрокрутка и история ввода
// ----------------------------------------
class Feed {

  // ----------------------------------------
  // Контейнер в #feeds; автопрокрутка, пока пользователь не ушёл вверх
  // ----------------------------------------
  constructor(id) {
    this.id = id;
    this.el = el("div", "feed");
    this.el.hidden = true;
    this.turn = null;
    this.sent = [];
    this.stick = true;
    this.el.addEventListener("scroll", () => {
      this.stick = this.el.scrollHeight - this.el.scrollTop - this.el.clientHeight < 40;
    });
    $("#feeds").append(this.el);
  }

  // ----------------------------------------
  // Добавляет узел в конец ленты
  // ----------------------------------------
  append(node) {
    this.el.append(node);
    this.scroll();
    return node;
  }

  // ----------------------------------------
  // Прокрутка вниз, если пользователь не прокрутил вверх сам
  // ----------------------------------------
  scroll() {
    if (this.stick) this.el.scrollTop = this.el.scrollHeight;
  }

  // ----------------------------------------
  // Новое сообщение пользователя — новый ход агента
  // ----------------------------------------
  startTurn(text, started, images = []) {
    if (this.turn && this.turn.running) this.turn.finish(null);
    this.append(el("div", "user-line", text));
    if (images.length) this.append(imageStrip(images));
    if (!text.startsWith("/") && this.sent[this.sent.length - 1] !== text) this.sent.push(text);
    this.reload = /^\/(продолжить|новый)(\s|$)/.test(text);  // журнал сессии сменился — лента пересоберётся из него
    this.turn = new Turn(this, started);
  }

  // ----------------------------------------
  // Текущий ход; события без сообщения пользователя (например, /продолжить) тоже получают ход
  // ----------------------------------------
  current() {
    if (!this.turn || !this.turn.running) this.turn = new Turn(this, Date.now());
    return this.turn;
  }

  // ----------------------------------------
  // Живое событие ядра
  // ----------------------------------------
  onEvent(ev) {
    const d = ev.data || {};
    // Пока карточка «нужен скилл» или подтверждение ждут ответа, время «Думал» не идёт
    if (this.turn && ev.type !== "status" && ev.type !== "done") {
      if (["need_skill", "confirm_request", "continue_request"].includes(ev.type)) this.turn.pause();
      else this.turn.resume();
    }
    switch (ev.type) {
      case "user": this.startTurn(ev.text, Date.now(), d.images || []); break;
      case "step": this.current().step(d.name, d.args); break;
      case "tool_result": this.current().result(ev.text, d.error); loadChanges(); break;
      case "thinking": this.current().thinking(ev.text); break;
      case "token": this.current().token(ev.text, d.reset); break;
      case "answer": this.current().answer(ev.text); break;
      case "need_skill": this.current().card(ev); break;
      case "continue_request": this.current().card(ev, false, ["↻ ", "продолжить", "стоп"]); break;
      case "confirm_request": this.current(); enqueueConfirm(this.id, ev); break;
      case "error": this.current().error(ev.text); break;
      case "warning": this.current().note(ev.text, "warning-line"); break;
      case "info": this.current().note(ev.text, "muted-line"); break;
      case "status": if (d.state === "working" && this.turn) this.turn.usage(d); break;
      case "done":
        if (this.turn) this.turn.finish(d);
        dropConfirms(this.id);
        refreshState();
        if (this.reload) { this.reload = false; api().history(this.id).then((records) => this.renderHistory(records || [])); }
        break;
    }
  }

  // ----------------------------------------
  // Лента из журнала ядра (logs/*.jsonl) — своей истории у UI нет
  // ----------------------------------------
  renderHistory(records) {
    this.el.textContent = "";
    this.turn = null;
    for (const r of records) {
      const at = Date.parse(r.time) || Date.now();
      if (r.type === "user") this.startTurn(r.text, at, r.images || []);
      else if (r.type === "call") this.current().step(r.name, r.args);
      else if (r.type === "result") this.current().result(r.error ? `ОШИБКА: ${r.error}` : r.result, r.error);
      else if (r.type === "answer") this.current().answer(r.text, at);
      else if (r.type === "need_skill") this.current().card({ text: `Нужен скилл ${r.name}: ${r.what}\nПакеты: ${(r.libs || []).join(", ") || "не нужны"}` }, true);
      else if (r.type === "note") this.current().note(r.text, "muted-line");
      else if (r.type === "end" && this.turn) {
        if (r.reason && r.reason !== "skill_declined") this.turn.note(r.reason === "max_steps" ? "шаги кончились — остановлено" : `задача прервана: ${r.reason}`, "muted-line");
        this.turn.finish(null, at);
      }
    }
    if (this.turn && this.turn.running) this.turn.finish(null);
    this.stick = true;
    this.scroll();
  }
}

// ----------------------------------------
// Один ход агента: сворачиваемый блок шагов, ответ под ним, строка с итогами
// ----------------------------------------
class Turn {

  // ----------------------------------------
  // Блок сразу раскрыт и показывает спиннер, пока агент работает
  // ----------------------------------------
  constructor(feed, started) {
    this.feed = feed;
    this.started = started;
    this.running = true;
    this.steps = 0;
    this.input = 0;
    this.output = 0;
    this.blocks = [];
    this.lastStep = null;
    this.thinkingEl = null;
    this.answerEl = null;
    this.draft = "";
    this.answered = false;
    this.waited = 0;          // мс, пока ждали ответа пользователя — не входят в «Думал»
    this.waitFrom = null;     // (до newBlock: он сразу рисует заголовок со временем)
    this.newBlock();
    this.timer = setInterval(() => this.updateHeads(), 1000);
  }

  // ----------------------------------------
  // Новый блок шагов (после карточки или ответа шаги продолжаются в новом блоке)
  // ----------------------------------------
  newBlock() {
    const block = { el: el("div", "work"), head: el("div", "work-head"), body: el("div", "work-body"), steps: 0 };
    block.el.append(block.head, block.body);
    block.head.onclick = () => { block.el.classList.toggle("collapsed"); this.updateHeads(); };
    this.blocks.push(block);
    this.feed.append(block.el);
    this.answerEl = null;
    this.updateHeads();
    return block;
  }

  // ----------------------------------------
  // Текущий блок; если после него в ленте уже что-то есть — новый
  // ----------------------------------------
  block() {
    const last = this.blocks[this.blocks.length - 1];
    return last && last.el === this.feed.el.lastElementChild ? last : this.newBlock();
  }

  // ----------------------------------------
  // Заголовки блоков: «⠋ Работаю 5 с · шаг 2» или «▸ Думал 12 с · 4 шага»
  // ----------------------------------------
  updateHeads(endAt) {
    const sec = Math.max(0, Math.round(this.busyMs(endAt || this.ended || Date.now()) / 1000));
    this.blocks.forEach((b, i) => {
      const last = i === this.blocks.length - 1;
      b.head.textContent = "";
      if (this.running && last) {
        b.head.append(el("span", "spinner"), ` Работаю ${sec} с` + (this.steps ? ` · шаг ${this.steps}` : ""));
        return;
      }
      const arrow = b.el.classList.contains("collapsed") ? "▸" : "▾";
      const steps = b.steps ? ` · ${plural(b.steps, "шаг", "шага", "шагов")}` : "";
      b.head.textContent = `${arrow} Думал ${sec} с${steps}`;
    });
  }

  // ----------------------------------------
  // Ждём ответа пользователя: время на паузе
  // ----------------------------------------
  pause() {
    if (this.waitFrom === null) this.waitFrom = Date.now();
  }

  // ----------------------------------------
  // Пользователь ответил (пришло следующее событие): время снова идёт
  // ----------------------------------------
  resume() {
    if (this.waitFrom !== null) { this.waited += Date.now() - this.waitFrom; this.waitFrom = null; }
  }

  // ----------------------------------------
  // Сколько агент работал к моменту end, без ожидания пользователя
  // ----------------------------------------
  busyMs(end) {
    const waiting = this.waitFrom !== null ? end - this.waitFrom : 0;
    return Math.max(0, end - this.started - this.waited - waiting);
  }

  // ----------------------------------------
  // Шаг: иконка, имя, короткие аргументы, статус; клик раскрывает вывод
  // ----------------------------------------
  step(name, args) {
    const block = this.block();
    this.closeThinking();
    const row = el("div", "step");
    const mark = el("span", "mark spinner");
    const label = el("span", "name", toolTitle(name));
    label.title = name;
    row.append(el("span", "icon", toolIcon(name)), label, " ", el("span", "args", shortArgs(args)), " ", mark);
    row.title = JSON.stringify(args || {}, null, 2).slice(0, 2000);
    const output = el("div", "step-output");
    output.hidden = true;
    row.onclick = () => { output.hidden = !output.hidden; };
    block.body.append(row, output);
    this.lastStep = { row, mark, output };
    this.steps += 1;
    block.steps += 1;
    this.updateHeads();
    this.feed.scroll();
  }

  // ----------------------------------------
  // Результат шага: ✓ или ✗ и вывод под строкой (свёрнут)
  // ----------------------------------------
  result(text, error) {
    if (!this.lastStep) this.step("?", {});
    const { mark, output } = this.lastStep;
    mark.className = error ? "mark fail" : "mark ok";
    mark.textContent = error ? "✗" : "✓";
    output.textContent = text || "(нет вывода)";
    if (error) { output.classList.add("error"); output.hidden = false; }
    this.lastStep = null;
    this.feed.scroll();
  }

  // ----------------------------------------
  // Ход мыслей модели — приглушённым курсивом, свёрнут под «▸ размышления»
  // ----------------------------------------
  thinking(text) {
    if (!this.thinkingEl) {  // свёрнуто: «▸ размышления», щелчок раскрывает текст
      const box = el("div", "thinking-box collapsed");
      const head = el("button", "thinking-head", "▸ размышления");
      head.onclick = () => {
        const closed = box.classList.toggle("collapsed");
        head.textContent = `${closed ? "▸" : "▾"} размышления`;
        this.feed.scroll();
      };
      this.thinkingEl = el("div", "thinking");
      box.append(head, this.thinkingEl);
      this.block().body.append(box);
    }
    this.thinkingEl.textContent += text;
    this.feed.scroll();
  }

  // ----------------------------------------
  // Следующая порция мыслей начнётся отдельной строкой
  // ----------------------------------------
  closeThinking() {
    this.thinkingEl = null;
  }

  // ----------------------------------------
  // Кусок потокового ответа; reset — модель начала заново
  // ----------------------------------------
  token(text, reset) {
    if (reset) { this.draft = ""; if (this.answerEl) this.answerEl.textContent = ""; return; }
    this.draft += text || "";
    if (!this.answerEl) this.answerEl = this.feed.append(el("div", "answer draft"));
    renderMarkdown(this.answerEl, this.draft);
    this.feed.scroll();
  }

  // ----------------------------------------
  // Итоговый ответ: Markdown, блоки шагов сворачиваются
  // ----------------------------------------
  answer(text, at) {
    this.closeThinking();
    if (!this.answerEl) this.answerEl = this.feed.append(el("div", "answer"));
    this.answerEl.classList.remove("draft");
    renderMarkdown(this.answerEl, text);
    this.draft = "";
    this.answered = true;
    this.answerAt = at || Date.now();
    this.blocks.forEach((b) => b.el.classList.add("collapsed"));
    this.feed.scroll();
  }

  // ----------------------------------------
  // Карточка с вопросом и двумя кнопками: need_skill — «создать» / «отмена», continue_request — «продолжить» / «стоп»
  // ----------------------------------------
  card(ev, past = false, [icon, yesText, noText] = ["✚ ", "создать", "отмена"]) {
    const card = el("div", "card");
    const [title, ...rest] = (ev.text || "").split("\n");
    card.append(el("div", "title", icon + title), el("div", "", rest.join("\n")));
    const buttons = el("div", "buttons");
    const yes = el("button", "primary", yesText), no = el("button", "", noText);
    buttons.append(yes, no);
    card.append(buttons);
    const answer = (value) => {
      yes.disabled = no.disabled = true;
      (value === "yes" ? yes : no).textContent += " ✓";
      api().confirm(ev.id, value);
    };
    yes.onclick = () => answer("yes");
    no.onclick = () => answer("no");
    if (past) yes.disabled = no.disabled = true;
    else this.pendingCard = { yes, no };
    this.feed.append(card);
    this.answerEl = null;
  }

  // ----------------------------------------
  // Ошибка — красной строкой в блоке шагов
  // ----------------------------------------
  error(text) {
    this.block().body.append(el("div", "error-line", "✗ " + text));
    this.feed.scroll();
  }

  // ----------------------------------------
  // info / warning: в блок, если там уже есть шаги, иначе — отдельной строкой
  // ----------------------------------------
  note(text, cls) {
    const block = this.blocks[this.blocks.length - 1];
    const line = el("div", cls, text);
    if (block && block.steps && block.el === this.feed.el.lastElementChild) block.body.append(line);
    else this.feed.append(line);
    this.feed.scroll();
  }

  // ----------------------------------------
  // Токены одного вызова модели (событие status во время работы)
  // ----------------------------------------
  usage(status) {
    this.input += status.prompt_tokens || 0;
    this.output += status.completion_tokens || 0;
  }

  // ----------------------------------------
  // Конец хода: спиннеры гаснут, пустые блоки убираются, под ответом — итоги
  // ----------------------------------------
  finish(status, at) {
    if (!this.running) return;
    this.running = false;
    this.resume();
    this.ended = at || this.answerAt || Date.now();
    clearInterval(this.timer);
    this.closeThinking();
    if (this.lastStep) { this.lastStep.mark.className = "mark"; this.lastStep.mark.textContent = "·"; }
    if (this.pendingCard) { this.pendingCard.yes.disabled = this.pendingCard.no.disabled = true; }
    if (this.answerEl && this.answerEl.classList.contains("draft")) this.answerEl.classList.remove("draft");
    for (const b of [...this.blocks]) {
      if (!b.body.childElementCount && !this.answered) { b.el.remove(); this.blocks.splice(this.blocks.indexOf(b), 1); }
    }
    this.updateHeads();
    if (this.answered) {
      const busy = this.busyMs(this.ended);
      const sec = (busy / 1000).toFixed(busy < 10000 ? 1 : 0);
      const parts = [plural(this.steps, "шаг", "шага", "шагов")];
      if (this.input || this.output) parts.push(`${short(this.input)} вход`, `${short(this.output)} выход`);
      parts.push(`${sec} с`);
      const meta = el("div", "meta", parts.join(" · "));
      if (this.answerEl && this.answerEl.nextSibling) this.answerEl.after(meta);
      else this.feed.append(meta);
    }
    this.feed.scroll();
  }
}

// ======== подтверждения ========

// ----------------------------------------
// confirm_request в очередь; показывается по одному
// ----------------------------------------
function enqueueConfirm(session, ev) {
  confirmQueue.push({ session, ...ev });
  if (confirmQueue.length === 1) showConfirm();
}

// ----------------------------------------
// Сессия закончилась — её подтверждения больше не нужны
// ----------------------------------------
function dropConfirms(session) {
  const showing = confirmQueue[0];
  for (let i = confirmQueue.length - 1; i >= 0; i--) if (confirmQueue[i].session === session) confirmQueue.splice(i, 1);
  if (showing && showing.session === session) showConfirm();
}

// ----------------------------------------
// Слой поверх ленты: действие, аргументы, дифф или код нового скилла
// ----------------------------------------
function showConfirm() {
  const box = $("#confirm");
  const ev = confirmQueue[0];
  if (!ev) { box.hidden = true; focusInput(); return; }
  const d = ev.data || {};
  const body = $("#confirm-body");
  body.textContent = "";
  const tab = app.sessions.find((s) => s.id === ev.session);
  $("#confirm-title").textContent = `подтверждение · ${d.name === "new_skill" ? "новый скилл " + d.skill : toolTitle(d.name)}` +
    (tab && ev.session !== app.session ? ` · ${tab.title}` : "");
  $("#confirm-session").hidden = d.name === "new_skill";
  if (d.name === "new_skill") {
    body.append(el("div", "args", `skills/${d.skill}/SKILL.md`), codeBlock(d.skill_md || "", "markdown"),
                el("div", "args", `skills/${d.skill}/run.py`), codeBlock(d.run_py || "", "python"),
                el("div", "args", `pip install: ${(d.libs || []).join(" ") || "не нужен"}`));
  } else if (d.diff) {
    const shown = Object.fromEntries(Object.entries(d.args || {}).filter(([k]) => !["content", "old", "new"].includes(k)));
    body.append(el("div", "args", JSON.stringify(shown)), diffBlock(d.diff));
  } else {
    body.append(el("pre", "args", d.args ? JSON.stringify(d.args, null, 2) : ev.text));
  }
  box.hidden = false;
  box.querySelector("button.primary").focus();
}

// ----------------------------------------
// Цветной unified diff
// ----------------------------------------
function diffBlock(text) {
  const pre = el("pre", "diff");
  for (const line of text.split("\n")) {
    let cls = "";
    if (line.startsWith("+++") || line.startsWith("---")) cls = "file";
    else if (line.startsWith("+")) cls = "add";
    else if (line.startsWith("-")) cls = "del";
    else if (line.startsWith("@@")) cls = "hunk";
    pre.append(el("span", cls, line), "\n");
  }
  return pre;
}

// ----------------------------------------
// Ответ на показанное подтверждение: yes | no | session
// ----------------------------------------
function answerConfirm(answer) {
  const ev = confirmQueue.shift();
  if (!ev) return;
  if (answer === "session" && ev.data && ev.data.name === "new_skill") answer = "yes";
  api().confirm(ev.id, answer);
  showConfirm();
}

$("#confirm").addEventListener("click", (e) => {
  const button = e.target.closest("button[data-answer]");
  if (button) answerConfirm(button.dataset.answer);
});

// ======== левая колонка, вкладки ========

// ----------------------------------------
// Перерисовка по состоянию из Python: пространства, вкладки, скиллы, ленты
// ----------------------------------------
function render(state) {
  if (!state) return;
  app.spaces = state.spaces;
  app.sessions = state.sessions;
  app.session = state.session;
  if (state.skills) app.skills = state.skills;
  if (state.tools) app.tools = state.tools;
  if (state.trash) app.trash = state.trash;
  if (state.memory) app.memory = state.memory;
  if (state.commands) app.commands = state.commands;
  renderSpaces();
  renderTabs();
  refreshSettings();
  loadTree();
  loadChanges();
  for (const s of app.sessions) ensureFeed(s.id);
  feeds.forEach((feed, id) => { if (feed.el.parentElement === $("#feeds")) feed.el.hidden = id !== app.session; });  // лента вики — в своей колонке
  const feed = feeds.get(app.session);
  if (feed) feed.scroll();
}

// ----------------------------------------
// Список пространств с точкой статуса
// ----------------------------------------
function renderSpaces() {
  const list = $("#spaces");
  list.textContent = "";
  for (const space of app.spaces) {
    const li = el("li", space.active ? "active" : "");
    const remove = el("button", "row-btn danger");
    remove.innerHTML = TRASH_ICON;
    remove.title = "убрать в корзину (папка на диске останется)";
    remove.onclick = (e) => { e.stopPropagation(); removeSpace(space.path); };
    li.append(el("span", `dot ${space.state}`, space.active ? "●" : "○"), el("span", "label", space.name), remove);
    li.title = `${space.path} · ${space.state}`;
    li.onclick = () => api().select_space(space.path).then(render);
    list.append(li);
  }
}

// ----------------------------------------
// Вкладки сессий на верхней линии рамки
// ----------------------------------------
function renderTabs() {
  const tabs = $("#tabs");
  tabs.textContent = "";
  for (const s of app.sessions) {
    const tab = el("span", "tab" + (s.active ? " active" : ""));
    tab.append(el("span", `dot ${s.busy ? "working" : s.state}`, "●"), el("span", "", s.title));
    if (!s.wiki) {  // агента вики не закрыть: раздел «Вики» держит его всегда
      const close = el("button", "close", "×");
      close.title = "закрыть сессию";
      close.onclick = (e) => { e.stopPropagation(); closeSession(s.id); };
      tab.append(close);
    }
    tab.onclick = () => api().select_session(s.id).then(render).then(focusInput);
    tabs.append(tab);
  }
  const wiki = app.sessions.some((s) => s.wiki);
  const add = el("span", "tab add", wiki ? "очистить" : "+");
  add.title = wiki ? "новый разговор с агентом вики — заметки остаются (ctrl+n)" : "новая сессия (ctrl+n)";
  add.onclick = newSession;
  tabs.append(add);
}

const TRASH_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round">' +
  '<path d="M2.5 4h11M6 4V2.5h4V4M4 4l.7 9.5h6.6L12 4M6.8 6.5v5M9.2 6.5v5"/></svg>';

// ----------------------------------------
// Пространство — в корзину; если нельзя (последнее или агент работает) — предупреждение в ленте
// ----------------------------------------
function removeSpace(path) {
  api().remove_space(path).then((r) => {
    if (r && r.error) feeds.get(app.session).append(el("div", "warning-line", r.error));
    render(r);
  });
}

// ======== окно настроек: подключение, инструменты, скиллы, память, корзина ========

const SETTINGS_TABS = [["connection", "подключение"], ["tools", "инструменты"], ["skills", "скиллы"],
                       ["memory", "память"], ["trash", "корзина"]];
const settings = { tab: "connection", pick: { tools: "", skills: "" } };  // вкладка и выбранный пункт списка

// ----------------------------------------
// Открывает окно настроек на вкладке tab (по умолчанию — последней открытой)
// ----------------------------------------
function openSettings(tab) {
  if (tab) settings.tab = tab;
  $("#settings").hidden = false;
  renderSettings();
}

// ----------------------------------------
// Закрывает окно настроек
// ----------------------------------------
function closeSettings() {
  $("#settings").hidden = true;
  focusInput();
}

// ----------------------------------------
// Переключает вкладку (запоминается)
// ----------------------------------------
function setSettingsTab(tab) {
  settings.tab = tab;
  api().save_ui({ settings_tab: tab });
  renderSettings();
}

// ----------------------------------------
// Полная перерисовка: вкладки и содержимое текущей
// ----------------------------------------
function renderSettings() {
  if ($("#settings").hidden) return;
  renderSettingsTabs();
  const body = $("#settings-body");
  body.textContent = "";
  body.className = `settings-${settings.tab}`;
  const tabs = { connection: renderConnection, tools: renderToolsTab, skills: renderSkillsTab,
                 memory: renderMemoryTab, trash: renderTrashTab };
  tabs[settings.tab](body);
}

// ----------------------------------------
// Свежее состояние из Python: только счётчики на вкладках и корзина — форму и правки памяти не трогаем
// ----------------------------------------
function refreshSettings() {
  if ($("#settings").hidden) return;
  renderSettingsTabs();
  if (settings.tab === "trash") renderSettings();
}

// ----------------------------------------
// Строка вкладок со счётчиками
// ----------------------------------------
function renderSettingsTabs() {
  const nav = $("#settings-tabs");
  nav.textContent = "";
  const counts = { tools: app.tools.length, skills: app.skills.length, memory: app.memory.facts.length,
                   trash: app.trash.length };
  for (const [id, title] of SETTINGS_TABS) {
    const tab = el("button", "stab" + (id === settings.tab ? " active" : ""), title);
    if (id in counts) tab.append(el("span", "count", ` ${counts[id]}`));
    tab.onclick = () => setSettingsTab(id);
    nav.append(tab);
  }
}

// ----------------------------------------
// Вкладка «Подключение»: адрес Ollama, модель из `ollama list`, размышления; пишется в config.toml
// ----------------------------------------
function renderConnection(body) {
  api().connection().then((conn) => {
    const form = el("div", "conn");
    const field = (label, input, hint = "") => {
      const row = el("label", "conn-row");
      row.append(el("span", "conn-label", label), input);
      if (hint) row.append(el("span", "conn-hint", hint));
      form.append(row);
      return input;
    };
    const input = (value) => { const i = el("input", "conn-input"); i.value = value; i.spellcheck = false; return i; };
    const select = (value) => {
      const box = el("select", "conn-input");
      for (const effort of conn.efforts) box.append(new Option(effort, effort, false, effort === value));
      return box;
    };
    const url = field("адрес Ollama", input(conn.base_url), "OpenAI-совместимый, обычно http://localhost:11434/v1");
    const model = field("модель", input(conn.model), "список — из ollama list; можно вписать свою");
    const models = el("datalist");
    models.id = "conn-models";
    model.setAttribute("list", models.id);
    form.append(models);
    const effort = field("размышления", select(conn.reasoning_effort), "none — быстрее, но хуже понимает задачу");
    const skillEffort = field("размышления для скилла", select(conn.skill_reasoning_effort), "когда агент пишет новый скилл");
    const state = el("div", "conn-state muted", "проверяю…");
    let found = null;  // модели последней проверки; null — Ollama не ответила
    const showState = () => {
      if (!found) return;
      const known = found.includes(model.value.trim());
      state.className = "conn-state " + (known ? "ok" : "warn");
      state.textContent = `● подключено · моделей: ${found.length}` + (known ? "" : ` · «${model.value.trim()}» в Ollama нет`);
    };
    const check = () => {
      state.className = "conn-state muted";
      state.textContent = "проверяю…";
      api().check_connection(url.value).then((r) => {
        models.textContent = "";
        found = r.ok ? r.models : null;
        if (!r.ok) { state.className = "conn-state error"; state.textContent = `● ${r.error}`; return; }
        for (const name of r.models) models.append(new Option(name, name));
        showState();
      });
    };
    url.onchange = check;
    model.oninput = showState;
    const save = el("button", "primary", "сохранить"), test = el("button", "", "проверить");
    test.onclick = check;
    save.onclick = () => {
      api().save_connection({ base_url: url.value, model: model.value, reasoning_effort: effort.value,
                              skill_reasoning_effort: skillEffort.value }).then((r) => {
        note.className = "meta " + (r.ok ? "saved" : "over");
        note.textContent = r.ok ? "сохранено в config.toml" : r.error;
        if (r.ok) { pollStatus(); check(); }
      });
    };
    const note = el("span", "meta", "действует для всех сессий со следующего шага");
    const buttons = el("div", "buttons");
    buttons.append(save, test, note);
    body.append(form, state, buttons);
    check();
  });
}

// ----------------------------------------
// Список слева, карточка выбранного справа (инструменты, скиллы)
// ----------------------------------------
function masterDetail(body, kind, items, label, show) {
  if (!items.length) { body.append(el("div", "muted", "пусто")); return; }
  const list = el("ul", "list settings-list"), detail = el("div", "settings-detail");
  if (!items.some((item) => item.name === settings.pick[kind])) settings.pick[kind] = items[0].name;
  for (const item of items) {
    const li = el("li", item.name === settings.pick[kind] ? "active" : "");
    label(li, item);
    li.title = `${item.name} — ${item.description}`;
    li.onclick = () => {
      settings.pick[kind] = item.name;
      list.querySelectorAll("li").forEach((row) => row.classList.toggle("active", row === li));
      show(item, detail);
    };
    list.append(li);
  }
  body.append(list, detail);
  show(items.find((item) => item.name === settings.pick[kind]), detail);
}

// ----------------------------------------
// Вкладка «Инструменты»: оранжевый «!» — спрашивает подтверждение
// ----------------------------------------
function renderToolsTab(body) {
  masterDetail(body, "tools", app.tools, (li, tool) => {
    li.append(el("span", "label", tool.title));
    if (tool.confirm !== "нет") li.append(el("span", "confirm", "!"));
  }, showTool);
}

// ----------------------------------------
// Вкладка «Скиллы»
// ----------------------------------------
function renderSkillsTab(body) {
  masterDetail(body, "skills", app.skills, (li, skill) => li.append(el("span", "label", skill.title || skill.name)),
               (skill, detail) => showSkill(skill.name, detail));
}

// ----------------------------------------
// Вкладка «Корзина»: убранные пространства — вернуть или убрать насовсем (папка на диске остаётся)
// ----------------------------------------
function renderTrashTab(body) {
  body.append(el("div", "meta", "Убранные пространства. Папки на диске не тронуты."));
  const list = el("ul", "list trash-list");
  for (const item of app.trash) {
    const li = el("li");
    const back = el("button", "row-btn", "вернуть"), purge = el("button", "row-btn danger", "убрать насовсем");
    back.onclick = () => api().restore_space(item.path).then(render);
    purge.onclick = () => api().purge_space(item.path).then(render);
    li.append(el("span", "label", item.name), el("span", "label muted", item.path), back, purge);
    li.title = item.path;
    list.append(li);
  }
  if (!app.trash.length) list.append(el("li", "muted", "пусто"));
  body.append(list);
}

$("#settings-open").onclick = () => openSettings();
$("#settings-close").onclick = closeSettings;
$("#settings").addEventListener("mousedown", (e) => { if (e.target.id === "settings") closeSettings(); });  // клик мимо окна

// ----------------------------------------
// Строка пути вверху карточки: где лежит инструмент или скилл
// ----------------------------------------
function pathLine(path) {
  const line = el("div", "panel-path");
  line.append(el("span", "muted", "путь "), el("span", "", path));
  line.title = "относительно папки проекта";
  return line;
}

// ----------------------------------------
// Табличка «поле — значение» с полосой слева (name, description, args, libs, подтверждение)
// ----------------------------------------
function fieldsTable(rows) {
  const table = el("dl", "fields");
  for (const [key, value] of rows) table.append(el("dt", "", key), el("dd", "", value || "—"));
  return table;
}

// ----------------------------------------
// Полный код без сворачивания — для просмотра в панели
// ----------------------------------------
function fullCode(text, lang) {
  const box = codeBlock(text, lang);
  box.classList.remove("folded");
  const more = box.querySelector(".more");
  if (more) more.remove();
  return box;
}

// ----------------------------------------
// Вкладка «Память»: факт — строка, × удаляет, внизу новый факт; «сохранить» пишет memory/memory.md
// ----------------------------------------
function renderMemoryTab(body) {
  api().memory().then((memory) => {
    const list = el("div", "memory-list");
    const counter = el("span", "meta");
    const count = () => {
      const size = [...list.querySelectorAll("textarea")].reduce((sum, t) => sum + (t.value.trim() ? t.value.trim().length + 3 : 0), 0);
      counter.textContent = `${size} / ${memory.limit} символов`;
      counter.classList.toggle("over", size > memory.limit);
    };
    const addRow = (text, focus = false) => {
      const row = el("div", "memory-row");
      const area = el("textarea", "memory-text");
      area.rows = 1;
      area.value = text;
      area.placeholder = "факт о вас — одной строкой";
      area.oninput = () => { grow(area); count(); };
      const remove = el("button", "row-btn danger", "×");
      remove.title = "удалить факт";
      remove.onclick = () => { row.remove(); count(); };
      row.append(area, remove);
      list.append(row);
      grow(area);
      if (focus) area.focus();
    };
    const grow = (area) => { area.style.height = "auto"; area.style.height = area.scrollHeight + "px"; };
    memory.facts.forEach((fact) => addRow(fact));
    const add = el("button", "", "+ добавить факт");
    add.className = "memory-add";
    add.onclick = () => { addRow("", true); count(); };
    const save = el("button", "primary", "сохранить"), cancel = el("button", "", "отменить правки");
    save.onclick = () => {
      const facts = [...list.querySelectorAll("textarea")].map((t) => t.value);
      api().save_memory(facts).then((saved) => { app.memory = saved; renderSettings(); });
    };
    cancel.onclick = renderSettings;
    const buttons = el("div", "buttons");
    buttons.append(save, cancel, counter);
    body.append(pathLine(memory.path),
                el("div", "meta", "Что агент помнит о вас между сессиями — видит в каждой задаче. Один факт — одна строка."),
                list, add, buttons);
    count();
    body.querySelectorAll("textarea").forEach(grow);
  });
}

// ----------------------------------------
// Карточка инструмента: название, путь, поля, код функции
// ----------------------------------------
function showTool(tool, body) {
  body.textContent = "";
  body.append(el("div", "detail-title", tool.title), pathLine(tool.path),
              fieldsTable([["имя", tool.name], ["вызов", tool.signature], ["что делает", tool.description],
                           ["подтверждение", tool.confirm]]),
              fullCode(tool.source || "", "python"));
}

// ----------------------------------------
// Лента сессии; при первом появлении — из журнала
// ----------------------------------------
function ensureFeed(id) {
  if (feeds.has(id)) return feeds.get(id);
  const feed = new Feed(id);
  feeds.set(id, feed);
  api().history(id).then((records) => { if (records && records.length && !feed.turn) feed.renderHistory(records); });
  return feed;
}

// ----------------------------------------
// Свежие пространства, вкладки и скиллы (после ответа агента)
// ----------------------------------------
function refreshState() {
  api().state().then(render);
}

// ----------------------------------------
// Новая сессия в активном пространстве
// ----------------------------------------
function newSession() {
  api().new_session().then(render).then(focusInput);
}

// ----------------------------------------
// Закрывает вкладку; её лента удаляется
// ----------------------------------------
function closeSession(id) {
  api().close_session(id).then((state) => {
    const feed = feeds.get(id);
    if (feed) { feed.el.remove(); feeds.delete(id); }
    render(state);
  });
}

// ----------------------------------------
// Карточка скилла: название, путь, поля SKILL.md табличкой, описание, run.py — только чтение
// ----------------------------------------
function showSkill(name, body) {
  api().skill_source(name).then((src) => {
    if (settings.pick.skills !== name) return;  // пока грузилось, выбрали другой
    body.textContent = "";
    const fields = {}, rest = [];
    for (const line of (src.skill_md || "").split("\n")) {
      const m = line.match(/^(name|title|description|args|libs):\s*(.*)$/);
      if (m && !(m[1] in fields)) fields[m[1]] = m[2];
      else rest.push(line);
    }
    const md = el("div", "answer");
    renderMarkdown(md, rest.join("\n").trim());
    body.append(el("div", "detail-title", toolTitle(name)), pathLine(src.path || src.folder),
                fieldsTable([["название", fields.title], ["имя", fields.name], ["что делает", fields.description],
                             ["аргументы", fields.args], ["пакеты", fields.libs]]),
                md, el("div", "panel-file", "run.py"), fullCode(src.run_py || "", "python"));
  });
}

$("#add-space").onclick = () => api().open_space("").then((r) => r && r.ok && render(r));

// ======== файлы пространства: дерево в левой колонке ========

const tree = { root: "", open: new Set(), load: 0, renaming: false, click: 0 };  // корень (активное пространство), раскрытые папки,
                                                                              // № загрузки, идёт переименование, таймер клика

// ----------------------------------------
// Перерисовка дерева: корень и раскрытые папки читаются заново (агент мог добавить файлы)
// ----------------------------------------
async function loadTree() {
  if (tree.renaming) return;  // перерисовка снесла бы поле ввода; после переименования дерево перечитается
  const space = app.spaces.find((s) => s.active);
  const root = space ? space.path : "";
  if (root !== tree.root) { tree.root = root; tree.open.clear(); showSpaceTabs(); }
  const load = ++tree.load;
  const rows = await treeRows("", 0);
  if (load !== tree.load) return;  // пока читали, начали новую загрузку
  $("#files").replaceChildren(...rows);
}

// ----------------------------------------
// Строки одной папки и вложенных раскрытых папок
// ----------------------------------------
async function treeRows(path, depth) {
  const r = await api().files(path);
  if (!r || !r.ok) return [el("li", "muted", r ? r.error : "нет ответа")];
  const rows = [];
  for (const item of r.items) {
    const open = item.dir && tree.open.has(item.path);
    const shown = !$("#editor").hidden && files.active && files.active.path === item.path;
    const li = el("li", (item.dir ? "dir" : "file") + (shown ? " active" : ""));
    li.style.paddingLeft = `${4 + depth * 14}px`;
    li.dataset.path = item.path;
    const label = el("span", "label" + (item.name.startsWith(".") ? " muted" : ""), item.name);
    const remove = el("button", "row-btn danger");
    remove.innerHTML = TRASH_ICON;
    remove.title = "удалить (в .trash/ пространства)";
    remove.onclick = (e) => { e.stopPropagation(); askDelete(item); };
    li.append(el("span", "arrow", item.dir ? (open ? "▾" : "▸") : ""), label, remove);
    li.title = item.path;
    li.onclick = (e) => {  // одиночный клик ждёт: вдруг это начало двойного (переименование)
      if (e.detail > 1) return;
      clearTimeout(tree.click);
      tree.click = setTimeout(() => {
        if (!item.dir) { openFile(item.path); return; }
        if (open) tree.open.delete(item.path); else tree.open.add(item.path);
        loadTree();
      }, 250);
    };
    label.ondblclick = (e) => { e.stopPropagation(); clearTimeout(tree.click); startRename(item, label); };
    rows.push(li);
    if (open) rows.push(...await treeRows(item.path, depth + 1));
  }
  if (!r.items.length && depth) rows.push(Object.assign(el("li", "muted", "пусто"), { style: `padding-left:${18 + depth * 14}px` }));
  return rows;
}

$("#files-refresh").onclick = () => { loadTree(); loadChanges(); };

// ----------------------------------------
// Переименование по двойному клику: имя становится полем; Enter / уход фокуса — сохранить, Esc — отмена
// ----------------------------------------
function startRename(item, label) {
  tree.renaming = true;
  const field = el("input", "rename");
  field.value = item.name;
  label.replaceWith(field);
  field.focus();
  const dot = item.dir ? -1 : item.name.lastIndexOf(".");
  field.setSelectionRange(0, dot > 0 ? dot : item.name.length);  // имя без расширения
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const name = field.value.trim();
    if (save && name && name !== item.name) {
      const r = await api().rename_file(item.path, name);
      if (!r || !r.ok) ask("не переименовано", `${item.path}: ${r ? r.error : "нет ответа"}`, [["понятно", "primary"]]);
      else movePaths(item.path, r.path);
    }
    tree.renaming = false;
    loadTree();
    loadChanges();
  };
  field.onkeydown = (e) => {
    e.stopPropagation();  // Esc не должен останавливать агента, Enter — уходить в чат
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  };
  field.onblur = () => finish(true);
  field.onclick = field.ondblclick = (e) => e.stopPropagation();
}

// ----------------------------------------
// После переименования: пути открытых вкладок и раскрытых папок — на новое имя
// ----------------------------------------
function movePaths(from, to) {
  const moved = (path) => (path === from ? to : path.startsWith(from + "/") ? to + path.slice(from.length) : null);
  for (const tab of spaceTabs().filter((tab) => !tab.kind && moved(tab.path))) {
    tab.path = moved(tab.path);
    tab.lang = fileLang(tab.path);
    tab.md = /\.(md|markdown|mkd)$/i.test(tab.path);
  }
  tree.open = new Set([...tree.open].map((path) => moved(path) || path));
  const tab = files.active;
  if (tab && !tab.kind && tab.root === tree.root) {  // шапка открытой вкладки и подсветка — по новому имени
    $("#editor-path").textContent = tab.path;
    $("#editor-path").title = `${tab.root}/${tab.path}`;
    $("#editor-preview-toggle").hidden = !tab.md;
    if (!tab.md && tab.preview) setPreview(false);
    files.frame = requestAnimationFrame(paintCode);
  }
  renderFileTabs();
}

// ----------------------------------------
// Удаление из дерева после подтверждения: в .trash/ пространства; вкладки этого файла (папки) закрываются
// ----------------------------------------
function askDelete(item) {
  const what = item.dir ? `папку ${item.path} со всем содержимым` : item.path;
  ask("удаление", `Удалить ${what}?\nОно переместится в .trash/ пространства.`,
      [["удалить", "primary", () => deleteItem(item)], ["отмена", ""]]);
}

async function deleteItem(item) {
  const r = await api().delete_file(item.path);
  if (!r || !r.ok) { ask("не удалено", `${item.path}: ${r ? r.error : "нет ответа"}`, [["понятно", "primary"]]); return; }
  const inside = (path) => path === item.path || path.startsWith(item.path + "/");
  for (const tab of spaceTabs().filter((tab) => !tab.kind && inside(tab.path))) {
    tab.saved = tab === files.active ? editText.value : tab.text;  // файла больше нет — закрыть без вопроса о правках
    closeTab(tab);
  }
  for (const path of [...tree.open]) if (inside(path)) tree.open.delete(path);
  loadTree();
  loadChanges();
}

// ======== git: счётчик и дерево изменённых файлов активного пространства ========

const git = { open: false, repo: false, items: [], loading: false };  // дерево раскрыто; ответ git_changes; идёт запрос
const GIT_STATUS = { M: "изменён", A: "добавлен", D: "удалён", R: "переименован", U: "новый" };

// ----------------------------------------
// Запрашивает изменения; пока прошлый запрос не вернулся — новый не шлётся
// ----------------------------------------
async function loadChanges() {
  if (git.loading) return;
  git.loading = true;
  try {
    const r = await api().git_changes();
    if (r && JSON.stringify(r) !== JSON.stringify({ repo: git.repo, items: git.items })) {
      git.repo = r.repo;
      git.items = r.items;
      renderChanges();
    }
  } finally {
    git.loading = false;
  }
}

// ----------------------------------------
// Кружок со счётчиком на иконке и дерево изменений (если раскрыто)
// ----------------------------------------
function renderChanges() {
  const btn = $("#git-btn"), badge = btn.querySelector(".badge");
  if (!git.repo) git.open = false;
  btn.classList.toggle("off", !git.repo);
  btn.classList.toggle("active", git.open);
  btn.title = git.repo ? `изменения git: ${git.items.length}` : "не git-репозиторий";
  badge.textContent = git.items.length > 99 ? "99+" : String(git.items.length);
  badge.hidden = !git.repo || !git.items.length;
  const list = $("#changes");
  list.hidden = !git.open;
  if (!git.open) return;
  if (!git.items.length) { list.replaceChildren(el("li", "muted", "изменений нет")); return; }
  const rows = [], shown = new Set();
  for (const item of git.items) {
    const parts = item.path.split("/");
    for (let depth = 0; depth < parts.length - 1; depth++) {  // папки пути, каждая один раз
      const dir = parts.slice(0, depth + 1).join("/");
      if (shown.has(dir)) continue;
      shown.add(dir);
      const li = el("li", "dir");
      li.style.paddingLeft = `${4 + depth * 14}px`;
      li.append(el("span", "arrow", "▾"), el("span", "label", parts[depth]));
      li.title = dir;
      rows.push(li);
    }
    const li = el("li", `file st-${item.status}`);
    li.style.paddingLeft = `${4 + (parts.length - 1) * 14}px`;
    li.dataset.path = item.path;
    li.append(el("span", "arrow", ""), el("span", "label", parts[parts.length - 1]),
              el("span", `st st-${item.status}`, item.status));
    li.title = `${item.orig ? item.orig + " → " : ""}${item.path} · ${GIT_STATUS[item.status] || item.status}`;
    li.onclick = () => openDiff(item.path, item.orig);
    rows.push(li);
  }
  list.replaceChildren(...rows);
  markTreeFile();
}

$("#git-btn").onclick = () => {
  if (!git.repo) return;
  git.open = !git.open;
  renderChanges();
};

// ======== редактор файлов: панель справа от чата, вкладки — свои у каждого пространства ========

// вкладка: {root, path, text, saved, mtime, crlf, lang, md, preview, scroll}; active — открытая сейчас
const files = { tabs: [], active: null, lines: 0, frame: 0 };
const editText = $("#editor-text");

// ----------------------------------------
// Язык подсветки по имени файла; незнакомое — без подсветки
// ----------------------------------------
function fileLang(path) {
  const name = path.split("/").pop().toLowerCase();
  if (name === "makefile") return "makefile";
  const ext = name.includes(".") ? name.split(".").pop() : "";
  return ext && hljs.getLanguage(ext) ? ext : "plaintext";
}

// ----------------------------------------
// Вкладки активного пространства
// ----------------------------------------
function spaceTabs() {
  return files.tabs.filter((tab) => tab.root === tree.root);
}

// ----------------------------------------
// Есть ли у вкладки несохранённые правки (текст открытой — прямо из поля)
// ----------------------------------------
function tabDirty(tab) {
  if (tab.kind === "diff" || tab.kind === "image") return false;
  return (tab === files.active ? editText.value : tab.text) !== tab.saved;
}

// ----------------------------------------
// Клик по файлу в дереве: уже открыт — на его вкладку, нет — читается и открывается новой
// ----------------------------------------
function openFile(path) {
  const open = spaceTabs().find((tab) => tab.path === path && (!tab.kind || tab.kind === "image"));
  if (open) { activateTab(open); return; }
  if (IMAGE_FILE.test(path)) {  // картинка — вкладка-просмотр, не текст
    api().file_image(path, tree.root).then((url) => {
      if (!url) { ask("картинка не открыта", `${path}: не картинка или больше 10 МБ`, [["понятно", "primary"]]); return; }
      const tab = { root: tree.root, path, text: "", saved: "", kind: "image", url, lang: "plaintext", scroll: [0, 0] };
      files.tabs.push(tab);
      activateTab(tab);
    });
    return;
  }
  api().open_file(path, tree.root).then((r) => {
    if (!r.ok) { ask("файл не открыт", `${path}: ${r.error}`, [["понятно", "primary"]]); return; }
    const md = /\.(md|markdown|mkd)$/i.test(path);
    const tab = { root: r.root, path, text: r.text, saved: r.text, mtime: r.mtime, crlf: r.crlf, lang: fileLang(path),
                  md, preview: false, scroll: [0, 0] };
    files.tabs.push(tab);
    activateTab(tab);
  });
}

// ----------------------------------------
// Текст и прокрутка открытой вкладки — в её объект (перед переключением)
// ----------------------------------------
function stashTab() {
  const tab = files.active;
  if (!tab) return;
  if (tab.kind === "diff") { tab.scroll = [$("#diff-left").scrollTop, $("#diff-left").scrollLeft]; return; }
  if (tab.kind === "image") return;
  tab.text = editText.value;
  tab.scroll = [editText.scrollTop, editText.scrollLeft];
}

// ----------------------------------------
// Показывает вкладку в редакторе
// ----------------------------------------
function activateTab(tab) {
  if (files.active !== tab) stashTab();
  files.active = tab;
  files.lines = 0;
  editText.value = tab.text;
  $("#editor").hidden = false;
  $("#editor-path").textContent = tab.path;
  $("#editor-path").title = `${tab.root}/${tab.path}`;
  $("#editor-note").textContent = "";
  $("#editor-preview-toggle").hidden = !tab.md;
  const diff = tab.kind === "diff", image = tab.kind === "image";
  $("#editor-main").classList.toggle("diff", diff);
  $("#editor-main").classList.toggle("image", image);
  $("#editor-save").hidden = diff || image;
  if (image) {
    $("#editor-main").classList.add("preview");
    const img = el("img", "editor-image");
    img.src = tab.url;
    img.title = "крупно — щелчок";
    img.onclick = () => showImage(tab.url);
    $("#editor-preview").replaceChildren(img);
    $("#editor-note").textContent = "картинка — только просмотр";
  } else if (diff) {
    $("#editor-main").classList.remove("preview");
    $("#editor-note").textContent = "HEAD ↔ сейчас";
    renderDiff(tab);
    [$("#diff-left").scrollTop, $("#diff-left").scrollLeft] = tab.scroll;
  } else {
    setPreview(tab.preview);
    [editText.scrollTop, editText.scrollLeft] = tab.scroll;
  }
  renderFileTabs();
  markTreeFile();
  if (diff) $("#diff-left").focus();
  else if (!tab.preview) editText.focus();
}

// ----------------------------------------
// Вкладки файлов на верхней линии рамки редактора: ● — не сохранено, × — закрыть (средняя кнопка — тоже)
// ----------------------------------------
function renderFileTabs() {
  const bar = $("#file-tabs");
  bar.textContent = "";
  for (const tab of spaceTabs()) {
    const el_ = el("span", "tab" + (tab === files.active ? " active" : ""));
    el_.append(el("span", "", tab.path.split("/").pop() + (tab.kind === "diff" ? " ↔" : "")));
    if (tabDirty(tab)) el_.append(el("span", "dirty", "●"));
    const close = el("button", "close", "×");
    close.title = "закрыть (ctrl+w)";
    close.onclick = (e) => { e.stopPropagation(); closeTab(tab); };
    el_.append(close);
    el_.title = tab.path;
    el_.onclick = () => activateTab(tab);
    el_.onauxclick = (e) => { if (e.button === 1) closeTab(tab); };
    bar.append(el_);
  }
}

// ----------------------------------------
// Закрывает вкладку; правки есть — сохранить / не сохранять / отмена. Последняя — редактор прячется
// ----------------------------------------
function closeTab(tab) {
  const drop = () => {
    const tabs = spaceTabs(), index = tabs.indexOf(tab);
    files.tabs.splice(files.tabs.indexOf(tab), 1);
    if (files.active === tab) {
      files.active = null;
      const next = tabs[index + 1] || tabs[index - 1];
      if (next) activateTab(next);
      else hideEditor();
    } else renderFileTabs();
  };
  if (!tabDirty(tab)) { drop(); return; }
  if (files.active !== tab) activateTab(tab);
  ask("несохранённые правки", `Сохранить изменения в ${tab.path}?`,
      [["сохранить", "primary", () => saveFile().then((ok) => ok && drop())], ["не сохранять", "", drop],
       ["отмена", "", () => editText.focus()]]);
}

// ----------------------------------------
// Нет вкладок в этом пространстве — чат снова на всю ширину
// ----------------------------------------
function hideEditor() {
  $("#editor").hidden = true;
  markTreeFile();
  focusInput();
}

// ----------------------------------------
// Сменилось пространство: показать его вкладки (последнюю открытую) или спрятать редактор
// ----------------------------------------
function showSpaceTabs() {
  stashTab();
  const tabs = spaceTabs();
  files.active = null;
  if (tabs.length) activateTab(tabs[tabs.length - 1]);
  else $("#editor").hidden = true;
}

// ----------------------------------------
// В дереве подсвечен открытый файл
// ----------------------------------------
function markTreeFile() {
  const tab = !$("#editor").hidden && files.active, diff = tab && tab.kind === "diff";
  const path = tab && !diff ? tab.path : "", changed = diff ? tab.path : "";
  document.querySelectorAll("#files li").forEach((li) => li.classList.toggle("active", li.dataset.path === path));
  document.querySelectorAll("#changes li").forEach((li) => li.classList.toggle("active", li.dataset.path === changed));
}

// ----------------------------------------
// Клик по файлу в дереве изменений: вкладка сравнения; уже открыта — строки перечитываются (файл мог поменяться)
// ----------------------------------------
function openDiff(path, orig = "") {
  api().git_diff(path, orig).then((r) => {
    if (!r.ok) { ask("сравнение не открыто", `${path}: ${r.error}`, [["понятно", "primary"]]); return; }
    let tab = spaceTabs().find((t) => t.kind === "diff" && t.path === path);
    if (tab) tab.rows = r.rows;
    else {
      tab = { root: r.root, path, kind: "diff", rows: r.rows, text: "", saved: "", mtime: "", crlf: false,
              lang: fileLang(path), md: false, preview: false, scroll: [0, 0] };
      files.tabs.push(tab);
    }
    activateTab(tab);
  });
}

// ----------------------------------------
// Две колонки сравнения: слева HEAD (удалённое красным), справа файл сейчас (добавленное зелёным)
// ----------------------------------------
function renderDiff(tab) {
  const side = (no, text, kind) => {
    const line = el("div", "diff-line " + (no === null ? "empty" : kind));
    const tx = el("span", "tx");
    if (no !== null && tab.lang !== "plaintext") tx.innerHTML = hljs.highlight(text, { language: tab.lang, ignoreIllegals: true }).value;
    else tx.textContent = text;
    line.append(el("span", "no", no === null ? "" : String(no)), tx);
    return line;
  };
  const left = el("div", "diff-lines"), right = el("div", "diff-lines");
  for (const [lno, ltext, rno, rtext, kind] of tab.rows) {
    if (kind === "eol") {  // разница только в переводе строки в конце файла
      left.append(el("div", "diff-line eol" + (ltext ? " del" : ""), ltext || " "));
      right.append(el("div", "diff-line eol" + (rtext ? " add" : ""), rtext || " "));
      continue;
    }
    left.append(side(lno, ltext, kind === "same" ? "" : "del"));
    right.append(side(rno, rtext, kind === "same" ? "" : "add"));
  }
  if (!tab.rows.length) right.append(el("div", "diff-line muted", "  файлы одинаковые"));
  $("#diff-left").replaceChildren(left);
  $("#diff-right").replaceChildren(right);
}

// ----------------------------------------
// Прокрутка одной колонки сравнения тянет другую
// ----------------------------------------
for (const [from, to] of [["#diff-left", "#diff-right"], ["#diff-right", "#diff-left"]]) {
  $(from).addEventListener("scroll", () => {
    const a = $(from), b = $(to);
    if (b.scrollTop !== a.scrollTop) b.scrollTop = a.scrollTop;
    if (b.scrollLeft !== a.scrollLeft) b.scrollLeft = a.scrollLeft;
  });
}

// ----------------------------------------
// Markdown: сырой текст ↔ просмотр (кнопка как в VS Code, ctrl+shift+v)
// ----------------------------------------
function setPreview(on) {
  const tab = files.active;
  tab.preview = on && tab.md;
  $("#editor-main").classList.toggle("preview", tab.preview);
  $("#editor-preview-toggle").classList.toggle("active", tab.preview);
  $("#editor-preview-toggle").title = tab.preview ? "исходный текст (ctrl+shift+v)" : "просмотр Markdown (ctrl+shift+v)";
  if (tab.preview) {
    renderMarkdown($("#editor-preview"), editText.value);
    showImages($("#editor-preview"), tab.path.split("/").slice(0, -1).join("/"), (path) => api().file_image(path, tab.root));
  }
  else { refreshEditor(); editText.focus(); }
}

$("#editor-preview-toggle").onclick = () => files.active && setPreview(!files.active.preview);

// ----------------------------------------
// После правки: ● на вкладке, подсветка (раз в кадр), номера строк
// ----------------------------------------
function refreshEditor() {
  const tab = files.active;
  if (!tab) return;
  const dirtyMark = $("#file-tabs .tab.active .dirty");
  if (!!dirtyMark !== tabDirty(tab)) renderFileTabs();
  cancelAnimationFrame(files.frame);
  files.frame = requestAnimationFrame(paintCode);
}

// ----------------------------------------
// Подсвеченная копия текста под прозрачным полем ввода и номера строк
// ----------------------------------------
function paintCode() {
  const text = editText.value, lang = files.active ? files.active.lang : "plaintext";
  const code = $(".code-view code");
  if (lang === "plaintext" || text.length > 300000) code.textContent = text + "\n";
  else code.innerHTML = hljs.highlight(text, { language: lang, ignoreIllegals: true }).value + "\n";
  const lines = text.split("\n").length;
  if (lines !== files.lines) {
    files.lines = lines;
    $(".gutter").textContent = Array.from({ length: lines }, (_, i) => i + 1).join("\n") + "\n";
  }
  syncEditorScroll();
}

// ----------------------------------------
// Подсветка и номера строк прокручиваются вместе с полем ввода
// ----------------------------------------
function syncEditorScroll() {
  const view = $(".code-view");
  view.scrollTop = editText.scrollTop;
  view.scrollLeft = editText.scrollLeft;
  $(".gutter").scrollTop = editText.scrollTop;
}

editText.addEventListener("input", refreshEditor);
editText.addEventListener("scroll", syncEditorScroll);

// ----------------------------------------
// Tab — отступ (табом, если файл на табах), Enter — новая строка с отступом текущей
// ----------------------------------------
editText.addEventListener("keydown", (e) => {
  if (e.key === "Tab" && !e.ctrlKey && !e.altKey) {
    e.preventDefault();
    document.execCommand("insertText", false, /\n\t/.test(files.active.saved) ? "\t" : "    ");
  } else if (e.key === "Enter" && !e.ctrlKey && !e.altKey && !e.shiftKey) {
    const start = editText.value.lastIndexOf("\n", editText.selectionStart - 1) + 1;
    const indent = editText.value.slice(start, editText.selectionStart).match(/^[ \t]*/)[0];
    e.preventDefault();
    document.execCommand("insertText", false, "\n" + indent);
  }
});

// ----------------------------------------
// Сохраняет открытую вкладку без вопросов; файл поменяли на диске после открытия — спросить, перезаписывать ли
// ----------------------------------------
function saveFile(force = false) {
  const tab = files.active;
  if (!tab || tab.kind === "diff" || tab.kind === "image") return Promise.resolve(false);
  const text = editText.value;
  return api().save_file(tab.path, text, tab.mtime, tab.crlf, force, tab.root).then((r) => {
    if (r.ok) {
      tab.saved = text;
      tab.mtime = r.mtime;
      renderFileTabs();
      flashNote("сохранено");
      return true;
    }
    if (r.conflict) {
      return new Promise((resolve) => ask("файл изменился на диске",
        `${tab.path} поменяли после открытия (возможно, агент). Перезаписать вашей версией?`,
        [["перезаписать", "primary", () => saveFile(true).then(resolve)], ["отмена", "", () => resolve(false)]]));
    }
    flashNote(r.error, true);
    return false;
  });
}

// ----------------------------------------
// Короткая отметка в строке редактора: «сохранено» или ошибка
// ----------------------------------------
function flashNote(text, error = false) {
  const note = $("#editor-note");
  note.textContent = text;
  note.className = "meta " + (error ? "over" : "saved");
  clearTimeout(note.timer);
  if (!error) note.timer = setTimeout(() => (note.textContent = ""), 1500);
}

$("#editor-save").onclick = () => saveFile();

// ----------------------------------------
// Ширина редактора: тянуть границу с чатом, запоминается; двойной клик — пополам
// ----------------------------------------
function setEditorWidth(px, save = true) {
  app.ui.editor_width = px ? Math.round(px) : 0;
  document.documentElement.style.setProperty("--editor-width", px ? `${app.ui.editor_width}px` : "50%");
  if (save) api().save_ui({ editor_width: app.ui.editor_width });
}

(() => {
  const handle = $("#editor-resizer");
  let start = null;
  handle.addEventListener("pointerdown", (e) => {
    start = { x: e.clientX, width: $("#editor").getBoundingClientRect().width };
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("resizing");
    e.preventDefault();
  });
  handle.addEventListener("pointermove", (e) => {
    if (!start) return;
    const max = $("#work").getBoundingClientRect().width - 330;
    setEditorWidth(Math.min(max, Math.max(320, start.width - (e.clientX - start.x))), false);
  });
  handle.addEventListener("pointerup", () => {
    if (!start) return;
    start = null;
    handle.classList.remove("dragging");
    document.body.classList.remove("resizing");
    setEditorWidth(app.ui.editor_width);
  });
  handle.addEventListener("dblclick", () => setEditorWidth(0));
})();

// ======== вопрос с кнопками поверх окон ========

// ----------------------------------------
// buttons: [подпись, класс, действие]; Enter — первая кнопка, Esc — последняя
// ----------------------------------------
function ask(title, text, buttons) {
  $("#ask-title").textContent = title;
  $("#ask-text").textContent = text;
  const row = $("#ask-buttons");
  row.textContent = "";
  for (const [label, cls, action] of buttons) {
    const button = el("button", cls || "", label);
    button.onclick = () => { $("#ask").hidden = true; if (action) action(); };
    row.append(button);
  }
  $("#ask").hidden = false;
  row.firstChild.focus();
}

// ======== переключатель пространств (ctrl+k) ========

let switcherSel = 0;

// ----------------------------------------
// Открывает список пространств с фильтром
// ----------------------------------------
function openSwitcher() {
  $("#switcher").hidden = false;
  $("#switcher-input").value = "";
  switcherSel = 0;
  renderSwitcher();
  $("#switcher-input").focus();
}

// ----------------------------------------
// Пункты: пространства по фильтру и «открыть папку…»
// ----------------------------------------
function switcherItems() {
  const q = $("#switcher-input").value.trim().toLowerCase();
  const items = app.spaces.filter((s) => !q || s.name.toLowerCase().includes(q) || s.path.toLowerCase().includes(q))
    .map((s) => ({ label: s.name, hint: s.path, run: () => api().select_space(s.path).then(render) }));
  if (/^([~/]|[a-z]:[\\/])/i.test(q)) items.unshift({ label: `открыть ${q}`, hint: "", run: () => openPath($("#switcher-input").value.trim()) });
  items.push({ label: "открыть папку…", hint: "диалог выбора", run: () => api().open_space("").then((r) => r && r.ok && render(r)) });
  return items;
}

// ----------------------------------------
// Открывает путь как пространство
// ----------------------------------------
function openPath(path) {
  return api().open_space(path).then((r) => {
    if (r && r.ok) render(r);
    else if (r && r.error) feeds.get(app.session).append(el("div", "error-line", r.error));
  });
}

// ----------------------------------------
// Перерисовка списка переключателя
// ----------------------------------------
function renderSwitcher() {
  const list = $("#switcher-list");
  list.textContent = "";
  const items = switcherItems();
  switcherSel = Math.min(switcherSel, items.length - 1);
  items.forEach((item, i) => {
    const li = el("li", i === switcherSel ? "sel" : "");
    li.append(item.label, " ", el("span", "muted", item.hint));
    li.onclick = () => { closeSwitcher(); item.run(); };
    list.append(li);
  });
}

// ----------------------------------------
// Закрывает переключатель
// ----------------------------------------
function closeSwitcher() {
  $("#switcher").hidden = true;
  focusInput();
}

$("#switcher-input").addEventListener("input", () => { switcherSel = 0; renderSwitcher(); });
$("#switcher-input").addEventListener("keydown", (e) => {
  const items = switcherItems();
  if (e.key === "ArrowDown") { switcherSel = (switcherSel + 1) % items.length; renderSwitcher(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { switcherSel = (switcherSel - 1 + items.length) % items.length; renderSwitcher(); e.preventDefault(); }
  else if (e.key === "Enter") { const item = items[switcherSel]; closeSwitcher(); if (item) item.run(); e.preventDefault(); }
  else if (e.key === "Escape") { closeSwitcher(); e.preventDefault(); e.stopPropagation(); }
});
$("#switcher").addEventListener("click", (e) => { if (e.target.id === "switcher") closeSwitcher(); });

// ======== поле ввода ========

const input = $("#input");
let historyIndex = -1;
let cmdSel = 0;

// ----------------------------------------
// Фокус в поле ввода (зовётся и из Python при открытии окна)
// ----------------------------------------
function focusInput() {
  if ($("#confirm").hidden) input.focus();
}
window.focusInput = focusInput;

// ----------------------------------------
// Высота поля по содержимому
// ----------------------------------------
function autosize() {
  input.style.height = "auto";
  input.style.height = input.scrollHeight + "px";
}

// ----------------------------------------
// Отправка: текст уходит в активную сессию; /стоп — сразу stop()
// ----------------------------------------
function submit() {
  const text = input.value.trim();
  if (!text && !attached.length) return;
  const feed = feeds.get(app.session);
  if (text === "/стоп") { api().stop(); input.value = ""; autosize(); return; }
  api().send(text, "", attached.map((a) => a.url)).then((r) => {
    if (r && r.ok) { input.value = ""; autosize(); historyIndex = -1; feed.stick = true; clearAttachments(); refreshState(); }
    else if (r && r.error) feed.append(el("div", "warning-line", r.error));
  });
}

// ----------------------------------------
// Быстрые команды, подходящие к набранному «/…»
// ----------------------------------------
function matchingCommands() {
  const text = input.value;
  if (!text.startsWith("/") || text.includes("\n")) return [];
  const head = text.split(" ")[0].toLowerCase();
  const seen = new Set();
  return app.commands.filter((c) => {
    const ok = c.command.toLowerCase().startsWith(head) && !seen.has(c.command);
    seen.add(c.command);
    return ok && (text.length <= c.command.length || c.command.includes(" "));
  }).slice(0, 30);
}

// ----------------------------------------
// Список автодополнения над полем
// ----------------------------------------
function renderCommands() {
  const list = $("#cmdlist");
  const items = input.value.includes(" ") && !matchingCommands().some((c) => c.command.startsWith(input.value)) ? [] : matchingCommands();
  list.textContent = "";
  list.hidden = !items.length;
  cmdSel = Math.min(cmdSel, Math.max(0, items.length - 1));
  items.forEach((c, i) => {
    const row = el("div", i === cmdSel ? "sel" : "");
    row.append(c.command, el("span", "desc", c.description));
    row.onmousedown = (e) => { e.preventDefault(); acceptCommand(c); };
    list.append(row);
  });
  return items;
}

// ----------------------------------------
// Подставляет команду без шаблонов {path} и <имя>
// ----------------------------------------
function acceptCommand(c) {
  const plain = c.command.replace(/\s*[{<][^}>]*[}>]/g, "");
  input.value = plain + (plain !== c.command ? " " : "");
  $("#cmdlist").hidden = true;
  autosize();
  input.focus();
}

input.addEventListener("input", () => { autosize(); cmdSel = 0; renderCommands(); });

input.addEventListener("keydown", (e) => {
  const list = $("#cmdlist");
  if (!list.hidden) {
    const items = matchingCommands();
    if (e.key === "ArrowDown") { cmdSel = (cmdSel + 1) % items.length; renderCommands(); e.preventDefault(); return; }
    if (e.key === "ArrowUp") { cmdSel = (cmdSel - 1 + items.length) % items.length; renderCommands(); e.preventDefault(); return; }
    if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && items[cmdSel] && input.value.trim() !== items[cmdSel].command)) {
      if (items[cmdSel]) acceptCommand(items[cmdSel]);
      e.preventDefault();
      return;
    }
    if (e.key === "Escape") { list.hidden = true; e.preventDefault(); e.stopPropagation(); return; }
  }
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); list.hidden = true; submit(); return; }
  const feed = feeds.get(app.session);
  if (e.key === "ArrowUp" && feed && feed.sent.length && !input.value.slice(0, input.selectionStart).includes("\n")) {
    historyIndex = historyIndex < 0 ? feed.sent.length - 1 : Math.max(0, historyIndex - 1);
    input.value = feed.sent[historyIndex];
    autosize();
    e.preventDefault();
  } else if (e.key === "ArrowDown" && historyIndex >= 0 && !input.value.slice(input.selectionEnd).includes("\n")) {
    historyIndex += 1;
    input.value = historyIndex < feed.sent.length ? feed.sent[historyIndex] : "";
    if (historyIndex >= feed.sent.length) historyIndex = -1;
    autosize();
    e.preventDefault();
  }
});

input.addEventListener("blur", () => setTimeout(() => { $("#cmdlist").hidden = true; }, 150));

// ----------------------------------------
// Микрофон: диктовка через voice/, текст вставляется в поле и не отправляется
// ----------------------------------------
let dictation = null;  // {before, after}: что было в поле до и после курсора, когда начали диктовать

// ----------------------------------------
// Текст диктовки — в поле на место курсора; черновик по ходу речи и точный текст в конце
// ----------------------------------------
function showDictation(text) {
  if (!dictation) return;
  const { before, after } = dictation;
  const gap = before && text && !/\s$/.test(before) ? " " : "";
  input.value = before + gap + text + after;
  input.selectionStart = input.selectionEnd = (before + gap + text).length;
  autosize();
}

// ----------------------------------------
// Микрофон: диктовка через voice/ — слова появляются в поле по ходу речи; отправляете вы сами
// ----------------------------------------
$("#mic").onclick = () => {
  const mic = $("#mic");
  if (mic.classList.contains("on")) { api().dictate(); return; }
  mic.classList.add("on");
  dictation = { before: input.value.slice(0, input.selectionStart), after: input.value.slice(input.selectionEnd) };
  input.classList.add("dictating");
  api().dictate().then((r) => {
    mic.classList.remove("on");
    input.classList.remove("dictating");
    if (r && r.text) showDictation(r.text);
    else if (dictation) showDictation("");  // ничего не распознано — убираем черновик
    if (r && r.error) feeds.get(app.session).append(el("div", "warning-line", r.error));
    dictation = null;
    input.focus();
  });
};

// ======== строка статуса ========

// ----------------------------------------
// Опрос agent.status() раз в секунду
// ----------------------------------------
function pollStatus() {
  api().status().then((data) => {
    if (!data) return;
    app.status = data;
    renderStatus(data);
    const changed = JSON.stringify(data.spaces) !== JSON.stringify(app.spaces) ||
                    JSON.stringify(data.sessions) !== JSON.stringify(app.sessions);
    if (changed) {
      app.spaces = data.spaces;
      app.sessions = data.sessions;
      renderSpaces();
      renderTabs();
    }
  }).catch(() => {});
}

// ----------------------------------------
// Модель · ток/с · шаг · контекст · время · папка · ветка · подтверждения · GPU · тема
// ----------------------------------------
function renderStatus(data) {
  const s = data.status;
  const bar = $("#statusbar");
  bar.textContent = "";
  const part = (text, cls = "", title = "") => { const span = el("span", cls, text); if (title) span.title = title; bar.append(span); return span; };
  const model = part(s.model, "clickable", "сменить модель (ollama list)");
  model.onclick = (e) => showModels(e);
  part(`${Math.round(s.tokens_per_sec)} t/s`);
  part(`шаг ${s.step}/${s.max_steps}`);
  bar.append(contextMeter(s));
  part(clock(s.session_seconds), "", `сессия ${s.session}`);
  part(s.project.split(/[\\/]/).pop() || s.project, "", s.project);
  if (s.branch) part(`⎇ ${s.branch}`);
  part(s.approved.length ? `авто: ${s.approved.map(toolTitle).join(", ")}` : "подтв.: спрашивать", "", "инструменты, разрешённые «на сессию»");
  if (data.gpu) part(data.gpu);
  const theme = part(app.ui.theme === "light" ? "☀" : "◐", "clickable", "тема: светлая / тёмная");
  theme.onclick = () => setTheme(app.ui.theme === "light" ? "dark" : "light");
}

// ----------------------------------------
// Занятый контекст: полоска и «12k/32k» по последнему запросу; к концу окна — жёлтая, потом красная
// ----------------------------------------
function contextMeter(s) {
  const box = el("span", "ctx");
  if (!s.context_max) {
    box.textContent = `контекст ${s.context_used ? short(s.context_used) : "—"}`;
    box.title = "окно модели пока неизвестно: Ollama сообщает его после первого запроса";
    return box;
  }
  const share = Math.min(1, s.context_used / s.context_max);
  if (share > 0.95) box.classList.add("full");
  else if (share > 0.8) box.classList.add("high");
  const meter = el("i", "meter");
  const fill = el("b");
  fill.style.width = `${Math.round(share * 100)}%`;
  meter.append(fill);
  box.append("контекст", meter, `${short(s.context_used)}/${short(s.context_max)}`);
  box.title = `занято ${s.context_used} из ${s.context_max} токенов окна модели (${Math.round(share * 100)}%) — по последнему запросу`;
  return box;
}

// ----------------------------------------
// Меню выбора модели из ollama list
// ----------------------------------------
function showModels(e) {
  const popup = $("#popup");
  api().models().then((models) => {
    popup.textContent = "";
    if (!models.length) popup.append(el("div", "", "Ollama не ответила"));
    for (const name of models) {
      const row = el("div", name === app.status.status.model ? "current" : "", name);
      row.onclick = () => {
        popup.hidden = true;
        api().set_model(name).then((r) => {
          if (r && !r.ok) feeds.get(app.session).append(el("div", "warning-line", r.error));
          pollStatus();
        });
      };
      popup.append(row);
    }
    popup.hidden = false;
    const rect = e.target.getBoundingClientRect();
    popup.style.left = rect.left + "px";
    popup.style.top = Math.max(4, rect.top - popup.offsetHeight - 4) + "px";
  });
}

document.addEventListener("mousedown", (e) => { if (!$("#popup").contains(e.target)) $("#popup").hidden = true; });

// ======== оформление ========

// ----------------------------------------
// Тема: тёмная по умолчанию, светлая — переключателем
// ----------------------------------------
function setTheme(theme, save = true) {
  app.ui.theme = theme;
  document.documentElement.dataset.theme = theme;
  $("#hljs-theme").href = theme === "light" ? "../vendor/hljs-github.css" : "../vendor/hljs-github-dark.css";
  if (save) api().save_ui({ theme });
  if (app.status) renderStatus(app.status);
}

// ----------------------------------------
// Размер шрифта: Ctrl+плюс / Ctrl+минус / Ctrl+0
// ----------------------------------------
function setFont(size, save = true) {
  app.ui.font_size = Math.min(24, Math.max(10, size));
  document.documentElement.style.setProperty("--font-size", app.ui.font_size + "px");
  if (save) api().save_ui({ font_size: app.ui.font_size });
}

// ----------------------------------------
// Ширина левой колонки: 160–600 px, запоминается
// ----------------------------------------
function setSideWidth(px, save = true) {
  app.ui.sidebar_width = Math.round(Math.min(600, Math.max(160, px)));
  document.documentElement.style.setProperty("--side-width", app.ui.sidebar_width + "px");
  if (save) api().save_ui({ sidebar_width: app.ui.sidebar_width });
}

(() => {
  const handle = $("#side-resizer");
  let start = null;
  handle.addEventListener("pointerdown", (e) => {
    start = { x: e.clientX, width: $("#side").getBoundingClientRect().width };
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("resizing");
    e.preventDefault();
  });
  handle.addEventListener("pointermove", (e) => { if (start) setSideWidth(start.width + e.clientX - start.x, false); });
  handle.addEventListener("pointerup", () => {
    if (!start) return;
    start = null;
    handle.classList.remove("dragging");
    document.body.classList.remove("resizing");
    setSideWidth(app.ui.sidebar_width);
  });
  handle.addEventListener("dblclick", () => setSideWidth(240));
})();

// ----------------------------------------
// Левая колонка: ctrl+b сворачивает
// ----------------------------------------
function setSidebar(open, save = true) {
  app.ui.sidebar = open;
  $("#app").classList.toggle("no-side", !open);
  if (save) api().save_ui({ sidebar: open });
}

// ======== картинки в сообщении ========

const attached = [];  // картинки к следующему сообщению: {url — data URL}
const IMAGE_MAX_SIDE = 1600;  // больше — уменьшается: модели хватает, а контекст не раздувается

// ----------------------------------------
// Файл картинки → data URL (большая — уменьшается; gif остаётся как есть, чтобы не потерять анимацию)
// ----------------------------------------
function attachImage(file) {
  if (!/^image\/(png|jpeg|webp|gif)$/.test(file.type)) return;
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, IMAGE_MAX_SIDE / Math.max(img.width, img.height));
      let url = reader.result;
      if (scale < 1 && file.type !== "image/gif") {
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        url = file.type === "image/png" ? canvas.toDataURL("image/png") : canvas.toDataURL("image/jpeg", 0.9);
      }
      attached.push({ url });
      renderAttachments();
    };
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
}

// ----------------------------------------
// Миниатюры над полем ввода; × — убрать
// ----------------------------------------
function renderAttachments() {
  const box = $("#attachments");
  box.textContent = "";
  attached.forEach((item, index) => {
    const thumb = el("div", "thumb");
    const img = el("img");
    img.src = item.url;
    img.onclick = () => showImage(item.url);
    const remove = el("button", "thumb-remove", "×");
    remove.title = "убрать";
    remove.onclick = () => { attached.splice(index, 1); renderAttachments(); };
    thumb.append(img, remove);
    box.append(thumb);
  });
  box.hidden = !attached.length;
}

// ----------------------------------------
// После отправки — картинок к сообщению больше нет
// ----------------------------------------
function clearAttachments() {
  attached.length = 0;
  renderAttachments();
}

// ----------------------------------------
// Картинки сообщения в ленте; клик — во весь экран
// ----------------------------------------
function imageStrip(paths) {
  const strip = el("div", "user-images");
  for (const path of paths) {
    const img = el("img");
    api().image(path).then((url) => { if (url) img.src = url; else img.alt = "картинка удалена"; });
    img.title = path.split(/[\\/]/).pop();
    img.onclick = () => showImage(img.src);
    strip.append(img);
  }
  return strip;
}

// ----------------------------------------
// Картинка поверх окна; клик или Esc — закрыть
// ----------------------------------------
function showImage(src) {
  $("#lightbox img").src = src;
  $("#lightbox").hidden = false;
}

$("#lightbox").onclick = () => { $("#lightbox").hidden = true; };
$("#attach").onclick = () => $("#attach-input").click();
$("#attach-input").onchange = (e) => { [...e.target.files].forEach(attachImage); e.target.value = ""; focusInput(); };
input.addEventListener("paste", (e) => {
  const files = [...(e.clipboardData ? e.clipboardData.items : [])].filter((i) => i.kind === "file").map((i) => i.getAsFile());
  const images = files.filter((f) => f && f.type.startsWith("image/"));
  if (!images.length) return;
  e.preventDefault();
  images.forEach(attachImage);
});
$("#main").addEventListener("dragover", (e) => { if ([...e.dataTransfer.types].includes("Files")) e.preventDefault(); });
$("#main").addEventListener("drop", (e) => {
  const images = [...e.dataTransfer.files].filter((f) => f.type.startsWith("image/"));
  if (!images.length) return;
  e.preventDefault();
  images.forEach(attachImage);
  focusInput();
});

// ======== горячие клавиши ========

document.addEventListener("keydown", (e) => {
  if (!$("#lightbox").hidden) {
    if (e.key === "Escape") { $("#lightbox").hidden = true; e.preventDefault(); }
    return;
  }
  if (!$("#ask").hidden) {
    const buttons = $("#ask-buttons").children;
    if (e.key === "Escape") buttons[buttons.length - 1].click();
    else if (e.key === "Enter" && document.activeElement.parentElement !== $("#ask-buttons")) buttons[0].click();
    else return;
    e.preventDefault();
    return;
  }
  const confirmOpen = !$("#confirm").hidden;
  if (confirmOpen) {
    if (e.key === "Enter") answerConfirm("yes");
    else if (e.key === "Escape") answerConfirm("no");
    else if ((e.code === "KeyS") && !$("#confirm-session").hidden) answerConfirm("session");
    else return;
    e.preventDefault();
    return;
  }
  if (!$("#editor").hidden && e.ctrlKey && !e.altKey) {  // редактор: ctrl+s — сохранить, ctrl+w — закрыть вкладку
    const inEditor = $("#editor").contains(document.activeElement);
    if (e.code === "KeyS") { e.preventDefault(); saveFile(); return; }
    if (e.code === "KeyW" && inEditor) { e.preventDefault(); closeTab(files.active); return; }
    if (e.code === "KeyV" && e.shiftKey && files.active && files.active.md) { e.preventDefault(); setPreview(!files.active.preview); return; }
  }
  if (e.ctrlKey && !e.altKey) {
    const actions = {
      KeyN: newSession,
      KeyB: () => setSidebar(!app.ui.sidebar),
      KeyO: toggleLastBlock,
      KeyK: openSwitcher,
      Comma: () => ($("#settings").hidden ? openSettings() : closeSettings()),
      Equal: () => setFont(app.ui.font_size + 1),
      NumpadAdd: () => setFont(app.ui.font_size + 1),
      Minus: () => setFont(app.ui.font_size - 1),
      NumpadSubtract: () => setFont(app.ui.font_size - 1),
      Digit0: () => setFont(FONT_DEFAULT),
    };
    if (actions[e.code]) { e.preventDefault(); actions[e.code](); return; }
  }
  if (e.key === "Escape") {
    if ($("#editor").contains(document.activeElement)) return;  // Esc в редакторе не останавливает агента
    if (!$("#settings").hidden) closeSettings();
    else if (!$("#popup").hidden) $("#popup").hidden = true;
    else api().stop();
    e.preventDefault();
  }
});

// ----------------------------------------
// ctrl+o: раскрыть или свернуть последний блок шагов
// ----------------------------------------
function toggleLastBlock() {
  const feed = feeds.get(app.session);
  const blocks = feed ? feed.el.querySelectorAll(".work") : [];
  const last = blocks[blocks.length - 1];
  if (last) last.querySelector(".work-head").click();
}

// ======== события из Python ========

// ----------------------------------------
// Событие ядра для сессии: {session, type, text, data, id}
// ----------------------------------------
window.onEvent = function (ev) {
  if (ev.type === "dictation") { showDictation(ev.text); return; }  // черновик диктовки — не событие сессии
  const feed = ensureFeed(ev.session);
  feed.onEvent(ev);
  if (ev.type === "status" && ev.session === app.session && app.status) {
    app.status.status = ev.data;
    renderStatus(app.status);
  }
};

// ----------------------------------------
// Старт: состояние, ленты из журналов, опрос статуса
// ----------------------------------------
window.addEventListener("pywebviewready", () => {
  api().state().then((state) => {
    const ui = state.ui || {};
    setTheme(ui.theme || "dark", false);
    setFont(ui.font_size || FONT_DEFAULT, false);
    setSidebar(ui.sidebar !== false, false);
    if (SETTINGS_TABS.some(([id]) => id === ui.settings_tab)) settings.tab = ui.settings_tab;
    if (ui.sidebar_width) setSideWidth(ui.sidebar_width, false);
    if (ui.editor_width) setEditorWidth(ui.editor_width, false);
    render(state);
    pollStatus();
    setInterval(pollStatus, STATUS_EVERY);
    setInterval(loadChanges, GIT_EVERY);
    focusInput();
  });
});
