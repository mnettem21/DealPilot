const STAGES = ["Intake", "Credit", "Committee", "Product", "Committed", "Closed"];

const state = {
  summary: null,
  requests: [],
  selectedDeal: null,
  user: null,
  role: null,
  patterns: [],
  applications: [],
  currentApplication: null,
  currentStep: 1,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}

function escapeHtml(value) {
  return String(value ?? "—").replace(/[&<>'"]/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;"}[char]));
}

function money(value) {
  return value == null ? "—" : new Intl.NumberFormat("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0}).format(value);
}

function shortDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-US", {weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}).format(new Date(value));
}

function statusTone(value) {
  if (["CLOSED", "READY", "READY_TO_CLOSE"].includes(value)) return "green";
  if (["HELD_FRAUD", "DECLINED"].includes(value)) return "red";
  if (["WAITING_HUMAN", "WAITING_DOCUMENTS"].includes(value)) return "amber";
  return "blue";
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.className = "toast", 2800);
}

async function refresh() {
  try {
    const [summary, requests, patterns] = await Promise.all([api("/api/summary"), api("/api/human-requests"), api("/api/fraud-patterns")]);
    state.summary = summary;
    state.requests = requests;
    state.patterns = patterns;
    render();
    if (state.selectedDeal) await openDeal(state.selectedDeal, false);
  } catch (error) {
    showToast(error.message, true);
  }
}

function render() {
  const summary = state.summary;
  $("#sim-time").textContent = shortDate(summary.now);
  $("#nav-inbox-count").textContent = summary.waiting_human;
  renderMetrics(summary);
  renderPriority(summary.deals);
  renderReadiness(summary.deals);
  renderPipeline(summary.deals);
  renderDealTable(summary.deals);
  renderInbox(state.requests);
  renderFraud(summary.deals);
  renderOperations(summary);
}

function renderMetrics(summary) {
  const metrics = [
    ["Active deals", summary.total_deals, "applications received", "◎"],
    ["Awaiting humans", summary.waiting_human, "decisions required", "✓"],
    ["Fraud flagged", summary.flagged, `${summary.held} currently held`, "⌘"],
    ["SLA alerts", summary.alerts, "operational attention", "!"],
    ["Safely closed", summary.closed, "funding confirmed", "↗"],
  ];
  $("#metrics").innerHTML = metrics.map(([label, value, note, icon]) => `
    <article class="metric">
      <div class="metric-header"><span>${label}</span><i class="metric-icon">${icon}</i></div>
      <strong>${value}</strong><small>${note}</small>
    </article>`).join("");
}

function priorityScore(deal) {
  let score = 0;
  if (deal.operational_state === "HELD_FRAUD") score += 100;
  if (deal.fraud.flagged) score += 75;
  if (deal.operational_state === "WAITING_HUMAN") score += 45;
  if (deal.operational_state === "WAITING_DOCUMENTS") score += 25;
  if (deal.needed_by) score += Math.max(0, 80 - (new Date(deal.needed_by) - new Date(state.summary.now)) / 36e5);
  return score;
}

function nextAction(deal) {
  if (deal.operational_state === "HELD_FRAUD") return "Fraud investigation";
  if (deal.fraud.flagged && deal.operational_state === "WAITING_HUMAN") return "Review fraud hold";
  if (deal.operational_state === "WAITING_DOCUMENTS") return deal.current_blockers[0] || "Resolve missing evidence";
  if (deal.operational_state === "WAITING_HUMAN") return `Complete ${deal.stage} decision`;
  if (deal.operational_state === "READY_TO_CLOSE") return "Release funding";
  if (deal.operational_state === "CLOSED") return "Funding confirmed";
  return `Continue ${deal.stage}`;
}

function renderPriority(deals) {
  const element = $("#priority-list");
  if (!deals.length) {
    element.className = "priority-list empty-state";
    element.textContent = "Advance the clock to receive documents.";
    return;
  }
  element.className = "priority-list";
  element.innerHTML = [...deals].sort((a,b) => priorityScore(b) - priorityScore(a)).slice(0, 6).map(deal => `
    <button class="priority-item" data-deal="${escapeHtml(deal.id)}">
      <span><strong>${escapeHtml(deal.folder)}</strong><small>${escapeHtml(deal.extracted.product || "Product pending")} · ${money(deal.extracted.amount_requested)}</small></span>
      <span class="badge ${statusTone(deal.operational_state)}">${escapeHtml(deal.operational_state.replaceAll("_", " "))}</span>
      <span><strong>${escapeHtml(nextAction(deal))}</strong><small>${deal.needed_by ? `Needed ${shortDate(deal.needed_by)}` : `${deal.document_count} documents`}</small></span>
      <span>›</span>
    </button>`).join("");
}

function renderReadiness(deals) {
  const buckets = [
    ["Ready / closed", deals.filter(d => ["READY_TO_CLOSE", "CLOSED"].includes(d.operational_state)).length],
    ["Waiting human", deals.filter(d => d.operational_state === "WAITING_HUMAN").length],
    ["Missing evidence", deals.filter(d => d.operational_state === "WAITING_DOCUMENTS").length],
    ["Fraud held", deals.filter(d => d.operational_state === "HELD_FRAUD").length],
  ];
  const max = Math.max(1, ...buckets.map(([, value]) => value));
  $("#readiness-chart").innerHTML = buckets.map(([label, value]) => `
    <div class="readiness-row"><span>${label}</span><div class="bar"><i style="width:${value/max*100}%"></i></div><strong>${value}</strong></div>`).join("");
}

function renderPipeline(deals) {
  const counts = Object.fromEntries(STAGES.map(stage => [stage, deals.filter(deal => deal.stage === stage).length]));
  $("#pipeline-caption").textContent = deals.length ? `${deals.length} deals across ${Object.values(counts).filter(Boolean).length} stages` : "No active deals";
  $("#pipeline").innerHTML = STAGES.map(stage => `
    <div class="stage ${counts[stage] ? "active" : ""}"><div class="stage-node"></div><strong>${stage}</strong><span>${counts[stage]} deals</span></div>`).join("");
}

function renderDealTable(deals) {
  const query = ($("#deal-search")?.value || "").toLowerCase();
  const filtered = deals.filter(deal => deal.folder.toLowerCase().includes(query) || (deal.extracted.legal_name || "").toLowerCase().includes(query));
  $("#deal-table").innerHTML = filtered.map(deal => `
    <tr data-deal="${escapeHtml(deal.id)}">
      <td class="deal-name"><strong>${escapeHtml(deal.folder)}</strong><small>${escapeHtml(deal.extracted.legal_name)}</small></td>
      <td><strong>${money(deal.extracted.amount_requested)}</strong><br><span class="muted">${escapeHtml(deal.extracted.product)}</span></td>
      <td><span class="badge blue">${escapeHtml(deal.stage)}</span></td>
      <td><span class="badge ${statusTone(deal.operational_state)}">${escapeHtml(deal.operational_state.replaceAll("_", " "))}</span></td>
      <td><div class="mini-progress"><div class="bar"><i style="width:${deal.readiness}%"></i></div><span>${deal.readiness}%</span></div></td>
      <td>${escapeHtml(nextAction(deal))}</td>
    </tr>`).join("") || `<tr><td colspan="6" class="empty-state">No matching deals</td></tr>`;
}

function renderInbox(requests) {
  const view = $("#inbox-view");
  view.innerHTML = `
    <div class="view-intro"><div><p class="eyebrow">HUMAN-IN-THE-LOOP</p><h2>${requests.length} decisions waiting</h2></div></div>
    <div class="operations-grid">${requests.map(requestCard).join("") || `<section class="panel empty-state">No decisions are waiting.</section>`}</div>`;
}

function requestCard(request) {
  const canAct = state.role === "admin" || state.role === request.required_role;
  return `<article class="control-card">
    <div class="request-head"><span class="badge blue">${escapeHtml(request.type.replaceAll("_", " "))}</span><small class="muted">${shortDate(request.requested_at)}</small></div>
    <h3 style="margin-top:14px">${escapeHtml(request.folder)}</h3>
    <p class="muted">To ${escapeHtml(request.recipient)} · Evidence v${request.evidence_version}</p>
    <p>${escapeHtml(request.message)}</p>
    <div class="request-actions">
      <button class="button primary" data-answer="approve" data-request="${request.id}" ${canAct ? "" : "disabled"}>Approve</button>
      <button class="button ghost" data-answer="request_info" data-request="${request.id}" ${canAct ? "" : "disabled"}>Request info</button>
      <button class="button danger" data-answer="decline" data-request="${request.id}" ${canAct ? "" : "disabled"}>Decline</button>
    </div>
    ${canAct ? "" : `<small class="muted">Switch to ${escapeHtml(request.required_role)} to act.</small>`}
  </article>`;
}

function renderFraud(deals) {
  const flagged = deals.filter(deal => deal.fraud.flagged);
  $("#fraud-list").innerHTML = flagged.map(deal => `
    <article class="fraud-item" data-deal="${escapeHtml(deal.id)}">
      <div class="request-head"><strong>${escapeHtml(deal.folder)}</strong><span class="badge red">${Math.round(deal.fraud.confidence*100)}% confidence</span></div>
      <p>${escapeHtml(deal.fraud.reason)}</p>
      <p><strong>${deal.fraud.linked_deals.length}</strong> linked current and historical deals</p>
    </article>`).join("") || `<div class="empty-state">No fraud cluster has enough evidence yet.</div>`;
  drawNetwork(flagged);
}

function drawNetwork(flagged) {
  const root = $("#network");
  if (!flagged.length) {
    root.innerHTML = `<div class="empty-state">The relationship graph will appear as connected deals arrive.</div>`;
    return;
  }
  const currentNames = new Set(flagged.map(d => d.folder));
  const linked = [...new Set(flagged.flatMap(d => d.fraud.linked_deals))];
  const nodes = [
    {name: "Shared evidence", x: 50, y: 50, type: "hub"},
    ...flagged.slice(0,5).map((d,i) => ({name:d.folder, x: 18 + (i%2)*64, y: 18 + Math.floor(i/2)*31, type:"current"})),
    ...linked.filter(n => !currentNames.has(n)).slice(0,5).map((name,i) => ({name, x: 18 + (i%2)*64, y: 78 + Math.floor(i/2)*14, type:"history"})),
  ];
  const hub = nodes[0];
  const lines = nodes.slice(1).map(node => {
    const dx = node.x - hub.x, dy = node.y - hub.y;
    const length = Math.sqrt(dx*dx + dy*dy);
    const angle = Math.atan2(dy, dx) * 180 / Math.PI;
    return `<i class="network-line" style="left:${hub.x}%;top:${hub.y}%;width:${length}%;transform:rotate(${angle}deg)"></i>`;
  }).join("");
  root.innerHTML = lines + nodes.map(node => `<div class="network-node ${node.type}" style="left:${node.x}%;top:${node.y}%">${escapeHtml(node.name)}</div>`).join("");
}

function renderOperations(summary) {
  const cards = [
    ["Document isolation", "Healthy", "Files are checksum-verified and processed outside the workflow state.", "green"],
    ["Evidence ledger", "Healthy", `${summary.total_deals} deal twins maintain versioned facts and audit events.`, "green"],
    ["Human gates", `${summary.waiting_human} waiting`, "Credit, committee, fraud, commitment and funding remain human-controlled.", summary.waiting_human ? "amber" : "green"],
    ["SLA scheduler", `${summary.alerts} alerts`, "Durable stage timers alert at 100%, 150% and 200% of SLA.", summary.alerts ? "amber" : "green"],
    ["Fraud circuit breaker", summary.held ? "Blocking funding" : "Ready", "An active hold prevents every path to Closed.", summary.held ? "red" : "green"],
    ["Adaptive pattern memory", `${state.patterns.length} learned`, state.patterns.length ? `Latest: ${state.patterns[0].name}. New multi-signal combinations are persisted for future comparisons.` : "Learns qualifying multi-signal relationships without treating common service providers alone as fraud.", "blue"],
    ["Submission exporter", "Ready", "Builds the required extracted facts, logs, approvals, alerts and fraud JSON.", "blue"],
  ];
  $("#operations-grid").innerHTML = cards.map(([title, status, description, tone]) => `
    <article class="control-card"><span class="badge ${tone}">${status}</span><h3 style="margin-top:16px">${title}</h3><p class="muted">${description}</p></article>`).join("");
}

async function openDeal(identifier, openDrawer = true) {
  try {
    const deal = await api(`/api/deals/${encodeURIComponent(identifier)}`);
    state.selectedDeal = deal.id;
    renderDealDetail(deal);
    if (openDrawer) {
      $("#deal-drawer").classList.add("open");
      $("#deal-drawer").setAttribute("aria-hidden", "false");
    }
  } catch (error) { showToast(error.message, true); }
}

function renderDealDetail(deal) {
  const fields = [
    ["Legal name", deal.extracted.legal_name], ["EIN", deal.extracted.ein],
    ["Amount", money(deal.extracted.amount_requested)], ["Product", deal.extracted.product],
    ["Purpose", deal.extracted.purpose], ["Term", deal.extracted.term_months ? `${deal.extracted.term_months} months` : null],
    ["Guarantor", deal.extracted.guarantor_name], ["Stated revenue", money(deal.extracted.annual_revenue_stated)],
  ];
  $("#deal-detail").innerHTML = `
    <header class="detail-header">
      <div class="detail-top"><div><p class="eyebrow">EVIDENCE VERSION ${deal.evidence_version}</p><h2>${escapeHtml(deal.folder)}</h2><p class="detail-meta">${escapeHtml(deal.extracted.product)} · ${money(deal.extracted.amount_requested)} · RM ${escapeHtml(deal.relationship_manager)}</p></div><button class="close-button" data-close-drawer>×</button></div>
      <div class="stage-strip" style="margin-top:18px">${STAGES.map((stage,i) => `<div class="stage-pill ${i < deal.stage_index ? "done" : i === deal.stage_index ? "current" : ""}">${stage}</div>`).join("")}</div>
    </header>
    <div class="detail-body">
      <section class="detail-card"><div class="request-head"><div><p class="eyebrow">SAFE-TO-CLOSE</p><h3>${deal.readiness}% ready</h3></div><span class="badge ${statusTone(deal.operational_state)}">${escapeHtml(deal.operational_state.replaceAll("_", " "))}</span></div><div class="bar" style="margin-top:14px;height:10px"><i style="width:${deal.readiness}%"></i></div><p class="muted">Next action: ${escapeHtml(nextAction(deal))}</p></section>
      ${deal.portal_application?.score?.tier ? `<section class="detail-card"><div class="request-head"><div><p class="eyebrow">APPROVAL READINESS</p><h3>${escapeHtml(deal.portal_application.score.tier.replaceAll("_", " "))}</h3></div><strong class="decision-score">${deal.portal_application.score.approval_readiness}</strong></div><p class="muted">${escapeHtml(deal.portal_application.decision.recommendation)}</p><p class="muted">Suggested amount: ${money(deal.portal_application.score.suggested_amount)} · Human confirmation required</p>${deal.portal_application.decision?.email_draft ? `<div style="margin-top:14px;padding:12px;background:var(--bg-subtle,#f8fafc);border-radius:6px;border:1px solid var(--border,#e2e8f0)"><div class="request-head"><strong>Borrower communication draft</strong><button class="button ghost" type="button" data-copy-text style="padding:4px 8px;font-size:11px">Copy draft</button></div><pre class="copy-source" style="white-space:pre-wrap;font:inherit;font-size:12px;margin-top:8px;color:#334155">${escapeHtml(deal.portal_application.decision.email_draft)}</pre></div>` : ""}</section>` : ""}
      <section class="detail-card"><p class="eyebrow">EVIDENCE PASSPORT</p><h3>Extracted facts</h3><div class="detail-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:16px">${fields.map(([label,value]) => `<div><small class="muted">${label}</small><strong style="display:block;margin-top:4px">${escapeHtml(value)}</strong></div>`).join("")}</div></section>
      <section class="detail-card"><div class="request-head"><div><p class="eyebrow">PACKAGE VALIDATION</p><h3>${deal.issues.length} issues</h3></div><span>${deal.document_count} documents</span></div><div class="issues" style="margin-top:14px">${deal.issues.map(issue => `<span class="badge ${issue.startsWith("OLD_TRICK") ? "red" : "amber"}">${escapeHtml(issue)}</span>`).join("") || `<span class="badge green">No detected issues</span>`}</div></section>
      ${fraudDetail(deal)}
      ${aiCopilotSection(deal)}
      <section class="detail-card"><p class="eyebrow">HUMAN REQUESTS</p><h3>Decisions</h3>${deal.human_requests.map(request => detailRequest(request)).join("") || `<p class="muted">No human decision requested yet.</p>`}</section>
      <section class="detail-card"><p class="eyebrow">DOCUMENTS</p><h3>Evidence package</h3>${deal.documents.map(doc => `<div class="doc-row"><span><strong>${escapeHtml(doc.filename)}</strong><small>${escapeHtml(doc.document_type.replaceAll("_", " "))} · ${doc.page_count || 1} pages</small></span><span class="badge ${doc.status === "PROCESSED" ? "green" : "amber"}">${doc.status}</span></div>`).join("")}</section>
      <section class="detail-card"><p class="eyebrow">AUDIT TRAIL</p><h3>Latest events</h3><div style="margin-top:16px">${deal.events.slice(0,12).map(event => `<div class="timeline-item"><strong>${escapeHtml(event.event_type.replaceAll(/([A-Z])/g," $1").trim())}</strong><span>${shortDate(event.at)} · ${escapeHtml(event.actor)}</span></div>`).join("")}</div></section>
    </div>`;
}

function fraudDetail(deal) {
  if (!deal.fraud.flagged) return `<section class="detail-card"><p class="eyebrow">DEAL DNA</p><h3>No coordinated fraud pattern</h3><p class="muted">The relationship engine has not found enough unusual shared evidence to recommend a hold.</p></section>`;
  return `<section class="detail-card"><div class="request-head"><div><p class="eyebrow">DEAL DNA</p><h3>Potential ${escapeHtml(deal.fraud.category.replaceAll("_", " "))}</h3></div><span class="badge red">${Math.round(deal.fraud.confidence*100)}% confidence</span></div><p>${escapeHtml(deal.fraud.reason)}</p><div class="issues">${deal.fraud.evidence.map(item => `<span class="badge red">${escapeHtml(item)}</span>`).join("")}</div><p class="muted">Linked: ${deal.fraud.linked_deals.map(escapeHtml).join(", ")}</p></section>`;
}

function aiCopilotSection(deal) {
  return `
    <section class="detail-card" style="border-left:4px solid #4338ca">
      <div class="request-head">
        <div><p class="eyebrow" style="color:#4338ca">AI REASONING COPILOT</p><h3>Gemini Forensic Analysis</h3></div>
        <button class="button primary" type="button" data-run-deal-ai="${escapeHtml(deal.id)}" style="background:linear-gradient(135deg, #1e3a8a, #4338ca);border:none;font-size:12px;padding:6px 12px">✨ Run AI Analysis</button>
      </div>
      <div id="deal-ai-result-${escapeHtml(deal.id)}" style="margin-top:12px">
        <p class="muted">Click "Run AI Analysis" or ask Gemini a question below regarding creditworthiness, document provenance, or cross-deal links.</p>
      </div>
      <form class="ai-ask-form" data-ai-ask-deal="${escapeHtml(deal.id)}" style="display:flex;gap:8px;margin-top:14px">
        <input type="text" name="question" placeholder="Ask AI: e.g. Why is this flagged? Or assess collateral..." style="flex:1;padding:8px 12px;border:1px solid var(--line);border-radius:6px;font-size:12px" required>
        <button class="button ghost" type="submit" style="font-size:12px">Ask</button>
      </form>
    </section>
  `;
}

async function runDealAiAnalysis(dealId, question = null) {
  const target = $(`#deal-ai-result-${dealId}`);
  if (!target) return;
  target.innerHTML = `<div style="padding:14px;background:#f8fafc;border:1px dashed #cbd5e1;border-radius:8px;color:#4338ca;font-size:13px"><strong>✨ Gemini is synthesizing evidence dossier & analyzing deal file...</strong></div>`;
  try {
    const res = await api("/api/ai/investigate-deal", {
      method: "POST",
      body: JSON.stringify({ deal_id: dealId, question: question })
    });
    target.innerHTML = `
      <div style="background:var(--bg-subtle,#f8fafc);border:1px solid #cbd5e1;border-radius:8px;padding:14px;margin-top:8px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <span class="badge blue" style="font-size:10px">${escapeHtml(res.ai_provider)}</span>
          <button class="button ghost" type="button" data-copy-text style="padding:2px 8px;font-size:10px">Copy</button>
        </div>
        <div class="copy-source" style="white-space:pre-wrap;font-size:12px;line-height:1.5;color:#1e293b">${escapeHtml(res.answer)}</div>
      </div>
    `;
  } catch (err) {
    target.innerHTML = `<div class="badge red" style="padding:10px;margin-top:8px">Analysis error: ${escapeHtml(err.message)}</div>`;
  }
}

async function runRingAiAnalysis() {
  const panel = $("#ai-ring-panel");
  if (!panel) return;
  panel.classList.remove("hidden");
  panel.innerHTML = `<div style="padding:20px;background:#f8fafc;border:1px dashed #cbd5e1;border-radius:8px;color:#4338ca;font-size:13px"><strong>✨ Gemini AI is analyzing cross-deal relationships, structuring, and historical anchors...</strong></div>`;
  try {
    const res = await api("/api/ai/investigate-ring", {method:"POST", body:"{}"});
    renderAiRingPanel(res);
  } catch (err) {
    panel.innerHTML = `<div class="badge red" style="padding:12px">Analysis error: ${escapeHtml(err.message)}</div>`;
  }
}

function renderAiRingPanel(data) {
  const panel = $("#ai-ring-panel");
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap">
      <div>
        <span class="badge red" style="font-size:12px;font-weight:700">${escapeHtml(data.verdict.replaceAll("_", " "))}</span>
        <span class="badge blue" style="font-size:11px;margin-left:8px">${escapeHtml(data.ai_provider)}</span>
        <h2 style="margin-top:10px;font-size:20px;color:#1e1b4b">Executive Fraud Syndicate Briefing</h2>
      </div>
      <button class="button ghost" type="button" data-close-ai-ring style="padding:4px 8px">✕ Close</button>
    </div>

    <div style="margin-top:16px;padding:14px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px">
      <strong style="color:#991b1b;display:block;margin-bottom:6px">EXECUTIVE SUMMARY</strong>
      <p style="margin:0;font-size:13px;line-height:1.5;color:#7f1d1d">${escapeHtml(data.executive_summary)}</p>
    </div>

    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px">
      <div style="padding:14px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px">
        <strong style="color:#334155;display:block;margin-bottom:6px">STRUCTURING & THRESHOLD EVASION</strong>
        <p style="margin:0;font-size:12px;line-height:1.5;color:#475569">${escapeHtml(data.structuring_analysis)}</p>
      </div>
      <div style="padding:14px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px">
        <strong style="color:#334155;display:block;margin-bottom:6px">HISTORICAL RECIDIVISM</strong>
        <p style="margin:0;font-size:12px;line-height:1.5;color:#475569">${escapeHtml(data.historical_recidivism)}</p>
      </div>
    </div>

    <div style="margin-top:16px;padding:14px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px">
      <strong style="color:#334155;display:block;margin-bottom:6px">SYNDICATE MODUS OPERANDI</strong>
      <pre style="white-space:pre-wrap;font-family:inherit;font-size:12px;line-height:1.5;color:#475569;margin:0">${escapeHtml(data.modus_operandi)}</pre>
    </div>

    <div style="margin-top:16px;padding:14px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <strong style="color:#1e40af">FINCEN SUSPICIOUS ACTIVITY REPORT (SAR) NARRATIVE</strong>
        <button class="button ghost" type="button" data-copy-text style="padding:4px 8px;font-size:11px">Copy SAR Narrative</button>
      </div>
      <pre class="copy-source" style="white-space:pre-wrap;font-family:inherit;font-size:12px;line-height:1.5;color:#1e3a8a;margin:0">${escapeHtml(data.sar_narrative)}</pre>
    </div>

    <div style="margin-top:16px">
      <strong style="display:block;font-size:12px;color:#334155;margin-bottom:8px">RECOMMENDED COMPLIANCE & UNDERWRITING ACTIONS:</strong>
      <div style="display:flex;flex-wrap:wrap;gap:8px">
        ${data.recommended_actions.map(act => `<span class="badge red" style="font-size:11px;padding:6px 10px">${escapeHtml(act)}</span>`).join("")}
      </div>
    </div>
  `;
}

function detailRequest(request) {
  const waiting = request.status === "waiting";
  const requiredRole = ({credit_decision:"credit_officer",committee_approval:"committee",commitment_signoff:"relationship_manager",funding_release:"funding_officer",fraud_hold:"fraud_ops",fraud_release:"fraud_ops"})[request.type];
  const canAct = state.role === "admin" || state.role === requiredRole;
  return `<div class="request"><div class="request-head"><strong>${escapeHtml(request.type.replaceAll("_", " "))}</strong><span class="badge ${waiting ? "amber" : request.answer === "approve" ? "green" : "gray"}">${escapeHtml(waiting ? "waiting" : request.answer || request.status)}</span></div><p>To ${escapeHtml(request.recipient)} · Evidence v${request.evidence_version}</p>${waiting ? `<div class="request-actions"><button class="button primary" data-answer="approve" data-request="${request.id}" ${canAct ? "" : "disabled"}>Approve</button><button class="button ghost" data-answer="request_info" data-request="${request.id}" ${canAct ? "" : "disabled"}>Request info</button><button class="button danger" data-answer="decline" data-request="${request.id}" ${canAct ? "" : "disabled"}>Decline</button></div>` : ""}</div>`;
}

async function answerRequest(id, answer) {
  try {
    await api(`/api/human-requests/${id}/answer`, {method:"POST", body: JSON.stringify({answer})});
    showToast(`Decision recorded: ${answer.replaceAll("_", " ")}`);
    await refresh();
  } catch (error) { showToast(error.message, true); }
}

function switchView(view) {
  $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach(section => section.classList.toggle("active", section.id === `${view}-view`));
  const titles = {overview:"Portfolio overview", deals:"Deal queue", inbox:"Human decision inbox", fraud:"Fraud relationship network", operations:"Operations and controls"};
  $("#page-title").textContent = titles[view];
}

const STEP_TITLES = ["Business profile", "Loan request", "Guarantor", "Documents", "Review and submit"];
const STEP_HELP = [
  "The legal business information is compared with state-formation and IRS records to prevent mismatches.",
  "The requested amount, purpose, product, and timing determine routing, supporting evidence, and priority.",
  "Guarantor information is matched to the uploaded government ID. Protected characteristics are not used in scoring.",
  "Files are type-checked, hashed with SHA-256 for integrity, classified by content, and linked to one evidence version.",
  "You can review the structured record before submission. Actual lending and funding decisions remain human-controlled.",
];

function setAuthTab(tab) {
  $$(".auth-tab").forEach(button => button.classList.toggle("active", button.dataset.authTab === tab));
  $("#login-form").classList.toggle("hidden", tab !== "login");
  $("#signup-form").classList.toggle("hidden", tab !== "signup");
  $("#bank-login-form").classList.toggle("hidden", tab !== "bank");
  $("#bank-entry").classList.toggle("hidden", tab === "bank");
  $("#auth-error").textContent = "";
}

function showWorkspace(user) {
  state.user = user;
  state.role = user?.role || null;
  $("#auth-screen").classList.toggle("hidden", Boolean(user));
  $("#consumer-shell").classList.toggle("hidden", user?.role !== "consumer");
  $("#bank-shell").classList.toggle("hidden", !user || user.role === "consumer");
  if (!user) return;
  if (user.role === "consumer") {
    $("#consumer-name").textContent = user.display_name;
    $("#consumer-org").textContent = user.organization || user.email;
    loadApplications();
  } else {
    const roleName = user.role.replaceAll("_", " ").replace(/\b\w/g, value => value.toUpperCase());
    $("#bank-role-badge").textContent = roleName;
    $("#active-role").textContent = roleName;
    $("#bank-user-name").textContent = user.display_name;
    refresh();
    checkAiStatus();
  }
}

async function checkAiStatus() {
  try {
    const status = await api("/api/ai/status");
    const btn = $("#gemini-key-btn");
    if (btn) {
      if (status.key_configured) {
        btn.innerHTML = `<span style="color:#16a34a;font-weight:bold">●</span> Gemini Active`;
        btn.style.borderColor = "#16a34a";
        btn.title = "Gemini API key is configured and active. Click to update.";
      } else {
        btn.innerHTML = `🔑 Configure Gemini`;
        btn.style.borderColor = "";
        btn.title = "Click to set your Gemini API key";
      }
    }
  } catch (e) {}
}

async function bootstrapAuth() {
  try {
    const result = await api("/api/auth/me");
    showWorkspace(result.user);
  } catch (error) {
    $("#auth-error").textContent = error.message;
    showWorkspace(null);
  }
}

async function loadApplications() {
  try {
    state.applications = await api("/api/consumer/applications");
    renderApplicationList();
    if (!state.currentApplication) {
      $("#consumer-home").classList.remove("hidden");
      $("#application-wizard").classList.add("hidden");
    }
  } catch (error) { showConsumerToast(error.message, true); }
}

function showConsumerToast(message, error = false) {
  const toast = $("#consumer-toast");
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(showConsumerToast.timer);
  showConsumerToast.timer = setTimeout(() => toast.className = "toast", 3000);
}

function renderApplicationList() {
  const root = $("#consumer-applications");
  if (!state.applications.length) {
    root.innerHTML = `<div class="panel empty-state"><div><h3>No applications yet</h3><p>Start when you are ready. Your draft saves after each step.</p></div></div>`;
    return;
  }
  root.innerHTML = state.applications.map(application => {
    const score = application.score?.approval_readiness;
    const subtitle = application.status === "draft" ? `Step ${application.current_step} of 5` : application.decision?.recommendation || "Submitted to the bank";
    return `<article class="application-card" data-application="${escapeHtml(application.id)}">
      <div><span class="badge ${application.status === "prequalified" ? "green" : application.status === "decline_recommended" ? "red" : "amber"}">${escapeHtml(application.status.replaceAll("_", " "))}</span><h3>${escapeHtml(application.form.legal_name || state.user.organization)}</h3><p>${escapeHtml(subtitle)}</p></div>
      ${score == null ? `<span class="muted">Not scored</span>` : `<div class="score-orb">${score}</div>`}<strong>Open →</strong>
    </article>`;
  }).join("");
}

async function createApplication() {
  try {
    const application = await api("/api/consumer/applications", {method:"POST", body:"{}"});
    state.applications.unshift(application);
    openApplication(application);
  } catch (error) { showConsumerToast(error.message, true); }
}

function openApplication(application) {
  state.currentApplication = application;
  state.currentStep = application.status === "draft" || application.status === "needs_information" ? application.current_step : 5;
  $("#consumer-home").classList.add("hidden");
  $("#application-wizard").classList.remove("hidden");
  renderWizard();
}

function field(name, label, type="text", extra="") {
  const value = state.currentApplication?.form?.[name] ?? "";
  return `<label>${label}<input data-field="${name}" type="${type}" value="${escapeHtml(value)}" ${extra}></label>`;
}

function renderWizard() {
  const application = state.currentApplication || {};
  const step = state.currentStep;
  $("#wizard-title").textContent = STEP_TITLES[step - 1];
  $("#step-help").textContent = STEP_HELP[step - 1];
  $("#wizard-progress").innerHTML = STEP_TITLES.map((title, index) => `<div class="wizard-step ${index + 1 === step ? "active" : index + 1 < step ? "done" : ""}">${index + 1}. ${title}</div>`).join("");
  const content = $("#wizard-content");
  if (step === 1) content.innerHTML = `<h2>Tell us about the business</h2><p>Use the exact legal information shown on formation and IRS documents.</p><div class="form-grid two">${field("legal_name","Legal business name", "text", "required")}${field("dba","DBA / trade name")}${field("ein","Federal EIN", "text", "placeholder=12-3456789 required")}${field("formation_date","Formation date", "date", "required")}${field("annual_revenue_stated","Annual revenue", "number", "min=0 required")}${field("bank_account","Primary bank / account")}</div><div class="form-grid" style="margin-top:14px">${field("business_address","Business address", "text", "required")}${field("industry","Industry")}</div>`;
  if (step === 2) content.innerHTML = `<h2>What financing do you need?</h2><p>Tell us the amount, purpose, product, and timing.</p><div class="form-grid two">${field("amount_requested","Amount requested", "number", "min=1 required")}<label>Product<select data-field="product"><option>Term Loan</option><option>Equipment Loan</option><option>Revolving Line of Credit</option><option>Working Capital Loan</option></select></label>${field("term_months","Requested term (months)", "number", "min=1 required")}${field("needed_by","Funds needed by", "datetime-local")}</div><label style="margin-top:14px">Use of proceeds<textarea data-field="purpose" required>${escapeHtml(application.form?.purpose || "")}</textarea></label>`;
  if (step === 3) content.innerHTML = `<h2>Guarantor information</h2><p>This is matched against the uploaded photo ID for identity consistency.</p><div class="form-grid two">${field("guarantor_name","Full legal name", "text", "required")}${field("guarantor_dob","Date of birth", "date", "required")}${field("guarantor_dl_number","Driver license number", "text", "required")}${field("guarantor_address","Home address", "text", "required")}</div>`;
  if (step === 4) content.innerHTML = `<h2>Upload your evidence package</h2><p>PDF, CSV, JPG, or PNG · maximum 10 MB per file. File content—not the filename—determines its type.</p><div class="upload-zone"><strong>Choose one or several documents</strong><p class="muted">Formation, EIN, ID, six bank statements, financials, tax return, and equipment quote when applicable.</p><input id="document-upload" type="file" accept=".pdf,.csv,.jpg,.jpeg,.png" multiple></div><div class="upload-list">${(application.uploaded_documents || []).filter(d => d.document_type !== "loan_application").map(doc => `<div class="upload-item"><span><strong>${escapeHtml(doc.filename)}</strong><br><small>${escapeHtml(doc.document_type.replaceAll("_", " "))}</small></span><span class="badge ${doc.status === "PROCESSED" ? "green" : "amber"}">${doc.status}</span></div>`).join("") || `<p class="muted">No supporting documents uploaded yet.</p>`}</div>`;
  if (step === 5) content.innerHTML = reviewMarkup(application);
  if (step === 2 && application.form?.product) $("[data-field='product']").value = application.form.product;
  $("#wizard-back").disabled = step === 1;
  const locked = !["draft", "needs_information"].includes(application.status || "draft");
  $("#wizard-next").classList.toggle("hidden", locked);
  $("#wizard-next").textContent = step === 5 ? "Submit application" : "Save and continue";
}

function reviewMarkup(application) {
  const form = application.form || {};
  if (application.status !== "draft" && application.status !== "needs_information") {
    const score = application.score || {};
    const tierClass = score.tier === "FAST_TRACK" ? "fast" : score.tier === "HUMAN_REVIEW" ? "review" : "decline";
    return `<div class="decision-banner ${tierClass}"><span class="decision-score">${score.approval_readiness ?? "—"}</span><h2>${escapeHtml((score.tier || application.status).replaceAll("_", " "))}</h2><p>${escapeHtml(application.decision?.recommendation)}</p>${score.suggested_amount != null ? `<p><strong>Suggested facility: ${money(score.suggested_amount)}</strong></p>` : ""}</div><div class="review-section" style="margin-top:22px"><h3>Application status</h3><p class="muted">Your application has been received and is being processed by Trinity Commercial Bank. A lending officer will contact you shortly with next steps.</p></div>`;
  }
  const pairs = [["Business", form.legal_name],["EIN",form.ein],["Requested",money(form.amount_requested)],["Product",form.product],["Purpose",form.purpose],["Term",form.term_months ? `${form.term_months} months` : null],["Guarantor",form.guarantor_name],["Documents",`${application.uploaded_documents.filter(d => d.document_type !== "loan_application").length} uploaded`]];
  return `<h2>Review before submitting</h2><p>This creates a versioned record for the bank. You cannot edit it unless a reviewer requests more information.</p><div class="review-section"><h3>Application record</h3><div class="review-grid">${pairs.map(([label,value]) => `<div><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div></div><div class="security-note"><strong>By submitting</strong><span>You confirm the information is accurate and authorize the bank to review the uploaded business records. Submission does not guarantee approval or funding.</span></div>`;
}

function collectStepForm() {
  const form = {};
  $$('[data-field]', $("#wizard-content")).forEach(input => form[input.dataset.field] = input.value);
  return form;
}

async function ensureCurrentApplication() {
  if (state.currentApplication?.id) return state.currentApplication;
  if (!state.applications || !state.applications.length) {
    try { state.applications = await api("/api/consumer/applications"); } catch (_) { state.applications = []; }
  }
  let app = state.applications.find(a => a && (a.status === "draft" || a.status === "needs_information"));
  if (!app) {
    app = await api("/api/consumer/applications", {method:"POST", body:"{}"});
    state.applications.unshift(app);
  }
  state.currentApplication = app;
  return app;
}

async function saveCurrentStep(nextStep = state.currentStep) {
  const app = await ensureCurrentApplication();
  const result = await api(`/api/consumer/applications/${app.id}/save`, {method:"POST", body:JSON.stringify({form:collectStepForm(), current_step:nextStep})});
  state.currentApplication = result;
  const index = state.applications.findIndex(item => item?.id === result?.id);
  if (index >= 0) state.applications[index] = result;
}

async function wizardNext() {
  try {
    const inputs = $$('input,select,textarea', $("#wizard-content"));
    const invalid = inputs.find(input => !input.checkValidity());
    if (invalid) { invalid.reportValidity(); return; }
    $("#wizard-next").disabled = true;
    const app = await ensureCurrentApplication();
    if (state.currentStep < 5) {
      await saveCurrentStep(state.currentStep + 1);
      state.currentStep += 1;
      renderWizard();
      return;
    }
    await saveCurrentStep(5);
    state.currentApplication = await api(`/api/consumer/applications/${app.id}/submit`, {method:"POST", body:"{}"});
    const index = state.applications.findIndex(item => item?.id === state.currentApplication?.id);
    if (index >= 0) state.applications[index] = state.currentApplication;
    renderWizard();
    showConsumerToast("Application submitted and scored securely");
  } catch (error) { showConsumerToast(error.message, true); }
  finally { $("#wizard-next").disabled = false; }
}

async function uploadFiles(files) {
  const app = await ensureCurrentApplication();
  for (const file of files) {
    try {
      showConsumerToast(`Uploading ${file.name}…`);
      const encoded = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",")[1]);
        reader.onerror = () => reject(new Error("Could not read the selected file"));
        reader.readAsDataURL(file);
      });
      const result = await api(`/api/consumer/applications/${app.id}/upload`, {method:"POST", body:JSON.stringify({filename:file.name, content_base64:encoded})});
      state.currentApplication = result.application;
    } catch (error) { showConsumerToast(`${file.name}: ${error.message}`, true); }
  }
  renderWizard();
  showConsumerToast("Documents processed and added to the evidence record");
}

