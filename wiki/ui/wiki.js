// Раздел «Вики» окна чата: дерево заметок | чат агента вики | заметка (просмотр и правка) или граф.
// Подключает ui/app.py после загрузки страницы чата (wiki/assets.py); берёт из chat.js $, el, api, ask,
// renderMarkdown, ensureFeed.

"use strict";

const wiki = {
  open: false,
  session: "",            // сессия агента вики
  tree: [],               // [{folder, notes: [{name, title, summary, updated}]}]
  name: "",               // открытая заметка
  note: null,             // ответ wiki_note для неё
  tab: "note",            // note | graph
  edit: false,            // заметка в редакторе
  dirty: false,           // в редакторе несохранённые правки
  closed: new Set(),      // свёрнутые разделы дерева
  hits: [],               // совпадения в тексте для строки поиска
  graph: null,            // WikiGraph — создаётся при первом показе вкладки
  sourceView: "",         // открыт сырой источник (путь) вместо заметки
};

// ----------------------------------------
// Разметка раздела: строится один раз, лежит поверх чата
// ----------------------------------------
function buildWiki() {
  const root = el("section");
  root.id = "wiki";
  root.hidden = true;
  root.innerHTML = `
    <aside id="wiki-side" class="box">
      <div id="wiki-side-resizer" class="wresizer" title="тянуть — ширина колонки · двойной клик — обычная"></div>
      <div class="box-title">вики <button class="title-btn" id="wiki-close" title="назад в чат (Esc)">← чат</button></div>
      <input id="wiki-filter" placeholder="найти заметку…" spellcheck="false" autocomplete="off">
      <div id="wiki-tree"></div>
      <div id="wiki-root" class="muted"></div>
    </aside>
    <div id="wiki-work">
      <section id="wiki-chat" class="box">
        <div class="box-title">агент вики <span id="wiki-model" class="muted"></span></div>
        <div id="wiki-feed"></div>
        <div id="wiki-composer">
          <span class="prompt">❯</span>
          <textarea id="wiki-input" rows="1" spellcheck="false"
            placeholder="текст и что с ним сделать · enter — отправить, shift+enter — строка"></textarea>
        </div>
      </section>
      <main id="wiki-center" class="box">
        <div id="wiki-pane-resizer" class="wresizer" title="тянуть — ширина · двойной клик — пополам"></div>
        <div class="box-title tabs" id="wiki-tabs">
          <button class="tab" data-tab="note">заметка</button><button class="tab" data-tab="graph">граф</button>
        </div>
        <div id="wiki-note">
          <div id="wiki-bar">
            <span id="wiki-path" class="muted"></span>
            <span id="wiki-flash" class="meta"></span>
            <button id="wiki-edit-toggle" class="title-btn" title="правка / просмотр">правка</button>
            <button id="wiki-save" class="title-btn" title="сохранить (ctrl+s)" hidden>сохранить</button>
            <button id="wiki-delete" class="title-btn danger" title="удалить (в .trash/ вики)">удалить</button>
          </div>
          <div id="wiki-view" class="answer"></div>
          <textarea id="wiki-text" spellcheck="false" hidden></textarea>
        </div>
        <div id="wiki-graph" hidden></div>
      </main>
    </div>`;
  document.body.append(root);
  const open = el("button", "", "◈ вики");
  open.id = "wiki-open";
  open.title = "вики: заметки, граф, агент вики";
  open.onclick = () => showWiki(true);
  $("#settings-open").before(open);

  $("#wiki-close").onclick = () => showWiki(false);
  $("#wiki-tabs").onclick = (e) => { const tab = e.target.closest(".tab"); if (tab) setWikiTab(tab.dataset.tab); };
  $("#wiki-filter").oninput = () => { renderWikiTree(); searchWikiText(); };
  $("#wiki-edit-toggle").onclick = () => setWikiEdit(!wiki.edit);
  $("#wiki-save").onclick = saveWikiNote;
  $("#wiki-delete").onclick = deleteWikiNote;
  $("#wiki-text").oninput = () => { wiki.dirty = true; flashWiki("не сохранено"); };
  $("#wiki-view").addEventListener("click", wikiViewClick);
  const input = $("#wiki-input");
  input.oninput = () => { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 220)}px`; };
  input.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendWiki(); }
  };
  wikiResizer($("#wiki-side-resizer"), () => $("#wiki-side").getBoundingClientRect().width, 1,
              (px, save) => setWikiSideWidth(px, save), () => setWikiSideWidth(WIKI_SIDE_WIDTH));
  wikiResizer($("#wiki-pane-resizer"), () => $("#wiki-center").getBoundingClientRect().width, -1,
              (px, save) => setWikiPaneWidth(px, save), () => setWikiPaneWidth(0));
}

// ======== ширина колонок — как в чате: ручка тянется, двойной клик — обычная, ширина запоминается ========

const WIKI_SIDE_WIDTH = 240;

// ----------------------------------------
// Ручка ширины: dir 1 — растёт вправо (дерево), -1 — влево (заметка справа)
// ----------------------------------------
function wikiResizer(handle, width, dir, apply, reset) {
  let start = null;
  handle.addEventListener("pointerdown", (e) => {
    start = { x: e.clientX, width: width() };
    handle.setPointerCapture(e.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("resizing");
    e.preventDefault();
  });
  handle.addEventListener("pointermove", (e) => {
    if (start) apply(start.width + dir * (e.clientX - start.x), false);
  });
  handle.addEventListener("pointerup", (e) => {
    if (!start) return;
    apply(start.width + dir * (e.clientX - start.x), true);
    start = null;
    handle.classList.remove("dragging");
    document.body.classList.remove("resizing");
  });
  handle.addEventListener("dblclick", reset);
}

// ----------------------------------------
// Ширина дерева: 160…600 px
// ----------------------------------------
function setWikiSideWidth(px, save = true) {
  const width = Math.round(Math.min(600, Math.max(160, px)));
  document.documentElement.style.setProperty("--wiki-side-width", `${width}px`);
  if (save) api().save_ui({ wiki_side_width: width });
}

// ----------------------------------------
// Ширина заметки и графа; 0 — пополам с чатом
// ----------------------------------------
function setWikiPaneWidth(px, save = true) {
  const max = $("#wiki-work").getBoundingClientRect().width - 330;
  const width = px ? Math.round(Math.min(max, Math.max(320, px))) : 0;
  document.documentElement.style.setProperty("--wiki-pane-width", width ? `${width}px` : "50%");
  if (save) api().save_ui({ wiki_pane_width: width });
  if (wiki.graph && wiki.tab === "graph") wiki.graph.draw();
}

// ----------------------------------------
// Открыть или закрыть раздел; состояние переживает перезапуск
// ----------------------------------------
function showWiki(on, save = true) {
  wiki.open = on;
  $("#wiki").hidden = !on;
  if (save) api().save_ui({ wiki_open: on });
  if (on) { refreshWiki(); $("#wiki-input").focus(); }
  else if (window.focusInput) window.focusInput();
}

// ----------------------------------------
// Свежие данные: дерево, лента агента, открытая заметка или граф
// ----------------------------------------
async function refreshWiki() {
  const r = await api().wiki_state();
  wiki.session = r.session;
  wiki.tree = r.tree;
  $("#wiki-model").textContent = `· ${r.model}`;
  $("#wiki-root").textContent = r.root;
  $("#wiki-root").title = "папка вики — [wiki] dir в config.toml";
  placeWikiFeed();
  renderWikiTree();
  renderWikiTabs();
  if (wiki.tab === "graph") await loadWikiGraph();
  if (wiki.name && !wiki.dirty && !wiki.sourceView) await openWikiNote(wiki.name, { keepEdit: true });
  if (!wiki.name) renderWikiEmpty();
}

// ----------------------------------------
// Лента агента вики — та же, что у вкладок чата, но в колонке вики
// ----------------------------------------
function placeWikiFeed() {
  if (!wiki.session) return;
  const feed = ensureFeed(wiki.session);
  if (feed.el.parentElement !== $("#wiki-feed")) $("#wiki-feed").append(feed.el);
  feed.el.hidden = false;
  feed.scroll();
}

// ======== дерево ========

// ----------------------------------------
// Строки в стиле терминала: «◆ раздел · 3», «├─ ○ заметка», открытая — «●»
// ----------------------------------------
function renderWikiTree() {
  const query = $("#wiki-filter").value.trim().toLowerCase();
  const box = $("#wiki-tree");
  box.textContent = "";
  let shown = 0;
  for (const group of wiki.tree) {
    const notes = group.notes.filter((n) => !query || `${n.title} ${n.name} ${n.summary}`.toLowerCase().includes(query));
    if (query && !notes.length) continue;
    const open = Boolean(query) || !wiki.closed.has(group.folder);
    const head = el("button", "wrow group", `${open ? "◆" : "◇"} ${group.folder || "без раздела"}`);
    head.append(el("span", "muted", ` · ${group.notes.length}`));
    head.onclick = () => {
      if (wiki.closed.has(group.folder)) wiki.closed.delete(group.folder); else wiki.closed.add(group.folder);
      api().save_ui({ wiki_closed: [...wiki.closed] });
      renderWikiTree();
    };
    box.append(head);
    if (!open) continue;
    notes.forEach((n, index) => {
      const current = n.name === wiki.name;
      const row = el("button", `wrow note${current ? " current" : ""}`);
      row.append(el("span", "guide", index === notes.length - 1 ? "└─ " : "├─ "),
                 el("span", "mark", current ? "● " : "○ "), el("span", "label", n.title));
      row.title = `${n.name}.md${n.summary ? `\n${n.summary}` : ""}${n.updated ? `\nобновлена ${n.updated}` : ""}`;
      row.onclick = () => openWikiNote(n.name);
      box.append(row);
      shown++;
    });
  }
  if (query && wiki.hits.length) {
    box.append(el("div", "wrow muted", "◆ в тексте"));
    for (const hit of wiki.hits) {
      const row = el("button", "wrow note hit");
      row.append(el("span", "guide", "   "), el("span", "label", hit.title), el("span", "line muted", hit.line));
      row.onclick = () => openWikiNote(hit.name);
      box.append(row);
    }
  }
  if (!wiki.tree.length) box.append(el("div", "muted wempty", "заметок пока нет — дайте агенту текст справа"));
  else if (query && !shown && !wiki.hits.length) box.append(el("div", "muted wempty", "ничего не найдено"));
}

// ----------------------------------------
// Поиск по тексту заметок — с задержкой, пока печатают
// ----------------------------------------
let wikiSearchTimer = 0;
function searchWikiText() {
  clearTimeout(wikiSearchTimer);
  const query = $("#wiki-filter").value.trim();
  if (query.length < 3) { wiki.hits = []; return; }
  wikiSearchTimer = setTimeout(async () => {
    const r = await api().wiki_search(query);
    wiki.hits = r.ok ? r.hits : [];
    renderWikiTree();
  }, 250);
}

// ======== заметка ========

// ----------------------------------------
// Открыть заметку; несохранённые правки — сначала спросить
// ----------------------------------------
async function openWikiNote(name, { keepEdit = false, force = false } = {}) {
  if (wiki.dirty && !force && name !== wiki.name) {
    ask("несохранённые правки", `Сохранить изменения в ${wiki.name}?`, [
      ["сохранить", "primary", async () => { if (await saveWikiNote()) openWikiNote(name); }],
      ["не сохранять", "", () => { wiki.dirty = false; openWikiNote(name, { force: true }); }],
      ["отмена", "", null],
    ]);
    return;
  }
  const r = await api().wiki_note(name);
  if (!r.ok) {
    if (name === wiki.name) { wiki.name = ""; wiki.note = null; renderWikiEmpty(); renderWikiTree(); }
    else ask("заметка не открыта", r.error, [["понятно", "primary"]]);
    return;
  }
  wiki.name = name;
  wiki.note = r;
  wiki.sourceView = "";
  wiki.dirty = false;
  if (!keepEdit) wiki.edit = false;
  api().save_ui({ wiki_note: name });
  if (wiki.tab !== "note") setWikiTab("note");
  renderWikiNote();
  renderWikiTree();
  if (wiki.graph) wiki.graph.setCurrent(name);
}

// ----------------------------------------
// Просмотр: заголовок, описание, раздел и источник, текст со ссылками [[…]], «Связано»
// ----------------------------------------
function renderWikiNote() {
  const n = wiki.note;
  $("#wiki-path").textContent = n.path;
  $("#wiki-delete").hidden = false;
  $("#wiki-edit-toggle").hidden = false;
  $("#wiki-edit-toggle").textContent = wiki.edit ? "просмотр" : "правка";
  $("#wiki-save").hidden = !wiki.edit;
  $("#wiki-text").hidden = !wiki.edit;
  $("#wiki-view").hidden = wiki.edit;
  flashWiki("");
  if (wiki.edit) {
    $("#wiki-text").value = n.text;
    return;
  }
  const view = $("#wiki-view");
  view.textContent = "";
  view.append(el("h1", "wtitle", n.title));
  if (n.summary) view.append(el("div", "wsummary", n.summary));
  const meta = el("div", "wmeta muted", `${n.folder || "без раздела"}${n.updated ? ` · обновлена ${n.updated}` : ""}`);
  for (const source of n.sources) {
    const link = el("a", "", source);
    link.href = `#wiki-source:${source}`;
    meta.append(" · источник: ", link);
  }
  view.append(meta);
  const related = [...new Map([...n.links, ...n.backlinks].map((ref) => [ref.name, ref])).values()];
  if (related.length) {
    const chips = el("div", "wrelated");
    chips.append(el("span", "muted", "связано: "));
    for (const ref of related) {
      const chip = el("a", "chip", ref.title);
      chip.href = `#wiki:${ref.name}`;
      chip.title = ref.summary || ref.name;
      chips.append(chip);
    }
    view.append(chips);
  }
  const body = el("div", "wbody");
  renderMarkdown(body, wikiLinks(n.body, n.titles));
  view.append(body);
}

