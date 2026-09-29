// parallax ui: an intake box, a queue (what waits on you first), and one decision card.
// Everything agent-written is set as text, never as HTML. No inline code: the page's CSP forbids it.
"use strict";

const KINDS = {
  ready: "Ready", scope: "Outside the plan", guard: "Protected file", conflict: "Intent vs plan",
  cap: "Cap reached", rework: "Kept failing", checker: "Checker failed", tests: "Tests couldn't run",
  error: "Error", stuck: "Stopped", drafting: "Drafting failed", launch: "Over your launch limit", review: "Plan review",
};
const DONE = { accepted: "accepted, the merge is yours", merged: "merged", rejected: "dropped", stopped: "stopped", closed: "closed" };
const READY_OPTIONS = [
  { name: "accept", does: "commits the reviewed change to its branch; merging stays yours" },
  { name: "reject", does: "the drafters redraft the intent and plan from your reason", needs_reason: true },
  { name: "drop", does: "ends the task; it leaves the queue", needs_reason: true },
];
const SEND = { reject: "Reject and redraft", drop: "Drop it", accept: "Accept the risk", intent: "Redraft to the intent" };
const LIVE_EVERY = 15000;  // working lines carry a clock: refresh them even when nothing new happened

const state = {
  token: "", board: null, boardKey: "", open: null, card: null, cardKey: "",
  doc: null, docText: null, version: "", busy: false, pending: null, lastLive: 0,
  drafts: {},  // reasons you started typing, per task, kept until you send one
};

// ---------- plumbing ----------

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(3)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

const cap = s => s.charAt(0).toUpperCase() + s.slice(1);

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

let statusTimer = null;
function say(text, error) {
  const s = document.getElementById("status");
  clearTimeout(statusTimer);
  s.textContent = text || "";
  s.className = "status" + (error ? " error" : "") + (text ? " shown" : "");
  if (text && !error) statusTimer = setTimeout(() => say(""), 8000);
}

function lock() {
  for (const id of ["app", "intake", "status", "offline"]) document.getElementById(id).hidden = true;
  document.getElementById("locked").hidden = false;
}

// keep what you were doing across a live refresh: focus, and a reason half typed
function focusKey() {
  const a = document.activeElement;
  return a && a.dataset ? a.dataset.focus || null : null;
}
function restoreFocus(key) {
  if (!key) return;
  const n = document.querySelector(`[data-focus="${CSS.escape(key)}"]`);
  if (n && document.activeElement !== n) n.focus({ preventScroll: true });
}

// ---------- the queue ----------

function allTasks() {
  const b = state.board;
  return b ? [...b.waiting, ...b.working, ...b.done] : [];
}

function row(t, line, tag) {
  const open = state.open === t.task;
  return el("li", {},
    el("button", { class: "row" + (open ? " open" : ""), "data-task": t.task, "data-focus": "task-" + t.task,
      "aria-current": open ? "true" : null, onclick: () => openCard(t.task, true) },
      el("span", { class: "row-top" }, el("span", { class: "title" }, t.title), tag),
      line ? el("span", { class: "line" }, line) : null));
}

function kindTag(t) {
  if (t.secret) return el("span", { class: "tag bad" }, "Secrets file");
  return el("span", { class: "tag " + (t.kind === "ready" ? "good" : "wait") }, KINDS[t.kind] || "Needs you");
}

function renderQueue() {
  const b = state.board;
  document.getElementById("project").textContent = b.project;
  const count = document.getElementById("count");
  count.hidden = !b.count;
  count.textContent = `${b.count} waiting on you`;
  document.title = b.count ? `(${b.count}) Parallax` : "Parallax";
  const key = focusKey();
  const nothing = !b.waiting.length && !b.working.length && !b.done.length;
  document.getElementById("queue").replaceChildren(...[
    el("section", { "aria-labelledby": "h-waiting" },
      el("h2", { id: "h-waiting" }, "Waiting on you"),
      b.waiting.length ? el("ol", {}, b.waiting.map(t => row(t, t.line, kindTag(t))))
        : el("p", { class: "empty" }, nothing
          ? "Nothing yet. Type the work above and press Enter. Agents draft, build and check it without you, and it comes back here when it needs a decision."
          : "Nothing waits on you.")),
    b.working.length ? el("section", { "aria-labelledby": "h-working" },
      el("h2", { id: "h-working" }, "Working"),
      el("ol", {}, b.working.map(t => row(t, t.line, null)))) : null,
    b.done.length ? el("details", { class: "done", open: b.done.some(t => t.task === state.open) || null },
      el("summary", {}, el("h2", {}, "Done")),
      el("ol", {}, b.done.map(t => row(t, DONE[t.status] || t.status, null)))) : null].filter(Boolean));
  restoreFocus(key);
  renderIdle();
}

