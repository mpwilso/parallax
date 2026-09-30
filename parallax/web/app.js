// parallax ui: an intake box, a queue (what waits on you first), and one decision card.
// Everything agent-written is set as text, never as HTML. No inline code: the page's CSP forbids it.
"use strict";

const KINDS = {
  ready: "Ready", scope: "Outside the plan", guard: "Protected file", conflict: "Intent vs plan",
  cap: "Cap reached", rework: "Kept failing", checker: "Second Eye failed", tests: "Tests couldn't run", turns: "Out of turns",
  error: "Error", stuck: "Stopped", flows: "UI test or app?", drafting: "Drafting failed", launch: "Over your launch limit", review: "Plan review",
};
const READY_OPTIONS = [
  { name: "accept", does: "commits the reviewed change to its branch; you merge it by hand" },
  { name: "merge", does: "accepts, then fast-forwards your base branch here; never forces, never pushes" },
  { name: "reject", does: "Focus redrafts the intent and plan from your reason", needs_reason: true },
  { name: "drop", does: "ends the task; it leaves the inbox", needs_reason: true },
];
const SEND = { reject: "Reject and redraft", "send back": "Send it back", drop: "Drop it", accept: "Accept the risk", intent: "Redraft to the intent", remove: "Remove the test" };
const LABEL = { "send back": "Send back with a note", merge: "Accept and merge" };  // a button's words, where its name alone says too little
const slug = (name) => name.replace(/[^\w-]+/g, "-");  // an option's name as an id: "send back" -> "send-back"
const CHIP = { Working: "info", Ready: "good", "Needs you": "wait", Failed: "bad", "Can't display": "bad" };
const MARK = { done: "\u2713", working: "\u25CF", failed: "\u2715", skipped: "\u25CB" };  // check, dot, cross, ring
const SAID = { done: "done", working: "working", failed: "failed", skipped: "didn't run" };
const LIVE_EVERY = 15000;  // working lines carry a clock: refresh them even when nothing new happened

const state = {
  token: "", board: null, boardKey: "", open: null, card: null, cardKey: "",
  doc: null, docText: null, version: "", busy: false, pending: null, lastLive: 0,
  drafts: {},  // reasons you started typing, per task, kept until you send one
  answers: {}, // what you asked about each task, and the answers, this session
  seen: {},    // each agent's last state per task, so a stage that just finished hops once
};
const brand = { logo: null, agents: {} };  // rendered by the server from one data file; the page only places it
const SVG_NS = "http://www.w3.org/2000/svg";

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

// ---------- brand: the logo and the agents' portraits ----------
// Motion means status: a portrait moves only while its agent is working on that task, hops once
// when its stage finishes, and is still otherwise. With prefers-reduced-motion nothing moves.

function svgFrom(body, label) {
  const doc = new DOMParser().parseFromString(`<svg xmlns="${SVG_NS}" viewBox="0 0 16 16"><g class="bust">${body}</g></svg>`, "image/svg+xml");
  const svg = document.importNode(doc.documentElement, true);
  svg.setAttribute("shape-rendering", "crispEdges");
  svg.setAttribute("focusable", "false");
  if (label) { svg.setAttribute("role", "img"); svg.setAttribute("aria-label", label); } else svg.setAttribute("aria-hidden", "true");
  return svg;
}

function motionFor(prev, now) {
  if (now === "working") return "working";
  if (prev === "working") return "hop";
  return "still";
}
window.parallaxMotion = motionFor;  // the mapping the browser test checks

function portrait(key, now, prev) {
  const a = brand.agents[key];
  if (!a) return null;
  const m = motionFor(prev, now);
  const node = el("span", { class: "portrait " + m, "data-agent": key, title: `${a.name}, ${a.role}` },
    svgFrom(a.svg, `${a.name}, ${a.role}`));
  if (m === "hop") node.addEventListener("animationend", () => { node.classList.remove("hop"); node.classList.add("still"); }, { once: true });
  return node;
}

// One stage strip, on every row and card: Focus, Reticle, Maker, Check, Ready, each done, working,
// failed or didn't run. The working stage's agent sits in a fixed square tile; only it moves, and a
// stage that just finished hops once there before it goes still.
function tile(key, stages) {
  // who's in the tile: the agent working now; else the last one that worked, dimmed and still (it hops
  // once if it was working the last time this view drew). key keeps a row's memory apart from the card's.
  const prev = state.seen[key] || {};
  const w = stages.find(s => s.state === "working" && s.agent);
  const last = w || [...stages].reverse().find(s => s.agent && (s.state === "done" || s.state === "failed"));
  state.seen[key] = Object.fromEntries(stages.filter(s => s.agent).map(s => [s.agent, s.state]));
  if (!last) return el("span", { class: "tile empty" });
  return el("span", { class: "tile" + (w ? "" : " rest") }, portrait(last.agent, w ? "working" : "done", prev[last.agent]));
}