// ----------------------------------------
// [[имя]] и [[имя|подпись]] → ссылки внутри вики; нет такой заметки — серым
// ----------------------------------------
function wikiLinks(text, titles) {
  return text.replace(/\[\[([^\[\]|#]+?)(?:\|([^\]]*))?\]\]/g, (all, name, label) => {
    name = name.trim();
    const title = (label || titles[name] || name).replace(/[\[\]]/g, "");
    return titles[name] ? `[${title}](#wiki:${encodeURIComponent(name)})` : `<span class="wmissing" title="заметки пока нет">${title}</span>`;
  });
}

// ----------------------------------------
// Щелчок по ссылке в заметке: своя заметка, сырой источник, назад к заметке
// ----------------------------------------
function wikiViewClick(e) {
  const link = e.target.closest("a");
  if (!link) return;
  const href = link.getAttribute("href") || "";
  if (href.startsWith("#wiki:")) { e.preventDefault(); openWikiNote(decodeURIComponent(href.slice(6))); }
  else if (href.startsWith("#wiki-source:")) { e.preventDefault(); openWikiSource(href.slice(13)); }
  else if (href === "#wiki-back") { e.preventDefault(); wiki.sourceView = ""; renderWikiNote(); }
}

// ----------------------------------------
// Сырой источник — только чтение, как блок текста
// ----------------------------------------
async function openWikiSource(path) {
  const r = await api().wiki_source(path);
  if (!r.ok) { ask("источник не открыт", r.error, [["понятно", "primary"]]); return; }
  wiki.sourceView = path;
  const view = $("#wiki-view");
  view.textContent = "";
  const back = el("a", "", "← к заметке");
  back.href = "#wiki-back";
  view.append(back, el("h1", "wtitle", path), el("div", "wmeta muted", "сырой текст — как его прислали, не правится"));
  const pre = el("pre", "wsource", r.text);
  view.append(pre);
}

// ----------------------------------------
// Пустой центр: подсказка, как начать
// ----------------------------------------
function renderWikiEmpty() {
  $("#wiki-path").textContent = "";
  for (const id of ["#wiki-delete", "#wiki-edit-toggle", "#wiki-save", "#wiki-text"]) $(id).hidden = true;
  $("#wiki-view").hidden = false;
  $("#wiki-view").innerHTML = "";
  renderMarkdown($("#wiki-view"), wiki.tree.length
    ? "Выберите заметку слева или откройте **граф**."
    : "Вики пока пустая.\n\nВставьте справа сырой текст и напишите, что с ним сделать, — например:\n\n" +
      "> создай заметку в разделе «Базы данных»: …текст…\n\nСырой текст сохранится в `raw/`, заметка — в `notes/`, " +
      "каталог `index.md` и журнал `log.md` агент вики ведёт сам.");
}

// ----------------------------------------
// Правка ↔ просмотр
// ----------------------------------------
function setWikiEdit(on) {
  if (!wiki.note) return;
  if (!on && wiki.dirty) {
    ask("несохранённые правки", "Сохранить перед просмотром?", [
      ["сохранить", "primary", async () => { if (await saveWikiNote()) setWikiEdit(false); }],
      ["не сохранять", "", () => { wiki.dirty = false; setWikiEdit(false); }],
      ["отмена", "", null],
    ]);
    return;
  }
  wiki.edit = on;
  wiki.sourceView = "";
  renderWikiNote();
  if (on) $("#wiki-text").focus();
}

// ----------------------------------------
// Сохранить из редактора; заметку изменили на диске — предложить открыть заново
// ----------------------------------------
async function saveWikiNote() {
  if (!wiki.note || !wiki.edit) return false;
  const r = await api().wiki_save(wiki.name, $("#wiki-text").value, wiki.note.mtime);
  if (!r.ok) { ask("не сохранено", r.error, [["понятно", "primary"]]); return false; }
  wiki.dirty = false;
  const fresh = await api().wiki_note(wiki.name);
  if (fresh.ok) wiki.note = fresh;
  flashWiki("сохранено");
  refreshWikiTree();
  return true;
}

// ----------------------------------------
// Удалить заметку (в .trash/ вики)
// ----------------------------------------
function deleteWikiNote() {
  if (!wiki.note) return;
  ask("удаление", `Удалить «${wiki.note.title}»?\nФайл переместится в .trash/ вики.`, [
    ["удалить", "primary", async () => {
      const r = await api().wiki_delete(wiki.name);
      if (!r.ok) { ask("не удалено", r.error, [["понятно", "primary"]]); return; }
      wiki.name = ""; wiki.note = null; wiki.dirty = false; wiki.edit = false;
      api().save_ui({ wiki_note: "" });
      refreshWiki();
    }],
    ["отмена", "", null],
  ]);
}

// ----------------------------------------
// Короткая надпись в строке заметки
// ----------------------------------------
function flashWiki(text) {
  $("#wiki-flash").textContent = text;
}

// ----------------------------------------
// Только дерево и граф (после сохранения)
// ----------------------------------------
async function refreshWikiTree() {
  const r = await api().wiki_state();
  wiki.tree = r.tree;
  renderWikiTree();
  if (wiki.tab === "graph") loadWikiGraph();
}

// ======== граф ========

// ----------------------------------------
// Вкладки центра: заметка | граф
// ----------------------------------------
function setWikiTab(tab) {
  wiki.tab = tab;
  api().save_ui({ wiki_tab: tab });
  renderWikiTabs();
  if (tab === "graph") loadWikiGraph();
}

// ----------------------------------------
// Активная вкладка и видимость панелей
// ----------------------------------------
function renderWikiTabs() {
  for (const button of $("#wiki-tabs").children) button.classList.toggle("active", button.dataset.tab === wiki.tab);
  $("#wiki-note").hidden = wiki.tab !== "note";
  $("#wiki-graph").hidden = wiki.tab !== "graph";
}

// ----------------------------------------
// Данные графа; щелчок по точке — открыть заметку
// ----------------------------------------
async function loadWikiGraph() {
  const r = await api().wiki_graph();
  if (!r.ok) return;
  if (!wiki.graph) wiki.graph = WikiGraph.create($("#wiki-graph"), { onOpen: (name) => openWikiNote(name) });
  wiki.graph.update(r, wiki.name);
  renderWikiLegend(r.folders);
}

// ----------------------------------------
// Легенда: цвет — раздел
// ----------------------------------------
function renderWikiLegend(folders) {
  let legend = $("#wiki-legend");
  if (!legend) {
    legend = el("div");
    legend.id = "wiki-legend";
    $("#wiki-graph").append(legend);
  }
  legend.textContent = "";
  folders.forEach((folder, index) => {
    const item = el("span", `wlegend wg-c${index % 8}`);
    item.append(el("i"), folder);
    legend.append(item);
  });
  if (!folders.length) legend.append(el("span", "muted", "заметок пока нет"));
}

// ======== агент вики ========

// ----------------------------------------
// Сообщение агенту вики
// ----------------------------------------
async function sendWiki() {
  const input = $("#wiki-input");
  const text = input.value.trim();
  if (!text || !wiki.session) return;
  const r = await api().send(text, wiki.session);
  if (!r.ok) { flashWiki(r.error || "не отправлено"); return; }
  input.value = "";
  input.style.height = "auto";
}

// ----------------------------------------
// События ядра: агент вики закончил — дерево, граф и заметка обновляются
// ----------------------------------------
const chatOnEvent = window.onEvent;
window.onEvent = function (ev) {
  chatOnEvent(ev);
  if (ev.session === wiki.session && ev.type === "done" && wiki.open) refreshWiki();
};

// ----------------------------------------
// Клавиши, пока открыт раздел (раньше обработчика чата): Esc — стоп агента или назад в чат, ctrl+s — сохранить
// ----------------------------------------
window.addEventListener("keydown", (e) => {
  if (!wiki.open || !$("#ask").hidden || !$("#confirm").hidden || !$("#settings").hidden || !$("#lightbox").hidden) return;
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    const filter = $("#wiki-filter");
    if (document.activeElement === filter && filter.value) { filter.value = ""; wiki.hits = []; renderWikiTree(); return; }
    api().wiki_state().then((r) => { if (r.busy) api().stop(wiki.session); else showWiki(false); });
    return;
  }
  if (e.ctrlKey && !e.altKey && e.code === "KeyS") { e.preventDefault(); e.stopPropagation(); saveWikiNote(); return; }
  if (e.ctrlKey && !e.altKey && ["KeyN", "KeyK", "KeyO", "KeyB"].includes(e.code)) e.stopPropagation();  // это клавиши чата
}, true);

buildWiki();
window.addEventListener("resize", () => { if (wiki.graph && wiki.tab === "graph") wiki.graph.draw(); });

// ----------------------------------------
// Старт: сохранённое состояние раздела; скрипт подключают после загрузки страницы — pywebview может быть уже готов
// ----------------------------------------
async function startWiki() {
  const r = await api().wiki_state();
  const ui = r.ui || {};
  wiki.closed = new Set(ui.wiki_closed || []);
  wiki.tab = ui.wiki_tab === "graph" ? "graph" : "note";
  wiki.name = ui.wiki_note || "";
  if (ui.wiki_side_width) setWikiSideWidth(ui.wiki_side_width, false);
  if (ui.wiki_pane_width) setWikiPaneWidth(ui.wiki_pane_width, false);
  if (ui.wiki_open) showWiki(true, false);
}

if (window.pywebview && window.pywebview.api && window.pywebview.api.wiki_state) startWiki();
else window.addEventListener("pywebviewready", startWiki, { once: true });