function renderIdle() {
  const idle = document.getElementById("idle");
  const b = state.board;
  idle.hidden = !!state.open || !b;
  if (!b) return;
  idle.textContent = b.count ? `${b.count} waiting on you. Press n for the first one, or pick one from the list.`
    : b.working.length ? "Nothing waits on you. The work on the left runs without you." : "";
  if (!idle.textContent) idle.hidden = true;
}

async function refresh() {
  try {
    const board = await api("/api/board");
    const key = JSON.stringify(board);
    state.board = board;
    if (key !== state.boardKey) { state.boardKey = key; renderQueue(); }
    if (state.open) await loadCard(state.open);
  } catch (err) {
    say(err.message, true);
  }
}

// ---------- the card ----------

async function openCard(task, focusTitle) {
  if (state.open !== task) {
    state.doc = null;
    state.docText = null;
    if (!state.pending || state.pending.task !== task) state.pending = null;
  }
  state.open = task;
  state.cardKey = "";
  await loadCard(task);
  state.boardKey = "";
  renderQueue();
  if (!state.card || state.card.task !== task) return;  // it didn't load: the list stays, the error says why
  document.body.classList.add("card-open");
  if (focusTitle) {
    const h = document.getElementById("card-title");
    if (h) h.focus();
    window.scrollTo(0, 0);
  }
}

function closeCard() {
  const was = state.open;
  state.open = null;
  state.card = null;
  state.pending = null;
  document.getElementById("card").hidden = true;
  document.body.classList.remove("card-open");
  state.boardKey = "";
  renderQueue();
  const r = was && document.querySelector(`[data-focus="task-${CSS.escape(was)}"]`);
  if (r) r.focus();
}

async function loadCard(task) {
  let card;
  try {
    card = await api(`/api/task/${encodeURIComponent(task)}`);
  } catch (err) {
    say(err.message, true);
    if (!state.card || state.card.task !== task) { state.open = null; document.getElementById("card").hidden = true; }
    return;
  }
  if (state.open !== task) return;  // you moved on while it loaded
  const key = JSON.stringify(card);
  if (key === state.cardKey) return;
  state.cardKey = key;
  state.card = card;
  renderCard();
}

function cited(item) {
  return el("li", {}, item.text, item.cite ? el("span", { class: "cite" }, " ", item.cite) : null);
}

function list(title, items) {
  if (!items || !items.length) return null;
  return [el("h3", {}, title), el("ul", { class: "plain" }, items.map(cited))];
}

function renderCard() {
  const c = state.card;
  const box = document.getElementById("card");
  const key = focusKey();
  const waits = c.state === "ready" || c.state === "needs you";
  const chip = c.state === "ready" ? "good" : waits ? "wait" : c.state === "done" ? "plain" : "info";
  let bottom = c.report.bottom;
  for (const p of ["Needs you: ", "Ready: "]) if (bottom.startsWith(p)) bottom = cap(bottom.slice(p.length));
  box.replaceChildren(...[
    el("header", { class: "card-head" },
      el("button", { class: "back", onclick: closeCard, "data-focus": "back" }, "Back to tasks"),
      el("p", { class: "meta" }, el("span", { class: "tag " + chip }, cap(c.state)), " ",
        el("span", { class: "mono" }, c.task), c.cost_usd ? ` $${c.cost_usd.toFixed(2)} spent` : ""),
      el("h2", { id: "card-title", tabindex: "-1" }, c.title)),
    c.live ? el("p", { class: "live" }, c.live) : null,
    el("p", { class: "bottom" }, bottom),
    actions(c),
    list("Not looked at", c.unseen.length === 1 && c.unseen[0].text === "nothing" ? [] : c.unseen),
    list("Changed since last time", c.changed),
    list("Found", c.found),
    shotsSection(c),
    docs(c),
    c.details.length ? el("details", { class: "more" }, el("summary", {}, "Details"),
      el("ul", { class: "plain" }, c.details.map(cited))) : null,
  ].flat(2).filter(Boolean));
  box.hidden = false;
  fillDoc();
  restoreFocus(key);
  const r = document.getElementById("reason");
  if (r && state.pending) r.value = state.pending.text || "";
}

