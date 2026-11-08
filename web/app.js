const ORDER = ["intake", "credit", "committee", "product", "committed", "closed"];
const STAGES = ["Intake", "Credit", "Committee", "Product", "Committed", "Closed"];

const ROLES = {
  admin: {
    role: "admin",
    name: "Lauren Kim (Chief Lending Officer)",
    person_id: "lauren.kim",
    department: "Executive & Lending Operations",
    badge: "Full Portfolio Access",
    badgeColor: "green",
    desc: "Whole Access: Can view all tables, approve any stage, run fraud scans, release holds, and disburse capital across Trinity Commercial Bank.",
    allowed_stages: ["intake", "credit", "committee", "product", "committed", "closed"],
    can_release_funds: true,
    can_manage_fraud: true,
  },
  intake: {
    role: "intake",
    name: "Olivia Chen (Intake Specialist)",
    person_id: "olivia.chen",
    department: "Intake Operations",
    badge: "Intake Records Only",
    badgeColor: "blue",
    desc: "Intake Only: Restricted exclusively to intake records and initial filings. Other stage queues are locked.",
    allowed_stages: ["intake"],
    can_release_funds: false,
    can_manage_fraud: false,
  },
  credit: {
    role: "credit",
    name: "Marcus Lee (Credit Officer)",
    person_id: "marcus.lee",
    department: "Credit Underwriting",
    badge: "Credit Records Only",
    badgeColor: "blue",
    desc: "Credit Only: Restricted exclusively to credit records, financial spreads, and bank statements.",
    allowed_stages: ["credit"],
    can_release_funds: false,
    can_manage_fraud: false,
  },
  committee: {
    role: "committee",
    name: "Priscilla Nguyen (Committee Chair)",
    person_id: "priscilla.nguyen",
    department: "Credit Committee",
    badge: "Committee Records Only",
    badgeColor: "blue",
    desc: "Committee Only: Restricted exclusively to committee review records and credit memos.",
    allowed_stages: ["committee"],
    can_release_funds: false,
    can_manage_fraud: false,
  },
  closing: {
    role: "closing",
    name: "Tasha Reed (Closing & Funding)",
    person_id: "tasha.reed",
    department: "Closing & Funding",
    badge: "Releasing Records Only",
    badgeColor: "amber",
    desc: "Releasing Records Only: Can view closed stage records and click Release Funds (blocked if a fraud hold exists).",
    allowed_stages: ["closed"],
    can_release_funds: true,
    can_manage_fraud: false,
  },
  fraud_ops: {
    role: "fraud_ops",
    name: "Victor Hale (Fraud Analyst)",
    person_id: "victor.hale",
    department: "Fraud Operations",
    badge: "Security & Fraud Intel",
    badgeColor: "red",
    desc: "Fraud & Alerts Intel: Inspects flagged syndicates and monitors alert queues.",
    allowed_stages: ["closed"],
    can_release_funds: false,
    can_manage_fraud: true,
  }
};

const state = {
  currentUser: null,
  activeRole: "admin",
  summary: null,
  selectedDeal: null,
  tables: [],
  currentTable: "intake",
  currentTableRecords: [],
  currentTableMetadata: null,
  alerts: [],
  fraudRecords: [],
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(path, options = {}) {
  const authHeader = state.currentUser ? `Bearer ${state.currentUser.person_id}` : "Bearer lauren.kim";
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "Authorization": authHeader,
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

function showToast(message, error = false) {
  const toast = $("#toast");
  if (!toast) return;
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.className = "toast", 3400);
}

// -------------------------------------------------------------
// Authentication & Officer Workstation Session Management
// -------------------------------------------------------------

const OFFICER_CREDENTIALS = {
  "lauren.kim": {
    name: "Lauren Kim",
    title: "Chief Lending Officer (Admin)",
    email: "lauren.kim@trinitycommercial.example",
    initials: "LK",
    roleClass: "admin"
  },
  "olivia.chen": {
    name: "Olivia Chen",
    title: "Intake Specialist",
    email: "olivia.chen@trinitycommercial.example",
    initials: "OC",
    roleClass: "intake"
  },
  "marcus.lee": {
    name: "Marcus Lee",
    title: "Credit Officer",
    email: "marcus.lee@trinitycommercial.example",
    initials: "ML",
    roleClass: "credit"
  },
  "priscilla.nguyen": {
    name: "Priscilla Nguyen",
    title: "Committee Chair",
    email: "priscilla.nguyen@trinitycommercial.example",
    initials: "PN",
    roleClass: "committee"
  },
  "tasha.reed": {
    name: "Tasha Reed",
    title: "Closing & Funding Officer",
    email: "tasha.reed@trinitycommercial.example",
    initials: "TR",
    roleClass: "closing"
  },
  "victor.hale": {
    name: "Victor Hale",
    title: "Fraud Operations Analyst",
    email: "victor.hale@trinitycommercial.example",
    initials: "VH",
    roleClass: "fraud"
  }
};

function openPasswordModal(personId) {
  const officer = OFFICER_CREDENTIALS[personId] || {
    name: "Bank Officer",
    title: "Commercial Lending",
    email: `${personId}@trinitycommercial.example`,
    initials: "BO",
    roleClass: "admin"
  };

  const personInput = $("#pass-modal-person-id");
  if (personInput) personInput.value = personId;

  const avatar = $("#pass-modal-avatar");
  if (avatar) {
    avatar.textContent = officer.initials;
    avatar.className = `account-avatar ${officer.roleClass}`;
  }

  const nameEl = $("#pass-modal-name");
  if (nameEl) nameEl.textContent = officer.name;

  const titleEl = $("#pass-modal-title");
  if (titleEl) titleEl.textContent = officer.title;

  const emailEl = $("#pass-modal-email");
  if (emailEl) emailEl.textContent = officer.email;

  const passInput = $("#pass-modal-input");
  if (passInput) passInput.value = "";

  $("#officer-password-modal")?.classList.remove("hidden");
  setTimeout(() => passInput?.focus(), 60);
}

function closePasswordModal() {
  $("#officer-password-modal")?.classList.add("hidden");
}

function applyUserSession(user) {
  state.currentUser = user;
  state.activeRole = user.role || "admin";

  // Update Sidebar User Profile
  const avatarEl = $("#sidebar-user-avatar");
  if (avatarEl) avatarEl.textContent = user.initials || "PB";
  const nameEl = $("#sidebar-user-name");
  if (nameEl) nameEl.textContent = user.name || "Officer";
  const titleEl = $("#sidebar-user-title");
  if (titleEl) titleEl.textContent = user.title || user.role_badge || "";

  // Update Department Access Banner in Tables View
  const bannerTitle = $("#dept-banner-title");
  if (bannerTitle) bannerTitle.textContent = user.role_badge ? `Viewing as ${user.role_badge} (${user.name})` : `Viewing as ${user.name}`;
  const bannerDesc = $("#dept-banner-desc");
  if (bannerDesc) bannerDesc.textContent = user.description || "Authorized bank officer session.";
  const bannerBadge = $("#dept-banner-badge");
  if (bannerBadge) {
    bannerBadge.textContent = user.department || "Operations";
    bannerBadge.className = `badge ${user.role === "admin" ? "green" : user.role === "fraud_ops" ? "red" : user.role === "closing" ? "amber" : "blue"}`;
  }

  // Hide Auth & Borrower Screens, Show Bank Shell
  $("#borrower-auth-screen")?.classList.add("hidden");
  $("#borrower-dashboard")?.classList.add("hidden");
  $("#auth-screen")?.classList.add("hidden");
  $("#bank-shell")?.classList.remove("hidden");

  // Select initial table authorized for this officer
  if (user.role !== "admin" && user.allowed_stages && user.allowed_stages.length > 0) {
    state.currentTable = user.allowed_stages[0];
  } else {
    state.currentTable = "intake";
  }

  loadTablesList();
  renderAlertsPage();
  renderFraudPage();
}

function showBorrowerAuth() {
  $("#auth-screen")?.classList.add("hidden");
  $("#bank-shell")?.classList.add("hidden");
  $("#borrower-dashboard")?.classList.add("hidden");
  $("#borrower-auth-screen")?.classList.remove("hidden");
}

function showStaffAuth() {
  $("#borrower-auth-screen")?.classList.add("hidden");
  $("#borrower-dashboard")?.classList.add("hidden");
  $("#bank-shell")?.classList.add("hidden");
  $("#auth-screen")?.classList.remove("hidden");
}

function showBorrowerDashboard(borrower) {
  state.currentBorrower = borrower;
  $("#borrower-auth-screen")?.classList.add("hidden");
  $("#auth-screen")?.classList.add("hidden");
  $("#bank-shell")?.classList.add("hidden");
  $("#borrower-dashboard")?.classList.remove("hidden");

  loadBorrowerStatus(borrower.folder || borrower.business_name);
}

async function loginUser(identifier, password) {
  if (!password || !password.trim()) {
    showToast("Password required: please type workstation password.", true);
    return false;
  }
  try {
    showToast("Verifying credentials...");
    const res = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ person_id: identifier, password: password.trim() })
    });
    if (!res.user) throw new Error("Authentication failed");
    localStorage.setItem("dealpilot_user", JSON.stringify(res.user));
    closePasswordModal();
    applyUserSession(res.user);
    showToast(`Welcome, ${res.user.name} (${res.user.title})!`);
    return true;
  } catch (err) {
    showToast(err.message, true);
    const passInput = $("#pass-modal-input");
    if (passInput) {
      passInput.value = "";
      passInput.focus();
    }
    return false;
  }
}

