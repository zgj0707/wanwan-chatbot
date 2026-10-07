(() => {
  "use strict";
  const bootstrapNode = document.getElementById("suwan-bootstrap");
  const bootstrap = JSON.parse(bootstrapNode?.textContent || "{}");
  const token = bootstrap.token || "";
  const api = "/api/v1";
  const els = {
    shell: document.getElementById("app-shell"),
    sidebarToggle: document.getElementById("sidebar-toggle"),
    sidebarCollapse: document.getElementById("sidebar-collapse"),
    sidebarBackdrop: document.getElementById("sidebar-backdrop"),
    sessionSearch: document.getElementById("session-search"),
    sessionSearchClear: document.getElementById("session-search-clear"),
    toastLayer: document.getElementById("toast-layer"),
    shortcutSheet: document.getElementById("shortcut-sheet"),
    closeShortcuts: document.getElementById("close-shortcuts"),
    newMessageFlag: document.getElementById("new-message-flag"),
    sendLabel: document.getElementById("send-label"),
    scrollAnchorTop: document.getElementById("scroll-anchor-top"),
    sessions: document.getElementById("sessions"),
    showArchived: document.getElementById("show-archived"),
    newSession: document.getElementById("new-session"),
    title: document.getElementById("session-title"),
    time: document.getElementById("time-context"),
    mode: document.getElementById("mode-badge"),
    uiVersion: document.getElementById("ui-version"),
    archive: document.getElementById("archive-current"),
    openSettings: document.getElementById("open-settings"),
    closeSettings: document.getElementById("close-settings"),
    settingsTabs: [...document.querySelectorAll(".settings-tab[data-view]")],
    settingsEntries: [...document.querySelectorAll(".settings-entry[data-view]")],
    settingsOverlay: document.getElementById("settings-overlay"),
    views: [...document.querySelectorAll("#settings-overlay .view")],
    chatScroll: document.getElementById("chat-scroll"),
    messages: document.getElementById("messages"),
    scrollBottom: document.getElementById("scroll-bottom"),
    pending: document.getElementById("pending-area"),
    form: document.getElementById("composer"),
    input: document.getElementById("message-input"),
    send: document.getElementById("send-button"),
    charCount: document.getElementById("char-count"),
    cancelWait: document.getElementById("cancel-wait"),
    queryOperation: document.getElementById("query-operation"),
    initiativeNotice: document.getElementById("initiative-notice"),
    managerNotice: document.getElementById("manager-notice"),
    personaNotice: document.getElementById("persona-notice"),
    diagnosticsNotice: document.getElementById("diagnostics-notice"),
    worldNotice: document.getElementById("world-notice"),
    worldLastUpdate: document.getElementById("world-last-update"),
    worldThreads: document.getElementById("world-threads"),
    worldEvents: document.getElementById("world-events"),
    worldEventCount: document.getElementById("world-event-count"),
    memoryWorldEvents: document.getElementById("memory-world-events"),
    memoryWorldEventCount: document.getElementById("memory-world-event-count"),
    refreshWorld: document.getElementById("refresh-world"),
    advanceWorld: document.getElementById("advance-world"),
    manualMemoryForm: document.getElementById("manual-memory-form"),
    memoryKey: document.getElementById("memory-key"),
    memoryValue: document.getElementById("memory-value"),
    memoryList: document.getElementById("memory-list"),
    memoryCount: document.getElementById("memory-count"),
    factProposals: document.getElementById("fact-proposals"),
    factProposalCount: document.getElementById("fact-proposal-count"),
    personaProposals: document.getElementById("persona-proposals"),
    personaProposalCount: document.getElementById("persona-proposal-count"),
    personaDetails: document.getElementById("persona-details"),
    personaViewProposals: document.getElementById("persona-view-proposals"),
    personaViewProposalCount: document.getElementById("persona-view-proposal-count"),
    diagnosticsSummary: document.getElementById("diagnostics-summary"),
    diagnosticsCalls: document.getElementById("diagnostics-calls"),
    diagnosticsRefresh: document.getElementById("refresh-diagnostics"),
    probeForm: document.getElementById("probe-form"),
    probeProfile: document.getElementById("probe-profile"),
    probeResult: document.getElementById("probe-result"),
    backupExport: document.getElementById("export-backup"),
    backupNotice: document.getElementById("backup-notice"),
    forgetDialog: document.getElementById("forget-dialog"),
    redactSessionHistory: document.getElementById("redact-session-history"),
    confirmForget: document.getElementById("confirm-forget"),
    cancelForget: document.getElementById("cancel-forget"),
  };
  const storageKey = "suwan.pending-operation.v1";
  const railKey = "suwan.sidebar.rail.v1";
  const DISPLAY_TZ = "Asia/Tokyo";
  let sessionsCache = [];
  let activeSessionId = null;
  let activeSession = null;
  let pending = loadPending();
  let isWaiting = false;
  let waitController = null;
  let polling = false;
  let isComposing = false;
  let currentView = "chat-view";
  let forgetFactId = null;
  let initiativeGeneration = 0;
  const initiativePendingSessions = new Set();
  let worldBusy = false;
  let nextBeforeId = null;
  let loadEarlierBusy = false;

  function loadPending() {
    try { return JSON.parse(sessionStorage.getItem(storageKey) || "null"); }
    catch { return null; }
  }
  function savePending() {
    if (pending) sessionStorage.setItem(storageKey, JSON.stringify(pending));
    else sessionStorage.removeItem(storageKey);
  }
  function headers(extra = {}) { return { "X-Suwan-Token": token, ...extra }; }
  async function request(path, options = {}) {
    const response = await fetch(api + path, {
      ...options,
      headers: headers(options.headers || {}),
      cache: "no-store",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const err = new Error(data?.error?.message || "请求未完成");
      err.api = data?.error || { code: "NETWORK_ERROR", retryable: true };
      err.status = response.status;
      throw err;
    }
    return { data, status: response.status };
  }
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function updateClock() {
    const dateText = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(new Date());
    els.time.textContent = `广州 · ${dateText}`;
  }
  /* 统一取「按 DISPLAY_TZ 换算后的日历天」，用于消息时间与日期分隔。
     注意：Intl 的 timeZone 只接受 IANA 时区名，不能传 Date 对象。 */
  function dayKeyInDisplayZone(date) {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: DISPLAY_TZ, year: "numeric", month: "2-digit", day: "2-digit",
    }).format(date);
  }
  function localTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("zh-CN", { timeZone: DISPLAY_TZ, hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  }
  /* 消息上方时间：当天只显示时刻，昨天加前缀，更早补上月日 */
  function messageTimeLabel(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const time = localTime(value);
    const key = dayKeyInDisplayZone(date);
    if (key === dayKeyInDisplayZone(new Date())) return time;
    if (key === dayKeyInDisplayZone(new Date(Date.now() - 86400000))) return `昨天 ${time}`;
    const md = new Intl.DateTimeFormat("zh-CN", { timeZone: DISPLAY_TZ, month: "numeric", day: "numeric" }).format(date);
    return `${md} ${time}`;
  }
  function dayDividerLabel(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const key = dayKeyInDisplayZone(date);
    if (key === dayKeyInDisplayZone(new Date())) return "今天";
    if (key === dayKeyInDisplayZone(new Date(Date.now() - 86400000))) return "昨天";
    const md = new Intl.DateTimeFormat("zh-CN", { timeZone: DISPLAY_TZ, month: "numeric", day: "numeric" }).format(date);
    return md.replace("月", "月").replace("日", " 日");
  }

  /* ── 侧边栏：抽屉与折叠窄条 ─────────────── */
  function closeSidebar() {
    els.shell.classList.remove("sidebar-open");
    els.sidebarToggle?.setAttribute("aria-expanded", "false");
  }
  function openSidebar() {
    els.shell.classList.add("sidebar-open");
    els.sidebarToggle?.setAttribute("aria-expanded", "true");
  }
  function applyRail(enabled) {
    els.shell.classList.toggle("rail", enabled);
    els.sidebarCollapse.textContent = enabled ? "»" : "«";
    els.sidebarCollapse.title = enabled ? "展开侧栏" : "收起侧栏";
  }
  function toggleRail() {
    const enabled = !els.shell.classList.contains("rail");
    applyRail(enabled);
    try { localStorage.setItem(railKey, enabled ? "1" : "0"); } catch { /* 本地存储不可用时忽略 */ }
  }

  /* ── 运行提示（Toast）─────────────────────────
     统一出口：info / success / warn / error，可带操作按钮与进度条。
     不再往 #pending-area 插入卡片——那里每次 renderPending() 都会被清空。 */
  const TOAST_ICON = { info: "i", success: "✓", warn: "!", error: "!" };
  const TOAST_LIMIT = 3;
  function toast(message, options = {}) {
    if (!message) return null;
    const { type = "info", duration, actionLabel, onAction } = options;
    const life = duration ?? (type === "error" ? 7000 : 3600);
    const node = make("div", `toast ${type}`);
    node.setAttribute("role", type === "error" ? "alert" : "status");
    node.append(make("span", "toast-icon", TOAST_ICON[type] || TOAST_ICON.info));
    node.append(make("span", "toast-text", message));
    if (actionLabel && typeof onAction === "function") {
      const action = make("button", "toast-action", actionLabel);
      action.type = "button";
      action.addEventListener("click", () => { dismiss(); onAction(); });
      node.append(action);
    }
    const close = make("button", "toast-close", "✕");
    close.type = "button";
    close.setAttribute("aria-label", "关闭提示");
    close.addEventListener("click", () => dismiss());
    node.append(close);
    if (life > 0) {
      const bar = make("span", "toast-bar");
      bar.style.animationDuration = `${life}ms`;
      node.append(bar);
    }
    els.toastLayer.prepend(node);
    while (els.toastLayer.childElementCount > TOAST_LIMIT) els.toastLayer.lastElementChild.remove();
    let timer = life > 0 ? setTimeout(() => dismiss(), life) : null;
    let closed = false;
    function dismiss() {
      if (closed) return;
      closed = true;
      if (timer) clearTimeout(timer);
      node.classList.add("leaving");
      node.addEventListener("animationend", () => node.remove(), { once: true });
      setTimeout(() => node.remove(), 400);
    }
    return dismiss;
  }
  const toastSuccess = (message, options = {}) => toast(message, { ...options, type: "success" });
  const toastError = (message, options = {}) => toast(message, { ...options, type: "error" });
  const toastWarn = (message, options = {}) => toast(message, { ...options, type: "warn" });

  /* 页面内常驻状态条（设置中心用）——与 toast 分工：这里是需要用户读完的说明 */
  function setViewNotice(viewId, message, isError = false) {
    const targets = {
      "memory-view": els.managerNotice,
      "persona-view": els.personaNotice,
      "diagnostics-view": els.diagnosticsNotice,
    };
    const target = targets[viewId];
    if (!target) return;
    target.replaceChildren();
    if (!message) {
      target.classList.add("hidden");
      target.classList.remove("error", "info");
      return;
    }
    const kind = isError ? "error" : "info";
    target.append(make("span", "toast-icon", isError ? "!" : "i"));
    target.append(make("span", "", message));
    target.classList.remove("hidden");
    target.classList.toggle("error", isError);
    target.classList.toggle("info", !isError);
  }
  function showNotice(message, options = {}) {
    toast(message, options);
  }

  /* 按钮内联加载态：保留原有子节点，避免破坏图标结构。
     注意不保存 HTML 字符串（测试禁止，且会绕过转义）；改为克隆子节点。 */
  const busyRestore = new WeakMap();
  function setBusy(button, busy, busyLabel) {
    if (!button) return;
    if (busy) {
      if (busyRestore.has(button)) return;
      busyRestore.set(button, [...button.childNodes].map((node) => node.cloneNode(true)));
      button.disabled = true;
      button.classList.add("is-busy");
      button.replaceChildren(make("span", "btn-spinner"), document.createTextNode(busyLabel || "处理中"));
      return;
    }
    button.classList.remove("is-busy");
    const restore = busyRestore.get(button);
    if (restore) {
      button.replaceChildren(...restore);
      busyRestore.delete(button);
    }
  }

  /* ── 设置中心（全屏层，tab 切换）──────────── */
  function switchSettingsTab(viewId) {
    currentView = viewId;
    for (const tab of els.settingsTabs) tab.classList.toggle("active", tab.dataset.view === viewId);
    for (const view of els.views) view.classList.toggle("hidden", view.id !== viewId);
    if (viewId === "memory-view") void loadMemoryView();
    if (viewId === "persona-view") void loadPersonaView();
    if (viewId === "diagnostics-view") void loadDiagnosticsView();
  }
  function openSettings(viewId) {
    els.settingsOverlay.classList.remove("hidden");
    switchSettingsTab(viewId);
  }
  function closeSettings() {
    els.settingsOverlay.classList.add("hidden");
    currentView = "chat-view";
  }

  /* ── 记忆管理 ─────────────────────────────── */
  const memoryLabels = {
    "profile.preferred_name": "称呼",
    "profile.birthday": "生日",
    "preference.drink": "饮品偏好",
    "preference.food": "饮食偏好",
    "preference.hobby": "兴趣爱好",
    "preference.chat_style": "聊天风格",
    "plan.personal": "个人计划",
  };
  function renderMemory(items) {
    els.memoryList.replaceChildren();
    els.memoryCount.textContent = String(items.length);
    if (!items.length) {
      els.memoryList.append(make("p", "manager-empty", "还没有用户记忆。可以手动添加，或在待确认事实中确认一条。"));
      return;
    }
    for (const item of items) {
      const card = make("article", "manager-card");
      const statusLabel = { active: "已确认", superseded: "旧版本", forgotten: "已忘记" }[item.status] || item.status;
      card.append(make("h3", "", `${memoryLabels[item.key] || item.key} · ${statusLabel}`));
      card.append(make("p", "", item.value || "内容已清除"));
      card.append(make("p", "manager-meta", `版本创建：${item.created_at || "时间未知"} · ${item.origin === "manual" ? "手动添加" : "来自对话"}`));
      for (const source of item.sources || []) {
        const sourceText = source.redacted ? "来源内容已因忘记操作移除" : (source.content || "（来源内容为空）");
        card.append(make("p", "manager-source", `来源 · ${source.created_at || ""} · ${sourceText}`));
      }
      if (item.status === "active") {
        const actions = make("div", "manager-actions");
        const editButton = make("button", "text-button", "修改");
        editButton.type = "button";
        editButton.addEventListener("click", () => openMemoryEditor(card, item));
        actions.append(editButton);
        const forgetButton = make("button", "text-button", "忘记");
        forgetButton.type = "button";
        forgetButton.addEventListener("click", () => openForgetDialog(item.id));
        actions.append(forgetButton);
        card.append(actions);
      }
      els.memoryList.append(card);
    }
  }
  function openMemoryEditor(card, item) {
    if (card.querySelector(".edit-memory-form")) return;
    const form = make("form", "edit-memory-form");
    const input = make("input", "edit-memory-input");
    input.maxLength = 120;
    input.required = true;
    input.value = item.value || "";
    input.setAttribute("aria-label", "修改后的记忆内容");
    const save = make("button", "secondary-button", "保存新版本");
    save.type = "submit";
    form.append(input, save);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      setBusy(save, true, "保存中");
      try {
        await request(`/memory/${item.id}/edit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value: input.value.trim() }) });
        setViewNotice("memory-view", "修改已保存为新的记忆版本。旧版本来源保留。");
        toastSuccess("修改已保存为新的记忆版本。");
        await loadMemoryView();
      } catch (error) {
        setViewNotice("memory-view", error.message || "无法修改这条记忆。", true);
        toastError(error.message || "无法修改这条记忆。");
        setBusy(save, false);
      }
    });
    card.append(form);
    input.focus();
  }
  let pendingProposalAction = false;
  function renderProposals(container, countNode, proposals, kind, viewId) {
    container.replaceChildren();
    countNode.textContent = String(proposals.length);
    if (!proposals.length) {
      container.append(make("p", "manager-empty", "目前没有待确认建议。"));
      return;
    }
    for (const proposal of proposals) {
      const card = make("article", "manager-card");
      const payload = proposal.payload || {};
      const heading = kind === "user_fact"
        ? `用户事实 · ${memoryLabels[payload.key] || payload.key || "未知类别"}`
        : `人设建议 · ${payload.field || "未知字段"}`;
      card.append(make("h3", "", heading));
      card.append(make("p", "", payload.value || "（建议内容已清除）"));
      if (payload.evidence_quote) card.append(make("p", "manager-meta", `证据摘录：${payload.evidence_quote}`));
      if (payload.reason) card.append(make("p", "manager-meta", `建议原因：${payload.reason}`));
      const source = proposal.source;
      if (source) {
        card.append(make("p", "manager-source", `来源消息 · ${source.created_at || ""} · ${source.redacted ? "内容已移除" : source.content || "（空内容）"}`));
        card.append(make("p", "manager-meta", `对话编号：${source.session_id}`));
      } else card.append(make("p", "manager-meta", "来源消息不可用。"));
      const actions = make("div", "manager-actions");
      for (const decision of ["accept", "reject"]) {
        const button = make("button", decision === "accept" ? "secondary-button" : "text-button", decision === "accept" ? "确认" : "拒绝");
        button.type = "button";
        button.disabled = pendingProposalAction;
        button.addEventListener("click", async () => {
          if (pendingProposalAction) return;
          pendingProposalAction = true;
          for (const sibling of actions.querySelectorAll("button")) sibling.disabled = true;
          setBusy(button, true, decision === "accept" ? "确认中" : "拒绝中");
          try {
            await request(`/proposals/${proposal.id}/resolve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) });
            const text = decision === "accept" ? "已确认；更新后的版本已经保存。" : "已拒绝这条建议。";
            setViewNotice(viewId, text);
            toastSuccess(text);
            if (viewId === "persona-view") await loadPersonaView();
            else await loadMemoryView();
          } catch (error) {
            setViewNotice(viewId, error.message || "无法处理这条建议。", true);
            toastError(error.message || "无法处理这条建议。");
            for (const sibling of actions.querySelectorAll("button")) sibling.disabled = false;
          } finally {
            setBusy(button, false);
            pendingProposalAction = false;
          }
        });
        actions.append(button);
      }
      card.append(actions);
      container.append(card);
    }
  }
  async function loadMemoryView() {
    setViewNotice("memory-view", "正在读取记忆和待确认建议…");
    try {
      const [memory, facts, persona] = await Promise.all([
        request("/memory?status=all&limit=100"),
        request("/proposals?kind=user_fact&status=pending&limit=100"),
        request("/proposals?kind=persona&status=pending&limit=100"),
      ]);
      renderMemory(memory.data.items || []);
      renderProposals(els.factProposals, els.factProposalCount, facts.data.items || [], "user_fact", "memory-view");
      renderProposals(els.personaProposals, els.personaProposalCount, persona.data.items || [], "persona", "memory-view");
      await loadWorldView();
      setViewNotice("memory-view", "");
    } catch (error) {
      setViewNotice("memory-view", error.message || "无法加载记忆数据。", true);
    }
  }

  /* ── 晚晚档案 ─────────────────────────────── */
  /* 档案锚点用英文 key 存储，页面上要显示中文 */
  const personaLabels = {
    name: "名字", nickname: "昵称", age: "年龄", city: "城市",
    occupation: "职业", cat_name: "猫名", cat_breed: "猫品种",
    virtual_identity: "虚拟身份", temperament: "性格", boundary: "相处边界",
    catchphrases: "口头禅", likes: "喜欢的", dislikes: "不喜欢的",
  };
  function personaLabel(key) {
    return personaLabels[key] || key;
  }
  async function loadPersonaView() {
    setViewNotice("persona-view", "正在读取档案…");
    try {
      const [persona, proposals] = await Promise.all([
        request("/persona"),
        request("/proposals?kind=persona&status=pending&limit=100"),
      ]);
      const data = persona.data;
      els.personaDetails.replaceChildren();
      const addPersonaCard = (title, value) => {
        const card = make("div", "panel-card persona-fact");
        card.append(make("strong", "", title));
        card.append(make("span", "", Array.isArray(value) ? value.join("、") : (typeof value === "boolean" ? (value ? "是" : "否") : value)));
        els.personaDetails.append(card);
      };
      addPersonaCard("档案版本", `v${data.version}`);
      for (const [key, value] of Object.entries(data.anchors || {})) addPersonaCard(personaLabel(key), value);
      addPersonaCard("已确认喜欢", data.preferences?.likes || []);
      addPersonaCard("已确认不喜欢", data.preferences?.dislikes || []);
      addPersonaCard("原有人设爱好", data.preferences?.hobbies || []);
      addPersonaCard("语气摘要", data.voice_summary || "");
      renderProposals(els.personaViewProposals, els.personaViewProposalCount, proposals.data.items || [], "persona", "persona-view");
      await loadWorldView();
      setViewNotice("persona-view", "");
    } catch (error) { setViewNotice("persona-view", error.message || "无法加载晚晚档案。", true); }
  }

  /* ── 设置与诊断 ───────────────────────────── */
  async function loadDiagnosticsView() {
    setViewNotice("diagnostics-view", "正在检查本地状态…");
    try {
      const { data } = await request("/diagnostics");
      const summary = data.metrics || {};
      els.diagnosticsSummary.replaceChildren();
      const metrics = [
        ["当前模式", data.mode === "fake" ? "离线模拟" : "真实模型"],
        ["健康检查", `正常 · ${data.health?.checked_at || ""}`],
        ["总调用数", summary.call_count ?? 0],
        ["输入 / 输出 tokens", `${summary.input_tokens ?? 0} / ${summary.output_tokens ?? 0}`],
        ["平均耗时", `${summary.average_latency_ms ?? 0} ms`],
        ["降级或失败调用", summary.degraded_count ?? 0],
      ];
      for (const [label, value] of metrics) {
        const card = make("div", "panel-card metric-card");
        card.append(make("strong", "", label));
        card.append(make("span", "", value));
        els.diagnosticsSummary.append(card);
      }
      for (const [name, profile] of Object.entries(data.profiles || {})) {
        const card = make("div", "panel-card metric-card");
        const credential = profile.credential_configured === null ? "模拟模式不使用密钥" : (profile.credential_configured ? "已配置密钥" : "未配置密钥");
        card.append(make("strong", "", `${name} · ${profile.model}`));
        card.append(make("span", "", `${profile.provider || "本地服务"} · ${credential}`));
        els.diagnosticsSummary.append(card);
      }
      const calls = summary.recent_calls || [];
      els.diagnosticsCalls.replaceChildren();
      if (!calls.length) {
        els.diagnosticsCalls.append(make("p", "manager-empty", "还没有模型调用记录。发送一条消息或运行一次测试后，这里会出现明细。"));
      } else {
        const table = make("table", "diagnostics-table");
        const head = make("tr", "");
        for (const label of ["时间", "配置", "结果", "耗时", "输入 tokens", "输出 tokens"]) head.append(make("th", "", label));
        const thead = make("thead", ""); thead.append(head); table.append(thead);
        const body = make("tbody", "");
        for (const call of calls) {
          const row = make("tr", "");
          const time = make("td", "", call.created_at || "—");
          time.title = call.created_at ? new Date(call.created_at).toLocaleString("zh-CN") : "";
          row.append(time);
          row.append(make("td", "", call.profile));
          const ok = ["succeeded", "success", "ok"].includes(String(call.status || "").toLowerCase());
          const status = make("td", ok ? "status-ok" : "status-bad", call.status || "—");
          row.append(status);
          const latency = make("td", "latency", call.latency_ms === null || call.latency_ms === undefined ? "—" : `${call.latency_ms} ms`);
          if (call.latency_ms > 8000) latency.style.color = "var(--danger)";
          row.append(latency);
          row.append(make("td", "", call.input_tokens ?? "—"));
          row.append(make("td", "", call.output_tokens ?? "—"));
          body.append(row);
        }
        table.append(body);
        els.diagnosticsCalls.append(table);
      }
      setViewNotice("diagnostics-view", data.configuration || "");
    } catch (error) { setViewNotice("diagnostics-view", error.message || "无法读取诊断信息。", true); }
  }

  /* ── 生活事项与生活事件 ───────────────────── */
  const worldStageLabels = { planned: "计划中", started: "已开始", progressed: "进行中", done: "已完成", paused: "已暂停" };
  function setWorldNotice(message, isError = false) {
    const target = els.worldNotice;
    target.replaceChildren();
    if (!message) {
      target.classList.add("hidden");
      target.classList.remove("error", "info");
      return;
    }
    target.append(make("span", "toast-icon", isError ? "!" : "i"));
    target.append(make("span", "", message));
    target.classList.remove("hidden");
    target.classList.toggle("error", isError);
    target.classList.toggle("info", !isError);
    if (message && isError && currentView === "memory-view") setViewNotice("memory-view", message, true);
  }
  async function loadWorldView() {
    setWorldNotice("正在读取生活事项…");
    try {
      const { data } = await request("/world");
      els.worldThreads.replaceChildren();
      els.worldLastUpdate.textContent = `最近检查：${localTime(data.last_update_at) || "时间未知"}`;
      for (const thread of data.threads || []) {
        const card = make("article", "manager-card world-thread-card");
        card.append(make("h3", "", thread.label));
        card.append(make("p", "manager-meta", `初始生活事项 · 第 ${thread.cycle + 1} 轮 · ${worldStageLabels[thread.stage] || thread.stage} · ${thread.step}/3`));
        if (thread.last_event_at) card.append(make("p", "manager-meta", `上次进展：${localTime(thread.last_event_at) || "时间未知"}`));
        const action = thread.stage === "done" ? "restart" : thread.stage === "paused" ? "resume" : "pause";
        const label = action === "restart" ? "重新开始" : action === "resume" ? "恢复" : "暂停";
        const button = make("button", "text-button", label);
        button.type = "button";
        button.disabled = worldBusy;
        button.addEventListener("click", () => { void controlWorldThread(thread.id, action); });
        card.append(button);
        els.worldThreads.append(card);
      }
      const threadById = new Map((data.threads || []).map((thread) => [thread.id, thread]));
      renderWorldEvents(els.worldEvents, els.worldEventCount, data.events || [], threadById, true);
      renderWorldEvents(els.memoryWorldEvents, els.memoryWorldEventCount, data.events || [], threadById, false);
      setWorldNotice("");
    } catch (error) {
      setWorldNotice(error.message || "无法加载生活事项。", true);
      els.worldThreads.replaceChildren(make("p", "manager-empty", "生活事项暂时不可用。"));
      renderWorldEvents(els.worldEvents, els.worldEventCount, [], new Map(), true);
      renderWorldEvents(els.memoryWorldEvents, els.memoryWorldEventCount, [], new Map(), false);
    }
  }
  function renderWorldEvents(container, countNode, events, threadById, allowActions) {
    container.replaceChildren();
    countNode.textContent = String(events.length);
    if (!events.length) {
      container.append(make("p", "manager-empty", "还没有已发生的生活事件。"));
      return;
    }
    for (const event of events) {
      const thread = threadById.get(event.thread_id);
      const card = make("article", "manager-card world-event-card");
      const label = thread?.label || event.thread_id;
      const status = event.status === "retracted" ? "已撤回" : `${worldStageLabels[event.stage_after] || event.stage_after}`;
      card.append(make("h3", "", `${label} · ${status}`));
      card.append(make("p", "", event.summary || "事件描述不可用。"));
      card.append(make("p", "manager-meta", `${localTime(event.occurred_at) || "时间未知"} · 第 ${event.cycle + 1} 轮 · 槽位 ${event.slot_key}`));
      if (allowActions && event.can_retract) {
        const retract = make("button", "text-button", "撤回这条经历");
        retract.type = "button";
        retract.disabled = worldBusy;
        retract.addEventListener("click", () => { void retractWorldEvent(event.id); });
        card.append(retract);
      } else if (allowActions && event.status === "committed") {
        card.append(make("p", "manager-meta", "已有后续进展，不能撤回。"));
      }
      container.append(card);
    }
  }
  async function withWorldMutation(action, successMessage) {
    if (worldBusy) return;
    worldBusy = true;
    setBusy(els.refreshWorld, true, "刷新中");
    setBusy(els.advanceWorld, true, "检查中");
    for (const button of document.querySelectorAll(".world-thread-card button, .world-event-card button")) {
      button.disabled = true;
      button.classList.add("is-busy");
    }
    try {
      const result = await action();
      await loadWorldView();
      const text = typeof successMessage === "function" ? successMessage(result) : successMessage;
      setWorldNotice(text);
      toast(text, { type: "success" });
    } catch (error) {
      setWorldNotice(error.message || "生活事项操作未完成。", true);
      toastError(error.message || "生活事项操作未完成。");
    } finally {
      worldBusy = false;
      setBusy(els.refreshWorld, false);
      setBusy(els.advanceWorld, false);
      for (const button of document.querySelectorAll(".world-thread-card button, .world-event-card button")) {
        button.disabled = false;
        button.classList.remove("is-busy");
      }
    }
  }
  function controlWorldThread(threadId, action) {
    const labels = { pause: "事项已暂停。", resume: "事项已恢复。", restart: "事项已重新开始。" };
    return withWorldMutation(
      () => request(`/world/threads/${threadId}/control`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) }),
      labels[action] || "事项已更新。",
    );
  }
  function retractWorldEvent(eventId) {
    return withWorldMutation(
      () => request(`/world/events/${eventId}/retract`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }),
      "生活经历已撤回，引用它的晚晚回复也已移除。",
    );
  }
  function advanceWorld() {
    return withWorldMutation(
      async () => (await request("/world/advance", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) })).data,
      (data) => data.warnings?.length
        ? `检查已完成。状态提示：${data.warnings.join("、")}`
        : data.advanced
          ? "生活进展已更新。"
          : "检查已完成；没有符合条件的新生活进展。",
    );
  }

  /* ── 主动开场 ─────────────────────────────── */
  function clearInitiativeNotice() {
    els.initiativeNotice.replaceChildren();
    els.initiativeNotice.classList.add("hidden");
    els.initiativeNotice.classList.remove("error");
  }
  function appendTypingDots(container) {
    const dots = make("span", "typing-dots");
    dots.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 3; index += 1) dots.append(make("span", "", " "));
    container.append(dots);
  }
  function showInitiativeTyping() {
    els.initiativeNotice.replaceChildren();
    els.initiativeNotice.append(make("span", "typing-label", "晚晚正在想"));
    appendTypingDots(els.initiativeNotice);
    els.initiativeNotice.classList.remove("hidden", "error");
  }
  async function requestInitiative(sessionId, generation) {
    if (!sessionId || pending || activeSession?.archived) return;
    initiativePendingSessions.add(sessionId);
    if (activeSessionId === sessionId) {
      showInitiativeTyping();
      renderPending();
    }
    try {
      const { data } = await request("/initiative", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation_id: crypto.randomUUID(), session_id: sessionId }),
      });
      if (generation !== initiativeGeneration || activeSessionId !== sessionId) return;
      if (data.sent && data.assistant_message) {
        clearInitiativeNotice();
        appendMessage(data.assistant_message);
        await loadSessionsListOnly();
      } else {
        clearInitiativeNotice();
      }
    } catch {
      if (generation === initiativeGeneration && activeSessionId === sessionId) {
        clearInitiativeNotice();
      }
    } finally {
      initiativePendingSessions.delete(sessionId);
      if (activeSessionId === sessionId) renderPending();
    }
  }

  /* ── 忘记记忆 ─────────────────────────────── */
  function openForgetDialog(factId) {
    forgetFactId = factId;
    els.redactSessionHistory.checked = true;
    if (typeof els.forgetDialog.showModal === "function") els.forgetDialog.showModal();
    else if (window.confirm("忘记这条记忆并移除来源对话的全部消息？旧备份仍可能包含旧内容。")) void forgetMemory();
  }
  async function forgetMemory() {
    if (!forgetFactId) return;
    try {
      const { data } = await request(`/memory/${forgetFactId}/forget`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ redact_session_history: els.redactSessionHistory.checked }),
      });
      const notice = `已忘记。移除了 ${data.redacted_message_count} 条消息。${data.notice || ""}`;
      els.forgetDialog.close();
      forgetFactId = null;
      await loadMemoryView();
      setViewNotice("memory-view", notice);
      toastSuccess(notice, { duration: 5000 });
    } catch (error) {
      setViewNotice("memory-view", error.message || "无法忘记这条记忆。", true);
      toastError(error.message || "无法忘记这条记忆。");
    } finally { setBusy(els.confirmForget, false); }
  }

  /* ── 会话列表：搜索 + 时间分组 ────────────── */
  function sessionDayKey(value) {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return null;
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
  }
  function sessionGroupIndex(session) {
    const stamp = Date.parse(session.updated_at || session.created_at || "");
    if (Number.isNaN(stamp)) return 2;
    const todayKey = sessionDayKey(new Date());
    if (sessionDayKey(new Date(stamp)) === todayKey) return 0;
    return Date.now() - stamp < 7 * 86400000 ? 1 : 2;
  }
  function renderSessions(items) {
    const query = els.sessionSearch.value.trim().toLowerCase();
    const filtered = query ? items.filter((item) => (item.title || "").toLowerCase().includes(query)) : items;
    const groups = [[], [], []];
    for (const session of filtered) groups[sessionGroupIndex(session)].push(session);
    els.sessions.replaceChildren();
    if (!filtered.length) {
      const hint = make("p", "session-empty");
      hint.append(make("span", "", query ? `没有匹配「${query}」的对话。` : "还没有对话。"));
      if (query) {
        const clear = make("button", "text-button", "清空搜索");
        clear.type = "button";
        clear.addEventListener("click", () => {
          els.sessionSearch.value = "";
          syncSearchClear();
          renderSessions(sessionsCache);
          els.sessionSearch.focus();
        });
        hint.append(clear);
      }
      els.sessions.append(hint);
      return;
    }
    const groupLabels = ["今天", "7 天内", "更早"];
    groups.forEach((group, index) => {
      if (!group.length) return;
      if (!query) els.sessions.append(make("p", "session-group-label", groupLabels[index]));
      for (const session of group) {
        const row = make("div", "session-item");
        const active = session.id === activeSessionId;
        const open = make("button", `session-open${active ? " active" : ""}${session.archived ? " archived" : ""}`);
        open.type = "button";
        if (!active && session.id === pending?.session_id) open.append(make("span", "session-dot"));
        open.append(document.createTextNode(session.title || "未命名对话"));
        if (session.archived) open.append(document.createTextNode(" · 已归档"));
        open.title = `${session.title || "未命名对话"}${session.updated_at ? ` · ${new Date(session.updated_at).toLocaleString("zh-CN")}` : ""}`;
        open.setAttribute("aria-current", active ? "true" : "false");
        open.addEventListener("click", () => {
          if (pending && session.id !== pending.session_id) {
            toastWarn("这条消息仍在处理中，完成或停止等待后再切换对话。");
            return;
          }
          void selectSession(session);
        });
        row.append(open);
        if (session.archived) {
          const restore = make("button", "session-flag", "恢复");
          restore.type = "button";
          restore.title = "恢复对话";
          restore.addEventListener("click", async () => {
            try {
              await request(`/sessions/${session.id}/archive`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived: false }) });
              await loadSessions();
              toastSuccess("已恢复对话。");
            } catch (error) { toastError(error.message || "无法恢复这条对话。"); }
          });
          row.append(restore);
        } else {
          const archiveRow = make("button", "session-flag", "归档");
          archiveRow.type = "button";
          archiveRow.title = "归档对话（从列表收起，数据仍保留）";
          archiveRow.addEventListener("click", async () => {
            if (pending && session.id === pending.session_id) {
              toastWarn("这条消息仍在处理中，完成后再归档。");
              return;
            }
            setBusy(archiveRow, true, "归档中");
            try {
              await request(`/sessions/${session.id}/archive`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived: true }) });
              if (session.id === activeSessionId) { activeSessionId = null; activeSession = null; }
              await loadSessions();
              toast("已归档，可在「已归档」中恢复。", {
                actionLabel: "撤销",
                onAction: async () => {
                  await request(`/sessions/${session.id}/archive`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived: false }) });
                  await loadSessions();
                  toastSuccess("已撤销归档。");
                },
              });
            } catch (error) { toastError(error.message || "无法归档这条对话。"); }
            finally { setBusy(archiveRow, false); }
          });
          row.append(archiveRow);
        }
        els.sessions.append(row);
      }
    });
  }
  async function loadSessions() {
    const include = els.showArchived.checked || Boolean(pending);
    const { data } = await request(`/sessions?include_archived=${include}`);
    sessionsCache = data.items || [];
    renderSessions(sessionsCache);
    let current = pending
      ? sessionsCache.find((item) => item.id === pending.session_id)
      : sessionsCache.find((item) => item.id === activeSessionId);
    if (!current && !include) current = sessionsCache[0];
    if (!current && sessionsCache.length === 0) {
      if (!activeSessionId) await createSession();
      return;
    }
    if (current && current.id !== activeSessionId) await selectSession(current);
  }
  async function loadSessionsListOnly() {
    const include = els.showArchived.checked;
    const { data } = await request(`/sessions?include_archived=${include}`);
    sessionsCache = data.items || [];
    renderSessions(sessionsCache);
  }
  async function createSession() {
    const { data } = await request("/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "新对话" }) });
    await loadSessions();
    const { data: sessions } = await request("/sessions");
    const created = sessions.items.find((item) => item.id === data.id);
    if (created) await selectSession(created);
  }
  async function selectSession(session) {
    closeSidebar();
    const entering = activeSessionId !== session.id;
    clearInitiativeNotice();
    activeSessionId = session.id;
    activeSession = session;
    nextBeforeId = null;
    if (els.settingsOverlay && !els.settingsOverlay.classList.contains("hidden")) closeSettings();
    els.title.textContent = session.title;
    els.archive.disabled = Boolean(session.archived);
    els.archive.title = session.archived ? "已归档" : "归档对话";
    await loadMessages();
    await loadSessionsListOnly();
    if (pending && pending.session_id === activeSessionId) {
      renderPending();
      await checkOperation(false);
    } else {
      renderPending();
    }
    if (entering) {
      const generation = ++initiativeGeneration;
      if (!pending && !session.archived) void requestInitiative(session.id, generation);
    }
  }

  /* ── 消息流 ───────────────────────────────── */
  function isNearBottom() {
    return els.chatScroll.scrollHeight - els.chatScroll.scrollTop - els.chatScroll.clientHeight < 120;
  }
  function updateScrollAffordance() {
    const distance = els.chatScroll.scrollHeight - els.chatScroll.scrollTop - els.chatScroll.clientHeight;
    els.scrollBottom.classList.toggle("hidden", distance < 240);
  }
  function scrollToBottom(smooth) {
    const behavior = smooth && !window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "smooth" : "auto";
    els.chatScroll.scrollTo({ top: els.chatScroll.scrollHeight, behavior });
    els.newMessageFlag.classList.add("hidden");
  }
  function markNewMessage() {
    if (!isNearBottom()) els.newMessageFlag.classList.remove("hidden");
  }
  function showMessageSkeleton() {
    els.messages.replaceChildren();
    const widths = ["58%", "42%", "66%", "48%"];
    widths.forEach((width, index) => {
      const row = make("div", `skeleton-row${index % 2 ? " user" : ""}`);
      if (!index % 2) {
        const avatar = make("div", "skeleton skeleton-avatar");
        row.append(avatar);
      }
      const bubble = make("div", "skeleton skeleton-bubble");
      bubble.style.width = width;
      bubble.style.height = `${34 + (index % 3) * 8}px`;
      row.append(bubble);
      els.messages.append(row);
    });
  }
  async function loadMessages() {
    if (!activeSessionId) return;
    showMessageSkeleton();
    const { data } = await request(`/sessions/${activeSessionId}/messages?limit=50`);
    els.messages.replaceChildren();
    nextBeforeId = data.next_before_id || null;
    const items = data.items || [];
    if (!items.length && !(pending && pending.session_id === activeSessionId)) {
      const welcome = make("div", "empty-state");
      welcome.append(make("span", "welcome-mark", "晚"));
      welcome.append(make("p", "welcome-eyebrow", "SU WAN · 广州"));
      welcome.append(make("h2", "welcome-title", "今天想聊点什么？"));
      welcome.append(make("p", "welcome-copy", "工作、咖啡，或者今天心里那点小事。慢慢说就好。"));
      const suggestions = make("div", "prompt-suggestions");
      for (const text of ["今天有点累", "想跟你说件小事", "随便聊聊吧"]) {
        const suggestion = make("button", "prompt-suggestion", text);
        suggestion.type = "button";
        suggestion.addEventListener("click", () => {
          els.input.value = text;
          resizeInput();
          els.input.focus();
        });
        suggestions.append(suggestion);
      }
      welcome.append(suggestions);
      els.messages.append(welcome);
      return;
    }
    let lastDayKey = "";
    if (nextBeforeId) appendLoadEarlier();
    for (const item of items) {
      const dayKey = dayDividerLabel(item.created_at);
      if (dayKey && dayKey !== lastDayKey) {
        els.messages.append(make("div", "day-divider", dayKey));
        lastDayKey = dayKey;
      }
      appendMessage(item);
    }
    scrollToBottom();
    updateScrollAffordance();
  }
  function appendLoadEarlier() {
    const button = make("button", "load-earlier", "加载更早的消息");
    button.type = "button";
    button.addEventListener("click", () => { void loadEarlier(button); });
    els.messages.prepend(button);
  }
  async function loadEarlier(button) {
    if (!nextBeforeId || loadEarlierBusy || !activeSessionId) return;
    loadEarlierBusy = true;
    button.disabled = true;
    button.textContent = "正在加载…";
    try {
      const { data } = await request(`/sessions/${activeSessionId}/messages?limit=50&before_id=${encodeURIComponent(nextBeforeId)}`);
      const items = data.items || [];
      const previousHeight = els.chatScroll.scrollHeight;
      for (const item of items.slice().reverse()) appendMessage(item, true);
      nextBeforeId = data.next_before_id || null;
      if (nextBeforeId) {
        appendLoadEarlier();
        els.chatScroll.scrollTop += els.chatScroll.scrollHeight - previousHeight;
      } else {
        button.remove();
        els.chatScroll.scrollTop += els.chatScroll.scrollHeight - previousHeight;
      }
    } catch (error) {
      button.disabled = false;
      button.textContent = "加载更早的消息";
      toastError(error.message || "无法加载更早的消息。", {
        actionLabel: "重试",
        onAction: () => { void loadEarlier(button); },
      });
    } finally {
      loadEarlierBusy = false;
    }
  }
  function appendMessage(message, prepend = false) {
    const user = message.role === "user";
    const row = make("div", `message-row ${user ? "user" : "assistant"}`);
    if (!user) row.append(make("span", "avatar", "晚"));
    const body = make("div", "message-body");
    const authorRow = make("div", "message-author-row");
    if (!user) authorRow.append(make("span", "message-author", "苏晚晚"));
    const timeNode = make("time", "message-time", messageTimeLabel(message.created_at));
    if (message.created_at) timeNode.dateTime = message.created_at;
    timeNode.title = message.created_at ? new Date(message.created_at).toLocaleString("zh-CN") : "";
    authorRow.append(timeNode);
    body.append(authorRow);
    const bubble = make("div", `bubble${message.redacted ? " redacted" : ""}`, message.redacted ? "此条内容已因忘记操作移除" : (message.content || ""));
    body.append(bubble);
    if (!message.redacted && message.content) {
      const actions = make("div", "message-actions");
      const copyButton = make("button", "copy-button", "复制");
      copyButton.type = "button";
      copyButton.title = "复制这条消息";
      copyButton.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(message.content || "");
          copyButton.textContent = "已复制";
          copyButton.classList.add("copied");
          toastSuccess("已复制到剪贴板。", { duration: 1800 });
          setTimeout(() => { copyButton.textContent = "复制"; copyButton.classList.remove("copied"); }, 1400);
        } catch {
          copyButton.textContent = "复制失败";
          toastError("浏览器拒绝了剪贴板访问，请手动选中复制。", { duration: 5000 });
          setTimeout(() => { copyButton.textContent = "复制"; }, 1800);
        }
      });
      actions.append(copyButton);
      body.append(actions);
    }
    row.append(body);
    if (user) row.append(make("span", "avatar user-avatar", "我"));
    if (prepend && els.messages.firstElementChild) els.messages.insertBefore(row, els.messages.firstElementChild);
    else els.messages.append(row);
    if (!prepend) {
      if (isNearBottom()) scrollToBottom(true);
      else markNewMessage();
    }
    updateScrollAffordance();
  }
  function renderPending() {
    els.pending.replaceChildren();
    const belongs = pending && pending.session_id === activeSessionId;
    if (!belongs) {
      renderComposerState();
      els.queryOperation.classList.add("hidden");
      return;
    }
    const outgoing = make("div", "message-row user pending-message");
    const outgoingBody = make("div", "message-body");
    outgoingBody.append(make("div", "bubble", pending.text));
    outgoingBody.append(make("time", "message-time", pending.error ? "未送达" : "正在发送"));
    outgoing.append(outgoingBody);
    els.pending.append(outgoing);
    if (pending.error) {
      const card = make("div", "pending-card error-card");
      card.append(make("div", "", "这条消息暂时没能确认送达。"));
      if (pending.error.message) card.append(make("div", "pending-detail", pending.error.message));
      card.append(make("div", "pending-detail", `操作编号 ${pending.operation_id.slice(0, 8)}… · 可在刷新后继续处理`));
      const actions = make("div", "pending-actions");
      if (pending.error.retryable) {
        const retry = make("button", "secondary-button", "再试一次");
        retry.type = "button";
        retry.addEventListener("click", () => { void sendPending(true); });
        actions.append(retry);
      }
      const query = make("button", "text-button", "查询状态");
      query.type = "button";
      query.addEventListener("click", () => { void checkOperation(true); });
      actions.append(query);
      card.append(actions);
      els.pending.append(card);
    } else {
      const waiting = make("div", "message-row assistant waiting-message");
      waiting.append(make("span", "avatar", "晚"));
      const waitingBody = make("div", "message-body");
      const waitingAuthor = make("div", "message-author-row");
      waitingAuthor.append(make("span", "message-author", "苏晚晚"));
      const elapsed = make("span", "message-time", "");
      waitingAuthor.append(elapsed);
      waitingBody.append(waitingAuthor);
      const bubble = make("div", "bubble");
      bubble.append(make("span", "typing-label", "正在想怎么回你"));
      appendTypingDots(bubble);
      waitingBody.append(bubble);
      waiting.append(waitingBody);
      els.pending.append(waiting);
      startElapsedTimer(elapsed);
    }
    els.queryOperation.classList.toggle("hidden", !pending.error);
    renderComposerState();
  }
  /* 等待计时：让"卡住了"和"在慢慢回"可区分 */
  let elapsedTimer = null;
  function startElapsedTimer(node) {
    stopElapsedTimer();
    const startedAt = Date.now();
    const tick = () => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      node.textContent = seconds < 3 ? "思考中" : `思考中 · ${seconds}s`;
    };
    tick();
    elapsedTimer = setInterval(tick, 1000);
  }
  function stopElapsedTimer() {
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
  function setPendingError(error) {
    if (!pending) return;
    stopElapsedTimer();
    pending.error = { code: error?.code || "NETWORK_ERROR", message: error?.message || "暂时无法确认处理结果", retryable: Boolean(error?.retryable) };
    pending.status = "failed";
    savePending();
    renderPending();
    toastError(error?.message || "这条消息暂时没能确认送达。", {
      actionLabel: error?.retryable ? "重试" : null,
      onAction: error?.retryable ? () => { void sendPending(true); } : null,
    });
  }

  /* ── 发送与轮询 ───────────────────────────── */
  async function sendPending(retry) {
    if (!pending || isWaiting) return;
    if (pending.session_id !== activeSessionId) await activatePendingSession();
    pending.error = null;
    pending.status = "pending";
    savePending();
    renderPending();
    setWaiting(true);
    waitController = new AbortController();
    const timeout = setTimeout(() => waitController?.abort(), 65000);
    try {
      const { data, status } = await request("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation_id: pending.operation_id, session_id: pending.session_id, text: pending.text, retry }),
        signal: waitController.signal,
      });
      if (status === 202 || data.status === "pending") {
        pending.status = "pending";
        savePending();
        renderPending();
        beginPolling();
        setWaiting(false);
        return;
      }
      if (data.status === "succeeded") await acceptSuccess(data);
    } catch (error) {
      if (error.name === "AbortError") {
        await checkOperation(false);
      } else if (error.api?.operation_id || error.status) {
        const state = await checkOperation(false);
        if (!state) setPendingError(error.api || { code: "REQUEST_FAILED", message: error.message, retryable: true });
      } else {
        const state = await checkOperation(false);
        if (!state) setPendingError({ code: "NETWORK_ERROR", message: "网络状态不确定，请先查询操作状态。", retryable: true });
      }
    } finally {
      clearTimeout(timeout);
      waitController = null;
      setWaiting(false);
      renderPending();
    }
  }
  async function acceptSuccess(data) {
    const completedSession = pending?.session_id || activeSessionId;
    stopElapsedTimer();
    pending = null;
    savePending();
    if (completedSession === activeSessionId) await loadMessages();
    await loadSessionsListOnly();
    renderPending();
    if (data.warnings?.length) toastWarn(`本轮完成，但有状态提示：${data.warnings.join("、")}`, { duration: 6000 });
  }
  async function checkOperation(showNoticeOnError) {
    if (!pending) return null;
    try {
      const { data } = await request(`/operations/${pending.operation_id}`);
      if (data.status === "succeeded") {
        await acceptSuccess(data);
        return data;
      }
      if (data.status === "failed" || data.status === "interrupted") {
        const code = data.error_code || "UPSTREAM_ERROR";
        const retryable = !["CONFIG_MISSING", "CONTEXT_TOO_LARGE", "SESSION_ARCHIVED", "OPERATION_CONFLICT"].includes(code);
        setPendingError({ code, message: errorMessage(code), retryable });
        return data;
      }
      pending.status = "pending";
      pending.error = null;
      savePending();
      renderPending();
      if (showNoticeOnError) toast("还在处理中，可以稍后再次查询。");
      return data;
    } catch (error) {
      if (error.status === 404 && pending) {
        setPendingError({ code: "OPERATION_NOT_FOUND", message: "服务端尚无此操作记录，可以使用相同编号重新提交。", retryable: true });
        return null;
      }
      if (showNoticeOnError) toastError(error.message || "暂时无法查询状态。");
      return null;
    }
  }
  function errorMessage(code) {
    const messages = {
      CONFIG_MISSING: "缺少模型密钥。请在项目目录配置 .env 后重启应用。",
      PROCESS_INTERRUPTED: "应用重启时中断，可以明确重试。",
      UPSTREAM_TIMEOUT: "模型请求超时，可以再试一次。",
      UPSTREAM_NETWORK: "无法连接模型服务，请检查网络或代理设置。",
      REPLY_INVALID: "模型回复无法使用，请再试一次。",
      CONTEXT_TOO_LARGE: "上下文超过输入限制，建议新开一段对话。",
      SESSION_ARCHIVED: "这条对话已归档，恢复后才能继续发送。",
    };
    return messages[code] || "本轮没有完成。";
  }
  function beginPolling() {
    if (polling) return;
    polling = true;
    renderComposerState();
    const poll = async () => {
      if (!pending || !polling) { polling = false; renderComposerState(); return; }
      const result = await checkOperation(false);
      if (result?.status === "pending" && polling) setTimeout(poll, 1200);
      else { polling = false; renderComposerState(); }
    };
    setTimeout(poll, 900);
  }
  function cancelWait() {
    polling = false;
    waitController?.abort();
    setWaiting(false);
    if (pending) {
      pending.status = "pending";
      savePending();
      renderPending();
      toast("已停止等待。消息仍会在后台继续处理，稍后自动出现。", { duration: 5000 });
    } else renderComposerState();
  }
  async function activatePendingSession() {
    const { data } = await request(`/sessions?include_archived=true`);
    const session = data.items.find((item) => item.id === pending.session_id);
    if (session) await selectSession(session);
  }

  /* ── 输入区状态 ───────────────────────────── */
  function setWaiting(value) {
    isWaiting = value;
    renderComposerState();
  }
  function renderComposerState() {
    const stopping = isWaiting || polling;
    const blocked = isWaiting || Boolean(pending) || initiativePendingSessions.has(activeSessionId);
    els.input.disabled = blocked;
    els.form.classList.toggle("is-disabled", blocked);
    els.send.classList.toggle("stopping", stopping);
    els.sendLabel.textContent = stopping ? "停止" : "发送";
    els.send.title = stopping ? "停止等待（后台仍会继续处理）" : "发送（Enter）";
    els.send.setAttribute("aria-label", stopping ? "停止等待" : "发送消息");
    els.send.disabled = blocked && !stopping;
    els.cancelWait.classList.toggle("hidden", !stopping);
    els.cancelWait.textContent = stopping && isWaiting ? "不等了" : "取消等待";
    updateCharCount();
  }
  function updateCharCount() {
    const length = els.input.value.length;
    els.charCount.textContent = `${length} / 2000`;
    els.charCount.classList.toggle("hidden", length === 0);
    els.charCount.classList.toggle("warn", length >= 1800);
    els.charCount.title = length >= 2000 ? "已达上限，发送时会截断" : `还可输入 ${2000 - length} 字`;
  }
  async function submit() {
    if (pending || isWaiting || !activeSessionId) return;
    const text = els.input.value.trim();
    if (!text) return;
    if (text.length > 2000) toastWarn(`这条消息有 ${text.length} 字，超过 2000 上限，已截断发送。`, { duration: 5000 });
    pending = { operation_id: crypto.randomUUID(), session_id: activeSessionId, text, status: "pending", error: null };
    savePending();
    els.input.value = "";
    resizeInput();
    renderPending();
    await sendPending(false);
  }
  function resizeInput() {
    els.input.style.height = "auto";
    els.input.style.height = `${Math.min(els.input.scrollHeight, 180)}px`;
    updateCharCount();
  }

  /* ── 搜索框 ───────────────────────────────── */
  function syncSearchClear() {
    els.sessionSearchClear.classList.toggle("hidden", !els.sessionSearch.value);
  }

  /* ── 事件绑定 ─────────────────────────────── */
  els.form.addEventListener("submit", (event) => { event.preventDefault(); void submit(); });
  els.send.addEventListener("click", (event) => {
    if (isWaiting || polling) { event.preventDefault(); cancelWait(); }
  });
  els.input.addEventListener("compositionstart", () => { isComposing = true; });
  els.input.addEventListener("compositionend", () => { isComposing = false; });
  els.input.addEventListener("input", resizeInput);
  els.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !isComposing && event.keyCode !== 229) {
      event.preventDefault();
      void submit();
    }
  });
  els.sessionSearchClear.addEventListener("click", () => {
    els.sessionSearch.value = "";
    syncSearchClear();
    renderSessions(sessionsCache);
    els.sessionSearch.focus();
  });
  els.closeShortcuts.addEventListener("click", () => els.shortcutSheet.classList.add("hidden"));
  els.shortcutSheet.addEventListener("click", (event) => {
    if (event.target === els.shortcutSheet) els.shortcutSheet.classList.add("hidden");
  });
  els.newSession.addEventListener("click", () => {
    if (pending) { toastWarn("还有一条消息在处理中，先等它完成。"); return; }
    setBusy(els.newSession, true, "创建中");
    createSession()
      .catch((error) => toastError(error.message || "无法新建对话。"))
      .finally(() => setBusy(els.newSession, false));
  });

  els.sidebarToggle.addEventListener("click", () => {
    els.shell.classList.contains("sidebar-open") ? closeSidebar() : openSidebar();
  });
  els.sidebarCollapse.addEventListener("click", toggleRail);
  els.sidebarBackdrop.addEventListener("click", closeSidebar);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!els.shortcutSheet.classList.contains("hidden")) { els.shortcutSheet.classList.add("hidden"); return; }
    if (!els.settingsOverlay.classList.contains("hidden")) { closeSettings(); return; }
    if (document.activeElement === els.input && els.input.value) {
      els.input.value = "";
      resizeInput();
      toast("已清空草稿。", { duration: 1800 });
      return;
    }
    closeSidebar();
  });
  els.sessionSearch.addEventListener("input", () => { syncSearchClear(); renderSessions(sessionsCache); });
  els.showArchived.addEventListener("change", () => { void loadSessions(); });
  els.archive.addEventListener("click", async () => {
    if (!activeSessionId || activeSession?.archived) return;
    if (pending) { toastWarn("这条消息仍在处理中，完成后再归档。"); return; }
    setBusy(els.archive, true, "归档中");
    try {
      await request(`/sessions/${activeSessionId}/archive`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived: true }) });
      activeSessionId = null;
      activeSession = null;
      await loadSessions();
      toast("已归档，可在「已归档」中恢复。");
    } catch (error) { toastError(error.message || "无法归档这条对话。"); }
    finally { setBusy(els.archive, false); }
  });
  els.cancelWait.addEventListener("click", cancelWait);
  els.queryOperation.addEventListener("click", () => { void checkOperation(true); });
  els.openSettings.addEventListener("click", () => { openSettings("memory-view"); });
  els.closeSettings.addEventListener("click", closeSettings);
  for (const tab of [...els.settingsTabs, ...els.settingsEntries]) {
    tab.addEventListener("click", () => {
      if (tab.classList.contains("settings-entry")) openSettings(tab.dataset.view);
      else switchSettingsTab(tab.dataset.view);
    });
  }
  els.scrollBottom.addEventListener("click", () => scrollToBottom(true));
  els.chatScroll.addEventListener("scroll", () => {
    updateScrollAffordance();
    if (isNearBottom()) els.newMessageFlag.classList.add("hidden");
  }, { passive: true });
  els.manualMemoryForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const key = els.memoryKey.value;
    const value = els.memoryValue.value.trim();
    if (!value) { toastWarn("请先填写记忆内容。"); return; }
    const submitButton = els.manualMemoryForm.querySelector("button[type='submit']");
    setBusy(submitButton, true, "保存中");
    try {
      const { data, status } = await request("/memory", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, value }),
      });
      els.memoryValue.value = "";
      await loadMemoryView();
      if (!data.id) { setViewNotice("memory-view", "服务器没有返回记忆编号。", true); return; }
      const text = status === 200 ? "这条记忆已存在，已复用当前版本。" : "已添加用户记忆。";
      setViewNotice("memory-view", text);
      toastSuccess(text, { duration: 2600 });
    } catch (error) {
      setViewNotice("memory-view", error.message || "无法添加这条记忆。", true);
      toastError(error.message || "无法添加这条记忆。");
    } finally { setBusy(submitButton, false); }
  });
  els.confirmForget.addEventListener("click", () => {
    setBusy(els.confirmForget, true, "处理中");
    void forgetMemory();
  });
  els.cancelForget.addEventListener("click", () => { els.forgetDialog.close(); forgetFactId = null; });
  els.diagnosticsRefresh.addEventListener("click", () => {
    setBusy(els.diagnosticsRefresh, true, "刷新中");
    loadDiagnosticsView().finally(() => setBusy(els.diagnosticsRefresh, false));
  });
  els.refreshWorld.addEventListener("click", () => {
    setBusy(els.refreshWorld, true, "刷新中");
    loadWorldView().finally(() => setBusy(els.refreshWorld, false));
  });
  els.advanceWorld.addEventListener("click", () => { void advanceWorld(); });
  els.probeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!window.confirm("将向选定模型发送一次短测试请求。真实模式下可能产生费用；是否继续？")) return;
    els.probeResult.className = "probe-result";
    els.probeResult.textContent = "正在发送一次测试请求…";
    const submitButton = els.probeForm.querySelector("button[type='submit']");
    setBusy(submitButton, true, "测试中");
    try {
      const { data } = await request("/diagnostics/probe", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: els.probeProfile.value }),
      });
      els.probeResult.classList.add(data.ok ? "ok" : "bad");
      els.probeResult.textContent = data.ok
        ? `测试通过 · ${data.latency_ms} ms · JSON 格式${data.schema_valid === null ? "不适用" : data.schema_valid ? "有效" : "无效"}`
        : `测试失败 · ${data.error?.code || "PROBE_FAILED"} · ${data.error?.message || "请查看配置。"}`;
      toast(data.ok ? "模型连接正常。" : "模型连接失败，详见下方结果。", { type: data.ok ? "success" : "error" });
      await loadDiagnosticsView();
    } catch (error) {
      els.probeResult.classList.add("bad");
      els.probeResult.textContent = error.message || "测试请求未完成。";
      toastError(error.message || "测试请求未完成。");
    } finally { setBusy(submitButton, false); }
  });
  els.backupExport.addEventListener("click", async () => {
    setBusy(els.backupExport, true, "生成中…");
    els.backupNotice.textContent = "正在校验并生成数据库快照…";
    els.backupNotice.classList.remove("hidden", "error");
    try {
      const response = await fetch(`${api}/backup`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: "{}",
        cache: "no-store",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error?.message || "备份未能完成，请查看本地数据状态。");
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const filename = disposition.match(/filename="([A-Za-z0-9._-]+)"/)?.[1] || "suwan-backup.zip";
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      els.backupNotice.textContent = "备份已生成并开始下载。请将 ZIP 保存在安全位置。";
      toastSuccess(`备份已开始下载：${filename}`);
    } catch (error) {
      els.backupNotice.textContent = error.message || "备份失败。";
      els.backupNotice.classList.add("error");
      toastError(error.message || "备份失败。");
    } finally { setBusy(els.backupExport, false); }
  });

  /* ── 启动 ─────────────────────────────────── */
  /* 全局快捷键（输入框内不劫持除 Enter/Esc 外的按键） */
  document.addEventListener("keydown", (event) => {
    const inField = event.target === els.input || event.target === els.sessionSearch;
    const mod = event.ctrlKey || event.metaKey;
    if (mod && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (!els.settingsOverlay.classList.contains("hidden")) return;
      openSidebarOnNarrow();
      els.sessionSearch.focus();
      els.sessionSearch.select();
      return;
    }
    if (mod && event.key.toLowerCase() === "j") {
      event.preventDefault();
      if (!pending) els.newSession.click();
      return;
    }
    if (mod && event.key === "/") {
      event.preventDefault();
      els.settingsOverlay.classList.contains("hidden") ? openSettings("memory-view") : closeSettings();
      return;
    }
    if (event.key === "?" && !inField && !mod) {
      event.preventDefault();
      els.shortcutSheet.classList.toggle("hidden");
    }
  });
  function openSidebarOnNarrow() {
    if (window.matchMedia("(max-width: 900px)").matches) openSidebar();
  }

  if (bootstrap.mode === "fake") {
    els.mode.textContent = "离线模拟";
    els.mode.title = "当前为离线模拟模式：不会真正调用模型，回复来自本地假数据。";
  } else {
    els.mode.textContent = "真实模型";
    els.mode.classList.add("real-mode");
    els.mode.title = "当前为真实模型模式：每次回复都会调用已配置的模型，可能产生费用。";
  }
  /* 侧栏底部的界面构建标记，用来确认浏览器加载的是不是最新一版 */
  const buildTag = document.querySelector('meta[name="ui-build"]')?.content || "";
  if (els.uiVersion) els.uiVersion.textContent = buildTag || "未知";
  try { applyRail(localStorage.getItem(railKey) === "1"); } catch { applyRail(false); }
  updateClock();
  setInterval(updateClock, 60_000);
  syncSearchClear();
  resizeInput();
  (async () => {
    if (pending) await checkOperation(false);
    await loadSessions();
  })().catch((error) => toastError(error.message || "无法加载对话，请检查应用是否仍在运行。"));
})();
