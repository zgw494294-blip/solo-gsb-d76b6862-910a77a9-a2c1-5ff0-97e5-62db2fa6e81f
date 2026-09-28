/* 闭环供水管网平衡台 — 原生 JS */
"use strict";

const state = {
  nodes: [],          // {id, type:'source'|'demand', fixed_head:'', demand:''}
  pipes: [],          // {id, from, to, r:''}
  result: null,
};

const W = 900, H = 520;
const pos = new Map();   // id -> {x,y,vx,vy,pinned}
const sim = { raf: null, alpha: 0 };

const $ = sel => document.querySelector(sel);
const nodeTbody = $("#node-table tbody");
const pipeTbody = $("#pipe-table tbody");
const svg = $("#graph");
const banner = $("#message");

/* ---------------- 工具 ---------------- */

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmt(x, digits) {
  if (!Number.isFinite(x)) return "—";
  const a = Math.abs(x);
  if (digits === undefined) digits = a === 0 ? 0 : a >= 1000 ? 2 : a >= 100 ? 3 : a >= 1 ? 4 : 6;
  return x.toFixed(digits);
}
function parseNum(v, name) {
  const t = String(v).trim();
  if (t === "") throw new Error(`请填写${name}`);
  const x = Number(t);
  if (!Number.isFinite(x)) throw new Error(`${name}必须是有限数值`);
  return x;
}
function showBanner(kind, text) {
  banner.hidden = false;
  banner.className = `banner ${kind}`;
  banner.textContent = text;
}
function hideBanner() { banner.hidden = true; }

function nodeById(id) { return state.nodes.find(n => n.id === id); }
function nodeIndex(id) { return state.nodes.findIndex(n => n.id === id); }

/* ---------------- 输入表格 ---------------- */

function renderNodeTable() {
  const sourceCount = state.nodes.filter(n => n.type === "source").length;
  nodeTbody.innerHTML = state.nodes.map((n, i) => `
    <tr>
      <td><input data-i="${i}" data-f="id" value="${esc(n.id)}"></td>
      <td>
        <select data-i="${i}" data-f="type">
          <option value="demand" ${n.type === "demand" ? "selected" : ""}>用水节点</option>
          <option value="source" ${n.type === "source" ? "selected" : ""}>水源</option>
        </select>
      </td>
      <td><input data-i="${i}" data-f="fixed_head" value="${esc(n.fixed_head ?? "")}"
            placeholder="${n.type === "source" ? "固定水头，如 50" : "仅水源填写"}"
            ${n.type !== "source" ? "disabled" : ""}></td>
      <td><input data-i="${i}" data-f="demand" value="${esc(n.demand ?? "")}"
            placeholder="${n.type === "demand" ? "需水量 ≥ 0" : "仅用水节点填写"}"
            ${n.type === "source" ? "disabled" : ""}></td>
      <td><button type="button" class="btn del" data-del-node="${i}"
            ${n.type === "source" && sourceCount === 1 ? "disabled title=\"至少保留一个水源\"" : ""}>删除</button></td>
    </tr>`).join("");
}

function optionList(selVal) {
  return state.nodes.map(n =>
    `<option value="${esc(n.id)}" ${n.id === selVal ? "selected" : ""}>${esc(n.id)}</option>`).join("");
}

