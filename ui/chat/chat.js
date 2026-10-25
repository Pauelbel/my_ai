// Окно чата: лента агента по событиям ядра (window.onEvent), вызовы Python — window.pywebview.api.

"use strict";

const FOLD_LINES = 20;          // блоки кода длиннее — свёрнуты
const STATUS_EVERY = 1000;      // строка статуса обновляется раз в секунду
const FONT_DEFAULT = 14;

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
  startTurn(text, started) {
    if (this.turn && this.turn.running) this.turn.finish(null);
    this.append(el("div", "user-line", text));
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
      if (ev.type === "need_skill" || ev.type === "confirm_request") this.turn.pause();
      else this.turn.resume();
    }
    switch (ev.type) {
      case "user": this.startTurn(ev.text, Date.now()); break;
      case "step": this.current().step(d.name, d.args); break;
      case "tool_result": this.current().result(ev.text, d.error); break;
      case "thinking": this.current().thinking(ev.text); break;
      case "token": this.current().token(ev.text, d.reset); break;
      case "answer": this.current().answer(ev.text); break;
      case "need_skill": this.current().card(ev); break;
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
      if (r.type === "user") this.startTurn(r.text, at);
      else if (r.type === "call") this.current().step(r.name, r.args);
      else if (r.type === "result") this.current().result(r.error ? `ОШИБКА: ${r.error}` : r.result, r.error);
      else if (r.type === "answer") this.current().answer(r.text, at);
      else if (r.type === "need_skill") this.current().card({ text: `Нужен скилл ${r.name}: ${r.what}\nПакеты: ${(r.libs || []).join(", ") || "не нужны"}` }, true);
      else if (r.type === "note") this.current().note(r.text, "muted-line");
      else if (r.type === "end" && this.turn) {
        if (r.reason && r.reason !== "skill_declined") this.turn.note(`задача прервана: ${r.reason}`, "muted-line");
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
    this.newBlock();
    this.waited = 0;          // мс, пока ждали ответа пользователя — не входят в «Думал»
    this.waitFrom = null;
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
  // Ход мыслей модели — приглушённым курсивом
  // ----------------------------------------
  thinking(text) {
    if (!this.thinkingEl) {
      this.thinkingEl = el("div", "thinking");
      this.block().body.append(this.thinkingEl);
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
  // Карточка need_skill: «создать» / «отмена»
  // ----------------------------------------
  card(ev, past = false) {
    const card = el("div", "card");
    const [title, ...rest] = (ev.text || "").split("\n");
    card.append(el("div", "title", "✚ " + title), el("div", "", rest.join("\n")));
    const buttons = el("div", "buttons");
    const yes = el("button", "primary", "создать"), no = el("button", "", "отмена");
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
  renderSettings();
  for (const s of app.sessions) ensureFeed(s.id);
  feeds.forEach((feed, id) => { feed.el.hidden = id !== app.session; });
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
    const close = el("button", "close", "×");
    close.title = "закрыть сессию";
    close.onclick = (e) => { e.stopPropagation(); closeSession(s.id); };
    tab.append(close);
    tab.onclick = () => api().select_session(s.id).then(render).then(focusInput);
    tabs.append(tab);
  }
  const add = el("span", "tab add", "+");
  add.title = "новая сессия (ctrl+n)";
  add.onclick = newSession;
  tabs.append(add);
}

const TRASH_ICON = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round">' +
  '<path d="M2.5 4h11M6 4V2.5h4V4M4 4l.7 9.5h6.6L12 4M6.8 6.5v5M9.2 6.5v5"/></svg>';
const openGroups = new Set(["skills", "trash"]);   // какие группы настроек раскрыты

// ----------------------------------------
// Пространство — в корзину; если нельзя (последнее или агент работает) — предупреждение в ленте
// ----------------------------------------
function removeSpace(path) {
  api().remove_space(path).then((r) => {
    if (r && r.error) feeds.get(app.session).append(el("div", "warning-line", r.error));
    render(r);
  });
}

// ----------------------------------------
// Настройки внизу левой колонки: инструменты, скиллы, корзина пространств
// ----------------------------------------
function renderSettings() {
  renderGroup("tools", `инструменты ${app.tools.length}`, app.tools, (tool) => {
    const li = el("li");
    li.append(el("span", "label", tool.title));
    if (tool.confirm !== "нет") li.append(el("span", "confirm", "!"));
    li.title = `${tool.name} — ${tool.description}`;
    li.onclick = () => showTool(tool);
    return li;
  });
  renderGroup("skills", `скиллы ${app.skills.length}`, app.skills, (skill) => {
    const li = el("li");
    li.append(el("span", "label", skill.title || skill.name));
    li.title = `${skill.name}(${skill.args}) — ${skill.description}`;
    li.onclick = () => showSkill(skill.name);
    return li;
  });
  renderGroup("memory", `память ${app.memory.facts.length}`, [{ edit: true }, ...app.memory.facts], (fact) => {
    const li = el("li");
    if (fact.edit) {
      li.append(el("span", "label muted", "✎ открыть редактор"));
    } else {
      li.append(el("span", "label", fact));
      li.title = fact;
    }
    li.onclick = showMemory;
    return li;
  });
  renderGroup("trash", `корзина ${app.trash.length}`, app.trash, (item) => {
    const li = el("li");
    const back = el("button", "row-btn", "вернуть"), purge = el("button", "row-btn danger", "×");
    back.title = "вернуть в пространства";
    purge.title = "убрать из корзины насовсем (папка на диске останется)";
    back.onclick = (e) => { e.stopPropagation(); api().restore_space(item.path).then(render); };
    purge.onclick = (e) => { e.stopPropagation(); api().purge_space(item.path).then(render); };
    li.append(el("span", "label muted", item.name), back, purge);
    li.title = item.path;
    return li;
  });
}

// ----------------------------------------
// Одна группа настроек: заголовок со стрелкой и счётчиком, список (или «пусто»)
// ----------------------------------------
function renderGroup(name, title, items, row) {
  const group = document.querySelector(`.group[data-group="${name}"]`);
  const head = group.querySelector(".group-head");
  head.textContent = `${openGroups.has(name) ? "▾" : "▸"} ${title}`;
  head.onclick = () => { openGroups.has(name) ? openGroups.delete(name) : openGroups.add(name); renderSettings(); };
  group.classList.toggle("closed", !openGroups.has(name));
  const list = group.querySelector(".list");
  list.textContent = "";
  for (const item of items) list.append(row(item));
  if (!items.length) list.append(el("li", "muted", "пусто"));
}

// ----------------------------------------
// Свернуть или раскрыть настройки (запоминается)
// ----------------------------------------
function setSettings(open, save = true) {
  app.ui.settings = open;
  $("#settings").hidden = !open;
  $("#settings-toggle").textContent = `⚙ настройки ${open ? "▾" : "▸"}`;
  if (save) api().save_ui({ settings: open });
}

$("#settings-toggle").onclick = () => setSettings(!app.ui.settings);

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
// Редактор памяти: факт — строка, × удаляет, внизу новый факт; «сохранить» пишет memory/memory.md
// ----------------------------------------
function showMemory() {
  api().memory().then((memory) => {
    $("#skill-title").textContent = "память";
    const body = $("#skill-body");
    body.textContent = "";
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
    const save = el("button", "primary", "сохранить"), cancel = el("button", "", "отмена");
    save.onclick = () => {
      const facts = [...list.querySelectorAll("textarea")].map((t) => t.value);
      api().save_memory(facts).then((saved) => { app.memory = saved; renderSettings(); closePanel(); });
    };
    cancel.onclick = closePanel;
    const buttons = el("div", "buttons");
    buttons.append(save, cancel, counter);
    body.append(pathLine(memory.path),
                el("div", "meta", "Что агент помнит о вас между сессиями — видит в каждой задаче. Один факт — одна строка."),
                list, add, buttons);
    count();
    $("#skillpanel").hidden = false;
    body.querySelectorAll("textarea").forEach(grow);
  });
}

// ----------------------------------------
// Закрывает боковую панель
// ----------------------------------------
function closePanel() {
  $("#skillpanel").hidden = true;
  focusInput();
}

// ----------------------------------------
// Панель инструмента: путь, название, описание, подтверждение, код функции
// ----------------------------------------
function showTool(tool) {
  $("#skill-title").textContent = `инструмент · ${tool.title}`;
  const body = $("#skill-body");
  body.textContent = "";
  body.append(pathLine(tool.path),
              fieldsTable([["имя", tool.name], ["вызов", tool.signature], ["что делает", tool.description],
                           ["подтверждение", tool.confirm]]),
              fullCode(tool.source || "", "python"));
  $("#skillpanel").hidden = false;
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
// Панель скилла: путь, поля SKILL.md табличкой, описание, run.py — только чтение
// ----------------------------------------
function showSkill(name) {
  api().skill_source(name).then((src) => {
    $("#skill-title").textContent = `скилл · ${toolTitle(name)}`;
    const body = $("#skill-body");
    body.textContent = "";
    const fields = {}, rest = [];
    for (const line of (src.skill_md || "").split("\n")) {
      const m = line.match(/^(name|title|description|args|libs):\s*(.*)$/);
      if (m && !(m[1] in fields)) fields[m[1]] = m[2];
      else rest.push(line);
    }
    const md = el("div", "answer");
    renderMarkdown(md, rest.join("\n").trim());
    body.append(pathLine(src.path || src.folder),
                fieldsTable([["название", fields.title], ["имя", fields.name], ["что делает", fields.description],
                             ["аргументы", fields.args], ["пакеты", fields.libs]]),
                md, el("div", "panel-file", "run.py"), fullCode(src.run_py || "", "python"));
    $("#skillpanel").hidden = false;
  });
}

$("#skill-close").onclick = () => { $("#skillpanel").hidden = true; focusInput(); };
$("#skillpanel").addEventListener("click", (e) => { if (e.target.id === "skillpanel") $("#skillpanel").hidden = true; });
$("#add-space").onclick = () => api().open_space("").then((r) => r && r.ok && render(r));

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
  if (!text) return;
  const feed = feeds.get(app.session);
  if (text === "/стоп") { api().stop(); input.value = ""; autosize(); return; }
  api().send(text).then((r) => {
    if (r && r.ok) { input.value = ""; autosize(); historyIndex = -1; feed.stick = true; refreshState(); }
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
// Модель · контекст · ток/с · шаг · время · папка · ветка · подтверждения · GPU · тема
// ----------------------------------------
function renderStatus(data) {
  const s = data.status;
  const bar = $("#statusbar");
  bar.textContent = "";
  const part = (text, cls = "", title = "") => { const span = el("span", cls, text); if (title) span.title = title; bar.append(span); return span; };
  const model = part(s.model, "clickable", "сменить модель (ollama list)");
  model.onclick = (e) => showModels(e);
  const ratio = s.context_max ? s.context_used / s.context_max : 0;
  const cells = 8, filled = Math.min(cells, Math.round(ratio * cells));
  const ctx = part("", "", `контекст: ${s.context_used} из ${s.context_max || "?"} токенов`);
  ctx.append("ctx ", el("span", "bar" + (ratio >= 0.9 ? " full" : ratio >= 0.7 ? " warn" : ""), "█".repeat(filled) + "░".repeat(cells - filled)),
             ` ${short(s.context_used)}/${s.context_max ? short(s.context_max) : "?"}`);
  part(`${Math.round(s.tokens_per_sec)} t/s`);
  part(`шаг ${s.step}/${s.max_steps}`);
  part(clock(s.session_seconds), "", `сессия ${s.session}`);
  part(s.project.split(/[\\/]/).pop() || s.project, "", s.project);
  if (s.branch) part(`⎇ ${s.branch}`);
  part(s.approved.length ? `авто: ${s.approved.map(toolTitle).join(", ")}` : "подтв.: спрашивать", "", "инструменты, разрешённые «на сессию»");
  if (data.gpu) part(data.gpu);
  const theme = part(app.ui.theme === "light" ? "☀" : "◐", "clickable", "тема: светлая / тёмная");
  theme.onclick = () => setTheme(app.ui.theme === "light" ? "dark" : "light");
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

// ======== горячие клавиши ========

document.addEventListener("keydown", (e) => {
  const confirmOpen = !$("#confirm").hidden;
  if (confirmOpen) {
    if (e.key === "Enter") answerConfirm("yes");
    else if (e.key === "Escape") answerConfirm("no");
    else if ((e.code === "KeyS") && !$("#confirm-session").hidden) answerConfirm("session");
    else return;
    e.preventDefault();
    return;
  }
  if (e.ctrlKey && !e.altKey) {
    const actions = {
      KeyN: newSession,
      KeyB: () => setSidebar(!app.ui.sidebar),
      KeyO: toggleLastBlock,
      KeyK: openSwitcher,
      Equal: () => setFont(app.ui.font_size + 1),
      NumpadAdd: () => setFont(app.ui.font_size + 1),
      Minus: () => setFont(app.ui.font_size - 1),
      NumpadSubtract: () => setFont(app.ui.font_size - 1),
      Digit0: () => setFont(FONT_DEFAULT),
    };
    if (actions[e.code]) { e.preventDefault(); actions[e.code](); return; }
  }
  if (e.key === "Escape") {
    if (!$("#skillpanel").hidden) { $("#skillpanel").hidden = true; focusInput(); }
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
    setSettings(ui.settings === true, false);
    if (ui.sidebar_width) setSideWidth(ui.sidebar_width, false);
    render(state);
    pollStatus();
    setInterval(pollStatus, STATUS_EVERY);
    focusInput();
  });
});