// what the UI tester saw: images need the token, so they come as blobs (the CSP allows blob: images only)
const shotURLs = {};
function shotsSection(c) {
  if (!c.shots || !c.shots.length) return null;
  return [el("h3", {}, "What the UI tester saw"),
    el("div", { class: "shots" }, c.shots.map(s => {
      const img = el("img", { alt: s.caption, loading: "lazy" });
      loadShot(img, c.task, s.name);
      return el("figure", { class: s.works === false ? "broken" : null }, img,
        el("figcaption", {}, s.works === false ? el("span", { class: "tag bad" }, "Didn't work") : null, " ", s.caption));
    }))];
}

async function loadShot(img, task, name) {
  const key = task + "/" + name;
  if (!shotURLs[key]) {
    try {
      const res = await fetch(`/api/task/${encodeURIComponent(task)}/shot/${encodeURIComponent(name)}`,
        { headers: { "X-Parallax-Token": state.token } });
      if (!res.ok) return;
      shotURLs[key] = URL.createObjectURL(await res.blob());
    } catch (err) { return; }
  }
  img.src = shotURLs[key];
}

function filesTable(files) {
  if (!files || !files.length) return null;
  const why = { protected: "protected path", outside: "not in the plan", binary: "unlisted binary",
    symlink: "unlisted symlink", dependency: "unlisted dependency" };
  const size = n => n === null || n === undefined ? "gone" : n === 0 ? "empty" : `${n} bytes`;
  return el("table", { class: "files" },
    el("thead", {}, el("tr", {}, el("th", { scope: "col" }, "File"), el("th", { scope: "col" }, "Why"), el("th", { scope: "col" }, "Size"))),
    el("tbody", {}, files.map(f => el("tr", { class: f.secret && f.size ? "risky" : null },
      el("td", { class: "mono" }, f.path), el("td", {}, why[f.cause] || f.cause),
      el("td", {}, f.secret && f.size ? `has content, ${size(f.size)}` : size(f.size))))));
}

function actions(c) {
  if (c.merge) {
    return el("section", { class: "decide", "aria-label": "Merge" },
      el("p", { class: "question" }, "Accepted. Merging is yours: run this in your repo's folder."),
      el("div", { class: "merge" }, el("code", { id: "merge" }, c.merge),
        el("button", { "data-focus": "copy", onclick: copy }, "Copy")));
  }
  const a = c.actions;
  let options, question, recommend;
  if (a.kind === "ready") { options = READY_OPTIONS; question = "Accept it, or send it back?"; recommend = null; }
  else if (a.kind === "decide") { options = a.options; question = a.question; recommend = a.recommend; }
  else return null;
  const p = state.pending && state.pending.task === c.task ? state.pending : null;
  return el("section", { class: "decide", "aria-label": "Your decision" },
    el("p", { class: "question" }, question),
    filesTable(c.files),
    el("ol", { class: "options" }, options.map((o, i) => el("li", {},
      el("button", { id: "opt-" + o.name, "data-focus": "opt-" + o.name,
        class: (o.name === (recommend || "accept") ? "primary" : "") + (o.name === "drop" ? " danger" : "") + (p && p.option === o.name ? " picked" : ""),
        "aria-describedby": "does-" + o.name, "aria-expanded": o.needs_reason ? String(!!(p && p.option === o.name)) : null,
        onclick: () => choose(c, o) }, cap(o.name)),
      el("span", { class: "does", id: "does-" + o.name },
        o.does, o.needs_reason ? el("span", { class: "hint" }, " Needs a reason.") : null,
        o.name === recommend ? el("span", { class: "tag rec" }, "Recommended") : null),
      el("kbd", { "aria-hidden": "true" }, o.name === "accept" && a.kind === "ready" ? "a" : String(i + 1))))),
    p ? el("div", { class: "reason" },
      el("label", { for: "reason" }, p.option === "reject" ? "Why? The drafters redraft from this." : `Why ${p.option}?`),
      el("textarea", { id: "reason", "data-focus": "reason", rows: "3",
        oninput: e => { p.text = e.target.value; state.drafts[p.task] = p.text; },
        onkeydown: e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } } }),
      el("div", { class: "reason-actions" },
        el("button", { class: p.option === "drop" ? "danger" : "primary", "data-focus": "send", onclick: send }, SEND[p.option] || cap(p.option)),
        el("button", { "data-focus": "cancel", onclick: cancel }, "Cancel"),
        el("span", { class: "hint" }, "Enter sends, Shift+Enter adds a line, Esc closes it and keeps your text."))) : null);
}

