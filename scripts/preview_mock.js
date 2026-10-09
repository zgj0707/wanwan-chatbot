/* 单文件预览用的内存假后端。
   仅在以 file:// 直接打开导出的 HTML 时生效：拦截 fetch，用内存数据模拟 /api/v1/*。
   走真实服务时这段代码不会被打包（构建脚本按 ui/ 源码内联，不含本文件）。 */
(() => {
  "use strict";

  /* file:// 在部分浏览器里不算安全上下文，补一个 randomUUID 兜底 */
  if (!crypto.randomUUID) {
    crypto.randomUUID = () => {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      bytes[6] = (bytes[6] & 0x0f) | 0x40;
      bytes[8] = (bytes[8] & 0x3f) | 0x80;
      const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
      return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    };
  }

  const MIN = 60_000;
  const HOUR = 60 * MIN;
  const DAY = 24 * HOUR;
  const boot = Date.now();
  const iso = (ms) => new Date(ms).toISOString();
  const uuid = () => crypto.randomUUID();

  /* ── 内存数据 ─────────────────────────────── */
  const sessions = [];
  const messages = new Map();
  const operations = new Map();

  function addSession(id, title, archived, createdMs, updatedMs) {
    sessions.push({ id, title, archived, created_at: iso(createdMs), updated_at: iso(updatedMs) });
    messages.set(id, []);
    return id;
  }
  function addMessage(sessionId, role, content, atMs) {
    const list = messages.get(sessionId);
    const item = {
      id: uuid(),
      seq: list.length + 1,
      role,
      content,
      redacted: false,
      created_at: iso(atMs),
      operation_id: null,
    };
    list.push(item);
    const session = sessions.find((entry) => entry.id === sessionId);
    if (session && Date.parse(session.updated_at) < atMs) session.updated_at = iso(atMs);
    return item;
  }

  const sToday = addSession(uuid(), "今天有点累", false, boot - 5 * HOUR, boot - 2 * HOUR);
  addMessage(sToday, "user", "在吗？", boot - 3 * HOUR);
  addMessage(sToday, "assistant", "在的。今天过得怎么样，先说说最想吐槽的那一件？", boot - 3 * HOUR + 40_000);
  addMessage(sToday, "user", "会开了四个小时，中间没怎么吃东西。", boot - 2 * HOUR);
  addMessage(sToday, "assistant", "四个小时……难怪你现在都不想动。先去喝点水，饭可以晚一点，但别省。", boot - 2 * HOUR + 55_000);

  const sWalk = addSession(uuid(), "周末想去哪里走走", false, boot - 26 * HOUR, boot - 25 * HOUR);
  addMessage(sWalk, "user", "周末想出去走走，但不想去太远的地方。", boot - 26 * HOUR);
  addMessage(sWalk, "assistant", "不远的话，江边那一段步道很合适，走二十分钟就有地方坐下来喝东西。要我帮你看看天气吗？", boot - 25 * HOUR - 20 * MIN);

  /* 长对话：跨两天、共 54 条，用来验证日期分隔条与「加载更早」分页 */
  const sReview = addSession(uuid(), "这周的复盘", false, boot - 2 * DAY, boot - 9 * HOUR);
  const reviewLines = [
    "这周的事情有点多，我想理一下优先级。",
    "我先说说你现在最在意的是哪一件？剩下的可以先放着。",
    "如果只能先完成一件，我会选那个 deadline 最近、但别人也在等你的。",
    "为什么它比其他几件更急？",
    "因为它卡住了下游两个人的排期。",
    "那把它放到今天下班前，先出一个能用的版本，不必等完美。",
    "好，我先写一个骨架。",
    "写完发我看一下？",
    "发你了，其实只有三段，后面还没写。",
    "三段已经够我看出方向了，第二段的顺序我调一下更好。",
    "你觉得怎么调？",
    "先说结论，再说过程，最后给下一步。",
    "明白了，我重排一下。",
    "重排完是不是清楚多了？",
    "清楚多了，谢谢。",
    "不客气。要不要顺手把下周的事也列一下？",
    "下周有评审，可能来不及。",
    "那就把评审需要的东西提前准备好，别到当天才找。",
    "评审要准备什么？",
    "一页摘要，加上你打算被问到的问题和答案。",
    "被问到的问题……这个我确实没想过。",
    "现在想还来得及。想象最挑剔的同事会问什么。",
    "他大概会问「这个结论有依据吗」。",
    "那就把依据放到最前面。",
    "依据我手里有数据。",
    "那放上去就行，不用多，一句话加一个数字。",
    "我怕显得太干。",
    "干一点没关系，评审要的是可信。",
    "好吧，我试试。",
    "写完记得自己先读一遍。",
    "读的时候把口水话删掉。",
    "我老是舍不得删。",
    "删掉不会有人记得的。",
    "也是。",
    "那我再砍一半。",
    "这就利落了。",
    "明天评审几点？",
    "上午十点。",
    "那今晚早点睡，别熬夜准备。",
    "睡不着的时候你会想什么？",
    "想明天要说的第一句话。",
    "第一句很重要，说清楚了后面就顺了。",
    "那我先把第一句写下来。",
    "写下来就安心了。",
    "剩下的交给明天的自己。",
    "希望明天的我比今天靠谱一点。",
    "今天已经很好了。",
    "你总是这么说。",
    "因为是真的。",
    "好，那我去睡了。",
    "晚安。明天见。",
  ];
  const reviewStart = boot - 30 * HOUR;
  for (let index = 0; index < 54; index += 1) {
    const role = index % 2 === 0 ? "user" : "assistant";
    addMessage(sReview, role, reviewLines[index % reviewLines.length], reviewStart + index * 26 * MIN);
  }

  const sCoffee = addSession(uuid(), "咖啡手冲记录", false, boot - 5 * DAY, boot - 5 * DAY + 30 * MIN);
  addMessage(sCoffee, "user", "今天用的水温好像偏低。", boot - 5 * DAY);
  addMessage(sCoffee, "assistant", "低一点会让酸更突出。可以试试 92 度，再闷蒸 30 秒。", boot - 5 * DAY + 12 * MIN);
  addMessage(sCoffee, "user", "闷蒸是为了排气吗？", boot - 5 * DAY + 30 * MIN);

  const sArchived = addSession(uuid(), "读书清单", true, boot - 24 * DAY, boot - 20 * DAY);
  addMessage(sArchived, "user", "帮我列一份本年的读书清单。", boot - 24 * DAY);
  addMessage(sArchived, "assistant", "先给你六本，都是能在一个下午读完的。", boot - 20 * DAY);

  const memory = [
    {
      id: uuid(),
      key: "profile.preferred_name",
      value: "阿城",
      status: "active",
      origin: "manual",
      source_message_ids: [],
      sources: [],
      created_at: iso(boot - 6 * DAY),
    },
    {
      id: uuid(),
      key: "preference.drink",
      value: "美式，不加糖",
      status: "active",
      origin: "conversation",
      source_message_ids: [],
      sources: [{ id: uuid(), created_at: iso(boot - 5 * DAY), content: "我平时都喝美式，不加糖。", redacted: false }],
      created_at: iso(boot - 5 * DAY),
    },
    {
      id: uuid(),
      key: "preference.hobby",
      value: "夜跑，一周两次",
      status: "active",
      origin: "conversation",
      source_message_ids: [],
      sources: [{ id: uuid(), created_at: iso(boot - 4 * DAY), content: "我一般一周夜跑两次。", redacted: false }],
      created_at: iso(boot - 4 * DAY),
    },
    {
      id: uuid(),
      key: "preference.chat_style",
      value: "少用感叹号",
      status: "superseded",
      origin: "conversation",
      source_message_ids: [],
      sources: [{ id: uuid(), created_at: iso(boot - 9 * DAY), content: "不要太多感叹号。", redacted: false }],
      created_at: iso(boot - 9 * DAY),
    },
    {
      id: uuid(),
      key: "plan.personal",
      value: "年底前读完十二本书",
      status: "active",
      origin: "manual",
      source_message_ids: [],
      sources: [],
      created_at: iso(boot - 3 * DAY),
    },
  ];

  const proposals = [
    {
      id: uuid(),
      kind: "user_fact",
      payload: {
        key: "profile.birthday",
        value: "11-07",
        evidence_quote: "我生日是 11 月 7 号。",
        reason: "句式明确，属于稳定的个人事实。",
      },
      source: {
        id: uuid(),
        session_id: sCoffee,
        role: "user",
        content: "我生日是 11 月 7 号，到时候记得提醒我。",
        redacted: false,
        created_at: iso(boot - 2 * DAY),
      },
      status: "pending",
      result_id: null,
      created_at: iso(boot - 2 * DAY),
      resolved_at: null,
    },
    {
      id: uuid(),
      kind: "user_fact",
      payload: {
        key: "preference.food",
        value: "不吃香菜",
        evidence_quote: "香菜我一律挑掉。",
        reason: "偏好明确，但样本只有一次。",
      },
      source: {
        id: uuid(),
        session_id: sWalk,
        role: "user",
        content: "香菜我一律挑掉，别的都行。",
        redacted: false,
        created_at: iso(boot - 26 * HOUR),
      },
      status: "pending",
      result_id: null,
      created_at: iso(boot - 26 * HOUR),
      resolved_at: null,
    },
    {
      id: uuid(),
      kind: "persona",
      payload: {
        field: "preferences.likes",
        value: "深夜的粥",
        evidence_quote: "她说最近很喜欢楼下那家粥。",
        reason: "反复提及，可作为稳定喜好。",
      },
      source: {
        id: uuid(),
        session_id: sToday,
        role: "assistant",
        content: "我最近挺喜欢楼下那家粥的，半夜还开着。",
        redacted: false,
        created_at: iso(boot - 3 * HOUR),
      },
      status: "pending",
      result_id: null,
      created_at: iso(boot - 3 * HOUR),
      resolved_at: null,
    },
    {
      id: uuid(),
      kind: "persona",
      payload: {
        field: "voice_summary",
        value: "句子偏短，很少用感叹号",
        evidence_quote: "她的回复通常很短。",
        reason: "从多条回复归纳的语气特征。",
      },
      source: {
        id: uuid(),
        session_id: sReview,
        role: "assistant",
        content: "重排完是不是清楚多了？",
        redacted: false,
        created_at: iso(boot - 28 * HOUR),
      },
      status: "pending",
      result_id: null,
      created_at: iso(boot - 28 * HOUR),
      resolved_at: null,
    },
  ];

  const persona = {
    id: uuid(),
    version: 7,
    anchors: {
      name: "苏晚",
      nickname: "晚晚",
      age: 25,
      city: "广州",
      occupation: "互联网中厂产品经理",
      cat_name: "年糕",
      cat_breed: "长毛三花猫",
      virtual_identity: true,
      temperament: ["温柔", "慵懒", "好奇", "机灵"],
      boundary: "温柔但有距离感，不讨好，不黏人",
      catchphrases: ["嗯…让我想想", "好无聊啊", "才不是呢", "再睡五分钟", "这个需求……再改改吧"],
    },
    preferences: {
      likes: ["深夜的粥", "手冲咖啡", "江边散步"],
      dislikes: ["太甜的饮料", "临时改计划"],
      hobbies: ["拍照", "胶片摄影", "老电影", "独立音乐", "手冲咖啡"],
    },
    voice_summary: "句子偏短，很少用感叹号；遇到对方难过时先接住情绪，再谈事情。",
    created_at: iso(boot - 30 * DAY),
  };

  const worldThreads = [
    {
      id: uuid(),
      template_key: "work_requirement",
      label: "整理这周的一个需求",
      cycle: 0,
      step: 2,
      stage: "progressed",
      active: true,
      last_event_at: iso(boot - 20 * HOUR),
    },
    {
      id: uuid(),
      template_key: "photo_selection",
      label: "整理一组随手拍",
      cycle: 0,
      step: 1,
      stage: "started",
      active: true,
      last_event_at: iso(boot - 2 * DAY),
    },
    {
      id: uuid(),
      template_key: "coffee_practice",
      label: "试一种手冲方法",
      cycle: 0,
      step: 0,
      stage: "planned",
      active: true,
      last_event_at: null,
    },
  ];
  const worldEvents = [
    {
      id: uuid(),
      thread_id: worldThreads[0].id,
      cycle: 0,
      slot_key: "slot_1",
      step_before: 1,
      step_after: 2,
      stage_after: "progressed",
      summary: "把需求拆成了三段，先写最容易落地的那部分。",
      occurred_at: iso(boot - 20 * HOUR),
      created_at: iso(boot - 20 * HOUR),
      retracted_at: null,
      status: "committed",
      can_retract: true,
    },
    {
      id: uuid(),
      thread_id: worldThreads[1].id,
      cycle: 0,
      slot_key: "slot_1",
      step_before: 0,
      step_after: 1,
      stage_after: "started",
      summary: "先挑了十张，光影合适的那几张留在前面。",
      occurred_at: iso(boot - 2 * DAY),
      created_at: iso(boot - 2 * DAY),
      retracted_at: null,
      status: "committed",
      can_retract: true,
    },
  ];

  const callStats = [
    { profile: "chat", status: "succeeded", latency_ms: 2140 },
    { profile: "chat", status: "succeeded", latency_ms: 1890 },
    { profile: "analysis", status: "succeeded", latency_ms: 3320 },
    { profile: "chat", status: "succeeded", latency_ms: 2010 },
    { profile: "world", status: "degraded", latency_ms: 8740 },
    { profile: "chat", status: "succeeded", latency_ms: 2260 },
    { profile: "chat", status: "succeeded", latency_ms: 1750 },
    { profile: "analysis", status: "failed", latency_ms: 940 },
  ];
  const calls = callStats.map((call, index) => ({
    created_at: iso(boot - (index + 1) * 17 * MIN),
    profile: call.profile,
    status: call.status,
    latency_ms: call.latency_ms,
    input_tokens: 820 + index * 37,
    output_tokens: 210 + index * 11,
  }));

  const replies = [
    "嗯，我听着。你想从哪一段说起？",
    "这件事你其实已经有判断了，只是还没说出来。要不先说结论？",
    "别急着评价自己。先把事实摆出来：发生了什么，你做了什么，接下来最想改哪一步。",
    "我记下了。如果明天还是这样，我们再一起想办法。",
    "慢一点没关系，方向是对的。",
    "听起来你需要的是有人帮你确认「这样也可以」，那我就确认一下：这样可以。",
  ];
  let replyCursor = 0;
  function nextReply() {
    const text = replies[replyCursor % replies.length];
    replyCursor += 1;
    return text;
  }

  /* ── fetch 拦截 ───────────────────────────── */
  const jsonResponse = (data, status = 200) =>
    new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  const fail = (status, code, message, retryable = false) =>
    jsonResponse({ error: { code, message, retryable } }, status);
  const delay = (ms, signal) =>
    new Promise((resolve, reject) => {
      const timer = setTimeout(resolve, ms);
      signal?.addEventListener("abort", () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      });
    });
  const listMessages = (sessionId, limit, beforeId) => {
    const all = messages.get(sessionId);
    if (!all) return null;
    let rows = all;
    if (beforeId) {
      const target = all.find((item) => item.id === beforeId);
      if (!target) return null;
      rows = all.filter((item) => item.seq < target.seq);
    }
    const page = rows.slice(-limit);
    const hasOlder = rows.length > limit;
    return { items: page, next_before_id: hasOlder && page.length ? page[0].id : null };
  };

  const realFetch = window.fetch.bind(window);
  window.fetch = async function mockFetch(input, init = {}) {
    const raw = typeof input === "string" ? input : input.url;
    if (!raw.includes("/api/v1/")) return realFetch(input, init);
    const url = new URL(raw, "http://localhost");
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = (init.method || "GET").toUpperCase();
    const body = init.body ? JSON.parse(init.body) : {};
    const signal = init.signal;
    await delay(140, signal);

    /* 会话 */
    if (path === "/sessions" && method === "GET") {
      const includeArchived = url.searchParams.get("include_archived") === "true";
      const items = sessions
        .filter((item) => includeArchived || !item.archived)
        .slice()
        .sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
      return jsonResponse({ items });
    }
    if (path === "/sessions" && method === "POST") {
      const id = uuid();
      addSession(id, body.title || "新对话", false, Date.now(), Date.now());
      return jsonResponse({ id, title: body.title || "新对话", archived: false }, 201);
    }
    const archiveMatch = path.match(/^\/sessions\/([^/]+)\/archive$/);
    if (archiveMatch && method === "POST") {
      const session = sessions.find((item) => item.id === archiveMatch[1]);
      if (!session) return fail(404, "SESSION_NOT_FOUND", "找不到这条对话。");
      session.archived = Boolean(body.archived);
      return jsonResponse({ id: session.id, archived: session.archived });
    }
    const messagesMatch = path.match(/^\/sessions\/([^/]+)\/messages$/);
    if (messagesMatch && method === "GET") {
      const limit = Number(url.searchParams.get("limit") || 50);
      const beforeId = url.searchParams.get("before_id");
      const page = listMessages(messagesMatch[1], limit, beforeId);
      if (!page) return fail(404, "SESSION_NOT_FOUND", "找不到这条对话。");
      return jsonResponse(page);
    }

    /* 聊天：先 202 挂起，稍后 operation 才成功，用来展示等待态 */
    if (path === "/chat" && method === "POST") {
      const sessionId = body.session_id;
      if (!messages.has(sessionId)) return fail(404, "SESSION_NOT_FOUND", "找不到这条对话。");
      addMessage(sessionId, "user", body.text, Date.now());
      operations.set(body.operation_id, { sessionId, text: body.text, readyAt: Date.now() + 1800 });
      return jsonResponse({ status: "pending", operation_id: body.operation_id }, 202);
    }
    const operationMatch = path.match(/^\/operations\/([^/]+)$/);
    if (operationMatch && method === "GET") {
      const operation = operations.get(operationMatch[1]);
      if (!operation) return fail(404, "OPERATION_NOT_FOUND", "服务端尚无此操作记录。", true);
      if (Date.now() < operation.readyAt) return jsonResponse({ operation_id: operationMatch[1], status: "pending" });
      if (operation.result) return jsonResponse(operation.result);
      const userMessage = messages.get(operation.sessionId).find((item) => item.content === operation.text && item.role === "user");
      const assistantMessage = addMessage(operation.sessionId, "assistant", nextReply(), Date.now());
      const result = {
        operation_id: operationMatch[1],
        status: "succeeded",
        user_message: userMessage,
        assistant_message: assistantMessage,
        message: assistantMessage,
        error_code: null,
        warnings: [],
        elapsed_ms: 1810,
        call_count: 2,
      };
      operation.result = result;
      return jsonResponse(result);
    }
    if (path === "/initiative" && method === "POST") return jsonResponse({ sent: false });

    /* 记忆 */
    if (path === "/memory" && method === "GET") return jsonResponse({ items: memory, total: memory.length });
    if (path === "/memory" && method === "POST") {
      const exists = memory.find((item) => item.key === body.key && item.status === "active");
      if (exists) {
        if (exists.value !== body.value) {
          exists.status = "superseded";
          const created = { ...exists, id: uuid(), value: body.value, status: "active", created_at: iso(Date.now()) };
          memory.unshift(created);
          return jsonResponse({ ...created }, 201);
        }
        return jsonResponse({ ...exists }, 200);
      }
      const created = {
        id: uuid(),
        key: body.key,
        value: body.value,
        status: "active",
        origin: "manual",
        source_message_ids: [],
        sources: [],
        created_at: iso(Date.now()),
      };
      memory.unshift(created);
      return jsonResponse({ ...created }, 201);
    }
    const editMatch = path.match(/^\/memory\/([^/]+)\/edit$/);
    if (editMatch && method === "POST") {
      const item = memory.find((entry) => entry.id === editMatch[1]);
      if (!item) return fail(404, "MEMORY_NOT_FOUND", "找不到这条记忆。");
      item.status = "superseded";
      memory.unshift({ ...item, id: uuid(), value: body.value, status: "active", created_at: iso(Date.now()) });
      return jsonResponse({ ok: true });
    }
    const forgetMatch = path.match(/^\/memory\/([^/]+)\/forget$/);
    if (forgetMatch && method === "POST") {
      const index = memory.findIndex((entry) => entry.id === forgetMatch[1]);
      if (index < 0) return fail(404, "MEMORY_NOT_FOUND", "找不到这条记忆。");
      memory.splice(index, 1);
      return jsonResponse({ redacted_message_count: 1, notice: "来源内容已按你的选择处理。" });
    }

    /* 档案与建议 */
    if (path === "/persona" && method === "GET") return jsonResponse({ ...persona });
    if (path === "/proposals" && method === "GET") {
      const kind = url.searchParams.get("kind");
      const items = proposals.filter((item) => item.status === "pending" && (!kind || item.kind === kind));
      return jsonResponse({ items, total: items.length });
    }
    const resolveMatch = path.match(/^\/proposals\/([^/]+)\/resolve$/);
    if (resolveMatch && method === "POST") {
      const proposal = proposals.find((item) => item.id === resolveMatch[1]);
      if (!proposal) return fail(404, "PROPOSAL_NOT_FOUND", "找不到这条建议。");
      proposal.status = body.decision === "accept" ? "accepted" : "rejected";
      proposal.resolved_at = iso(Date.now());
      if (body.decision === "accept" && proposal.kind === "user_fact") {
        memory.unshift({
          id: uuid(),
          key: proposal.payload.key,
          value: proposal.payload.value,
          status: "active",
          origin: "conversation",
          source_message_ids: [],
          sources: [proposal.source],
          created_at: iso(Date.now()),
        });
      }
      return jsonResponse({ ok: true, status: proposal.status });
    }

    /* 生活事项 */
    if (path === "/world" && method === "GET") {
      return jsonResponse({
        threads: worldThreads,
        events: worldEvents,
        state: { valence: 0.2, energy: 0.4, closeness: 0.3, guard_until: null },
        last_update_at: iso(boot - 20 * HOUR),
      });
    }
    if (path === "/world/advance" && method === "POST") {
      return jsonResponse({ advanced: false, event: null, warnings: [], elapsed_ms: 120 });
    }
    const threadMatch = path.match(/^\/world\/threads\/([^/]+)\/control$/);
    if (threadMatch && method === "POST") {
      const thread = worldThreads.find((item) => item.id === threadMatch[1]);
      if (!thread) return fail(404, "WORLD_THREAD_NOT_FOUND", "找不到这个生活事项。");
      if (body.action === "pause") thread.stage = "paused";
      if (body.action === "resume") thread.stage = "progressed";
      if (body.action === "restart") { thread.stage = "planned"; thread.step = 0; thread.cycle += 1; }
      return jsonResponse({ ...thread });
    }
    const retractMatch = path.match(/^\/world\/events\/([^/]+)\/retract$/);
    if (retractMatch && method === "POST") {
      const event = worldEvents.find((item) => item.id === retractMatch[1]);
      if (!event) return fail(404, "WORLD_EVENT_NOT_FOUND", "找不到这条经历。");
      event.status = "retracted";
      event.can_retract = false;
      return jsonResponse({ ...event });
    }

    /* 诊断与备份 */
    if (path === "/diagnostics" && method === "GET") {
      const inputTokens = calls.reduce((sum, call) => sum + call.input_tokens, 0);
      const outputTokens = calls.reduce((sum, call) => sum + call.output_tokens, 0);
      const average = Math.round(calls.reduce((sum, call) => sum + call.latency_ms, 0) / calls.length);
      return jsonResponse({
        mode: "fake",
        schema_version: 1,
        health: { alive: true, checked_at: iso(Date.now()) },
        profiles: {
          chat: { model: "local-echo", provider: "本地模拟", credential_configured: null },
          analysis: { model: "local-echo", provider: "本地模拟", credential_configured: null },
          world: { model: "local-echo", provider: "本地模拟", credential_configured: null },
        },
        metrics: {
          call_count: calls.length,
          input_tokens: inputTokens,
          output_tokens: outputTokens,
          average_latency_ms: average,
          degraded_count: calls.filter((call) => call.status !== "succeeded").length,
          recent_calls: calls.slice().reverse(),
        },
        configuration: "当前为单文件预览：数据来自内置假后端，不写入任何文件。",
      });
    }
    if (path === "/diagnostics/probe" && method === "POST") {
      return jsonResponse({ ok: true, latency_ms: 640, schema_valid: null, error: null });
    }
    if (path === "/backup" && method === "POST") {
      const archive = new Blob(["单文件预览：这里不会真的生成备份。"], { type: "application/zip" });
      return new Response(archive, {
        status: 200,
        headers: { "Content-Disposition": 'attachment; filename="suwan-preview.zip"' },
      });
    }

    return fail(404, "NOT_FOUND", `预览模式未实现 ${method} ${path}`);
  };

  console.info("[单文件预览] 已启用内置假后端，数据仅存在于当前页面内存中。");
})();