function renderPipeTable() {
  pipeTbody.innerHTML = "";
  state.pipes.forEach((p, i) => {
    const tr = document.createElement("tr");

    const tdId = document.createElement("td");
    const inId = document.createElement("input");
    inId.dataset.pi = i; inId.dataset.pf = "id"; inId.value = p.id; inId.style.minWidth = "70px";
    tdId.appendChild(inId);

    const tdFrom = document.createElement("td");
    const selFrom = document.createElement("select");
    selFrom.dataset.pi = i; selFrom.dataset.pf = "from";
    selFrom.innerHTML = optionList(p.from);
    tdFrom.appendChild(selFrom);

    const tdTo = document.createElement("td");
    const selTo = document.createElement("select");
    selTo.dataset.pi = i; selTo.dataset.pf = "to";
    selTo.innerHTML = optionList(p.to);
    tdTo.appendChild(selTo);

    const tdR = document.createElement("td");
    const inR = document.createElement("input");
    inR.dataset.pi = i; inR.dataset.pf = "r"; inR.value = p.r;
    inR.inputMode = "decimal"; inR.placeholder = "r > 0";
    tdR.appendChild(inR);

    const tdDel = document.createElement("td");
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "btn del"; btn.dataset.delPipe = i;
    btn.textContent = "删除";
    tdDel.appendChild(btn);

    tr.append(tdId, tdFrom, tdTo, tdR, tdDel);
    pipeTbody.appendChild(tr);
  });
}

function renderTables() { renderNodeTable(); renderPipeTable(); }

nodeTbody.addEventListener("input", e => {
  const t = e.target;
  const i = Number(t.dataset.i), f = t.dataset.f;
  if (Number.isNaN(i) || !f) return;
  state.nodes[i][f] = t.value;
});
nodeTbody.addEventListener("change", e => {
  const t = e.target;
  const i = Number(t.dataset.i), f = t.dataset.f;
  if (Number.isNaN(i) || !f) return;
  if (f === "id") {
    const oldId = state.nodes[i].id;
    const newId = t.value.trim();
    state.nodes[i].id = newId;
    state.pipes.forEach(p => { if (p.from === oldId) p.from = newId; if (p.to === oldId) p.to = newId; });
    if (pos.has(oldId)) { pos.set(newId, pos.get(oldId)); pos.delete(oldId); }
    renderTables();
    renderGraph();
  } else if (f === "type") {
    state.nodes[i].type = t.value;
    renderTables();
    renderGraph();
  }
});
nodeTbody.addEventListener("click", e => {
  const btn = e.target.closest("[data-del-node]");
  if (!btn) return;
  const i = Number(btn.dataset.delNode);
  const id = state.nodes[i].id;
  state.nodes.splice(i, 1);
  for (let k = state.pipes.length - 1; k >= 0; k--) {
    if (state.pipes[k].from === id || state.pipes[k].to === id) state.pipes.splice(k, 1);
  }
  pos.delete(id);
  state.result = null; renderAll();
});

pipeTbody.addEventListener("input", e => {
  const t = e.target;
  const i = Number(t.dataset.pi), f = t.dataset.pf;
  if (Number.isNaN(i) || !f) return;
  state.pipes[i][f] = t.value;
});
pipeTbody.addEventListener("change", e => {
  const t = e.target;
  const i = Number(t.dataset.pi), f = t.dataset.pf;
  if (Number.isNaN(i) || !f) return;
  state.pipes[i][f] = t.value;
  if (f === "from" || f === "to") reheat(0.3);
  else renderGraph();
});
pipeTbody.addEventListener("click", e => {
  const btn = e.target.closest("[data-del-pipe]");
  if (!btn) return;
  state.pipes.splice(Number(btn.dataset.delPipe), 1);
  state.result = null; renderAll();
});

/* ---------------- 增删 / 示例 / 清空 ---------------- */

$("#add-node").addEventListener("click", () => {
  if (state.nodes.length >= 10) return showBanner("error", "节点数不能超过 10 个。");
  let k = state.nodes.length + 1, id;
  do { id = `N${k++}`; } while (nodeById(id));
  state.nodes.push({ id, type: "demand", fixed_head: "", demand: "" });
  hideBanner(); renderAll();
});
$("#add-pipe").addEventListener("click", () => {
  if (state.pipes.length >= 16) return showBanner("error", "管段数不能超过 16 根。");
  if (state.nodes.length < 2) return showBanner("error", "请先录入至少 2 个节点。");
  let k = state.pipes.length + 1, id;
  do { id = `P${k++}`; } while (state.pipes.some(p => p.id === id));
  state.pipes.push({ id, from: state.nodes[0].id, to: state.nodes[1].id, r: "" });
  hideBanner(); renderAll();
});