function docs(c) {
  const names = [...(c.has_change ? ["diff"] : []), ...c.docs];
  if (!names.length) return null;
  const label = n => n === "diff" ? "The change" : cap(n);
  return [el("div", { class: "docs", role: "group", "aria-label": "Documents" }, names.map(n =>
      el("button", { "aria-pressed": state.doc === n ? "true" : "false", "data-focus": "doc-" + n,
        onclick: () => toggleDoc(n) }, label(n), n === "diff" ? el("kbd", { "aria-hidden": "true" }, "d") : null))),
    state.doc ? el("pre", { class: "doc", id: "doc", tabindex: "0", "aria-label": label(state.doc) }) : null];
}

async function toggleDoc(name) {
  state.doc = state.doc === name ? null : name;
  state.docText = null;
  renderCard();
  if (!state.doc) return;
  try {
    const { text } = await api(`/api/task/${encodeURIComponent(state.open)}/doc/${name}`);
    if (state.doc !== name) return;
    state.docText = text || "(nothing yet)";
    fillDoc();
  } catch (err) { say(err.message, true); }
}

function fillDoc() {
  const pre = document.getElementById("doc");
  if (!pre || state.docText === null) return;
  pre.replaceChildren(...state.docText.split("\n").map(l => {
    let cls = null;
    if (state.doc === "diff") {
      if (l.startsWith("@@")) cls = "hunk";
      else if (l.startsWith("+") && !l.startsWith("+++")) cls = "add";
      else if (l.startsWith("-") && !l.startsWith("---")) cls = "del";
    }
    return el("span", { class: cls }, l + "\n");
  }));
}

async function copy(e) {
  const b = e.currentTarget;
  try {
    await navigator.clipboard.writeText(state.card.merge);
    b.textContent = "Copied";
  } catch (err) {
    getSelection().selectAllChildren(document.getElementById("merge"));
    b.textContent = "Press Ctrl+C";
  }
  setTimeout(() => { b.textContent = "Copy"; }, 2000);
}

// ---------- deciding ----------

function nextWaiting(after) {
  const w = state.board ? state.board.waiting.filter(t => t.task !== after) : [];
  return w.length ? w[0].task : null;
}

async function run(path, body) {
  if (state.busy) return false;
  state.busy = true;
  document.body.classList.add("busy");
  try {
    const out = await api(path, body);
    say(out.merge ? `${out.message} ${out.merge}` : out.message);
    return out;
  } catch (err) {
    say(err.message, true);
    return false;
  } finally {
    state.busy = false;
    document.body.classList.remove("busy");
  }
}

async function acted(task, stay) {
  state.pending = null;
  state.cardKey = "";
  await refresh();
  if (stay) return;
  const next = nextWaiting(task);
  if (next) openCard(next, true); else closeCard();
}