document.addEventListener("click", async event => {
  const runAiBtn = event.target.closest("[data-run-deal-ai]");
  if (runAiBtn) {
    event.preventDefault();
    return runDealAiAnalysis(runAiBtn.dataset.runDealAi);
  }
  const runRingBtn = event.target.closest("#btn-run-ai-ring");
  if (runRingBtn) {
    event.preventDefault();
    return runRingAiAnalysis();
  }
  const copyBtn = event.target.closest("[data-copy-text]");
  if (copyBtn) {
    event.preventDefault();
    const container = copyBtn.closest(".detail-card") || copyBtn.parentElement;
    const textTarget = container?.querySelector(".copy-source") || copyBtn.closest("div")?.nextElementSibling;
    const text = textTarget ? (textTarget.value || textTarget.innerText || textTarget.textContent) : "";
    if (text) {
      await navigator.clipboard.writeText(text.trim());
      showToast("Copied to clipboard");
    }
    return;
  }
  if (event.target.closest("[data-close-ai-ring]")) {
    event.preventDefault();
    $("#ai-ring-panel")?.classList.add("hidden");
    return;
  }
  const authTab = event.target.closest("[data-auth-tab]");
  if (authTab) return setAuthTab(authTab.dataset.authTab);
  const nav = event.target.closest("[data-view]");
  if (nav) return switchView(nav.dataset.view);
  const switcher = event.target.closest("[data-switch]");
  if (switcher) return switchView(switcher.dataset.switch);
  const deal = event.target.closest("[data-deal]");
  if (deal) return openDeal(deal.dataset.deal);
  if (event.target.closest("[data-close-drawer]")) {
    $("#deal-drawer").classList.remove("open");
    $("#deal-drawer").setAttribute("aria-hidden", "true");
    state.selectedDeal = null;
    return;
  }
  const answer = event.target.closest("[data-answer]");
  if (answer) return answerRequest(answer.dataset.request, answer.dataset.answer);
  const advance = event.target.closest("[data-advance]");
  if (advance) {
    advance.disabled = true;
    try {
      const result = await api("/api/sim/advance", {method:"POST", body:JSON.stringify({by:advance.dataset.advance})});
      showToast(`Advanced to ${shortDate(result.now)} · ${result.processed} files processed`);
      await refresh();
    } catch (error) { showToast(error.message, true); }
    finally { advance.disabled = false; }
  }
});