$("#load-sample").addEventListener("click", () => {
  state.nodes = [
    { id: "S", type: "source", fixed_head: "50", demand: "" },
    { id: "A", type: "demand", fixed_head: "", demand: "30" },
    { id: "B", type: "demand", fixed_head: "", demand: "20" },
    { id: "C", type: "demand", fixed_head: "", demand: "25" },
    { id: "D", type: "demand", fixed_head: "", demand: "15" },
  ];
  state.pipes = [
    { id: "P1", from: "S", to: "A", r: "0.008" },
    { id: "P2", from: "A", to: "B", r: "0.03" },
    { id: "P3", from: "B", to: "C", r: "0.05" },
    { id: "P4", from: "C", to: "D", r: "0.02" },
    { id: "P5", from: "D", to: "S", r: "0.02" },
    { id: "P6", from: "A", to: "D", r: "0.04" },
    { id: "P7", from: "S", to: "B", r: "0.03" },
  ];
  $("#min-head").value = "32";
  pos.clear();
  state.result = null;
  hideBanner(); renderAll(); reheat(1);
});

$("#clear-all").addEventListener("click", () => {
  state.nodes = []; state.pipes = []; state.result = null;
  pos.clear(); $("#min-head").value = "";
  hideBanner(); renderAll();
});

/* ---------------- 求解 ---------------- */

$("#solve").addEventListener("click", async () => {
  state.result = null;
  try {
    const ids = new Set();
    const nodes = state.nodes.map(n => {
      const id = n.id.trim();
      if (!id) throw new Error("存在未填写编号的节点。");
      if (ids.has(id)) throw new Error(`节点编号“${id}”重复。`);
      ids.add(id);
      const out = { id, type: n.type };
      if (n.type === "source") out.fixed_head = parseNum(n.fixed_head, `水源“${id}”的固定水头`);
      else out.demand = parseNum(n.demand, `用水节点“${id}”的需水量`);
      return out;
    });
    if (nodes.length < 2) throw new Error("至少录入 2 个节点。");
    if (nodes.filter(n => n.type === "source").length !== 1)
      throw new Error("必须恰有 1 个水源节点。");
    for (const n of nodes) if (n.type === "demand" && n.demand < 0)
      throw new Error(`用水节点“${n.id}”的需水量不能为负。`);

    const pidSeen = new Set();
    const pairSeen = new Set();
    const pipes = state.pipes.map(p => {
      const id = p.id.trim() || "(未命名)";
      if (pidSeen.has(id)) throw new Error(`管段编号“${id}”重复。`);
      pidSeen.add(id);
      const a = p.from, b = p.to;
      if (!ids.has(a) || !ids.has(b)) throw new Error(`管段“${id}”的端点不存在。`);
      if (a === b) throw new Error(`管段“${id}”的起终点不能相同。`);
      const key = [a, b].sort().join("|");
      if (pairSeen.has(key)) throw new Error(`管段“${id}”与另一根管段端点重复（不允许并联管）。`);
      pairSeen.add(key);
      const r = parseNum(p.r, `管段“${id}”的阻力 r`);
      if (r <= 0) throw new Error(`管段“${id}”的阻力必须为正数。`);
      return { id, from: a, to: b, r };
    });
    if (pipes.length < 1) throw new Error("至少录入 1 根管段。");

    const minRaw = $("#min-head").value.trim();
    const payload = { nodes, pipes };
    if (minRaw !== "") payload.min_head = parseNum(minRaw, "最低水头预警值");

    const resp = await fetch("/api/solve", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) throw new Error(data.error || `求解失败（HTTP ${resp.status}）`);

    state.result = data.result;
    renderResults();
    reheat(0.25);
  } catch (err) {
    renderResults();
    showBanner("error", err.message);
  }
});

/* ---------------- 结果展示 ---------------- */

