(() => {
  "use strict";
  const bootstrapNode = document.getElementById("suwan-bootstrap");
  const bootstrap = JSON.parse(bootstrapNode?.textContent || "{}");
  const token = bootstrap.token || "";
  const api = "/api/v1";
  const els = {
    sessions: document.getElementById("sessions"),
    messages: document.getElementById("messages"),
    title: document.getElementById("session-title"),
    time: document.getElementById("time-context"),
    mode: document.getElementById("mode-badge"),
    form: document.getElementById("composer"),
    input: document.getElementById("message-input"),
    send: document.getElementById("send-button"),
    newSession: document.getElementById("new-session"),
    sidebarToggle: document.getElementById("sidebar-toggle"),
    sidebarBackdrop: document.getElementById("sidebar-backdrop"),
    showArchived: document.getElementById("show-archived"),
    archive: document.getElementById("archive-current"),
    pending: document.getElementById("pending-area"),
    cancelWait: document.getElementById("cancel-wait"),
    queryOperation: document.getElementById("query-operation"),
    pageLinks: [...document.querySelectorAll(".page-link[data-view]")],
    views: [...document.querySelectorAll(".view")],
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
    initiativeNotice: document.getElementById("initiative-notice"),
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
  function setWaiting(value) {
    isWaiting = value;
    const blocked = value || Boolean(pending) || initiativePendingSessions.has(activeSessionId);
    els.send.disabled = blocked;
    els.input.disabled = blocked;
    els.cancelWait.classList.toggle("hidden", !value && !polling);
  }
  function updateClock() {
    const dateText = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(new Date());
    els.time.textContent = `东京时间 ${dateText} · 苏晚所在时区：广州`;
  }
  function localTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Tokyo", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  }
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function closeSidebar() {
    document.querySelector(".app-shell")?.classList.remove("sidebar-open");
    els.sidebarToggle?.setAttribute("aria-expanded", "false");
  }
  function openSidebar() {
    document.querySelector(".app-shell")?.classList.add("sidebar-open");
    els.sidebarToggle?.setAttribute("aria-expanded", "true");
  }
  function setViewNotice(viewId, message, isError = false) {
    const targets = {
      "memory-view": els.managerNotice,
      "persona-view": els.personaNotice,
      "diagnostics-view": els.diagnosticsNotice,
    };
    const target = targets[viewId];
    if (!target) return;
    target.textContent = message || "";
    target.classList.toggle("hidden", !message);
    target.classList.toggle("error", Boolean(message && isError));
  }
  function switchView(viewId) {
    currentView = viewId;
    closeSidebar();
    for (const view of els.views) view.classList.toggle("hidden", view.id !== viewId);
    for (const link of els.pageLinks) link.classList.toggle("active", link.dataset.view === viewId);
    if (viewId === "memory-view") void loadMemoryView();
    if (viewId === "persona-view") void loadPersonaView();
    if (viewId === "diagnostics-view") void loadDiagnosticsView();
  }
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
      els.memoryList.append(make("p", "empty-state manager-empty", "还没有用户记忆。可以手动添加，或在待确认事实中确认一条。"));
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
      try {
        await request(`/memory/${item.id}/edit`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value: input.value.trim() }) });
        setViewNotice("memory-view", "修改已保存为新的记忆版本。旧版本来源保留。", false);
        await loadMemoryView();
      } catch (error) { setViewNotice("memory-view", error.message || "无法修改这条记忆。", true); }
    });
    card.append(form);
    input.focus();
  }
  let pendingProposalAction = false;
  function renderProposals(container, countNode, proposals, kind, viewId) {
    container.replaceChildren();
    countNode.textContent = String(proposals.length);
    if (!proposals.length) {
      container.append(make("p", "empty-state manager-empty", "目前没有待确认建议。"));
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
          try {
            await request(`/proposals/${proposal.id}/resolve`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) });
            setViewNotice(viewId, decision === "accept" ? "已确认；更新后的版本已经保存。" : "已拒绝这条建议。", false);
            if (viewId === "persona-view") await loadPersonaView();
            else await loadMemoryView();
          } catch (error) { setViewNotice(viewId, error.message || "无法处理这条建议。", true); }
          finally { pendingProposalAction = false; }
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
      for (const [key, value] of Object.entries(data.anchors || {})) addPersonaCard(key, value);
      addPersonaCard("已确认喜欢", data.preferences?.likes || []);
      addPersonaCard("已确认不喜欢", data.preferences?.dislikes || []);
      addPersonaCard("原有人设爱好", data.preferences?.hobbies || []);
      addPersonaCard("语气摘要", data.voice_summary || "");
      renderProposals(els.personaViewProposals, els.personaViewProposalCount, proposals.data.items || [], "persona", "persona-view");
      await loadWorldView();
      setViewNotice("persona-view", "");
    } catch (error) { setViewNotice("persona-view", error.message || "无法加载晚晚档案。", true); }
  }
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
        els.diagnosticsCalls.append(make("p", "empty-state manager-empty", "还没有模型调用记录。"));
      } else {
        const table = make("table", "diagnostics-table");
        const head = make("tr", "");
        for (const label of ["时间", "配置", "结果", "耗时", "输入 tokens", "输出 tokens"]) head.append(make("th", "", label));
        const thead = make("thead", ""); thead.append(head); table.append(thead);
        const body = make("tbody", "");
        for (const call of calls) {
          const row = make("tr", "");
          for (const value of [call.created_at, call.profile, call.status, `${call.latency_ms} ms`, call.input_tokens ?? "—", call.output_tokens ?? "—"]) row.append(make("td", "", value));
          body.append(row);
        }
        table.append(body);
        els.diagnosticsCalls.append(table);
      }
      setViewNotice("diagnostics-view", data.configuration || "");
    } catch (error) { setViewNotice("diagnostics-view", error.message || "无法读取诊断信息。", true); }
  }
  const worldStageLabels = { planned: "计划中", started: "已开始", progressed: "进行中", done: "已完成", paused: "已暂停" };
  function setWorldNotice(message, isError = false) {
    els.worldNotice.textContent = message || "";
    els.worldNotice.classList.toggle("hidden", !message);
    els.worldNotice.classList.toggle("error", Boolean(message && isError));
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
      els.worldThreads.replaceChildren(make("p", "empty-state manager-empty", "生活事项暂时不可用。"));
      renderWorldEvents(els.worldEvents, els.worldEventCount, [], new Map(), true);
      renderWorldEvents(els.memoryWorldEvents, els.memoryWorldEventCount, [], new Map(), false);
    }
  }
  function renderWorldEvents(container, countNode, events, threadById, allowActions) {
    container.replaceChildren();
    countNode.textContent = String(events.length);
    if (!events.length) {
      container.append(make("p", "empty-state manager-empty", "还没有已发生的生活事件。"));
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
    els.refreshWorld.disabled = true;
    els.advanceWorld.disabled = true;
    try {
      const result = await action();
      await loadWorldView();
      setWorldNotice(typeof successMessage === "function" ? successMessage(result) : successMessage);
    } catch (error) {
      setWorldNotice(error.message || "生活事项操作未完成。", true);
    } finally {
      worldBusy = false;
      els.refreshWorld.disabled = false;
      els.advanceWorld.disabled = false;
      for (const button of document.querySelectorAll(".world-thread-card button, .world-event-card button")) button.disabled = false;
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
  const initiativeReasons = {
    DISABLED: "主动开场已关闭。",
    QUIET_HOURS: "现在是安静时间，晚晚不会主动打扰。",
    GUARD_ACTIVE: "晚晚目前想保持一点距离。",
    LOW_ENERGY: "晚晚今天有些累，先不主动开场。",
    COOLDOWN: "距离上次主动开场还不到六小时。",
    NO_RECENT_EVENT: "最近没有新的生活经历，暂时没有合适的话题。",
    WAITING_USER: "晚晚已经发过主动消息，等你回复后再继续。",
    MODEL_FAILED: "主动开场暂时没有完成。",
  };
  async function requestInitiative(sessionId, generation) {
    if (!sessionId || pending || activeSession?.archived) return;
    initiativePendingSessions.add(sessionId);
    if (activeSessionId === sessionId) {
      els.initiativeNotice.textContent = "正在查看最近的生活主题…";
      els.initiativeNotice.classList.remove("hidden", "error");
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
        els.initiativeNotice.textContent = "";
        els.initiativeNotice.classList.add("hidden");
        appendMessage(data.assistant_message);
        await loadSessionsListOnly();
      } else {
        els.initiativeNotice.textContent = initiativeReasons[data.reason] || "这次没有主动开场。";
        els.initiativeNotice.classList.remove("hidden", "error");
      }
    } catch (error) {
      if (generation === initiativeGeneration && activeSessionId === sessionId) {
        els.initiativeNotice.textContent = error.message || "无法检查主动开场状态。";
        els.initiativeNotice.classList.remove("hidden");
        els.initiativeNotice.classList.add("error");
      }
    } finally {
      initiativePendingSessions.delete(sessionId);
      if (activeSessionId === sessionId) renderPending();
    }
  }
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
    } catch (error) { setViewNotice("memory-view", error.message || "无法忘记这条记忆。", true); }
  }
  function renderSessions(items) {
    els.sessions.replaceChildren();
    for (const session of items) {
      const row = make("div", "session-item");
      const open = make("button", `session-open${session.id === activeSessionId ? " active" : ""}${session.archived ? " archived" : ""}`, session.title);
      open.type = "button";
      open.addEventListener("click", () => {
        if (pending && session.id !== pending.session_id) {
          showNotice("这次消息仍在处理，先查询或完成它再切换对话。");
          return;
        }
        void selectSession(session);
      });
      row.append(open);
      if (session.archived) {
        const restore = make("button", "restore-button", "恢复");
        restore.type = "button";
        restore.addEventListener("click", async () => {
          await request(`/sessions/${session.id}/archive`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived: false }) });
          await loadSessions();
        });
        row.append(restore);
      }
      els.sessions.append(row);
    }
  }
  async function loadSessions() {
    const include = els.showArchived.checked || Boolean(pending);
    const { data } = await request(`/sessions?include_archived=${include}`);
    renderSessions(data.items || []);
    let current = pending
      ? (data.items || []).find((item) => item.id === pending.session_id)
      : (data.items || []).find((item) => item.id === activeSessionId);
    if (!current && !include) current = (data.items || [])[0];
    if (!current && (data.items || []).length === 0) {
      if (!activeSessionId) await createSession();
      return;
    }
    if (current && current.id !== activeSessionId) await selectSession(current);
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
    activeSessionId = session.id;
    activeSession = session;
    if (currentView !== "chat-view") switchView("chat-view");
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
  async function loadSessionsListOnly() {
    const include = els.showArchived.checked;
    const { data } = await request(`/sessions?include_archived=${include}`);
    renderSessions(data.items || []);
  }
  async function loadMessages() {
    if (!activeSessionId) return;
    const { data } = await request(`/sessions/${activeSessionId}/messages?limit=50`);
    els.messages.replaceChildren();
    const items = data.items || [];
    if (!items.length) {
      const welcome = make("div", "empty-state");
      welcome.append(make("span", "welcome-mark", "晚"));
      welcome.append(make("p", "welcome-eyebrow", "SU WAN · 广州"));
      welcome.append(make("h2", "welcome-title", "今天想聊点什么？"));
      welcome.append(make("p", "welcome-copy", "工作、咖啡，或者今天心里那点小事。慢慢说就好。"));
      const suggestions = make("div", "prompt-suggestions");
      for (const text of ["下班以后有点累，陪我聊会儿", "最近想练练手冲咖啡", "今天心情有点乱"]) {
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
    for (const item of items) appendMessage(item);
    els.messages.scrollTop = els.messages.scrollHeight;
  }
  function appendMessage(message) {
    const user = message.role === "user";
    const row = make("div", `message-row ${user ? "user" : "assistant"}`);
    if (!user) row.append(make("span", "avatar", "晚"));
    const body = make("div", "message-body");
    if (!user) body.append(make("span", "message-author", "晚晚"));
    const bubble = make("div", `bubble${message.redacted ? " redacted" : ""}`, message.redacted ? "此条内容已因忘记操作移除" : (message.content || ""));
    body.append(bubble);
    body.append(make("time", "message-time", localTime(message.created_at)));
    row.append(body);
    if (user) row.append(make("span", "avatar user-avatar", "我"));
    els.messages.append(row);
    els.messages.scrollTop = els.messages.scrollHeight;
  }
  function renderPending(message = "") {
    els.pending.replaceChildren();
    const belongs = pending && pending.session_id === activeSessionId;
    if (!belongs) {
      const blocked = isWaiting || Boolean(pending) || initiativePendingSessions.has(activeSessionId);
      els.send.disabled = blocked;
      els.input.disabled = blocked;
      els.queryOperation.classList.add("hidden");
      return;
    }
    const card = make("div", "pending-card");
    const label = pending.error ? `未发送 · ${pending.error.message || "本轮没有完成"}` : (pending.status === "pending" ? "正在处理 · 消息尚未计入历史" : "等待确认 · 消息尚未计入历史");
    card.append(make("div", "", label));
    card.append(make("div", "pending-text", pending.text));
    const actions = make("div", "pending-actions");
    if (pending.error && pending.error.retryable) {
      const retry = make("button", "text-button", "使用相同编号重试");
      retry.type = "button";
      retry.addEventListener("click", () => sendPending(true));
      actions.append(retry);
    }
    const check = make("button", "text-button", "查询状态");
    check.type = "button";
    check.addEventListener("click", () => checkOperation(true));
    actions.append(check);
    card.append(actions);
    els.pending.append(card);
    els.send.disabled = true;
    els.input.disabled = true;
    els.queryOperation.classList.toggle("hidden", !pending.error);
  }
  function setPendingError(error) {
    pending.error = { code: error?.code || "NETWORK_ERROR", message: error?.message || "暂时无法确认处理结果", retryable: Boolean(error?.retryable) };
    pending.status = "failed";
    savePending();
    renderPending();
  }
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
    pending = null;
    savePending();
    if (completedSession === activeSessionId) await loadMessages();
    await loadSessionsListOnly();
    renderPending();
    if (data.warnings?.length) showNotice(`本轮完成。状态提示：${data.warnings.join("、")}`);
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
      if (showNoticeOnError) showNotice("还在处理中，可以稍后再次查询。");
      return data;
    } catch (error) {
      if (error.status === 404 && pending) {
        setPendingError({ code: "OPERATION_NOT_FOUND", message: "服务端尚无此操作记录，可以使用相同编号重新提交。", retryable: true });
        return null;
      }
      if (showNoticeOnError) showNotice(error.message || "暂时无法查询状态。");
      return null;
    }
  }
  function errorMessage(code) {
    const messages = {
      CONFIG_MISSING: "缺少模型密钥。", PROCESS_INTERRUPTED: "应用重启时中断，可明确重试。",
      UPSTREAM_TIMEOUT: "模型请求超时。", UPSTREAM_NETWORK: "无法连接模型服务。",
      REPLY_INVALID: "模型回复无法使用。", CONTEXT_TOO_LARGE: "上下文超过输入限制。",
    };
    return messages[code] || "本轮没有完成。";
  }
  function beginPolling() {
    if (polling) return;
    polling = true;
    els.cancelWait.classList.remove("hidden");
    const poll = async () => {
      if (!pending || !polling) { polling = false; els.cancelWait.classList.toggle("hidden", !isWaiting); return; }
      const result = await checkOperation(false);
      if (result?.status === "pending" && polling) setTimeout(poll, 1200);
      else { polling = false; els.cancelWait.classList.toggle("hidden", !isWaiting); }
    };
    setTimeout(poll, 900);
  }
  async function activatePendingSession() {
    const { data } = await request(`/sessions?include_archived=true`);
    const session = data.items.find((item) => item.id === pending.session_id);
    if (session) await selectSession(session);
  }
  function showNotice(message) {
    if (currentView !== "chat-view") {
      setViewNotice(currentView, message, false);
      return;
    }
    const notice = make("div", "pending-card", message);
    els.pending.prepend(notice);
    setTimeout(() => notice.remove(), 6000);
  }
  async function submit() {
    if (pending || isWaiting || !activeSessionId) return;
    const text = els.input.value.trim();
    if (!text) return;
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
  }

  els.form.addEventListener("submit", (event) => { event.preventDefault(); void submit(); });
  els.input.addEventListener("compositionstart", () => { isComposing = true; });
  els.input.addEventListener("compositionend", () => { isComposing = false; });
  els.input.addEventListener("input", resizeInput);
  els.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !isComposing && event.keyCode !== 229) {
      event.preventDefault();
      void submit();
    }
  });
  els.newSession.addEventListener("click", () => { if (!pending) void createSession(); });
  els.sidebarToggle.addEventListener("click", () => {
    document.querySelector(".app-shell")?.classList.contains("sidebar-open") ? closeSidebar() : openSidebar();
  });
  els.sidebarBackdrop.addEventListener("click", closeSidebar);
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeSidebar(); });
  els.showArchived.addEventListener("change", () => { void loadSessions(); });
  els.archive.addEventListener("click", async () => {
    if (!activeSessionId || activeSession?.archived || pending) return;
    await request(`/sessions/${activeSessionId}/archive`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ archived: true }) });
    activeSessionId = null;
    activeSession = null;
    await loadSessions();
  });
  els.cancelWait.addEventListener("click", () => {
    polling = false;
    waitController?.abort();
    setWaiting(false);
    if (pending) { pending.status = "pending"; savePending(); renderPending(); }
  });
  els.queryOperation.addEventListener("click", () => { void checkOperation(true); });
  for (const link of els.pageLinks) link.addEventListener("click", () => switchView(link.dataset.view));
  els.manualMemoryForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const key = els.memoryKey.value;
    const value = els.memoryValue.value.trim();
    try {
      const { data, status } = await request("/memory", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, value }),
      });
      els.memoryValue.value = "";
      await loadMemoryView();
      setViewNotice("memory-view", status === 200 ? "这条记忆已存在，已复用当前版本。" : "已添加用户记忆。", false);
      if (!data.id) setViewNotice("memory-view", "服务器没有返回记忆编号。", true);
    } catch (error) { setViewNotice("memory-view", error.message || "无法添加这条记忆。", true); }
  });
  els.confirmForget.addEventListener("click", () => { void forgetMemory(); });
  els.cancelForget.addEventListener("click", () => { els.forgetDialog.close(); forgetFactId = null; });
  els.diagnosticsRefresh.addEventListener("click", () => { void loadDiagnosticsView(); });
  els.refreshWorld.addEventListener("click", () => { void loadWorldView(); });
  els.advanceWorld.addEventListener("click", () => { void advanceWorld(); });
  els.probeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!window.confirm("将向选定模型发送一次短测试请求。真实模式下可能产生费用；是否继续？")) return;
    els.probeResult.textContent = "正在发送一次测试请求…";
    const submitButton = els.probeForm.querySelector("button[type='submit']");
    submitButton.disabled = true;
    try {
      const { data } = await request("/diagnostics/probe", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: els.probeProfile.value }),
      });
      els.probeResult.textContent = data.ok
        ? `测试通过 · ${data.latency_ms} ms · JSON 格式${data.schema_valid === null ? "不适用" : data.schema_valid ? "有效" : "无效"}`
        : `测试失败 · ${data.error?.code || "PROBE_FAILED"} · ${data.error?.message || "请查看配置。"}`;
      await loadDiagnosticsView();
    } catch (error) { els.probeResult.textContent = error.message || "测试请求未完成。"; }
    finally { submitButton.disabled = false; }
  });
  els.backupExport.addEventListener("click", async () => {
    els.backupExport.disabled = true;
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
    } catch (error) {
      els.backupNotice.textContent = error.message || "备份失败。";
      els.backupNotice.classList.add("error");
    } finally { els.backupExport.disabled = false; }
  });
  if (bootstrap.mode === "fake") els.mode.textContent = "离线模拟";
  else { els.mode.textContent = "真实模型"; els.mode.classList.add("real-mode"); }
  updateClock();
  setInterval(updateClock, 60_000);
  (async () => {
    if (pending) await checkOperation(false);
    await loadSessions();
  })().catch((error) => showNotice(error.message || "无法加载对话。"));
})();
