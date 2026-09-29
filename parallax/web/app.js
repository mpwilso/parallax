// parallax ui: an intake box, a board of tasks by state, and one decision card.
// Everything agent-written is set as text, never as HTML. No inline code: the page's CSP forbids it.
"use strict";

const STATES = ["drafting", "building", "checking", "ready", "needs you", "done"];
const WAITS = new Set(["ready", "needs you"]);
const state = { token: "", board: null, open: null, card: null, doc: null, docText: null, version: "", busy: false };

// ---------- plumbing ----------

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

async function api(path, body) {
  const opts = { headers: { "X-Parallax-Token": state.token } };
  if (body !== undefined) {
    opts.method = "POST";
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) lock();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function flash(text, error) {
  const f = document.getElementById("flash");
  f.textContent = text;
  f.className = error ? "error" : "";
  f.hidden = !text;
}

function lock() {
  document.querySelector("main").hidden = true;
  document.getElementById("intake").hidden = true;
  document.getElementById("locked").hidden = false;
}

// ---------- the board ----------

function allTasks() {
  if (!state.board) return [];
  return STATES.flatMap(s => state.board.columns[s]);
}

function renderBoard() {
  const b = state.board;
  document.getElementById("project").textContent = b.project;
  const badge = document.getElementById("waiting");
  badge.hidden = !b.waiting;
  badge.textContent = `${b.waiting} waiting on you`;
  document.title = b.waiting ? `(${b.waiting}) Parallax` : "Parallax";
  const board = document.getElementById("board");
  board.replaceChildren(...STATES.map(s => {
    const tasks = b.columns[s];
    const cls = "column" + (WAITS.has(s) ? " waits" : "") + (s === "done" ? " done" : "");
    return el("div", { class: cls },
      el("h2", {}, el("span", {}, s), el("span", {}, tasks.length || "")),
      tasks.length ? el("ol", {}, tasks.map(t => el("li", {},
        el("button", { class: "task" + (state.open === t.task ? " open" : ""), "data-task": t.task, onclick: () => openCard(t.task) },
          el("span", { class: "title" }, t.title),
          el("span", { class: "meta" }, t.task + (t.cost_usd ? `  $${t.cost_usd.toFixed(2)} est` : "")))))) : el("p", { class: "empty" }, "none"));
  }));
}

async function refresh() {
  try {
    state.board = await api("/api/board");
    renderBoard();
    if (state.open) await loadCard(state.open);
  } catch (err) {
    flash(err.message, true);
  }
}

// ---------- the card ----------

async function openCard(task) {
  state.open = task;
  state.doc = null;
  state.docText = null;
  await loadCard(task);
  renderBoard();
}

function closeCard() {
  state.open = null;
  state.card = null;
  document.getElementById("card").hidden = true;
  renderBoard();
}

async function loadCard(task) {
  const card = await api(`/api/task/${encodeURIComponent(task)}`);
  const typing = document.activeElement && document.activeElement.id === "reason" ? document.activeElement.value : null;
  state.card = card;
  renderCard();
  if (typing !== null) {
    const r = document.getElementById("reason");
    if (r) { r.value = typing; r.focus(); }
  }
}

function section(title, items) {
  if (!items || !items.length) return null;
  return [el("h3", {}, title), el("ul", { class: "plain" }, items.map(i => el("li", {}, i)))];
}

function renderCard() {
  const c = state.card;
  const r = c.report;
  const box = document.getElementById("card");
  const waits = WAITS.has(c.state);
  const parts = [
    el("div", { class: "card-head" },
      el("span", {}, el("span", { class: "chip" + (waits ? " waits" : "") }, c.state), "  ", c.task,
        c.cost_usd ? `  $${c.cost_usd.toFixed(2)} est` : ""),
      el("button", { onclick: closeCard, title: "close (Esc)" }, "Close")),
    el("p", { class: "bottom" }, r.bottom),
    el("p", { class: "nla" }, "Not looked at: " + r.not_looked_at),
    actions(c),
    section("Changed since last time", r.sections["Changed since last time"]),
    section("Found", r.sections["Found"]),
    docs(c),
    r.sections["Details"] && r.sections["Details"].length
      ? el("details", {}, el("summary", {}, "Details"), el("ul", { class: "plain" }, r.sections["Details"].map(i => el("li", {}, i))))
      : null,
    el("p", { class: "next" }, "Next: " + r.next)];
  box.replaceChildren(...parts.flat(2).filter(Boolean));  // sections and docs come back as lists, or null
  box.hidden = false;
  fillDoc();  // a live refresh re-renders the card: keep the document you opened
}

function reasonBox(placeholder) {
  return el("textarea", { id: "reason", placeholder, "aria-label": "Your reason",
    onkeydown: e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitReason(); } } });
}

let pending = null;  // the action waiting for a reason: {kind, option}