function renderResults() {
  const r = state.result;
  ["summary-panel", "node-result-panel", "pipe-result-panel"].forEach(id => { $("#" + id).hidden = !r; });
  if (!r) { renderGraph(); return; }

  const lowText = r.min_head === null
    ? "未设置最低水头预警"
    : r.below_min_nodes.length
      ? `低于最低水头 ${fmt(r.min_head)} 的节点：${r.below_min_nodes.map(esc).join("、")}`
      : `所有节点水头均 ≥ ${fmt(r.min_head)}`;

  $("#summary").innerHTML = `
    <div class="card"><div class="k">水源总供水量</div><div class="v">${fmt(r.source_supply)}</div></div>
    <div class="card"><div class="k">总需水量</div><div class="v">${fmt(r.total_demand)}</div></div>
    <div class="card"><div class="k">节点最大守恒残差</div><div class="v">${r.max_node_residual.toExponential(2)}</div></div>
    <div class="card"><div class="k">管段最大压降残差</div><div class="v">${r.max_pipe_residual.toExponential(2)}</div></div>
    <div class="card ${r.below_min_nodes.length ? "bad" : ""}"><div class="k">最低水头校验（1e-6 容差）</div>
      <div class="v" style="font-size:14px">${esc(lowText)}</div></div>`;

  showBanner("ok",
    `求解收敛，已按 1×10⁻⁶ 容差通过校验：\n` +
    `• 节点质量守恒最大残差 ${r.max_node_residual.toExponential(3)}（节点 ${esc(r.worst_node)}）\n` +
    `• 逐段压降 H起−H终=r·q·|q| 最大残差 ${r.max_pipe_residual.toExponential(3)}（管段 ${esc(r.worst_pipe)}）\n` +
    `• 水源总供水量 ${fmt(r.source_supply)}，总需水量 ${fmt(r.total_demand)}，全局闭合差 ${fmt(r.supply_demand_closure)}\n` +
    (r.min_head !== null ? `• ${lowText}` : "• 未设置最低水头预警值。"));

  $("#node-result-table tbody").innerHTML = r.nodes.map(nd => `
    <tr>
      <td><strong>${esc(nd.id)}</strong></td>
      <td>${nd.type === "source" ? '<span class="tag src">水源</span>' : '<span class="tag dem">用水</span>'}</td>
      <td>${fmt(nd.head)}</td>
      <td>${nd.type === "source" ? "—" : fmt(nd.demand)}</td>
      <td>${fmt(nd.net_in)}</td>
      <td>${nd.residual.toExponential(2)}</td>
      <td>${nd.type === "source"
        ? '<span class="tag src">固定水头</span>'
        : nd.below_min ? '<span class="tag low">⚠ 低于最低水头</span>' : '<span class="tag dem">正常</span>'}</td>
    </tr>`).join("");

  $("#pipe-result-table tbody").innerHTML = r.pipes.map(p => {
    const zero = Math.abs(p.q) < 1e-9;
    return `
    <tr>
      <td><strong>${esc(p.id)}</strong></td>
      <td>${esc(p.from)} → ${esc(p.to)}</td>
      <td>${fmt(p.r)}</td>
      <td class="${p.q > 0 ? "num-pos" : p.q < 0 ? "num-neg" : ""}">${fmt(p.q)}</td>
      <td>${zero ? '<span class="tag zero">零流量</span>' : `${esc(p.flow_from)} → ${esc(p.flow_to)}`}</td>
      <td>${fmt(p.head_loss)}</td>
      <td>${p.residual.toExponential(2)}</td>
    </tr>`;
  }).join("");

  renderGraph();
}

/* ---------------- 网络图 ---------------- */