async function logoutUser() {
  try {
    await api("/api/auth/logout", { method: "POST", body: "{}" });
  } catch (e) {}
  localStorage.removeItem("dealpilot_user");
  state.currentUser = null;
  $("#bank-shell")?.classList.add("hidden");
  showBorrowerAuth();
  showToast("Officer signed out of workstation.");
}

async function logoutBorrower() {
  localStorage.removeItem("dealpilot_borrower");
  state.currentBorrower = null;
  $("#borrower-dashboard")?.classList.add("hidden");
  showBorrowerAuth();
  showToast("Borrower signed out.");
}

async function loadBorrowerStatus(folder) {
  if (!folder) return;
  try {
    const data = await api(`/api/borrower/status?folder=${encodeURIComponent(folder)}`);
    const bizEl = $("#borrower-dash-biz");
    if (bizEl) bizEl.textContent = data.folder || folder;

    const nameEl = $("#borrower-dash-name");
    if (nameEl) nameEl.textContent = data.extracted?.guarantor_name || state.currentBorrower?.guarantor_name || "Borrower";

    const titleEl = $("#borrower-dash-stage-title");
    if (titleEl) titleEl.textContent = data.stage_label || "Application Review";

    const descEl = $("#borrower-dash-status-desc");
    if (descEl) {
      descEl.textContent = data.waiting_on ? `Status: Waiting on ${data.waiting_on}` : "Your application is active and under review by Trinity Commercial Bank underwriters.";
    }

    const amtEl = $("#borrower-dash-amount");
    if (amtEl) {
      const amt = data.extracted?.amount_requested || state.currentBorrower?.amount_requested || 150000;
      amtEl.textContent = `$${Number(amt).toLocaleString()}`;
    }

    const purpEl = $("#borrower-dash-purpose");
    if (purpEl) {
      const purp = data.extracted?.purpose || state.currentBorrower?.purpose || "Working Capital";
      purpEl.textContent = purp.replace(/_/g, " ");
    }

    // 5-Stage Step Progress
    const step = data.stage_step || 1;
    for (let i = 1; i <= 5; i++) {
      const stepEl = $(`#bstep-${i}`);
      if (!stepEl) continue;
      stepEl.classList.remove("active", "completed");
      if (i < step) {
        stepEl.classList.add("completed");
      } else if (i === step) {
        stepEl.classList.add("active");
      }
    }

    // Checklist Rendering
    const checklistItems = $("#borrower-checklist-items");
    const checklist = data.checklist || [];
    let receivedCount = 0;
    if (checklistItems) {
      checklistItems.innerHTML = "";
      checklist.forEach(item => {
        const isRec = item.status === "received";
        if (isRec) receivedCount++;
        const row = document.createElement("div");
        row.className = `checklist-item ${isRec ? "received" : ""}`;
        row.innerHTML = `
          <div>
            <strong style="font-size:13px;color:#0f172a;display:block;">${escapeHtml(item.label)}</strong>
            <small style="font-size:11px;color:#64748b;">${escapeHtml(item.description || "")}</small>
          </div>
          <span class="badge ${isRec ? "green" : "amber"}">${isRec ? "✅ Received" : "⏳ Pending"}</span>
        `;
        checklistItems.appendChild(row);
      });
    }

    const badgeEl = $("#borrower-docs-badge");
    if (badgeEl) badgeEl.textContent = `${receivedCount} of ${checklist.length} Submitted`;

    // Submitted Files Table
    const filesTbody = $("#borrower-files-tbody");
    const files = data.files || [];
    if (filesTbody) {
      if (files.length === 0) {
        filesTbody.innerHTML = '<tr><td colspan="3" style="padding:12px;text-align:center;color:#94a3b8;">No uploaded documents yet. Submit your first document on the right.</td></tr>';
      } else {
        filesTbody.innerHTML = files.map(f => `
          <tr style="border-bottom:1px solid #f1f5f9;">
            <td style="padding:8px 12px;font-weight:600;color:#1e293b;">${escapeHtml(f.filename || "")}</td>
            <td style="padding:8px 12px;"><span class="badge blue">${escapeHtml(f.doc_type || "document")}</span></td>
            <td style="padding:8px 12px;color:#64748b;">${escapeHtml(shortDate(f.arrived_at) || "Recently")}</td>
          </tr>
        `).join("");
      }
    }
  } catch (err) {
    showToast(`Could not load application status: ${err.message}`, true);
  }
}

// -------------------------------------------------------------
// 1. Tables Page (PocketBase Collections Explorer)
// -------------------------------------------------------------

async function loadTablesList() {
  const user = state.currentUser || ROLES["admin"];
  try {
    const data = await api(`/api/tables?person_id=${encodeURIComponent(user.person_id)}&role=${encodeURIComponent(user.role)}`);
    state.tables = data.tables || [];
    const countBadge = $("#nav-tables-count");
    if (countBadge) countBadge.textContent = state.tables.length;

    renderTablesPills(state.tables);

    if (!state.currentTable || !state.tables.find(t => t.name === state.currentTable)) {
      state.currentTable = user.role !== "admin" && user.allowed_stages?.[0] ? user.allowed_stages[0] : "intake";
    }
    await selectTable(state.currentTable);
  } catch (e) {
    console.error("Failed to load tables list:", e);
  }
}