function actions(c) {
  const a = c.actions;
  if (c.merge) {
    return el("div", { class: "decide" },
      el("p", { class: "question" }, "Merging is yours. Run this in your repo's folder:"),
      el("div", { class: "merge" }, el("code", {}, c.merge),
        el("button", { onclick: e => { navigator.clipboard.writeText(c.merge); e.target.textContent = "Copied"; } }, "Copy")));
  }
  if (a.kind === "ready") {
    return el("div", { class: "decide" },
      el("div", { class: "options" },
        el("button", { class: "good", onclick: accept, title: "accept (a)" }, "Accept"),
        el("button", { class: "bad", onclick: () => ask({ kind: "reject" }), title: "reject (r)" }, "Reject")),
      pending ? [reasonBox(pending.kind === "reject" ? "Why? The drafters redraft from this." : "Why?"),
        el("label", {}, el("input", { type: "checkbox", id: "drop" }), " drop it instead of redrafting")] : null,
      el("p", { class: "hint" }, "a accept, r reject with a reason, d the change, Esc close"));
  }
  if (a.kind === "decide") {
    return el("div", { class: "decide" },
      el("p", { class: "question" }, a.question),
      el("div", { class: "options" }, a.options.map((o, i) =>
        el("button", { class: (o.name === a.recommend ? "primary rec" : "") + (o.name === "drop" ? " bad" : ""),
          title: `${o.does} (${i + 1})`, onclick: () => choose(o) }, o.name))),
      el("ul", { class: "plain hint" }, a.options.map(o => el("li", {}, `${o.name}: ${o.does}`))),
      pending ? reasonBox(`Why ${pending.option}?`) : null,
      el("p", { class: "hint" }, "1 to " + a.options.length + " choose, d the change, Esc close"));
  }
  return null;
}

function docs(c) {
  const names = ["diff", ...c.docs];
  return [el("div", { class: "docs" }, names.map(n =>
      el("button", { "aria-pressed": state.doc === n ? "true" : "false", onclick: () => toggleDoc(n),
        title: n === "diff" ? "the change (d)" : n }, n === "diff" ? "The change" : n))),
    state.doc ? el("pre", { class: "doc", id: "doc" }) : null];
}

async function toggleDoc(name) {
  state.doc = state.doc === name ? null : name;
  state.docText = null;
  renderCard();
  if (!state.doc) return;
  const { text } = await api(`/api/task/${encodeURIComponent(state.open)}/doc/${name}`);
  if (state.doc !== name) return;  // you moved on while it loaded
  state.docText = text || "(nothing yet)";
  fillDoc();
}

function fillDoc() {
  const pre = document.getElementById("doc");
  if (!pre || state.docText === null) return;
  pre.replaceChildren(...state.docText.split("\n").map(l => {
    let cls = "";
    if (state.doc === "diff") {
      if (l.startsWith("@@")) cls = "hunk";
      else if (l.startsWith("+") && !l.startsWith("+++")) cls = "add";
      else if (l.startsWith("-") && !l.startsWith("---")) cls = "del";
    }
    return el("span", { class: cls }, l + "\n");
  }));
}

// ---------- actions ----------

async function run(path, body) {
  if (state.busy) return;
  state.busy = true;
  try {
    const out = await api(path, body);
    flash(out.merge ? `${out.message} ${out.merge}` : out.message);
    pending = null;
    await refresh();
  } catch (err) {
    flash(err.message, true);
  } finally {
    state.busy = false;
  }
}

function accept() {
  if (state.card && state.card.actions.kind === "ready") run("/api/accept", { task: state.open });
}

function ask(action) {
  pending = action;
  renderCard();
  const r = document.getElementById("reason");
  if (r) r.focus();
}

function choose(option) {
  if (option.needs_reason) return ask({ kind: "decide", option: option.name });
  run("/api/decide", { task: state.open, option: option.name });
}

function submitReason() {
  const r = document.getElementById("reason");
  const reason = r ? r.value.trim() : "";
  if (!reason) { flash("a reason is needed for this", true); return; }
  if (pending.kind === "reject") {
    const drop = document.getElementById("drop");
    run("/api/reject", { task: state.open, reason, drop: !!(drop && drop.checked) });
  } else {
    run("/api/decide", { task: state.open, option: pending.option, reason });
  }
}

// ---------- intake ----------

document.getElementById("intake").addEventListener("submit", async e => {
  e.preventDefault();
  const input = document.getElementById("work");
  const work = input.value.trim();
  if (!work) return;
  await run("/api/do", { work });
  input.value = "";
  input.blur();
});

// ---------- keyboard ----------

document.addEventListener("keydown", e => {
  const typing = ["INPUT", "TEXTAREA"].includes(document.activeElement.tagName);
  if (e.key === "Escape") {
    if (pending) { pending = null; renderCard(); }
    else if (typing) document.activeElement.blur();
    else if (state.open) closeCard();
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const tasks = allTasks();
  const at = tasks.findIndex(t => t.task === state.open);
  if (e.key === "/") { e.preventDefault(); document.getElementById("work").focus(); }
  else if (e.key === "j" || e.key === "k") {
    if (!tasks.length) return;
    const next = e.key === "j" ? Math.min(at + 1, tasks.length - 1) : Math.max(at - 1, 0);
    openCard(tasks[at < 0 ? 0 : next].task);
  }
  else if (e.key === "a") accept();
  else if (e.key === "r" && state.card && state.card.actions.kind === "ready") { e.preventDefault(); ask({ kind: "reject" }); }
  else if (e.key === "d" && state.open) toggleDoc("diff");
  else if (/^[1-9]$/.test(e.key) && state.card && state.card.actions.kind === "decide") {
    const o = state.card.actions.options[Number(e.key) - 1];
    if (o) choose(o);
  }
});

// ---------- live ----------

async function poll() {
  try {
    const { version } = await api("/api/version");
    if (version !== state.version) {
      state.version = version;
      await refresh();
    }
  } catch (err) { /* the server may be restarting; try again */ }
  setTimeout(poll, 2000);
}

state.token = location.hash.slice(1) || sessionStorage.getItem("parallax-token") || "";
if (location.hash) {
  sessionStorage.setItem("parallax-token", state.token);
  history.replaceState(null, "", location.pathname);  // the token leaves the address bar
}
if (!state.token) lock(); else poll();