function ensurePositions() {
  const cx = W / 2, cy = H / 2, R = Math.min(W, H) * 0.32;
  state.nodes.forEach((n, i) => {
    if (!pos.has(n.id)) {
      const ang = (i / Math.max(state.nodes.length, 1)) * Math.PI * 2 - Math.PI / 2;
      pos.set(n.id, { x: cx + R * Math.cos(ang), y: cy + R * Math.sin(ang), vx: 0, vy: 0, pinned: false });
    }
  });
  for (const id of [...pos.keys()]) if (!nodeById(id)) pos.delete(id);
}

function stepSim() {
  if (sim.alpha < 0.02) { sim.raf = null; return; }
  ensurePositions();
  const ns = state.nodes.map(n => pos.get(n.id));

  // 节点斥力
  for (let i = 0; i < ns.length; i++) {
    for (let j = i + 1; j < ns.length; j++) {
      let dx = ns[i].x - ns[j].x, dy = ns[i].y - ns[j].y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 1) { dx = Math.random() - .5; dy = Math.random() - .5; d2 = dx * dx + dy * dy + 1; }
      const d = Math.sqrt(d2), f = 14000 / d2;
      const fx = f * dx / d, fy = f * dy / d;
      ns[i].vx += fx; ns[i].vy += fy;
      ns[j].vx -= fx; ns[j].vy -= fy;
    }
  }
  // 弹簧（管段）
  const L0 = 120, k = 0.02;
  for (const p of state.pipes) {
    const a = pos.get(p.from), b = pos.get(p.to);
    if (!a || !b) continue;
    const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
    const f = k * (d - L0);
    const fx = f * dx / d, fy = f * dy / d;
    a.vx += fx; a.vy += fy;
    b.vx -= fx; b.vy -= fy;
  }
  // 向心 + 积分
  for (const p of ns) {
    if (p.pinned) { p.vx = 0; p.vy = 0; continue; }
    p.vx += (W / 2 - p.x) * 0.004 * sim.alpha;
    p.vy += (H / 2 - p.y) * 0.004 * sim.alpha;
    p.vx *= 0.82; p.vy *= 0.82;
    p.x = Math.max(36, Math.min(W - 36, p.x + p.vx * sim.alpha));
    p.y = Math.max(36, Math.min(H - 36, p.y + p.vy * sim.alpha));
  }
  sim.alpha *= 0.975;
  renderGraph();
  sim.raf = requestAnimationFrame(stepSim);
}
function reheat(a) {
  ensurePositions();
  sim.alpha = Math.max(sim.alpha, a);
  if (!sim.raf) sim.raf = requestAnimationFrame(stepSim);
}

function svgPoint(evt) {
  const pt = new DOMPoint(evt.clientX, evt.clientY);
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}
let dragId = null;
svg.addEventListener("pointerdown", e => {
  const g = e.target.closest("[data-node]");
  if (!g) return;
  dragId = g.dataset.node;
  const p = pos.get(dragId);
  if (p) p.pinned = true;
  svg.setPointerCapture(e.pointerId);
  reheat(0.15);
});
svg.addEventListener("pointermove", e => {
  if (!dragId) return;
  const p = pos.get(dragId), m = svgPoint(e);
  p.x = Math.max(36, Math.min(W - 36, m.x));
  p.y = Math.max(36, Math.min(H - 36, m.y));
  p.vx = 0; p.vy = 0;
  renderGraph();
});
function endDrag(e) {
  if (!dragId) return;
  const p = pos.get(dragId);
  if (p) p.pinned = false;
  dragId = null;
  reheat(0.2);
}
svg.addEventListener("pointerup", endDrag);
svg.addEventListener("pointercancel", endDrag);