function renderTablesPills(tables) {
  const container = $("#tables-pills");
  if (!container) return;

  const user = state.currentUser || ROLES["admin"];
  const isSuperAdmin = user.role === "admin";
  const allowedStages = new Set(user.allowed_stages || []);

  const stages = ORDER.map(name => tables.find(t => t.name === name)).filter(Boolean);
  const supporting = tables.filter(t => !ORDER.includes(t.name));

  // Count active deals
  const totalPipelineDeals = stages.reduce((acc, t) => acc + (t.count || 0), 0);
  const dealsChip = $("#active-deals-count-chip");
  if (dealsChip) dealsChip.textContent = `${totalPipelineDeals} Active Deals`;

  const stageOrderLabels = {
    intake: "01",
    credit: "02",
    committee: "03",
    product: "04",
    committed: "05",
    closed: "06"
  };

  container.innerHTML = `
    <!-- 1. Commercial Loan Pipeline Queues (Stages 01 - 06) -->
    <div class="pipeline-section-card">
      <div class="section-label-row">
        <div>
          <span class="section-title">COMMERCIAL LENDING PIPELINE</span>
          <span class="section-subtitle" style="margin-left:8px;">${totalPipelineDeals} active tickets advancing through bank underwriting gates</span>
        </div>
        <span class="badge green" style="font-size:10px;">Six-Gate State Machine</span>
      </div>
      <div class="pipeline-stages-grid">
        ${stages.map((t, idx) => {
          const isLocked = !isSuperAdmin && !allowedStages.has(t.name);
          const isActive = t.name === state.currentTable;
          const stageNum = stageOrderLabels[t.name] || `0${idx + 1}`;

          return `
            <button class="stage-pipeline-card ${isActive ? "active" : ""} ${isLocked ? "locked" : ""}" data-table-select="${escapeHtml(t.name)}" title="${isLocked ? `Restricted to ${t.name.toUpperCase()} department` : t.description}">
              <div class="stage-card-top">
                <span class="stage-step-pill">${stageNum}</span>
                <span class="stage-count-badge ${t.count > 0 ? "has-records" : ""}">${t.count ?? 0}</span>
              </div>
              <div class="stage-card-bottom">
                <span class="stage-card-icon">${isLocked ? "🔒" : escapeHtml(t.icon || "📋")}</span>
                <div class="stage-card-text">
                  <strong class="stage-card-name">${escapeHtml(t.name)}</strong>
                  <span class="stage-card-sub">${isLocked ? "Restricted" : (t.count === 1 ? "1 ticket" : `${t.count} tickets`)}</span>
                </div>
              </div>
              ${idx < stages.length - 1 ? `<span class="stage-connector-arrow">›</span>` : ""}
            </button>
          `;
        }).join("")}
      </div>
    </div>

    <!-- 2. Supporting Database Collections Ribbon -->
    <div class="supporting-collections-row">
      <span class="section-title-sm">DATA, EVIDENCE, AUDIT & SECURITY REPOSITORIES</span>
      <div class="supporting-pills-wrap">
        ${supporting.map(t => {
          const isActive = t.name === state.currentTable;
          const isFraud = t.name === "fraud_detected";
          const isAlerts = t.name === "alerts";

          return `
            <button class="support-pill ${isActive ? "active" : ""} ${isFraud ? "fraud-pill" : ""} ${isAlerts ? "alerts-pill" : ""}" data-table-select="${escapeHtml(t.name)}" title="${escapeHtml(t.description || t.name)}">
              <span class="support-icon">${escapeHtml(t.icon || "📋")}</span>
              <span class="support-name">${escapeHtml(t.name)}</span>
              <span class="support-count">${t.count ?? 0}</span>
            </button>
          `;
        }).join("")}
      </div>
    </div>
  `;
}

async function selectTable(tableName) {
  state.currentTable = tableName;
  $$("[data-table-select]").forEach(el => {
    el.classList.toggle("active", el.dataset.tableSelect === tableName);
  });

  const user = state.currentUser || ROLES["admin"];
  const meta = state.tables.find(t => t.name === tableName) || {name: tableName, badge: "Collection", icon: "📋", description: ""};
  state.currentTableMetadata = meta;

  const titleEl = $("#active-table-title");
  if (titleEl) titleEl.textContent = meta.name;
  const iconEl = $("#active-table-icon");
  if (iconEl) iconEl.textContent = meta.icon || "📋";
  const badgeEl = $("#active-table-badge");
  if (badgeEl) badgeEl.textContent = meta.badge || "Collection";
  const descEl = $("#active-table-desc");
  if (descEl) descEl.textContent = meta.description || "";

  const tbody = $("#dynamic-table-body");
  const thead = $("#dynamic-table-head");

  // Check RBAC before querying stage records
  const isStage = ORDER.includes(tableName);
  const isSuperAdmin = user.role === "admin";
  const allowedStages = new Set(user.allowed_stages || []);

  if (!isSuperAdmin && isStage && !allowedStages.has(tableName)) {
    if (thead) thead.innerHTML = `<tr><th>Department Access Control</th></tr>`;
    if (tbody) {
      tbody.innerHTML = `
        <tr><td class="empty-state" style="padding:48px 24px;background:#fefbfb;">
          <div style="max-width:520px;margin:0 auto;text-align:center;">
            <span style="font-size:42px;display:block;margin-bottom:12px;">🔒</span>
            <h3 style="margin:0 0 8px;font-size:18px;color:#991b1b;">Department Access Restricted</h3>
            <p style="margin:0 0 16px;font-size:13px;color:var(--muted);line-height:1.5;">
              You are signed in as <strong>${escapeHtml(user.name)}</strong> (${escapeHtml(user.title)}).
              Your credentials are restricted exclusively to <strong>${escapeHtml(user.department)}</strong>.
              The <strong>${escapeHtml(tableName.toUpperCase())}</strong> stage queue requires authorized department clearance.
            </p>
            <div style="display:flex;gap:8px;justify-content:center;">
              <span class="badge red">Queue Locked</span>
              <button class="button ghost" style="font-size:12px;padding:4px 10px;" data-table-select="${user.allowed_stages?.[0] || 'files'}">
                Switch to My Work Queue (${user.allowed_stages?.[0] || 'files'})
              </button>
            </div>
          </div>
        </td></tr>`;
    }
    const countEl = $("#active-table-count");
    if (countEl) countEl.textContent = `Locked`;
    return;
  }

  if (tbody) {
    tbody.innerHTML = `<tr><td colspan="12" style="text-align:center;padding:24px;color:var(--muted)">Loading records from PocketBase <code>${escapeHtml(tableName)}</code>...</td></tr>`;
  }

  try {
    const res = await api(`/api/tables/${encodeURIComponent(tableName)}?person_id=${encodeURIComponent(user.person_id)}&role=${encodeURIComponent(user.role)}`);
    state.currentTableRecords = res.records || [];
    const countEl = $("#active-table-count");
    if (countEl) countEl.textContent = `${state.currentTableRecords.length} records`;
    renderDynamicTable(state.currentTableRecords, meta);
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="12" class="empty-state" style="color:var(--red)">Failed to load table: ${escapeHtml(err.message)}</td></tr>`;
    }
  }
}

function renderDynamicTable(records, meta) {
  const thead = $("#dynamic-table-head");
  const tbody = $("#dynamic-table-body");
  if (!thead || !tbody) return;

  const query = ($("#table-search")?.value || "").toLowerCase().trim();

  const filtered = query ? records.filter(r => {
    return Object.values(r).some(v => {
      if (v == null) return false;
      if (typeof v === "object") return JSON.stringify(v).toLowerCase().includes(query);
      return String(v).toLowerCase().includes(query);
    });
  }) : records;

  if (!records.length) {
    thead.innerHTML = `<tr><th>Collection Status</th></tr>`;
    tbody.innerHTML = `<tr><td class="empty-state">No records found in PocketBase collection <code>${escapeHtml(meta.name)}</code> for your department.</td></tr>`;
    return;
  }

  const baseCols = meta.columns || [];
  const recordCols = [...new Set(records.flatMap(r => Object.keys(r)))].filter(c => !["collectionId", "collectionName"].includes(c));
  
  const priorityCols = baseCols.filter(c => recordCols.includes(c));
  const remainingCols = recordCols.filter(c => !priorityCols.includes(c) && c !== "id");
  const finalCols = ["id", ...priorityCols, ...remainingCols].slice(0, 8);

  thead.innerHTML = `<tr>${finalCols.map(col => `<th>${escapeHtml(col.replaceAll("_", " "))}</th>`).join("")}<th>Actions</th></tr>`;

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="${finalCols.length + 1}" class="empty-state">No records matching "${escapeHtml(query)}"</td></tr>`;
    return;
  }

  const user = state.currentUser || ROLES["admin"];

  tbody.innerHTML = filtered.map(r => {
    const isClosedQueue = meta.name === "closed";
    const canDisburse = isClosedQueue && r.status !== "funded" && (user.role === "admin" || user.role === "closing");

    return `<tr>
      ${finalCols.map(col => {
        const val = r[col];
        if (val == null || val === "") return `<td><span class="muted">—</span></td>`;
        if (typeof val === "object") {
          const count = Object.keys(val).length;
          return `<td><span class="table-cell-json" data-inspect-json='${escapeHtml(JSON.stringify(val))}'>📦 ${count} Extracted Fields</span></td>`;
        }
        if (col === "status") {
          const tone = (val === "funded" || val === "approved") ? "green" : val === "declined" ? "red" : "amber";
          return `<td><span class="badge ${tone}" style="font-size:11px;padding:3px 8px;text-transform:uppercase;letter-spacing:0.04em;">● ${escapeHtml(val)}</span></td>`;
        }
        if (col === "waiting_on") {
          if (String(val).includes("fraud")) {
            return `<td><span class="badge red" style="font-size:11px;padding:3px 8px;">🚨 ${escapeHtml(val)}</span></td>`;
          }
          if (String(val).trim()) {
            return `<td><span class="badge amber" style="font-size:11px;padding:3px 8px;max-width:280px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">⚠️ ${escapeHtml(val)}</span></td>`;
          }
          return `<td><span class="muted">—</span></td>`;
        }
        if (col === "assignee") {
          return `<td><span class="badge gray" style="font-size:11px;padding:3px 8px;">👤 ${escapeHtml(val)}</span></td>`;
        }
        if (col === "folder" || col === "deal_id") {
          return `<td><div style="display:flex;align-items:center;gap:6px;"><span style="font-size:14px;">🏢</span><strong style="font-size:13px;color:var(--ink);">${escapeHtml(val)}</strong></div></td>`;
        }
        if (col === "amount_requested" && !isNaN(Number(val))) {
          return `<td><strong style="color:var(--green);font-size:14px;font-family:Manrope,sans-serif;">${money(Number(val))}</strong></td>`;
        }
        if (col === "id") {
          return `<td><code style="background:#f1f5f9;color:#475569;font-size:11px;padding:3px 6px;border-radius:5px;">${escapeHtml(String(val).slice(0, 12))}</code></td>`;
        }
        const str = String(val);
        const display = str.length > 44 ? str.slice(0, 42) + "…" : str;
        return `<td title="${escapeHtml(str)}">${escapeHtml(display)}</td>`;
      }).join("")}
      <td style="white-space:nowrap;">
        <button class="button ghost" style="padding:4px 9px;font-size:11px;" data-inspect-record="${escapeHtml(r.id)}">🔍 Inspect</button>
        ${canDisburse ? `
          <button class="button primary" style="padding:4px 10px;font-size:11px;background:linear-gradient(135deg, #10b981, #059669);border:none;margin-left:6px;box-shadow:0 2px 8px rgba(16,185,129,0.3);" data-release-fund="${escapeHtml(r.folder)}">
            ⚡ Release Funds
          </button>
        ` : ""}
      </td>
    </tr>`;
  }).join("");
}