function strip(task, stages, withTile) {
  if (!stages || !stages.length) return null;
  const box = el("div", { class: "strip-wrap" }, withTile ? tile("card:" + task, stages) : null,
    el("ol", { class: "strip", "aria-label": "Stages" }, stages.map(s => el("li", { class: "st s-" + s.state, "data-stage": s.stage, "data-state": s.state },
      el("span", { class: "mark", "aria-hidden": "true" }, MARK[s.state] || ""), el("span", { class: "name" }, s.name),
      el("span", { class: "vh" }, `: ${SAID[s.state] || s.state}`)))));
  return box;
}

function spendBar(sp) {
  if (!sp) return null;
  if (!sp.cap) return sp.spent ? el("span", { class: "spend" }, `$${sp.spent.toFixed(2)} spent`) : null;
  return el("span", { class: "spend" + (sp.spent >= sp.cap * 0.9 ? " high" : "") },
    el("progress", { max: String(sp.cap), value: String(Math.min(sp.spent, sp.cap)), "aria-hidden": "true" }),
    `$${sp.spent.toFixed(2)} of $${sp.cap.toFixed(2)}`);
}

function elapsed(ts) {
  const t = Date.parse(ts);
  if (!ts || isNaN(t)) return "";
  const m = Math.max(0, Math.floor((Date.now() - t) / 60000));
  return m < 1 ? "just now" : m < 60 ? `${m}m` : m < 1440 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${Math.floor(m / 1440)}d`;
}

async function loadBrand() {
  try {
    const res = await fetch("/brand.json");
    const data = await res.json();
    brand.agents = data.agents || {};
    const doc = new DOMParser().parseFromString(`<svg xmlns="${SVG_NS}" viewBox="0 0 14 16">${data.logo}</svg>`, "image/svg+xml");
    const svg = document.importNode(doc.documentElement, true);
    svg.setAttribute("shape-rendering", "crispEdges");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    document.getElementById("logo").replaceChildren(svg);
  } catch (err) { /* the page works without its pictures */ }
}

// ---------- the queue ----------

function allTasks() {
  const b = state.board;
  return b ? [...b.waiting, ...b.working, ...b.done] : [];
}

function row(t) {
  const open = state.open === t.task;
  const done = !t.chip;
  const kind = t.secret ? "Secrets file" : KINDS[t.kind] || "";
  return el("li", {},
    el("button", { class: "row" + (open ? " open" : "") + (done ? " finished" : ""), "data-task": t.task, "data-focus": "task-" + t.task,
      "aria-current": open ? "true" : null, onclick: () => openCard(t.task, true) },
      done ? null : tile("row:" + t.task, t.strip || []),
      el("span", { class: "row-body" },
        el("span", { class: "row-top" }, el("span", { class: "title" }, t.title),
          done ? null : el("span", { class: "tag " + (t.secret ? "bad" : CHIP[t.chip] || "info"), title: kind || null }, t.secret ? "Secrets file" : t.chip)),
        done ? el("span", { class: "line" }, [t.outcome, t.date, `$${(t.cost_usd || 0).toFixed(2)}`,
          `${t.touches} touch${t.touches === 1 ? "" : "es"}`].filter(Boolean).join(" \u00B7 "))
          : [t.line ? el("span", { class: "line" }, t.line) : null, strip(t.task, t.strip, false),
             el("span", { class: "row-foot" }, spendBar(t.spend), t.started ? el("span", { class: "age" }, elapsed(t.started)) : null)])));
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
      b.waiting.length ? el("ol", {}, b.waiting.map(row))
        : el("p", { class: "empty" }, nothing
          ? "Nothing yet. Type the work above and press Enter. Agents draft, build and check it without you, and it comes back here when it needs a decision."
          : "Nothing waits on you.")),
    b.working.length ? el("section", { "aria-labelledby": "h-working" },
      el("h2", { id: "h-working" }, "Working"),
      el("ol", {}, b.working.map(row))) : null,
    b.done.length ? el("details", { class: "done", open: b.done.some(t => t.task === state.open) || null },
      el("summary", {}, el("h2", {}, "Done")),
      el("ol", {}, b.done.map(row))) : null].filter(Boolean));
  restoreFocus(key);
  renderIdle();
}

function renderIdle() {
  const idle = document.getElementById("idle");
  const b = state.board;
  idle.hidden = !!state.open || !b;
  if (!b) return;
  const lead = b.count ? `${b.count} waiting on you. Press n for the first one, or pick one from the list.`
    : b.working.length ? "Nothing waits on you. The work on the left runs without you." : "Nothing waits on you.";
  const parts = [el("p", { class: "lead" }, lead)];
  if ((b.overview || []).length) parts.push(el("h2", {}, "How it's going"), el("ul", { class: "plain overview" }, b.overview.map(l => el("li", {}, l))));
  idle.replaceChildren(...parts);
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
  const id = item.cite && item.cite.startsWith("ledger ") ? item.cite.slice(7) : null;
  return el("li", {}, item.text, " ", id
    ? el("a", { class: "cite", href: "#", "data-focus": "cite-" + id, title: "Open this ledger entry",
        onclick: e => { e.preventDefault(); toggleDoc("ledger:" + id); } }, item.cite)
    : item.cite ? el("span", { class: "cite" }, item.cite) : null);
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
  const chip = CHIP[c.chip] || (c.state === "ready" ? "good" : waits ? "wait" : c.state === "done" ? "plain" : "info");
  let bottom = c.report.bottom;
  for (const p of ["Needs you: ", "Ready again after your reject: ", "Ready: "]) if (bottom.startsWith(p)) bottom = cap(bottom.slice(p.length));
  box.replaceChildren(...[
    el("header", { class: "card-head" },
      el("button", { class: "back", onclick: closeCard, "data-focus": "back" }, "Back to tasks"),
      el("p", { class: "meta" }, el("span", { class: "tag " + chip }, c.chip || cap(c.state)), " ",
        el("span", { class: "mono" }, c.task), spendBar(c.spend)),
      el("h2", { id: "card-title", tabindex: "-1" }, c.title)),
    strip(c.task, c.strip, true),
    c.live ? el("p", { class: "live" }, c.live) : null,
    c.redraft ? el("p", { class: "redraft" }, "Redrafted after you rejected it") : null,
    el("p", { class: "bottom" }, bottom),
    c.redraft ? list("Since you rejected it", c.changed) : null,  // a redraft leads with what changed
    actions(c),
    list("Not looked at", c.unseen.length === 1 && c.unseen[0].text === "nothing" ? [] : c.unseen),
    c.redraft ? null : list("Changed since last time", c.changed),
    list("Found", c.found),
    shotsSection(c),
    askBox(c),
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

// the Ask box: a question about this task, answered from its record only; it can't change anything
function askBox(c) {
  const mine = state.answers[c.task] || [];
  const used = c.ask ? `$${c.ask.spent.toFixed(2)} of $${c.ask.budget.toFixed(2)} used on questions about this task.` : "";
  return el("section", { class: "ask", "aria-labelledby": "ask-h" },
    el("h3", { id: "ask-h" }, "Ask about this task"),
    mine.length ? el("ul", { class: "answers" }, mine.map(a => el("li", {},
      el("p", { class: "q" }, a.question), el("p", { class: "a" }, a.answer),
      a.sources && a.sources.length ? el("p", { class: "cite" }, "From: " + a.sources.join(", ")) : null))) : null,
    el("form", { class: "ask-form", onsubmit: e => { e.preventDefault(); askNow(c.task); } },
      el("label", { for: "ask", class: "vh" }, "Your question"),
      el("input", { id: "ask", "data-focus": "ask", placeholder: "Why did the check fail? What did Reticle test?", autocomplete: "off" }),
      el("button", { type: "submit", "data-focus": "ask-send" }, "Ask")),
    el("p", { class: "hint" }, "Answers come from this task's record only, and can't change or start anything. ", used));
}

async function askNow(task) {
  const input = document.getElementById("ask");
  const question = input ? input.value.trim() : "";
  if (!question) { if (input) input.focus(); return; }
  const out = await run("/api/ask", { task, question }, true);
  if (!out) return;
  (state.answers[task] = state.answers[task] || []).push({ question, answer: out.answer, sources: out.sources });
  state.cardKey = "";
  await loadCard(task);
  const again = document.getElementById("ask");
  if (again) again.focus();
}

// what the UI tester saw: images need the token, so they come as blobs (the CSP allows blob: images only)
const shotURLs = {};
function shotsSection(c) {
  if (!c.shots || !c.shots.length) return null;
  return [el("h3", {}, "What Field, the UI tester, saw"),
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
      el("p", { class: "question" }, c.merge_note || "Accepted. Merging is yours: run this in your repo's folder."),
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
    a.kind === "decide" && a.owner ? el("p", { class: "whose" }, `Whose call: ${a.owner}. Why a human: ${a.why_human}.`) : null,
    filesTable(c.files),
    el("ol", { class: "options" }, options.map((o, i) => el("li", {},
      el("button", { id: "opt-" + slug(o.name), "data-focus": "opt-" + slug(o.name),
        class: (o.name === (recommend || "accept") ? "primary" : "") + (o.name === "drop" ? " danger" : "") + (p && p.option === o.name ? " picked" : ""),
        "aria-describedby": "does-" + slug(o.name), "aria-expanded": o.needs_reason ? String(!!(p && p.option === o.name)) : null,
        onclick: () => choose(c, o) }, LABEL[o.name] || cap(o.name)),
      el("span", { class: "does", id: "does-" + slug(o.name) },
        o.does, o.needs_reason ? el("span", { class: "hint" }, " Needs a reason.") : null,
        o.name === recommend ? el("span", { class: "tag rec" }, "Recommended") : null),
      el("kbd", { "aria-hidden": "true" }, o.name === "accept" && a.kind === "ready" ? "a" : String(i + 1))))),
    p ? el("div", { class: "reason" },
      el("label", { for: "reason" }, p.option === "reject" ? "Why? Focus redrafts from this." : `Why ${p.option}?`),
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
  const label = n => n === "diff" ? "The change" : n.startsWith("ledger:") ? `Ledger entry ${n.slice(7)}` : cap(n);
  return [el("div", { class: "docs", role: "group", "aria-label": "Documents" }, names.map(n =>
      el("button", { "aria-pressed": state.doc === n ? "true" : "false", "data-focus": "doc-" + n,
        onclick: () => toggleDoc(n) }, label(n), n === "diff" ? el("kbd", { "aria-hidden": "true" }, "d") : null))),
    state.doc ? [state.doc.startsWith("ledger:") ? el("p", { class: "hint" }, label(state.doc)) : null,
      el("pre", { class: "doc", id: "doc", tabindex: "0", "aria-label": label(state.doc) })] : null];
}

async function toggleDoc(name) {
  state.doc = state.doc === name ? null : name;
  state.docText = null;
  renderCard();
  if (!state.doc) return;
  try {
    const path = name.startsWith("ledger:") ? `ledger/${encodeURIComponent(name.slice(7))}` : `doc/${name}`;
    const { text } = await api(`/api/task/${encodeURIComponent(state.open)}/${path}`);
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

// n opens the next task that waits on you. The page's copy of the board can be up to a poll behind
// the ledger (a task that just went Ready still sits under Working here), so when it shows nothing
// waiting, fetch the board first and look again, rather than dropping the key.
async function openNext() {
  let t = nextWaiting(state.open);
  if (!t) { await refresh(); t = nextWaiting(state.open); }
  if (t) openCard(t, true);
}

async function run(path, body, quiet) {
  if (state.busy) return false;
  state.busy = true;
  document.body.classList.add("busy");
  try {
    const out = await api(path, body);
    if (!quiet) say(out.merge ? `${out.message} ${out.merge}` : out.message);
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
  const out = c.actions.kind !== "ready" ? await run("/api/decide", { task: c.task, option: o.name })
    : await run("/api/accept", o.name === "merge" ? { task: c.task, merge: true } : { task: c.task });
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
  const b = option && document.getElementById("opt-" + slug(option));
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
  else if (e.key === "n") openNext();
  else if (e.key === "a" && c && c.actions.kind === "ready") { const b = document.getElementById("opt-accept"); if (b) { e.preventDefault(); b.focus(); } }
  else if (e.key === "r" && c && c.actions.kind === "ready") { e.preventDefault(); choose(c, READY_OPTIONS.find(o => o.name === "reject")); }
  else if (e.key === "d" && c && c.has_change) toggleDoc("diff");
  else if (/^[1-9]$/.test(e.key) && c && (c.actions.kind === "decide" || c.actions.kind === "ready")) {
    const opts = c.actions.kind === "ready" ? READY_OPTIONS : c.actions.options;
    const o = opts[Number(e.key) - 1];
    const b = o && document.getElementById("opt-" + slug(o.name));
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
if (!state.token) lock(); else { loadBrand(); poll(); }