function renderGraph() {
  ensurePositions();
  if (!state.nodes.length) {
    svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="#90a4ae" font-size="15">请先录入节点与管段，再点击“求解稳态”</text>`;
    return;
  }

  const resPipes = new Map();
  let qmax = 0;
  if (state.result) for (const p of state.result.pipes) { resPipes.set(p.id, p); qmax = Math.max(qmax, Math.abs(p.q)); }

  const headOf = id => {
    if (!state.result) return null;
    const nd = state.result.nodes.find(x => x.id === id);
    return nd ? nd.head : null;
  };
  const isLow = id => {
    if (!state.result) return false;
    const nd = state.result.nodes.find(x => x.id === id);
    return nd && nd.below_min;
  };
  const isSource = id => nodeById(id)?.type === "source";

  let html = "";

  // 管段
  for (const p of state.pipes) {
    const a = pos.get(p.from), b = pos.get(p.to);
    if (!a || !b) continue;
    const rp = resPipes.get(p.id);
    const w = rp ? 1.5 + 4.5 * (qmax ? Math.abs(rp.q) / qmax : 0) : 2;
    const color = rp ? "#1565c0" : "#90a4ae";
    html += `<line x1="${a.x.toFixed(1)}" y1="${a.y.toFixed(1)}" x2="${b.x.toFixed(1)}" y2="${b.y.toFixed(1)}"
              stroke="${color}" stroke-width="${w.toFixed(2)}" stroke-linecap="round" opacity="0.85"/>`;

    // 流向三角（放在管长 62% 处，朝 flow_from -> flow_to）
    if (rp && Math.abs(rp.q) > 1e-9) {
      const fa = pos.get(rp.flow_from), tb = pos.get(rp.flow_to);
      const ux = (tb.x - fa.x), uy = (tb.y - fa.y);
      const L = Math.hypot(ux, uy) || 1, nx = ux / L, ny = uy / L;
      const cxp = fa.x + nx * L * 0.6, cyp = fa.y + ny * L * 0.6;
      const size = 7;
      const bx = cxp - nx * size * 1.4, by = cyp - ny * size * 1.4;
      const px2 = -ny, py2 = nx;
      html += `<polygon points="${cxp.toFixed(1)},${cyp.toFixed(1)} ${(bx + px2 * size * .7).toFixed(1)},${(by + py2 * size * .7).toFixed(1)} ${(bx - px2 * size * .7).toFixed(1)},${(by - py2 * size * .7).toFixed(1)}" fill="#1565c0"/>`;
    }

    // 流量 / 阻力标签
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const label = rp ? `${esc(rp.id)}: q=${fmt(rp.q)}` : `${esc(p.id)}: r=${esc(p.r)}`;
    const wlen = label.length * 6.6 + 8;
    html += `<rect x="${(mx - wlen / 2).toFixed(1)}" y="${(my - 10).toFixed(1)}" width="${wlen}" height="15" rx="3" fill="#ffffff" opacity="0.82"/>`;
    html += `<text x="${mx.toFixed(1)}" y="${my + 1.6}" text-anchor="middle" font-size="10.5" fill="#37474f">${label}</text>`;
  }

  // 节点
  for (const n of state.nodes) {
    const p = pos.get(n.id);
    const h = headOf(n.id);
    const low = isLow(n.id);
    const src = isSource(n.id);
    const fill = low ? "#e53935" : src ? "#1565c0" : "#607d8b";
    html += `<g data-node="${esc(n.id)}" style="cursor:grab">
      <circle cx="${p.x.toFixed(1)}" cy="${p.y.toFixed(1)}" r="20" fill="${fill}" stroke="#ffffff" stroke-width="2"/>
      <text x="${p.x.toFixed(1)}" y="${(p.y + 4.2).toFixed(1)}" text-anchor="middle" font-size="12" font-weight="700" fill="#fff">${esc(n.id)}</text>`;
    if (h !== null) {
      html += `<text x="${p.x.toFixed(1)}" y="${(p.y + 36).toFixed(1)}" text-anchor="middle" font-size="11" fill="${low ? "#c62828" : "#1f2933"}"
                 font-weight="${low ? 700 : 400}">H=${fmt(h)}</text>`;
    }
    html += `</g>`;
  }

  svg.innerHTML = html;
}

/* ---------------- 初始化 ---------------- */

function renderAll() {
  renderTables();
  renderResults();
}

renderAll();
reheat(1);