// -------------------------------------------------------------
// 2. Alerts Page
// -------------------------------------------------------------

async function renderAlertsPage() {
  const tbody = $("#alerts-table-body");
  if (!tbody) return;

  const user = state.currentUser || ROLES["admin"];

  try {
    const res = await api(`/api/alerts?person_id=${encodeURIComponent(user.person_id)}&role=${encodeURIComponent(user.role)}`);
    state.alerts = res.records || [];

    const totalEl = $("#alerts-metric-total");
    if (totalEl) totalEl.textContent = state.alerts.length;
    const slaEl = $("#alerts-metric-sla");
    if (slaEl) slaEl.textContent = state.alerts.filter(a => a.level === "alert" && !a.message?.includes("FRAUD")).length;
    const escEl = $("#alerts-metric-escalations");
    if (escEl) escEl.textContent = state.alerts.filter(a => a.level === "escalation").length;
    const fraudEl = $("#alerts-metric-fraud");
    if (fraudEl) fraudEl.textContent = state.alerts.filter(a => a.message?.includes("FRAUD")).length;

    const countBadge = $("#nav-alerts-count");
    if (countBadge) countBadge.textContent = state.alerts.length;

    const query = ($("#alerts-search")?.value || "").toLowerCase().trim();
    const filtered = query ? state.alerts.filter(a => {
      return (a.folder || "").toLowerCase().includes(query)
          || (a.message || "").toLowerCase().includes(query)
          || (a.to || "").toLowerCase().includes(query);
    }) : state.alerts;

    if (!filtered.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-state">${query ? `No alerts match "${escapeHtml(query)}"` : "No active alerts in PocketBase for this department."}</td></tr>`;
      return;
    }

    tbody.innerHTML = filtered.map(a => {
      const isFraud = (a.message || "").includes("FRAUD");
      const isEscalation = a.level === "escalation";
      const levelTone = isFraud ? "red" : isEscalation ? "red" : "amber";

      return `<tr>
        <td style="white-space:nowrap;"><small>${shortDate(a.at)}</small></td>
        <td><strong>${escapeHtml(a.folder)}</strong></td>
        <td><span class="badge blue">${escapeHtml(a.stage || "—")}</span></td>
        <td><span class="badge ${levelTone}">${isFraud ? "🚨 Fraud" : isEscalation ? "↗ Escalation" : "⚠️ Warning"}</span></td>
        <td><code>${escapeHtml(a.to)}</code></td>
        <td style="font-size:12px;line-height:1.4;max-width:480px;">${escapeHtml(a.message)}</td>
      </tr>`;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-state" style="color:var(--red)">Failed to load alerts: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// -------------------------------------------------------------
// 3. Fraud Detected Page
// -------------------------------------------------------------

async function renderFraudPage() {
  const tbody = $("#fraud-table-body");
  if (!tbody) return;

  try {
    const res = await api("/api/fraud-detected");
    state.fraudRecords = res.records || [];

    const countEl = $("#fraud-stat-count");
    if (countEl) countEl.textContent = state.fraudRecords.length;
    const heldEl = $("#fraud-stat-held");
    if (heldEl) heldEl.textContent = state.fraudRecords.filter(f => f.stage === "closed" || f.stage === "committee").length;

    const countBadge = $("#nav-fraud-count");
    if (countBadge) countBadge.textContent = state.fraudRecords.length;

    const query = ($("#fraud-search")?.value || "").toLowerCase().trim();
    const filtered = query ? state.fraudRecords.filter(f => {
      return (f.folder || "").toLowerCase().includes(query)
          || (f.reason || "").toLowerCase().includes(query);
    }) : state.fraudRecords;

    if (!filtered.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="empty-state">No coordinated fraud clusters currently flagged in PocketBase.</td></tr>`;
      return;
    }

    const user = state.currentUser || ROLES["admin"];
    const canManageFraud = user.role === "admin" || user.role === "fraud_ops";

    tbody.innerHTML = filtered.map(f => {
      return `<tr>
        <td><strong style="color:#991b1b">${escapeHtml(f.folder)}</strong></td>
        <td><span class="badge blue">${escapeHtml(f.stage || "Intake")}</span></td>
        <td style="white-space:nowrap;"><small>${shortDate(f.at)}</small></td>
        <td style="font-size:12px;line-height:1.4;max-width:440px;">${escapeHtml(f.reason)}</td>
        <td><span class="badge red">🚫 Capital Held</span></td>
        <td style="white-space:nowrap;">
          ${canManageFraud ? `
            <button class="button ghost" style="padding:4px 8px;font-size:11px;color:var(--red)" data-unflag-fraud="${escapeHtml(f.folder)}">
              Release Hold
            </button>
          ` : `<small class="muted">Admin only</small>`}
        </td>
      </tr>`;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-state" style="color:var(--red)">Failed to load fraud flags: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// -------------------------------------------------------------
// Fraud Detection Agent Trigger
// -------------------------------------------------------------

async function triggerFraudDetectionAgent() {
  const btn = $("#btn-run-fraud-agent");
  if (btn) btn.disabled = true;
  showToast("Fraud Detection Agent is scanning multi-deal syndicate signals...");

  try {
    const res = await api("/api/fraud/scan", {method: "POST", body: "{}"});
    showToast(`Scan complete: ${res.scanned} scanned, ${res.flagged} flagged, ${res.alerts_released} alerts released to PocketBase!`);
    await renderFraudPage();
    await renderAlertsPage();
    await loadTablesList();
  } catch (err) {
    showToast(`Agent error: ${err.message}`, true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// -------------------------------------------------------------
// Capital Release / Funding Execution
// -------------------------------------------------------------

async function releaseDealFunding(folder) {
  if (!confirm(`Confirm capital release and funds disbursement for '${folder}'?`)) return;
  showToast(`Releasing funding for ${folder}...`);

  const user = state.currentUser || ROLES["admin"];

  try {
    const res = await api("/api/deals/fund", {
      method: "POST",
      body: JSON.stringify({ folder: folder, person_id: user.person_id, role: user.role })
    });
    showToast(`Funds successfully disbursed for ${folder}!`);
    await loadTablesList();
    await renderAlertsPage();
  } catch (err) {
    showToast(err.message, true);
  }
}

// -------------------------------------------------------------
// Navigation & Views
// -------------------------------------------------------------

function switchView(view) {
  $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach(section => section.classList.toggle("active", section.id === `${view}-view`));
  const titles = {
    tables: "PocketBase Tables",
    alerts: "Operational Alerts & Escalations",
    fraud: "Fraud Detected & Coordinated Rings",
  };
  const titleEl = $("#page-title");
  if (titleEl) titleEl.textContent = titles[view] || "Control Tower";

  if (view === "tables") loadTablesList();
  if (view === "alerts") renderAlertsPage();
  if (view === "fraud") renderFraudPage();
}

// -------------------------------------------------------------
// Modals & Record Inspectors
// -------------------------------------------------------------

function openRecordInspector(record) {
  const root = $("#record-modal-root");
  if (!root) return;
  root.innerHTML = `
    <div class="record-modal">
      <div class="record-modal-header">
        <div>
          <span class="badge blue" style="margin-bottom:4px">PocketBase Record</span>
          <h3 style="margin:0">${escapeHtml(record.folder || record.deal_id || record.id || "Record Details")}</h3>
        </div>
        <button class="button ghost" data-close-record-modal style="padding:4px 10px;font-size:14px">✕</button>
      </div>
      <div class="record-modal-body">
        <div>
          <strong style="display:block;margin-bottom:8px;font-size:12px;color:var(--muted);text-transform:uppercase">Structured Fields</strong>
          <table class="field-table">
            <tbody>
              ${Object.entries(record).map(([k, v]) => `
                <tr>
                  <th>${escapeHtml(k)}</th>
                  <td>${typeof v === "object" && v !== null ? `<pre style="margin:0;font-family:ui-monospace,monospace;font-size:11px;background:#f8fafc;padding:8px;border-radius:6px;max-height:160px;overflow-y:auto">${escapeHtml(JSON.stringify(v, null, 2))}</pre>` : escapeHtml(v ?? "—")}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
        <div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <strong style="font-size:12px;color:var(--muted);text-transform:uppercase">Raw Record JSON</strong>
            <button class="button ghost" id="btn-copy-record-json" style="padding:4px 8px;font-size:11px">Copy JSON</button>
          </div>
          <pre class="raw-json-block" id="raw-record-json-text">${escapeHtml(JSON.stringify(record, null, 2))}</pre>
        </div>
      </div>
    </div>
  `;
  root.classList.remove("hidden");
}

function openJsonInspector(jsonObj) {
  const root = $("#record-modal-root");
  if (!root) return;
  root.innerHTML = `
    <div class="record-modal">
      <div class="record-modal-header">
        <div>
          <span class="badge blue" style="margin-bottom:4px">Nested Payload</span>
          <h3 style="margin:0">Structured Fact JSON</h3>
        </div>
        <button class="button ghost" data-close-record-modal style="padding:4px 10px;font-size:14px">✕</button>
      </div>
      <div class="record-modal-body">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <strong style="font-size:12px;color:var(--muted);text-transform:uppercase">JSON Content</strong>
          <button class="button ghost" id="btn-copy-record-json" style="padding:4px 8px;font-size:11px">Copy JSON</button>
        </div>
        <pre class="raw-json-block" id="raw-record-json-text">${escapeHtml(JSON.stringify(jsonObj, null, 2))}</pre>
      </div>
    </div>
  `;
  root.classList.remove("hidden");
}

// -------------------------------------------------------------
// Global Event Handlers
// -------------------------------------------------------------

document.addEventListener("click", async event => {
  // Trigger Password Modal on Officer Card Click
  const authCardBtn = event.target.closest("[data-auth-card]");
  if (authCardBtn) {
    return openPasswordModal(authCardBtn.dataset.authCard);
  }

  if (event.target.closest("#btn-close-pass-modal") || event.target.closest("#btn-cancel-pass-modal")) {
    return closePasswordModal();
  }

  // Logout button in sidebar footer
  if (event.target.closest("#btn-logout")) {
    return logoutUser();
  }

  const tableSelect = event.target.closest("[data-table-select]");
  if (tableSelect) return selectTable(tableSelect.dataset.tableSelect);

  const inspectRecBtn = event.target.closest("[data-inspect-record]");
  if (inspectRecBtn) {
    const recId = inspectRecBtn.dataset.inspectRecord;
    const found = state.currentTableRecords.find(r => String(r.id) === String(recId));
    if (found) openRecordInspector(found);
    return;
  }

  const inspectJsonSpan = event.target.closest("[data-inspect-json]");
  if (inspectJsonSpan) {
    try {
      const data = JSON.parse(inspectJsonSpan.dataset.inspectJson);
      openJsonInspector(data);
    } catch (e) {}
    return;
  }

  const releaseBtn = event.target.closest("[data-release-fund]");
  if (releaseBtn) {
    return releaseDealFunding(releaseBtn.dataset.releaseFund);
  }

  const unflagBtn = event.target.closest("[data-unflag-fraud]");
  if (unflagBtn) {
    const folder = unflagBtn.dataset.unflagFraud;
    if (!confirm(`Release fraud hold for '${folder}'?`)) return;
    try {
      await api(`/api/fraud/unflag/${encodeURIComponent(folder)}`, {method:"DELETE"});
      showToast(`Fraud hold released for ${folder}`);
      await renderFraudPage();
      await loadTablesList();
    } catch (err) { showToast(err.message, true); }
    return;
  }

  if (event.target.closest("[data-close-record-modal]")) {
    $("#record-modal-root")?.classList.add("hidden");
    return;
  }

  if (event.target.closest("#btn-copy-record-json")) {
    const text = $("#raw-record-json-text")?.textContent || "";
    if (text) {
      await navigator.clipboard.writeText(text);
      showToast("Record JSON copied to clipboard");
    }
    return;
  }

  if (event.target.closest("#refresh-tables-btn")) {
    showToast(`Refreshing ${state.currentTable}...`);
    await selectTable(state.currentTable);
    return;
  }

  if (event.target.closest("#refresh-alerts-btn")) {
    showToast("Refreshing alerts...");
    await renderAlertsPage();
    return;
  }

  if (event.target.closest("#refresh-fraud-btn")) {
    showToast("Refreshing fraud records...");
    await renderFraudPage();
    return;
  }

  if (event.target.closest("#btn-run-fraud-agent")) {
    return triggerFraudDetectionAgent();
  }

  if (event.target.closest("#btn-open-flag-modal")) {
    $("#flag-deal-modal")?.classList.remove("hidden");
    return;
  }

  if (event.target.closest("#btn-close-flag-modal") || event.target.closest("#btn-cancel-flag-modal")) {
    $("#flag-deal-modal")?.classList.add("hidden");
    return;
  }

  if (event.target.closest("#export-table-btn")) {
    const blob = new Blob([JSON.stringify(state.currentTableRecords, null, 2)], {type:"application/json"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `pocketbase_${state.currentTable}_records.json`;
    link.click();
    URL.revokeObjectURL(url);
    showToast(`Exported ${state.currentTableRecords.length} records to JSON`);
    return;
  }

  if (event.target.closest("#btn-export-alerts")) {
    const blob = new Blob([JSON.stringify(state.alerts, null, 2)], {type:"application/json"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `pocketbase_alerts.json`;
    link.click();
    URL.revokeObjectURL(url);
    showToast(`Exported ${state.alerts.length} alerts to JSON`);
    return;
  }

  const nav = event.target.closest("[data-view]");
  if (nav) return switchView(nav.dataset.view);

  const advance = event.target.closest("[data-advance]");
  if (advance) {
    advance.disabled = true;
    try {
      const result = await api("/api/sim/advance", {method:"POST", body:JSON.stringify({by:advance.dataset.advance})});
      showToast(`Advanced to ${shortDate(result.now)}`);
      await loadTablesList();
      await renderAlertsPage();
      await renderFraudPage();
    } catch (error) { showToast(error.message, true); }
    finally { advance.disabled = false; }
  }
});

// Toggle password visibility in modal
$("#btn-toggle-modal-pass")?.addEventListener("click", () => {
  const passInput = $("#pass-modal-input");
  if (!passInput) return;
  passInput.type = passInput.type === "password" ? "text" : "password";
});

// Toggle password visibility in manual login
$("#btn-toggle-manual-pass")?.addEventListener("click", () => {
  const passInput = $("#auth-password-input");
  if (!passInput) return;
  passInput.type = passInput.type === "password" ? "text" : "password";
});

// Quick-Fill Persona Chips in Auth Screen
$$(".auth-chip")?.forEach(chip => {
  chip.addEventListener("click", () => {
    $$(".auth-chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    const email = chip.dataset.fillEmail;
    const emailInput = $("#auth-email-input");
    const passInput = $("#auth-password-input");
    if (emailInput) emailInput.value = email;
    if (passInput) {
      passInput.value = "";
      passInput.focus();
    }
  });
});

// Officer Password Modal Submit
$("#form-officer-password")?.addEventListener("submit", async event => {
  event.preventDefault();
  const personId = $("#pass-modal-person-id")?.value;
  const password = $("#pass-modal-input")?.value;
  await loginUser(personId, password);
});

// Manual Login Form Submit
$("#form-manual-login")?.addEventListener("submit", async event => {
  event.preventDefault();
  const email = $("#auth-email-input")?.value.trim();
  const password = $("#auth-password-input")?.value.trim();
  if (!email) {
    showToast("Please enter officer email", true);
    return;
  }
  if (!password) {
    showToast("Please enter workstation password", true);
    return;
  }
  await loginUser(email, password);
});

// -------------------------------------------------------------
// Document Upload & AI Parsing Functions
// -------------------------------------------------------------

let selectedUploadFile = null;

async function loadDealFoldersDropdown() {
  const select = $("#upload-deal-folder-select");
  if (!select) return;
  try {
    const res = await api("/api/deal-folders");
    const folders = res.folders || [];
    select.innerHTML = '<option value="" disabled selected>Select an existing deal folder...</option>';
    folders.forEach(f => {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f;
      select.appendChild(opt);
    });
    const newOpt = document.createElement("option");
    newOpt.value = "__new__";
    newOpt.textContent = "+ Create New Deal Folder...";
    select.appendChild(newOpt);
  } catch (e) {
    select.innerHTML = `
      <option value="" disabled selected>Select deal folder...</option>
      <option value="Oakline Auto">Oakline Auto</option>
      <option value="Ridgeway Freight Logistics">Ridgeway Freight Logistics</option>
      <option value="Apex Industrial Supply">Apex Industrial Supply</option>
      <option value="Metroplex Dental Group">Metroplex Dental Group</option>
      <option value="__new__">+ Create New Deal Folder...</option>
    `;
  }
}

function openUploadModal() {
  selectedUploadFile = null;
  const modal = $("#upload-doc-modal");
  const form = $("#form-upload-document");
  const resultContainer = $("#upload-result-container");
  const customInput = $("#upload-custom-folder-input");
  const fileInput = $("#upload-file-input");

  if (form) form.classList.remove("hidden");
  if (resultContainer) resultContainer.classList.add("hidden");
  if (customInput) {
    customInput.classList.add("hidden");
    customInput.value = "";
  }
  if (fileInput) fileInput.value = "";
  resetDropzone();

  loadDealFoldersDropdown();
  modal?.classList.remove("hidden");
}

function closeUploadModal() {
  $("#upload-doc-modal")?.classList.add("hidden");
}

function resetDropzone() {
  selectedUploadFile = null;
  $("#dropzone-empty-state")?.classList.remove("hidden");
  $("#dropzone-file-state")?.classList.add("hidden");
}

function setUploadFile(file) {
  if (!file) return;
  selectedUploadFile = file;
  const nameEl = $("#upload-file-name");
  if (nameEl) nameEl.textContent = file.name;
  const sizeKb = (file.size / 1024).toFixed(1);
  const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
  const sizeEl = $("#upload-file-size");
  if (sizeEl) sizeEl.textContent = file.size > 1024 * 1024 ? `${sizeMb} MB` : `${sizeKb} KB`;

  const ext = file.name.split(".").pop().toLowerCase();
  const icon = $("#upload-file-icon");
  if (icon) {
    icon.textContent = ext === "pdf" ? "📕" : ext === "csv" ? "📊" : (ext === "jpg" || ext === "jpeg" || ext === "png") ? "🖼️" : "📄";
  }

  $("#dropzone-empty-state")?.classList.add("hidden");
  $("#dropzone-file-state")?.classList.remove("hidden");
}

function formatFactValue(key, val) {
  if (val === null || val === undefined) return "—";
  if (typeof val === "number") {
    if (key.includes("amount") || key.includes("balance") || key.includes("deposits") || key.includes("revenue") || key.includes("quote")) {
      return `$${val.toLocaleString()}`;
    }
    return val;
  }
  if (Array.isArray(val)) return val.join(", ");
  if (typeof val === "object") return JSON.stringify(val);
  return String(val);
}

function renderUploadResults(data) {
  const form = $("#form-upload-document");
  const resultContainer = $("#upload-result-container");
  if (form) form.classList.add("hidden");
  if (resultContainer) resultContainer.classList.remove("hidden");

  const metaEl = $("#upload-result-meta");
  if (metaEl) {
    const stageNotice = data.absorbed_stage ? ` Absorbed into <strong>${data.absorbed_stage}</strong> queue facts.` : "";
    metaEl.innerHTML = `Saved to PocketBase <code>files</code> table. Path: <code>${escapeHtml(data.record?.path || "")}</code>.${stageNotice}`;
  }

  const badge = $("#upload-result-type-badge");
  if (badge) {
    badge.textContent = data.document_type || "document";
  }

  const grid = $("#upload-facts-grid");
  if (grid) {
    grid.innerHTML = "";
    const facts = data.facts || {};
    const keys = Object.keys(facts);
    if (keys.length === 0) {
      grid.innerHTML = '<div style="grid-column:1/-1;color:#64748b;font-size:13px;padding:8px;">Document ingested. No specific fact fields detected.</div>';
    } else {
      keys.forEach(k => {
        const item = document.createElement("div");
        item.className = "fact-item-card";
        item.innerHTML = `
          <span class="fact-item-key">${escapeHtml(k.replace(/_/g, " "))}</span>
          <span class="fact-item-val">${escapeHtml(String(formatFactValue(k, facts[k])))}</span>
        `;
        grid.appendChild(item);
      });
    }
  }
}

// Dropzone & File Events
const dropzone = $("#upload-dropzone");
const fileInput = $("#upload-file-input");

dropzone?.addEventListener("click", e => {
  if (e.target.closest("#btn-remove-file")) return;
  fileInput?.click();
});

fileInput?.addEventListener("change", e => {
  if (e.target.files && e.target.files[0]) {
    setUploadFile(e.target.files[0]);
  }
});

$("#btn-remove-file")?.addEventListener("click", e => {
  e.stopPropagation();
  resetDropzone();
  if (fileInput) fileInput.value = "";
});

dropzone?.addEventListener("dragover", e => {
  e.preventDefault();
  dropzone.classList.add("dragover");
});

dropzone?.addEventListener("dragleave", () => {
  dropzone.classList.remove("dragover");
});

dropzone?.addEventListener("drop", e => {
  e.preventDefault();
  dropzone.classList.remove("dragover");
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
    setUploadFile(e.dataTransfer.files[0]);
  }
});

// Deal folder select change
$("#upload-deal-folder-select")?.addEventListener("change", e => {
  const custom = $("#upload-custom-folder-input");
  if (!custom) return;
  if (e.target.value === "__new__") {
    custom.classList.remove("hidden");
    custom.focus();
  } else {
    custom.classList.add("hidden");
  }
});

// Upload Modal Buttons
$("#btn-open-upload-modal")?.addEventListener("click", () => openUploadModal());
$("#btn-open-upload-modal-tools")?.addEventListener("click", () => openUploadModal());
$("#btn-close-upload-modal")?.addEventListener("click", () => closeUploadModal());
$("#btn-cancel-upload")?.addEventListener("click", () => closeUploadModal());

$("#btn-upload-another")?.addEventListener("click", () => {
  openUploadModal();
});

$("#btn-done-upload")?.addEventListener("click", () => {
  closeUploadModal();
  selectTable("files");
});

// Upload Form Submit
$("#form-upload-document")?.addEventListener("submit", async event => {
  event.preventDefault();
  const folderSelect = $("#upload-deal-folder-select")?.value;
  const customFolder = $("#upload-custom-folder-input")?.value.trim();
  const folder = folderSelect === "__new__" ? customFolder : folderSelect;

  if (!folder) {
    showToast("Please choose or specify a deal folder name.", true);
    return;
  }
  if (!selectedUploadFile) {
    showToast("Please select a document file to upload.", true);
    return;
  }

  const docType = $("#upload-doc-type-select")?.value || "auto";
  const submitBtn = $("#btn-submit-upload");
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span>⏳ Ingesting & Extracting...</span>';
  }

  showToast(`Ingesting ${selectedUploadFile.name} and extracting facts...`);

  try {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const base64Data = reader.result.split(",")[1];
        const res = await api("/api/upload", {
          method: "POST",
          body: JSON.stringify({
            folder,
            filename: selectedUploadFile.name,
            content_base64: base64Data,
            doc_type: docType,
            actor: state.currentUser?.person_id || "bank.officer"
          })
        });

        renderUploadResults(res);
        showToast(`Document ingested! ${Object.keys(res.facts || {}).length} facts extracted.`);
        await loadTablesList();
        if (state.currentTable === "files" || state.currentTable === res.absorbed_stage) {
          await loadTableRecords(state.currentTable);
        }
      } catch (err) {
        showToast(err.message, true);
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.innerHTML = '<span>⚡</span> Upload & Extract Facts';
        }
      }
    };
    reader.readAsDataURL(selectedUploadFile);
  } catch (err) {
    showToast(`File read error: ${err.message}`, true);
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = '<span>⚡</span> Upload & Extract Facts';
    }
  }
});

// Manual Fraud Flag Form Submit
$("#form-flag-deal")?.addEventListener("submit", async event => {
  event.preventDefault();
  const folder = $("#flag-deal-folder")?.value.trim();
  const reason = $("#flag-deal-reason")?.value.trim();
  if (!folder || !reason) return;

  try {
    await api("/api/fraud/flag", {
      method: "POST",
      body: JSON.stringify({ folder, reason })
    });
    showToast(`Flagged ${folder} for fraud and released alerts to PocketBase`);
    $("#flag-deal-modal")?.classList.add("hidden");
    $("#flag-deal-folder").value = "";
    $("#flag-deal-reason").value = "";
    await renderFraudPage();
    await renderAlertsPage();
    await loadTablesList();
  } catch (err) {
    showToast(err.message, true);
  }
});

$("#reset-button")?.addEventListener("click", async () => {
  if (!confirm("Reset simulation time to Monday 08:00 CT?")) return;
  await api("/api/sim/reset", {method:"POST", body:"{}"});
  showToast("Simulation clock reset");
  await loadTablesList();
  await renderAlertsPage();
  await renderFraudPage();
});

$("#refresh-button")?.addEventListener("click", async () => {
  await loadTablesList();
  await renderAlertsPage();
  await renderFraudPage();
  showToast("Refreshed all PocketBase collections");
});

$("#table-search")?.addEventListener("input", () => renderDynamicTable(state.currentTableRecords, state.currentTableMetadata));
$("#alerts-search")?.addEventListener("input", () => renderAlertsPage());
$("#fraud-search")?.addEventListener("input", () => renderFraudPage());

// -------------------------------------------------------------
// Borrower Customer Portal Event Handlers
// -------------------------------------------------------------

// Switch between Customer Portal and Staff Workstation Portal
$("#btn-goto-staff-portal")?.addEventListener("click", () => showStaffAuth());
$("#btn-goto-borrower-portal")?.addEventListener("click", () => showBorrowerAuth());
$("#btn-borrower-logout")?.addEventListener("click", () => logoutBorrower());

// Borrower Tabs Toggle (Login vs Register)
$("#tab-borrower-login-btn")?.addEventListener("click", () => {
  $("#tab-borrower-login-btn")?.classList.add("active");
  $("#tab-borrower-register-btn")?.classList.remove("active");
  $("#borrower-login-pane")?.classList.remove("hidden");
  $("#borrower-register-pane")?.classList.add("hidden");
});

$("#tab-borrower-register-btn")?.addEventListener("click", () => {
  $("#tab-borrower-register-btn")?.classList.add("active");
  $("#tab-borrower-login-btn")?.classList.remove("active");
  $("#borrower-register-pane")?.classList.remove("hidden");
  $("#borrower-login-pane")?.classList.add("hidden");
});

// Demo Borrower Quick Fill Chips
$$(".demo-borrower-chip")?.forEach(chip => {
  chip.addEventListener("click", () => {
    const email = chip.dataset.borrowerEmail;
    const pass = chip.dataset.borrowerPass;
    const emailInput = $("#borrower-login-email");
    const passInput = $("#borrower-login-pass");
    if (emailInput) emailInput.value = email;
    if (passInput) passInput.value = pass;
    passInput?.focus();
  });
});

// Eye toggles for borrower password fields
$("#btn-toggle-borrower-login-pass")?.addEventListener("click", () => {
  const p = $("#borrower-login-pass");
  if (p) p.type = p.type === "password" ? "text" : "password";
});

$("#btn-toggle-borrower-reg-pass")?.addEventListener("click", () => {
  const p = $("#borrower-reg-pass");
  if (p) p.type = p.type === "password" ? "text" : "password";
});

// Borrower Login Submit
$("#form-borrower-login")?.addEventListener("submit", async event => {
  event.preventDefault();
  const email = $("#borrower-login-email")?.value.trim();
  const password = $("#borrower-login-pass")?.value.trim();

  if (!email || !password) {
    showToast("Please enter borrower email and password.", true);
    return;
  }

  showToast("Signing in to borrower portal...");
  try {
    const res = await api("/api/borrower/login", {
      method: "POST",
      body: JSON.stringify({ email, password })
    });
    if (!res.borrower) throw new Error("Borrower authentication failed");
    localStorage.setItem("dealpilot_borrower", JSON.stringify(res.borrower));
    showToast(`Welcome back, ${res.borrower.guarantor_name} (${res.borrower.business_name})!`);
    showBorrowerDashboard(res.borrower);
  } catch (err) {
    showToast(err.message, true);
  }
});

// Borrower Register Submit
$("#form-borrower-register")?.addEventListener("submit", async event => {
  event.preventDefault();
  const business_name = $("#borrower-reg-biz")?.value.trim();
  const guarantor_name = $("#borrower-reg-name")?.value.trim();
  const email = $("#borrower-reg-email")?.value.trim();
  const password = $("#borrower-reg-pass")?.value.trim();
  const amount_requested = $("#borrower-reg-amount")?.value;
  const purpose = $("#borrower-reg-purpose")?.value;

  if (!business_name || !guarantor_name || !email || !password) {
    showToast("Please fill in all required application fields.", true);
    return;
  }

  showToast("Submitting loan application...");
  try {
    const res = await api("/api/borrower/register", {
      method: "POST",
      body: JSON.stringify({
        business_name,
        guarantor_name,
        email,
        password,
        amount_requested,
        purpose
      })
    });
    if (!res.borrower) throw new Error("Application registration failed");
    localStorage.setItem("dealpilot_borrower", JSON.stringify(res.borrower));
    showToast(`Application created for ${res.borrower.business_name}!`);
    showBorrowerDashboard(res.borrower);
  } catch (err) {
    showToast(err.message, true);
  }
});

// Borrower Document Upload Dropzone & Form
const bDropzone = $("#borrower-dropzone");
const bFileInput = $("#borrower-file-input");

bDropzone?.addEventListener("click", e => {
  if (e.target.closest("#btn-borrower-remove-file")) return;
  bFileInput?.click();
});

bFileInput?.addEventListener("change", e => {
  if (e.target.files && e.target.files[0]) {
    borrowerSelectedFile = e.target.files[0];
    const nameEl = $("#borrower-selected-name");
    if (nameEl) nameEl.textContent = borrowerSelectedFile.name;
    $("#borrower-dropzone-empty")?.classList.add("hidden");
    $("#borrower-dropzone-selected")?.classList.remove("hidden");
  }
});

$("#btn-borrower-remove-file")?.addEventListener("click", e => {
  e.stopPropagation();
  borrowerSelectedFile = null;
  if (bFileInput) bFileInput.value = "";
  $("#borrower-dropzone-empty")?.classList.remove("hidden");
  $("#borrower-dropzone-selected")?.classList.add("hidden");
});

bDropzone?.addEventListener("dragover", e => {
  e.preventDefault();
  bDropzone.style.borderColor = "#10b981";
  bDropzone.style.background = "#ecfdf5";
});

bDropzone?.addEventListener("dragleave", () => {
  bDropzone.style.borderColor = "#cbd5e1";
  bDropzone.style.background = "#f8fafc";
});

bDropzone?.addEventListener("drop", e => {
  e.preventDefault();
  bDropzone.style.borderColor = "#cbd5e1";
  bDropzone.style.background = "#f8fafc";
  if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
    borrowerSelectedFile = e.dataTransfer.files[0];
    const nameEl = $("#borrower-selected-name");
    if (nameEl) nameEl.textContent = borrowerSelectedFile.name;
    $("#borrower-dropzone-empty")?.classList.add("hidden");
    $("#borrower-dropzone-selected")?.classList.remove("hidden");
  }
});

$("#form-borrower-doc-upload")?.addEventListener("submit", async event => {
  event.preventDefault();
  if (!borrowerSelectedFile) {
    showToast("Please choose a file to upload.", true);
    return;
  }
  const folder = state.currentBorrower?.folder || state.currentBorrower?.business_name;
  if (!folder) {
    showToast("Borrower deal folder is missing.", true);
    return;
  }

  const docType = $("#borrower-upload-doctype")?.value || "other";
  const btn = $("#btn-borrower-submit-upload");
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span>⏳ Uploading & Ingesting...</span>';
  }

  showToast(`Uploading ${borrowerSelectedFile.name}...`);
  try {
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const base64Data = reader.result.split(",")[1];
        const res = await api("/api/borrower/upload", {
          method: "POST",
          body: JSON.stringify({
            folder,
            filename: borrowerSelectedFile.name,
            content_base64: base64Data,
            doc_type: docType
          })
        });

        showToast(`Document uploaded successfully! Type: ${res.document_type}`);
        // Reset file input
        borrowerSelectedFile = null;
        if (bFileInput) bFileInput.value = "";
        $("#borrower-dropzone-empty")?.classList.remove("hidden");
        $("#borrower-dropzone-selected")?.classList.add("hidden");

        // Reload borrower status
        await loadBorrowerStatus(folder);
      } catch (err) {
        showToast(err.message, true);
      } finally {
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<span>⚡ Upload Document</span>';
        }
      }
    };
    reader.readAsDataURL(borrowerSelectedFile);
  } catch (err) {
    showToast(err.message, true);
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<span>⚡ Upload Document</span>';
    }
  }
});

// Initial Boot: Check authenticated session
(function init() {
  // Check if a borrower session is active
  const savedBorrower = localStorage.getItem("dealpilot_borrower");
  if (savedBorrower) {
    try {
      const borrower = JSON.parse(savedBorrower);
      showBorrowerDashboard(borrower);
      return;
    } catch (e) {
      localStorage.removeItem("dealpilot_borrower");
    }
  }

  // Check if an officer session is active
  const savedUser = localStorage.getItem("dealpilot_user");
  if (savedUser) {
    try {
      const user = JSON.parse(savedUser);
      applyUserSession(user);
      return;
    } catch (e) {
      localStorage.removeItem("dealpilot_user");
    }
  }

  // Not logged in: Default to Borrower Customer Portal (keeps internal employee page restricted)
  showBorrowerAuth();
})();