$("#reset-button").addEventListener("click", async () => {
  if (!confirm("Reset the simulation and remove all current workflow state?")) return;
  await api("/api/sim/reset", {method:"POST", body:"{}"});
  showToast("Simulation reset");
  await refresh();
});

$("#refresh-button").addEventListener("click", refresh);
$("#deal-search").addEventListener("input", () => renderDealTable(state.summary.deals));

$("#export-button").addEventListener("click", async () => {
  try {
    const result = await api("/api/export", {method:"POST", body:JSON.stringify({team:"DealPilot", set:"sample"})});
    const blob = new Blob([JSON.stringify(result.submission, null, 2)], {type:"application/json"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url; link.download = "submission.json"; link.click();
    URL.revokeObjectURL(url);
    showToast("Submission JSON generated and downloaded");
  } catch (error) { showToast(error.message, true); }
});

$("#gemini-key-btn")?.addEventListener("click", async () => {
  const currentKey = prompt("Paste your Google Gemini API Key (starts with AIzaSy...):");
  if (currentKey === null) return;
  const key = currentKey.trim();
  if (!key) {
    await api("/api/ai/config", { method: "POST", body: JSON.stringify({ api_key: "" }) });
    showToast("Gemini API key cleared (using built-in engine)");
    await checkAiStatus();
    return;
  }
  showToast("Saving Gemini API key...");
  try {
    const res = await api("/api/ai/config", { method: "POST", body: JSON.stringify({ api_key: key }) });
    if (res.ok) {
      showToast("Gemini API key connected and saved successfully!");
      await checkAiStatus();
    }
  } catch (err) {
    showToast(`Error saving key: ${err.message}`, true);
  }
});

$("#btn-run-ai-ring")?.addEventListener("click", async () => {
  const panel = $("#ai-ring-panel");
  if (!panel) return;
  panel.classList.remove("hidden");
  panel.innerHTML = `<div style="display:flex;align-items:center;gap:12px;padding:20px;color:var(--text-muted)"><strong>✨ Gemini AI is analyzing cross-deal relationships, structuring, and historical anchors...</strong></div>`;
  try {
    const res = await api("/api/ai/investigate-ring", {method:"POST", body:"{}"});
    renderAiRingPanel(res);
  } catch (err) {
    panel.innerHTML = `<div class="badge red" style="padding:12px">Analysis error: ${escapeHtml(err.message)}</div>`;
  }
});

document.addEventListener("submit", async event => {
  const askForm = event.target.closest("[data-ai-ask-deal]");
  if (askForm) {
    event.preventDefault();
    const dealId = askForm.dataset.aiAskDeal;
    const input = askForm.querySelector('input[name="question"]');
    const question = input ? input.value.trim() : "";
    if (!question) return;
    input.value = "";
    return runDealAiAnalysis(dealId, question);
  }
});

$$("[data-auth-tab]").forEach(button => button.addEventListener("click", () => setAuthTab(button.dataset.authTab)));

$("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const result = await api("/api/auth/login", {method:"POST", body:JSON.stringify(values)});
    showWorkspace(result.user);
  } catch (error) { $("#auth-error").textContent = error.message; }
});