async function choose(c, o) {
  if (o.needs_reason) {
    state.pending = { task: c.task, option: o.name, text: state.drafts[c.task] || "" };
    renderCard();
    document.getElementById("reason").focus();
    return;
  }
  const out = c.actions.kind === "ready" ? await run("/api/accept", { task: c.task })
    : await run("/api/decide", { task: c.task, option: o.name });
  if (out) await acted(c.task, !!out.merge);  // after accept, stay: the merge command is on the card
}

async function send() {
  const p = state.pending;
  const r = document.getElementById("reason");
  const reason = r ? r.value.trim() : "";
  if (!p) return;
  if (!reason) { say("This needs a reason: the next step is based on it.", true); if (r) r.focus(); return; }
  const c = state.card;
  const out = c.actions.kind === "ready"
    ? await run("/api/reject", { task: p.task, reason, drop: p.option === "drop" })
    : await run("/api/decide", { task: p.task, option: p.option, reason });
  if (out) { delete state.drafts[p.task]; await acted(p.task, false); }
}

function cancel() {
  const option = state.pending && state.pending.option;
  state.pending = null;  // what you typed stays in drafts for next time
  renderCard();
  const b = option && document.getElementById("opt-" + option);
  if (b) b.focus();
}

// ---------- intake ----------

document.getElementById("intake").addEventListener("submit", async e => {
  e.preventDefault();
  const input = document.getElementById("work");
  const work = input.value.trim();
  if (!work) return;
  const out = await run("/api/do", { work });
  if (!out) return;  // keep what you typed
  input.value = "";
  state.version = "";
  await refresh();
});

// ---------- keyboard ----------
// Keys only move you or open things. Nothing is decided by one key: a and 1 to 9 move to a
// button, and Enter presses it. Keys are off while you type.

document.addEventListener("keydown", e => {
  const a = document.activeElement;
  const typing = a && (a.tagName === "TEXTAREA" || (a.tagName === "INPUT" && a.type !== "checkbox"));
  if (e.key === "Escape") {
    if (state.pending) { e.preventDefault(); cancel(); }
    else if (typing) a.blur();
    else if (state.open) closeCard();
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const tasks = allTasks();
  const at = tasks.findIndex(t => t.task === state.open);
  const c = state.card;
  if (e.key === "/") { e.preventDefault(); document.getElementById("work").focus(); }
  else if (e.key === "j" || e.key === "k") {
    if (!tasks.length) return;
    const to = at < 0 ? 0 : e.key === "j" ? Math.min(at + 1, tasks.length - 1) : Math.max(at - 1, 0);
    openCard(tasks[to].task, true);
  }
  else if (e.key === "n") { const t = nextWaiting(state.open); if (t) openCard(t, true); }
  else if (e.key === "a" && c && c.actions.kind === "ready") { const b = document.getElementById("opt-accept"); if (b) { e.preventDefault(); b.focus(); } }
  else if (e.key === "r" && c && c.actions.kind === "ready") { e.preventDefault(); choose(c, READY_OPTIONS[1]); }
  else if (e.key === "d" && c && c.has_change) toggleDoc("diff");
  else if (/^[1-9]$/.test(e.key) && c && (c.actions.kind === "decide" || c.actions.kind === "ready")) {
    const opts = c.actions.kind === "ready" ? READY_OPTIONS : c.actions.options;
    const o = opts[Number(e.key) - 1];
    const b = o && document.getElementById("opt-" + o.name);
    if (b) { e.preventDefault(); b.focus(); }
  }
});

// ---------- live ----------

async function poll() {
  try {
    const { version } = await api("/api/version");
    document.getElementById("offline").hidden = true;
    const working = state.board && state.board.working.length;
    if (version !== state.version || (working && Date.now() - state.lastLive > LIVE_EVERY)) {
      state.version = version;
      state.lastLive = Date.now();
      await refresh();
    }
  } catch (err) {
    if (!document.getElementById("locked").hidden) return;  // locked: nothing to poll for
    document.getElementById("offline").hidden = false;
  }
  setTimeout(poll, 2000);
}

// the link stays in the address bar so you can bookmark it; the fragment never reaches a server
state.token = location.hash.slice(1) || sessionStorage.getItem("parallax-token") || "";
if (location.hash) sessionStorage.setItem("parallax-token", state.token);
if (!state.token) lock(); else poll();