$("#signup-form").addEventListener("submit", async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const result = await api("/api/auth/signup", {method:"POST", body:JSON.stringify(values)});
    showWorkspace(result.user);
  } catch (error) { $("#auth-error").textContent = error.message; }
});

$("#bank-login-button").addEventListener("click", () => setAuthTab("bank"));

$("#bank-login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.currentTarget));
  try {
    const result = await api("/api/auth/bank-login", {method:"POST", body:JSON.stringify(values)});
    showWorkspace(result.user);
  } catch (error) { $("#auth-error").textContent = error.message; }
});

$$('[data-logout]').forEach(button => button.addEventListener("click", async () => {
  await api("/api/auth/logout", {method:"POST", body:"{}"});
  state.user = null; state.summary = null; state.applications = []; state.currentApplication = null;
  showWorkspace(null);
}));

$("#new-application").addEventListener("click", createApplication);
$("#back-to-applications").addEventListener("click", async () => {
  $("#application-wizard").classList.add("hidden");
  $("#consumer-home").classList.remove("hidden");
  await loadApplications();
});
$("#wizard-next").addEventListener("click", wizardNext);
$("#wizard-back").addEventListener("click", () => { if (state.currentStep > 1) { state.currentStep -= 1; renderWizard(); } });

document.addEventListener("click", event => {
  const card = event.target.closest("[data-application]");
  if (card) {
    const application = state.applications.find(item => item.id === card.dataset.application);
    if (application) openApplication(application);
  }
});

document.addEventListener("change", event => {
  if (event.target.id === "document-upload" && event.target.files.length) uploadFiles([...event.target.files]);
});

bootstrapAuth();
