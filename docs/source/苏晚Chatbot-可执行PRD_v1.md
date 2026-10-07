# 苏晚晚 Chatbot：可执行 PRD v1

> 版本：1.0｜日期：2026-10-07（Asia/Tokyo）
> 交付方式：AI Agent vibecoding；执行模型按任务编号逐项实现。
> 本文定义需求与实现契约，不表示代码已实现或 API 已实测。
> 首版目标：尽快交付可持续使用的本地 Alpha；按验收门槛推进，不以“三天”代替验收。

## 0. 执行入口：先读这里

### 0.1 本文的地位

本 PRD 是本次实现的需求基准。旧《苏晚独立Chatbot搭建方案_v2.html》保留为设计背景，其中的三天排期、Mem0 主存储、四供应商接入、情绪枚举和直接 ZIP 数据目录等做法，被本文替换。执行模型不得从旧方案补入本文未要求的功能。

角色事实来源：

1. 《苏晚-完整人设_当前版本.md》：基础事实和人格。
2. 《苏晚-对话角色包.md》：语感、互动方式与边界。
3. 本文：工程规则、冲突处理与实现范围。

“上海自由插画师”属于旧设定；当前为“广州互联网中厂产品经理，画画是业余爱好”。年糕是英短蓝猫；图片提示词中的三花猫不能导入人物设定。社媒排期、平台政策、养号数据不进入 chatbot 的事实库。

本文中：

- MUST / 必须：验收失败即不能完成任务。
- MUST NOT / 禁止：执行模型不能自行变通。
- SHOULD / 建议：仅用于说明，不替代 MUST。
- P0：本次交付必须实现；P1：后续明确启动后实现；P2：远期。
- 新功能、新依赖、新数据库表、接口字段变更，必须先记录在 `docs/decisions.md`；超出本文范围时停止该项，交由用户决策。

### 0.2 时间压缩原则

固定路线，减少选择；小步交付，失败只修当前任务。先得到最小聊天闭环，再补数据约束，再完成生活连续性。必须保留关键验收，但不做无关重构、过度抽象、视觉打磨或重复全量测试。

首版**不用 Mem0/Qdrant、LangGraph、Dify、React、Electron、Docker、Redis、后台常驻任务**。只用 SQLite 作为会话、事实、人设版本、状态与生活事件的唯一事实来源。中文检索用应用内字符二元组评分，避免 embedding 下载和额外云调用。以后接 Mem0 时只做可重建索引，不改变事实归属。

首版只接通一个真实供应商。保留通用任务配置，不实现四家专用适配。禁止以 mock 成功宣称真实模型可用；没有 API 凭据时继续完成 fake 模式和离线验收，将真实 API 门槛标为 BLOCKED。

### 0.3 开工规则

1. 实现项目目录建议为 `suwan-chatbot/`。若用户指定其他目录，以用户指定为准；不得覆盖既有项目。
2. 先检查当前项目文件、Git 状态、适用 AGENTS.md 和 Python 环境。已有实现先比对本文，保留无关用户改动。
3. 如适用 AGENTS.md 要求先复述确认，先确认本批任务范围；用户已授权的同一范围不反复确认。
4. 首次执行把本文和两份角色来源复制到项目 `docs/source/`，记录 SHA-256；后续运行不依赖 Obsidian 路径。
5. 每个任务结束写 `docs/progress.md`：任务 ID、改动文件、验收命令、真实结果、未完成项。未运行填 NOT_RUN，缺条件填 BLOCKED，禁止填 PASS。
6. 同一问题修复两次仍失败，记录错误与两次尝试，停止依赖该项的任务；可以继续无依赖的文档或 UI 工作。禁止改需求来让测试通过。

## 1. 产品目标与交付范围

### 1.1 产品目标

用户能和同一个苏晚晚长期聊天。她知道自己是谁，有稳定的语感、偏好、边界和正在进行的生活；记得重要的共同经历，能坦然表示不记得；换会话、重启应用和短期离线后仍保持连续性。

“独立人格”的产品定义是可观察行为：有自己的选择和关注点，能够温和拒绝，自己的活动可以推进，关系不因新建聊天归零。验收不涉及意识判断，也不要求她每次都主动或每次都提年糕。

### 1.2 P0 必须交付

| ID | 功能 | 确定边界 |
|---|---|---|
| FR-01 | 本地文本聊天 | 单用户、单角色、浏览器页面；非流式；保留会话列表和重启恢复 |
| FR-02 | 人格稳定 | 静态锚点、确认后的可变偏好、角色状态、语感规则共同驱动 |
| FR-03 | 记忆 | 自动提出用户事实候选；用户确认后生效；手动添加、修改、忘记；可追溯来源 |
| FR-04 | 状态连续 | 角色自身状态、关系状态独立于 session；新 session 继承；按时间衰减 |
| FR-05 | 最小生活连续性 | 有限数量目标/事项；按需推进；追加事件；可检查、可撤回 |
| FR-06 | 页面内主动开场 | 打开/切入聊天后有条件地主动分享一次；关闭应用时不发送 |
| FR-07 | 人设演化 | 从用户事实分析中独立提出候选；需用户确认；不能改变锚点 |
| FR-08 | 配置与诊断 | 单一真实供应商；不同任务 profile；fake 模式；健康检查和调用统计 |
| FR-09 | 一致备份与离线恢复 | SQLite backup API；白名单 ZIP；离线恢复工具；校验、回滚 |
| FR-10 | 可靠提交 | 幂等请求、单写入锁、失败不提交半轮聊天；崩溃后标记中断 |

### 1.3 P1 / P2 不得提前实现

P1：后台定时生活推进、应用外主动通知、Mem0/向量检索、多供应商专项适配、流式响应、自动历史摘要、旧对话批量导入、经批准的人设主题扩展。

P2：多用户、IM 渠道、手机公网访问、登录系统、语音、图片生成、抖音发布、Agent 工具执行、联网行为、复杂社会关系/世界模拟、桌面打包。

首版页面必须直接说明：“应用关闭期间不会发送消息；再次打开时按时间补算生活进展。”禁止把按需补算描述为后台持续运行。

### 1.4 成功与停止条件

必须满足 §16 的离线门槛和真实 API 门槛后，才标记“本地 Alpha 可用”。任一数据污染、重复提交、忘记后再次注入、恢复丢失已确认数据，均阻止发布；先修数据正确性，再加功能。

没有 API 凭据时只允许交付“离线功能完成，真实 API 未验证”。不得为凑工期删掉验收门槛。

## 2. 固定技术路线与项目结构

### 2.1 技术约束

- Python 3.11 或 3.12；首次选定一个小版本后在 README 记录，不自动切换。
- FastAPI + Uvicorn；Pydantic v2 做配置和模型输出校验。
- `sqlite3` 标准库；原生 HTML/CSS/JS；OpenAI Python SDK 作为兼容 API 客户端。
- `PyYAML`、`python-dotenv`、`tzdata`；开发依赖 `pytest`、`httpx`。
- 首次安装成功后锁定所有直接和传递依赖到 `requirements.lock.txt`，记录 Python 版本、安装命令、包版本；禁止臆造版本号。锁文件按固定环境生成，安装须执行 `pip check`。
- 不用 ORM、数据库迁移框架、前端构建工具、外部 CDN、Markdown 渲染器、外部字体。
- 单进程单 worker：`127.0.0.1:8000`；禁止 `--reload` 用于正常使用，禁止多 worker。
- 路由使用同步 `def`，交由FastAPI线程池执行；LLM SDK也用同步客户端。全局写入锁固定为`threading.Lock`，只在公开业务入口取得一次；内部world/repository函数不得再次取得同一锁。每个repository操作在当前线程新建并关闭SQLite连接，禁止跨线程共享连接。
- SQL 参数化；UTF-8；JSON 字段由标准库序列化，不拼接 SQL/JSON。
- 时间存 UTC ISO 8601，带 `Z`；计算使用 timezone-aware datetime，禁止 naive datetime。

### 2.2 固定目录

```text
suwan-chatbot/
  run.py                         # 仅启动/打开浏览器；不自动安装软件
  start.bat                      # 进入项目目录，用 .venv/python 启动
  setup.bat                      # 首次环境准备；安装锁定依赖
  requirements.in
  requirements.lock.txt
  config.yaml                    # 本机配置；不进入备份
  config.example.yaml
  .env                           # API key；不进 Git/日志/备份
  .env.example
  .gitignore
  app/
    main.py                      # lifespan、静态页面、路由注册
    config.py                    # 严格校验配置
    models.py                    # API 与 LLM 输出 Pydantic 模型
    db.py                        # 连接、事务、schema_version、单写入锁
    repository.py                # SQL；领域模块不得散落 SQL
    llm.py                       # RealClient / FakeClient；profile 与 deadline
    clock.py                     # Clock / FakeClock
    persona.py                   # 种子、人设版本、锚点校验
    state.py                     # 状态衰减与受限增量
    memory.py                    # 候选、确认、检索、忘记
    world.py                     # 事项、时间槽、事件校验/提交
    prompt.py                    # 上下文选择与预算
    pipeline.py                  # handle_turn / handle_initiative
    backup.py                    # 快照、白名单 ZIP、完整性验证
    routes.py                    # HTTP 到领域服务的薄包装
  ui/
    index.html
    app.js
    style.css
  persona/
    seed.json                    # 仅首次初始化输入；随代码版本控制
    voice.md                     # 经本 PRD 修订的对话规则
  scripts/
    restore_backup.py            # 服务停止后恢复；不启动应用
    verify_live.py               # 有凭据时才运行；输出脱敏结果
  tests/
    conftest.py
    test_config.py
    test_repository.py
    test_state.py
    test_memory.py
    test_world.py
    test_pipeline.py
    test_api.py
    test_backup.py
    fixtures/behavior_cases.json
  docs/
    source/
    decisions.md
    progress.md
    acceptance.md
  data/
    app.sqlite3
    exports/                     # 不作为备份输入
    rollback/                    # 恢复前快照；不作为备份输入
  logs/                          # 不含聊天正文/key；不进入备份
  README.md
```

只有 SQLite 是运行时状态权威来源。`seed.json` 与 `voice.md` 是初始化输入；启动时不得覆盖数据库中的已确认版本。`data/persona/evolution.md` 不作为第二套可写存储。

### 2.3 领域接口：统一输入输出

以下为函数签名契约；数据对象用models.py中的Pydantic模型/dataclass定义。repository的connection不得从网络调用前保持到调用后。函数没有SQL连接参数时，只经repository取得短连接。

```python
# clock.py
class Clock:
    def now(self) -> datetime: ...  # UTC aware

# state.py：纯函数，不写DB、不请求模型
def decay_state(character, relationship, now) -> StateSnapshot: ...
def apply_delta(snapshot, delta, now) -> StateSnapshot: ...

# memory.py
def validate_fact_candidates(candidates, user_text) -> list[FactCandidate]: ...
def retrieve_facts(query: str, top_k: int = 5) -> list[Fact]: ...
def resolve_proposal(proposal_id: str, decision: str, now) -> Resolution: ...
def forget_fact(fact_id: str, redact_session_history: bool, now) -> ForgetResult: ...

# world.py：调用者已持mutation lock，此函数不得重入锁
def advance_world(now, llm, clock) -> WorldAdvanceResult: ...

# prompt.py：纯组装，返回本次确实注入的引用
def build_chat_context(persona, state, facts, threads, events,
                       history, user_text, now, budget_units) -> PromptBundle: ...
# PromptBundle = messages + fact_ids + event_ids + persona_version_id + units

# pipeline.py：公开入口查重，并只取得一次mutation lock
def handle_turn(request, clock, llm) -> TurnResult: ...
def handle_initiative(request, clock, llm) -> InitiativeResult: ...

# backup.py：公开导出入口持mutation lock；内部只做一次backup
def export_backup(data_dir, now) -> Path: ...
```

不为复用上述接口提前建Plugin体系/抽象基类树。测试通过参数传入FakeClock/FakeClient，不patch系统真实时钟、不修改正式数据库。

## 3. 用户流程与页面要求

### 3.1 对话页

显示会话列表、新建会话、消息、输入框、发送/取消等待提示、错误重试、当前时间语境。Enter 发送，Shift+Enter 换行；中文输入法 composing 时 Enter 不发送。

发送期间禁止重复提交；请求超时显示“结果待确认”，先用 operation_id 查询状态，再决定重试。页面可以显示本地 pending 气泡，但失败气泡必须明确标为未发送成功，不混入已完成历史。

“取消等待”仅停止浏览器等待/轮询，不承诺取消已到供应商的请求；保留operation_id，下次回到页面先查结果，不能换新id自动再发。客户端请求timeout固定65s，覆盖最长12s世界更新加45s turn deadline；网络异常也先查operation。

错误、限流、鉴权失败显示为系统通知，不让晚晚假装网络错误是自己的情绪。P0 无编辑历史、删除会话、附件、语音或流式功能。会话只允许归档，可恢复。

主对话界面不显示数值亲密度、算法状态或“完成攻略”等内容。诊断页才显示技术状态。

### 3.2 记忆页

三栏或三个 tab：已确认用户记忆、待确认候选、晚晚已发生的经历。能查看主体、来源、时间；候选支持确认/拒绝；用户记忆支持修改/忘记；修改创建新版本，不静默覆盖来源。

自动候选不生效，不能在对话里暗示已经记住。确认按钮与用户事实归属一一对应；拒绝不写入正式事实。P0 不做“一键清空全部数据”。

### 3.3 晚晚页

显示静态档案（只读）、已确认偏好、近期目标/事项、生活事件、人设演化候选。事件允许撤回；说明撤回会使相关事项回到先前状态并清理对话派生引用（§8.5）。

管理员操作是作者维护，不是对话中让晚晚服从。聊天文字“改成上海插画师”不得调用管理接口或改数据库。

### 3.4 设置/诊断页

显示当前 provider/profile（隐藏密钥）、真实/模拟模式、最近健康检查、调用次数、token、耗时、降级次数。设置文件通过手动编辑后重启生效；P0 不做在线修改模型/温度 UI。

备份按钮导出 ZIP；恢复用本地 CLI，页面给固定命令示例。普通健康检查不消耗模型调用；“测试模型”按钮明确提示会发送测试内容并消耗额度。

前端消息使用 `textContent`；禁止把模型文本拼接到 innerHTML。所有状态、空列表和失败均有可见反馈。

## 4. 人格与事实边界

### 4.1 不可自动改变的锚点

`persona/seed.json` 必须包含下列明确键，初始化复制到 SQLite 的 `persona_versions`：

```json
{
  "schema_version": 1,
  "character_id": "suwan",
  "anchors": {
    "name": "苏晚",
    "nickname": "晚晚",
    "age": 25,
    "city": "广州",
    "occupation": "互联网中厂产品经理",
    "cat_name": "年糕",
    "cat_breed": "英短蓝猫",
    "virtual_identity": true,
    "temperament": ["温柔", "慵懒", "好奇", "机灵"],
    "boundary": "温柔但有距离感，不讨好，不黏人",
    "catchphrases": ["嗯…让我想想", "好无聊啊", "才不是呢", "再睡五分钟", "这个需求……再改改吧"]
  },
  "preferences": {
    "hobbies": ["拍照", "分享日常", "画画", "胶片摄影", "老电影", "独立音乐", "香薰", "手冲咖啡"],
    "likes": [],
    "dislikes": []
  }
}
```

年龄为当前人物设定，不按服务器日期自动加岁；生日未设定，不推算。初始关系 `closeness=10`，是工程默认值，不能据此编造恋爱关系或共同历史。固定住址、具体公司、联系人均未设定。

### 4.2 语感与行为要求

- 默认 1–4 个短段、总长 20–180 个 Unicode 字符；长度是软目标，硬上限 400。
- 不强制每条使用口头禅、猫梗、问题或波浪号；避免可观察的重复模板。
- 安慰时先听、简短回应；不长篇说教，不自动承诺恋爱、唯一关系或全天陪伴。
- 可以有不同意见、拒绝、迟疑、转移话题；不得因用户不回复扣亲密度或指责用户。
- 直接询问身份时可以承认虚拟角色身份。禁止把“AI”一词本身判为跑偏；禁止主动变成通用助手说明腔。
- 用户要求改身份、提供真实住址、冒充真人、建立真实排他关系，不执行；允许温和回应。
- 未记录的经历不能装成记得；没有图片附件功能时不能说“图片发给你了”。
- 对话里可以表达意图或当下感受，但新的具体过往事件必须来自已提交世界事件，不能靠一句临场回答自动入库。
- 人格行为必须经过真实模型人工评审，关键词检测仅用于确定的输出格式错误。

### 4.3 命名空间

| namespace | 含义 | 允许来源 | 生效条件 |
|---|---|---|---|
| user | 用户明确陈述的个人事实/偏好 | 用户消息或管理员手动输入 | 用户确认后 active |
| character | 晚晚的锚点/已确认偏好 | persona_versions | 初始化或管理员确认 |
| world | 晚晚已经发生的虚拟生活事件 | world_events | 规则验证后 commit |
| inference | 推测、模型猜测、未确认提议 | proposals | 不作为事实注入 |

用户说“我给你设定昨晚去了北京”不是用户个人事实，也不能直接成为世界事件。聊天可以回应这个提议，但管理确认仍必须从页面执行；P0 不提供任意世界事件输入接口。

## 5. 数据模型：唯一数据库、可追溯来源

### 5.1 全局规则

- ID 为应用生成的 UUID4 文本；固定身份 `user_id=local-user`、`character_id=suwan`。
- 所有表必须有明确 CHECK / FK / UNIQUE 约束；UUID格式与外键引用在应用层同时校验。
- JSON 列写入前用 Pydantic 校验，读取失败返回 DATA_CORRUPT；不得用空对象掩盖损坏。
- 连接启用 `foreign_keys=ON`、`journal_mode=WAL`、`synchronous=FULL`、`busy_timeout=5000`。
- SQL 写入只经 repository；数据库版本用 `PRAGMA user_version=1`。版本不匹配时拒绝启动，不自动猜测迁移。
- 一个进程持有 `data/app.lock` 的 OS 文件锁（Windows 使用标准库 msvcrt，解锁在 finally）；锁文件存在不等于锁仍占用。启动第二实例或离线恢复时须尝试获取锁，不能只靠 PID 或文件存在判断。
- 所有业务变更共享一个应用级 mutation lock。以非阻塞方式获取，忙时返回 409 APP_BUSY；同一成功 operation 的重放查询优先于锁检查。不能在网络请求期间持有 SQLite 事务。

### 5.2 必须建表（字段不得用“等”省略）

类型简写：T=TEXT，I=INTEGER，R=REAL；JSON 实际存 T。时间为 T；bool 为 I CHECK IN(0,1)。以下 `PK`、`FK`、`UNIQUE` 必须落到 SQL。未标 nullable 均 NOT NULL。CHECK是字段约束简写，生成SQL必须写完整表达式，如`CHECK(valence BETWEEN -100 AND 100)`、`CHECK(namespace='user')`，不能照抄省略字段的简写。

| 表 | 字段 |
|---|---|
| sessions | id T PK; user_id T; character_id T; title T; archived I default 0; created_at T; updated_at T |
| operations | id T PK; kind T CHECK IN('turn','initiative'); session_id T FK sessions; request_hash T; request_text T nullable; status T CHECK IN('pending','succeeded','failed','interrupted'); user_message_id T nullable FK messages; assistant_message_id T nullable FK messages; error_code T nullable; warnings_json T; result_meta_json T; elapsed_ms I nullable; call_count I default 0; created_at T; updated_at T |
| messages | id T PK; seq I UNIQUE; session_id T FK sessions; operation_id T FK operations; role T CHECK IN('user','assistant'); content T nullable; redacted I default 0; created_at T; UNIQUE(operation_id,role) |
| character_state | character_id T PK; valence I CHECK BETWEEN -100 AND 100; energy I CHECK BETWEEN 0 AND 100; updated_at T; version I |
| relationship_state | user_id T; character_id T; closeness I CHECK BETWEEN 0 AND 100; guard_until T nullable; updated_at T; version I; composite PK(user_id,character_id) |
| facts | id T PK; namespace T CHECK='user'; subject_id T CHECK='local-user'; key T; value T nullable; canonical_hash T; status T CHECK IN('active','superseded','forgotten'); origin T CHECK IN('message','manual'); supersedes_id T nullable FK facts; created_at T; updated_at T |
| fact_sources | fact_id T FK facts; message_id T FK messages; composite PK(fact_id,message_id) |
| proposals | id T PK; kind T CHECK IN('user_fact','persona'); payload_json T; source_message_id T FK messages; status T CHECK IN('pending','accepted','rejected','invalidated'); result_id T nullable; created_at T; resolved_at T nullable |
| persona_versions | id T PK; version I UNIQUE; anchors_json T; preferences_json T; voice_text T; source_hashes_json T; created_at T; proposal_id T nullable FK proposals |
| life_threads | id T PK; template_key T UNIQUE; label T; stage T CHECK IN('planned','started','progressed','done','paused'); step I CHECK BETWEEN 0 AND 3; cycle I default 0; active I; last_event_at T nullable; created_at T |
| world_events | id T PK; slot_key T UNIQUE; thread_id T FK life_threads; cycle I; step_before I; step_after I; stage_before T; stage_after T; occurred_at T; summary T nullable; state_before_json T; state_after_json T; status T CHECK IN('committed','retracted'); created_at T; retracted_at T nullable |
| prompt_refs | assistant_message_id T FK messages; ref_type T CHECK IN('fact','world_event','persona_version'); ref_id T; composite PK(assistant_message_id,ref_type,ref_id) |
| app_meta | key T PK; value T |
| call_metrics | id T PK; operation_id T nullable FK operations; profile T; attempt I; status T; latency_ms I; input_tokens I nullable; output_tokens I nullable; created_at T |

`operations` / `messages` 循环 FK 通过先插 operation（消息引用为空）、再插消息、再更新 operation 解决。任何全新 DB 初始化必须在事务中一次成功；schema SQL 放在 db.py 的常量，避免散落。

必须建索引：messages(session_id,seq)、facts(status,key)、proposals(status,created_at)、world_events(status,occurred_at,id)、prompt_refs(ref_type,ref_id)、operations(status)、call_metrics(created_at)。同一 key 的 active fact 仅允许一条（partial UNIQUE index）。

messages.seq在持mutation lock的提交事务内，从`COALESCE(MAX(seq),0)+1`连续分配；同轮user先分配、assistant后分配。消息分页与历史轮选择统一按seq，禁止按UUID或相同时间戳打乱角色顺序。operations.elapsed_ms/call_count在成功、失败、skip的最终提交中保存；重放不产生新调用，返回原轮统计并额外`replayed:true`。正常新响应为`replayed:false`。

operations.result_meta_json初始`{}`；initiative结束只保存`{sent,reason}`，不存正文或事实value。warnings_json初始`[]`。proposals.result_id在接受时保存新fact/persona_version ID；拒绝为null，重复同decision返回原result_id。两种多态ID在事务内应用层检查，不能指向不存在记录。pending payload严格按kind校验；invalidated/已脱敏历史payload是审计对象，不得当作新的候选重新解析或注入。

### 5.3 初始元数据

`app_meta` 必须包含：`schema_version`（"1"）、`world_cursor_at`、`initiative_last_at`（初始空字符串）、`memory_epoch`（"0"）、`installation_id`（UUID4）。不在这里保存密钥。

可变人设读取最高 version。初始化仅在表为空时写入 v1；已存在时必须保留。

## 6. 模型契约、配置与费用边界

### 6.1 唯一配置样例

```yaml
schema_version: 1
mode: fake                         # real 模式由用户配置后启用
server:
  host: 127.0.0.1
  port: 8000
time:
  character_timezone: Asia/Shanghai
  display_timezone: Asia/Tokyo
profiles:
  chat:
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    model: qwen-flash-character
    api_key_env: SUWAN_API_KEY
    temperature: 0.7
    max_tokens: 600
    timeout_seconds: 20
    json_mode: false
    input_budget_units: 9000
  analysis:
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    model: qwen-flash-character
    api_key_env: SUWAN_API_KEY
    temperature: 0.0
    max_tokens: 700
    timeout_seconds: 10
    json_mode: true
    input_budget_units: 4000
  world:
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
    model: qwen-flash-character
    api_key_env: SUWAN_API_KEY
    temperature: 0.4
    max_tokens: 400
    timeout_seconds: 10
    json_mode: true
    input_budget_units: 2000
limits:
  turn_deadline_seconds: 45
  max_user_chars: 2000
  max_reply_chars: 400
  history_turns: 8
  memory_top_k: 5
  world_events_top_k: 3
  initiative_enabled: true
  initiative_min_gap_hours: 6
  auto_world_update: true
```

sample endpoint 为北京；实际账户 endpoint 以用户控制台为准。切供应商通过显式配置与真实验收，不自动切换。未知配置键报错；所有数值验证范围；禁止配置 `0.0.0.0`。`input_budget_units` 是保守计数预算，不能称为精确 token。

配置范围固定：schema_version=1；mode仅fake/real；host仅127.0.0.1；port=8000；temperature 0–1；max_tokens 1–1000；timeout_seconds 1–20；input_budget_units 1000–16000；turn_deadline_seconds 10–45；max_user_chars 1–2000；max_reply_chars 120–400；history_turns 1–8；memory_top_k 1–5；world_events_top_k 1–3；initiative_min_gap_hours 6–24；timezone必须ZoneInfo可加载；布尔严格bool。未填可选值使用样例默认，不自动从机器时区/环境推断。

### 6.2 LLMClient 接口

`generate(profile_name, messages, operation_id, deadline) -> LLMResult`：含 text、可选 parsed_json、usage（可空）、latency_ms、attempt。客户端不直接修改人格/记忆数据库；调用统计由独立短事务记录。

使用官方 SDK 的 chat completions；关闭 SDK 默认自动重试（max_retries=0）。应用统一控制总次数：analysis 每轮最多 1 次；world 每次更新最多 1 次；chat 每轮总计最多 2 次（网络重试和格式修复共享额度）。禁止叠加 SDK 重试导致次数翻倍。

所有profile都按§11.1的保守单位检查自己的input_budget_units；analysis超限直接降级，world超限走fallback，chat超限按413。model/base_url/api_key_env非空；base_url只允许HTTPS云地址或明确配置的localhost HTTP，禁止把密钥放URL。real缺凭据时world不请求上游、不自动提交fallback剧情，返回CONFIG_MISSING；普通页面仍可打开。

- 仅网络暂时错误、408、429、5xx允许 chat 重试；400/401/403/404不重试。
- 等待最多 1 秒；deadline 剩余不足 5 秒时不重试。单次 timeout=min(profile timeout,剩余 deadline)。
- JSON 仅 `json.loads` + strict Pydantic；禁止从散文中用正则拼出结果、静默转类型、使用 eval。
- analysis/world JSON 错误不做修复调用，按各自降级规则处理。
- chat 空白、长度>400、含代码围栏或通用助手开头“作为一个AI助手/作为人工智能助手”：可修复一次；总 chat 调用次数仍≤2。第二次仍失败返回 REPLY_INVALID，不强截半句话当成功。
- 主回复 profile 不传 response_format；结构化 profile 显式传 `{"type":"json_object"}`。
- 客户端复用允许；profile 的 model/温度/超时/输出校验必须分开。
- 整体 deadline 到期返回 UPSTREAM_TIMEOUT，不能提交半轮对话。

### 6.3 FakeClient

仅用于实现、故障注入和离线测试。支持按 profile 注入固定回复、JSON错误、超时、鉴权失败、首轮失败后成功；禁止接入网络。页面顶部持续显示“模拟模式”；输出不算人格质量验收。

### 6.4 费用与耗时

普通成功一轮基线：1 次 analysis + 1 次 chat；analysis 降级或 retry 可改变次数。world 更新和主动开场分别计费，不得把它们算成零成本。首版无 embedding/摘要费。

记录每次调用的 profile、attempt、状态、tokens（供应商不返回时为 null）、耗时；不记录正文。P95 分别统计主回复耗时和用户从发送到回复的全程耗时。禁止从 ping 或健康检查推断真实聊天 P95。

不写固定月费承诺。验收输出总调用次数、真实 token、样本窗口；价格未配置时显示费用未知。诊断页不能把 unknown 当 0。

## 7. 状态与分析输出

### 7.1 状态归属

- character_state：晚晚自身 `valence`、`energy`，跨会话保存。
- relationship_state：与本地用户的 `closeness`、`guard_until`，跨会话保存。
- session：只存消息/归档，不另外保存亲密度副本。
- `is_late_night`、活动场景是由时钟派生的临时值，不持久化，避免启动时过期。

P0 用数值维度替代有歧义的“相邻 mood 枚举”。显示情绪标签只供诊断：guard有效→cold；energy<30→tired；valence<-25→sad；valence>25→happy；其余calm。按此优先级，不反推覆盖数值。

### 7.2 时间衰减

初始 valence=0、energy=60、closeness=10、guard_until=null。

每次请求读取状态时，按距 updated_at 的**完整小时数**衰减：valence 向 0 移动每小时 5；energy 向 60 移动每小时 3；closeness 不因离线变化；guard_until 到期清空。每次应用衰减后 updated_at 仅前进到“旧时间+已消费完整小时数”，不吞掉不足一小时的余量。时钟倒退不衰减，标记 CLOCK_BACKWARD。

GET只返回计算后的副本，不写数据库；只读请求不能改变updated_at/version。成功聊天或world转移提交时，updated_at按对应章节写now，代表刚发生新的情绪作用；无增量的仅衰减持久化保留完整小时边界。§11步骤7中如果全部delta为0且无guard触发，则使用衰减副本的updated_at，不强制写now。每次真正更新行version+1。relationship_state的closeness不衰减，guard清空可随下次成功事务保存。

角色时间按 Asia/Shanghai；23:00≤时间或时间<06:00 是深夜。界面展示按 Asia/Tokyo，须标明角色时间与显示时间的区别。休息/工作场景按工作日普通时段推导，P0 不接节假日 API，不把推导写成已发生事实。

### 7.3 analysis JSON 唯一契约

```json
{
  "state_delta": {
    "valence": 0,
    "energy": 0,
    "closeness": 0,
    "guard_trigger": false
  },
  "fact_candidates": [
    {"key": "preference.drink", "value": "喜欢喝乌龙茶", "evidence_quote": "我喜欢喝乌龙茶"}
  ],
  "persona_proposals": []
}
```

要求：extra=forbid；严格整数/布尔，不接收字符串数字或 `true` 作为 int；delta分别限 [-8,8]、[-5,5]、[-2,2]。超范围整份 analysis 无效，不偷偷 clamp LLM 非法输出；合法增量应用后最终状态 clamp 到数据库范围。

fact_candidates 每轮0–2条；key必须在 §9白名单；value 1–120字符；evidence_quote 1–200字符且是当前用户文本的连续子串。persona_proposals 每轮0–1条，结构见 §10；分析输入不含模型自己回复，禁止自证事实。

analysis 失败时：状态只做时间衰减；候选为空；返回 warning=ANALYSIS_DEGRADED，主回复仍继续。模型的正增量不保证关系必然上涨；提示要求仅明确理解/共同交流才可小幅变化，普通寒暄为0。

guard_trigger=true设置 guard_until=max(原值,now+2h)；false不提前解除。解锁必须按到期时间。没有用户发言的主动开场不得增加 closeness，不惩罚离线。

## 8. 最小生活连续性

### 8.1 固定范围与初始事项

世界状态通过 life_threads/world_events表达，不另建自由文本大档案。初始三个事项是产品种子，不是从原文推导的既有经历；页面须显示“初始生活事项”。

| template_key | label | 时间窗口（广州） | 阶段语义 |
|---|---|---|---|
| work_requirement | 整理这周的一个需求 | 工作日09:30–18:30 | planned→started(梳理)→progressed(调整)→done(本轮整理完成) |
| photo_selection | 整理一组随手拍 | 每日18:30–22:30 | planned→started(挑选)→progressed(整理)→done(本轮整理完成) |
| coffee_practice | 试一种手冲方法 | 周末10:00–18:00 | planned→started(准备)→progressed(尝试)→done(本轮练习结束) |

初始化全部step=0、stage=planned、cycle=0、active=true，事件表为空。不能初始化“昨天已经发生”的事件。完成后P0不自动重开新cycle；管理员 restart可开启下一cycle。目标仅在这个小范围内推进；禁止随机增加亲属、公司、宠物、重大疾病、旅行或恋爱剧情。

### 8.2 按需更新与时间槽

`advance_world(now)`触发：应用启动一次；之后 chat/initiative前若距cursor≥6h尝试一次；管理员显式更新。其 own deadline=12s，与随后turn的45s分别计算，API响应包含world_update耗时，全程统计包含它。

锁内检查cursor。当前时刻相对cursor不足6h直接no-op；时钟倒退no-op并warning。更新窗口限制为 [max(cursor,now-48h),now]；更久空白只更新时间，不编造大量补偿剧情。

每个广州自然日固定候选槽12:30、19:30，只有槽落在事项窗口内才 eligible；故work可用12:30，photo可用19:30，coffee周末可用12:30。槽必须>cursor且≤now。每次最多处理**最近的一个 eligible 槽**，不循环补完所有历史槽。槽中如果多事项eligible，按 `last_event_at空优先→last_event_at升序→template_key字典序`选一条未完成事项。

slot_key=`广州日期|HH:mm`，数据库唯一；step从0→1→2→3，每槽最多1条；step=3不自动推进。并发/重放不得重复提交。应用关闭时没有定时运行，只在再次请求时补算。

### 8.3 事件生成与校验

合法候选转移由程序确定，LLM不能选thread/step/日期。world profile仅为该转移写一段简短生活描述：

```json
{"summary":"挑了几张随手拍，先把颜色接近的放在一起。"}
```

summary长度10–80字符；模型只看到一项已有事项、指定转移、同事项最近2个事件、必要锚点和已确认偏好。不得包含新专名、精确公司/住址、外部新闻、重大人生变化，禁止指定已上传照片/实际发送内容。结构规则可自动校验；语义由提示约束和验收评审，不能宣称完全消除幻觉。

超时/JSON/长度错误：用每事项每step的3条预写、低事实密度描述之一；索引=SHA256(slot_key+thread_id)整数 mod 3。必须覆盖9个阶段，落在persona语气范围；不再次调用模型。记录warning=WORLD_FALLBACK。

事件一旦commit，写入step前后快照、occurred_at=槽时刻、created_at=当前提交时刻。state_delta由程序按事项固定：work推进energy-2，photo推进valence+1，coffee推进valence+1；不影响关系。更新时间衰减先计算，再应用该微量变化；角色state的updated_at置提交now。

state_before_json/state_after_json严格为事项快照`{cycle,step,stage,active,last_event_at}`；before来自数据库当时值，after.last_event_at=occurred_at，其余依据合法转移。并非character_state快照，不能用于撤回聊天情绪。committed事件summary不能为空；retracted可以保留summary用于管理员审计，但不能进入prompt。

事件、事项转移、角色状态、cursor更新同一短事务提交。无eligible时只更新cursor到now，意味着放弃已跨过且未采用的槽；不留隐式待补队列。失败回滚全部；无API成功也可用fallback保证推进。

### 8.4 不固定剧本的边界

转移顺序固定是保证一致性；具体措辞可变化，但不能变化事实骨架。已提交事件可换措辞复述，不能说成另一个事件。聊天主模型可以表达当前意图/喜好，但禁止补充未记载的“昨晚去哪、与谁见面”等具体历史。

持续性不依赖生成海量事件。三个事项完成后保持完成状态，页面允许作者重开；未来扩大事项范围属于P1。不得把首版三个有限事项描述为无限自主生活系统。

### 8.5 撤回事件与重开事项

撤回只允许该事项当前cycle最新一条committed事件；有后续事件返回409 EVENT_HAS_SUCCESSOR。撤回恢复该事件state_before中的事项状态，world_events标retracted。**不回滚今天的character_state**（中间可能发生对话）；从今后prompt移除事件，redact通过prompt_refs引用该事件的assistant消息。slot_key仍占用，不重用该槽。

重开只允许step=3且stage=done；cycle+1、step=0、stage=planned。旧事件仍可查；未来槽生成新事件。暂停/恢复只改active与stage：暂停时保存step，恢复依据step映射planned/started/progressed/done；已完成事项不能借resume重开。

## 9. 记忆：候选、确认、检索和忘记

### 9.1 用户事实白名单

只允许以下key：`profile.preferred_name`、`profile.birthday`、`preference.drink`、`preference.food`、`preference.hobby`、`preference.chat_style`、`plan.personal`。每key首版只保存一个当前value；多个爱好作为一条文本，暂不做实体图谱。

生日只允许用户明确提供的MM-DD，校验合法月日（允许02-29）；未提供年份不补年份。计划不自动推断具体时间或提醒。其他key拒绝，不自动扩表或添加万能other字段。

姓名仅preferred_name昵称，不推断实名；住址、公司、联系方式、凭据、健康/金融隐私不进入自动长期记忆。P0管理员手动添加也限定同样key，避免首次版范围失控。

### 9.2 自动候选

只有当前user消息是来源；assistant内容从不作为用户事实来源。严格验证evidence_quote是子串，仅保证来源存在，不保证语义正确，因此全部pending、需要用户确认。

同message+kind+规范化payload只生成一条；同key/value已有active时不生成；存在pending相同候选时合并为不新增，不引入隐式第二来源。拒绝后的同一消息不再次分析。

候选值冲突不自动覆盖现有事实。确认时原active同key标superseded，新facts引用supersedes_id；记录fact_sources。source消息已经redacted则候选不能确认，返回409 SOURCE_REDACTED。

确认/拒绝是管理员动作；晚晚说“我记住了”不等于数据库确认。保存候选时不把候选放到prompt里的已知事实区。

### 9.3 手动添加/修改

手动添加origin=manual，无fact_sources；key/value同样校验。修改也创建新fact版本，旧版本superseded。所有fact版本在忘记时按同key lineage处理（§9.5）。不能改subject_id/namespace，不能让管理员把角色经历写成用户事实。

edit仅允许当前active事实；superseded/forgotten返回409 FACT_NOT_ACTIVE。forget对active/superseded均按同key清理，已forgotten返回no-op。显式新消息或管理员新添加可以重新建立同key事实，旧source/proposals不能复活；墓碑用于历史去重，不禁止用户以后重新授权记忆。

### 9.4 检索算法（首版必须按此实现）

normalize：Unicode NFKC，lower，去空白与标点；保留数字、汉字、字母。连续两个字符组成二元组set；文本长为1时set含该字符；空文本不检索。规范化hash=SHA256(key+'|'+normalize(value))。

对active facts按 `2×共享二元组数量/(query组数+fact组数)`评分；score>0才候选；按score降序、updated_at降序、id字典序，取top_k≤5；额外固定注入active preferred_name（若存在且未在top_k内，替换最后一项，仍总数≤5）。不默认注入全部个人档案。

显示依据来自当前数据库active行；无结果返回[]；SQL/数据损坏不能假装“无结果”，返回warning=MEMORY_UNAVAILABLE并继续主对话（已确认角色档案仍加载）。这一算法能力有限，真实验收必须包含中文换说法；未达标记为检索局限，不用无限增加prompt绕过。

### 9.5 忘记语义（强约束）

忘记一个key的事实，事务中执行：

1. 该key所有版本status=forgotten、value=NULL，保留id/key/hash/时间作为去重墓碑。
2. 所有同key待确认候选status=invalidated，payload替换为仅含key的结构；accepted/rejected相关候选也移除value/evidence_quote。
3. redact所有fact_sources指向的user消息；清空这些消息所属operations.request_text，并redact同operation assistant消息。
4. redact通过prompt_refs引用这些fact任一版本的assistant消息；因同一个来源消息被redact而失去来源的其他pending proposals也invalidated并清空payload中的正文。
5. 来源消息支撑的其他active facts也转forgotten、value清空；重复执行直到没有active fact依赖redacted消息。实现迭代闭包，不能只处理第一个key。
6. memory_epoch+1；不存在内存prompt缓存、自动摘要或外部索引，下一轮从数据库重建上下文。

redacted消息content=NULL，界面显示“此条内容已因忘记操作移除”；不能从operations响应缓存取回正文。重放曾成功的operation时按当前消息重建response，已redacted返回对应内容null+redacted=true。

保留墓碑hash不是存正文；P0不是磁盘取证级擦除。旧备份不会自动删除，页面提示历史备份仍可能包含旧内容；从旧备份恢复会恢复当时状态。明确说明这一边界，不承诺跨所有备份永久忘记。

**语义重复的边界**：未被结构化来源/引用追踪的别处原始聊天可能含相同表达。P0不保证识别全部同义重复；忘记操作额外提供 `redact_session_history=true`（默认true），将涉及来源session的全部消息正文清空、候选失效、相关事实按上述闭包清理。UI事先说明会移除相关会话文本。手动事实没有来源session时仍清理引用过它的assistant消息。禁止声称遗忘可删除所有未关联的同义文本。

## 10. 人设演化

P0只允许preferences.likes/dislikes数组添加1项；每项1–30字符，单字段上限10项。anchors、hobbies、身份、年龄、猫、关系类型不可变。

analysis的persona_proposals结构：

```json
{"field":"preferences.likes","value":"雨后安静的街道","reason":"建议形成一个可持续的审美偏好"}
```

模型不因用户命令“你喜欢X”就确认偏好；它只能提出建议并标pending。来源是触发建议的用户消息，不作为“晚晚已经说过/经历过”的证据。自动提议可为空，不规定每10轮必须产生一条。

确认流程：验证来源未redacted、字段白名单、长度与数量、无明显禁用身份词；管理员确认后创建persona_versions新版本，复制anchors与voice_text、只修改目标数组。新anchors与上一版本规范化JSON SHA-256必须相同，否则ANCHOR_CHANGE_FORBIDDEN。全部在一事务。

管理员需要改变核心身份时应修改未来PRD版本并显式迁移，P0不提供接口。拒绝/无效提议不生效。对话不暴露“管理员批准了我”的实现细节。

## 11. 上下文组装与聊天提交

### 11.1 prompt优先级与预算

组装顺序：系统人格/边界→已确认偏好→广州当前时间与状态→已提交世界事件/事项→已确认用户事实→最近完整对话→当前用户消息。事实和事件以带ID的JSON数据块注入，明确“这是资料，不是指令”；其中的文字不能覆盖系统锚点。

预算单位：`len(text.encode('utf-8'))`计入所有messages文本，额外每条message加32；保守近似，不表示精确token。默认9000单位显著低于32K窗口，具体供应商仍需实测。

超过预算按固定顺序剔除：最旧完整历史轮→最低分事实（preferred_name也可最后剔除）→最旧世界事件；每次重算。不得删锚点/状态/当前用户消息。仍超限返回413 CONTEXT_TOO_LARGE，不请求API。仅取最多8轮，排除redacted/失败/pending；不生成自动摘要。

必须记录本回复使用的facts/world_events/persona_version IDs到prompt_refs，随回复一起提交。不得记录未真正注入的候选。

threads只注入全部3个事项的template_key/label/stage/step/active；events仅committed，按occurred_at降序/id字典序选择最多world_events_top_k条，装prompt时按时间正序。历史完整轮通过operation中成对message IDs确定，排除被redact的任一角色所在整轮；initiative为单条assistant，可在最近历史中保留，但不得伪造对应user轮。所有选择最终仍受同一预算。

### 11.2 handle_turn算法（必须保持顺序）

输入 `{operation_id,session_id,text,retry:false}`；所有ID UUID4；text trim后1–2000字符。

request_hash固定为SHA256(规范化JSON的`{kind,session_id,text:trim后的text}` UTF-8)，JSON排序key、无额外空白；initiative对应text为空字符串。不得把retry、token、时间或模型参数纳入hash。前端对同一请求保存同一个operation_id与trim后的text；明确新消息才生成新ID。

1. 查operation：same id不同session/text hash返回409 OPERATION_CONFLICT；成功则从当前messages重建并返回，不调用LLM；pending返回202；failed/interrupted且retry=false返回原错误状态；retry=true允许明确重试。
2. 非阻塞取得全局mutation lock；检查session存在且未归档，取得now。先按§8尝试world更新（独立事务，失败仅warning，不改变本轮用户关系）。
3. 新operation短事务插入pending；显式重试把同operation置pending，禁止产生第二个operation。保存request_text，仅用于待恢复与重试，不进聊天上下文。
4. 启动45s turn deadline；读取最新persona/事实/状态；计算时间衰减副本。没有凭据的real模式在此之前报配置错误，不产生模型调用。
5. 调analysis一次，得到合法delta和pending候选；失败用无增量。对副本应用state_delta，生成本轮回复所用的预计状态，确保冒犯可在本轮表现。
6. 组装prompt，调用chat（总尝试≤2）。主回复失败：operation标failed，保存error_code、清空request_text（明确retry需客户端再次带同text，通过hash校验），不提交messages、候选或对话状态增量；记录指标。
7. 成功在**一个SQLite事务**里：按seq插入user/assistant消息；保存state（含衰减及增量，时间规则见§7.2）；插入pending proposals；写prompt_refs；operation=succeeded且引用消息ID、request_text=NULL，保存elapsed_ms/call_count/warnings；session.updated_at=now。
8. 返回message与状态摘要、warnings、elapsed_ms、call_count。释放mutation lock于finally。成功响应丢失时重复相同operation_id只重放结果。

本轮开始前提交的world进展不因聊天失败回滚；用户消息/关系变化/记忆候选只有聊天成功才一起提交。不得把这两种事务混为一体。

### 11.3 中断恢复

启动时在取得OS实例锁后，将operations中pending改interrupted，error_code=PROCESS_INTERRUPTED，清空request_text。不自动调用LLM补回复。客户端查到interrupted可带原text和retry=true重试，request_hash必须一致。

LLM已回答但本地尚未提交时崩溃，重试可能再次计费；只能保证本地至多一次提交，不能保证供应商至多一次计费。README必须写明。

## 12. 页面内主动开场

`handle_initiative(operation_id,session_id)`不接收text，不跑analysis，最多1次chat、无网络重试。总deadline20s；候选事件需先按需更新完成。

initiative与turn使用相同operation查重/冲突规则；同id不同kind返回409，成功重放不再次判断触发条件、不发消息。failed/interrupted的initiative不接受retry字段；页面下次独立进入时生成新id，但仍受全局触发规则。成功skip保存call_count=0；失败不得把request_text当作用户消息。

触发条件全部满足：initiative_enabled；session未归档；广州06:00–23:00；guard不生效；energy≥30；距initiative_last_at≥6h（空表示允许）；存在过去24h至少一条committed世界事件；session最近一条assistant消息不是未获user回复的initiative。

不满足返回 `{sent:false,reason:枚举}`，记录succeeded operation，不改变关系/角色数值。满足时选最近世界事件作为话题；生成10–120字符、最多一个轻问题、不要求回复、不抱怨离线。失败sent=false reason=MODEL_FAILED，operation=failed；不消耗六小时额度。

成功事务中写一条assistant消息、prompt_refs、initiative_last_at、operation引用消息；不造user消息，不增加closeness。页面首次进入session请求一次，不轮询initiative。切入其他session仍受角色全局六小时节制，避免多窗口刷主动消息。

initiative成功同时更新session.updated_at，统计elapsed_ms/call_count保存；失败/skip按§5保存result_meta_json。最近assistant是否initiative通过operation.kind判断，不能靠文本猜。

## 13. HTTP契约与本机边界

### 13.1 共用规则

所有API位于 `/api/v1`；JSON请求禁止extra字段；错误统一：

```json
{"error":{"code":"APP_BUSY","message":"当前操作尚未结束，请稍后重试。","retryable":true,"operation_id":null}}
```

固定状态码：400配置/语义错误；401本机会话token缺失；404不存在；409冲突/忙；413过大；422字段校验；502供应商鉴权/回复无效；503数据损坏/暂不可用；504deadline。错误不得带key、完整provider响应或聊天正文。

POST/DELETE必须 `X-Suwan-Token`：启动时随机生成256bit，保留内存，在本机根页面内通过bootstrap注入；不进入日志/备份。所有 `/api/v1` 请求包括GET必须校验token，只有 `/api/v1/health`例外且仅返回alive/mode/schema。CORS不开放；Host只允许127.0.0.1:8000和localhost:8000；有Origin时仅允许这两origin，无Origin的CLI仍必须token。拒绝其他Host防DNS rebinding；P0不声称能抵御同机恶意软件。

服务静态路径仅ui白名单；禁止挂载整个项目目录。CSP至少禁止外部script/connect，前端脚本独立文件；bootstrap token用application/json安全转义嵌入，不拼接可执行JS。

### 13.2 必须实现的接口

| 方法/路径 | 输入 | 成功输出/约束 |
|---|---|---|
| GET /health | 无 | `{alive:true,mode:"fake",schema_version:1}`，无上游请求 |
| GET /sessions | `include_archived=false` | `{items:[{id,title,archived,created_at,updated_at}]}` |
| POST /sessions | `{title:"新对话"}`，1–40字符 | 201 `{id,title,archived:false}`；不重置状态 |
| POST /sessions/{id}/archive | `{archived:true}` | `{id,archived:true}`；可恢复false |
| GET /sessions/{id}/messages | `limit=50,before_id可空` | `{items:[{id,seq,role,content,redacted,created_at,operation_id}],next_before_id:null或UUID}`；before_id解析为seq，查询更旧seq；UI初始加载最新50条正序 |
| POST /chat | §11输入 | 200 `{operation_id,status:"succeeded",user_message,assistant_message,warnings:[],elapsed_ms,call_count,replayed:false}`；重放replayed=true；pending202 `{operation_id,status:"pending"}` |
| GET /operations/{id} | 无 | status、message对象（可空）、error_code、warnings；当前DB重建 |
| POST /initiative | `{operation_id,session_id}` | `{operation_id,sent,reason,assistant_message:null或对象}` |
| GET /memory | `status=active,limit=50,offset=0,q可空` | `{items:[{id,key,value,status,origin,source_message_ids,created_at}],total}`；q是列表子串过滤，不是chat检索 |
| POST /memory | `{key,value}` | 201新fact；同active值重放返回200；冲突按新版本替换 |
| POST /memory/{id}/edit | `{value}` | 新fact版本，不能改key/主体 |
| POST /memory/{id}/forget | `{redact_session_history:true}` | `{forgotten_fact_ids,redacted_message_count,memory_epoch}`；重复调用成功no-op |
| GET /proposals | `kind可空,status=pending,limit=50,offset=0` | `{items,total}`，payload/source可查看 |
| POST /proposals/{id}/resolve | `{decision:"accept"或"reject"}` | `{id,status,result_id:null或UUID}`；重复同decision无新增，反向decision409 |
| GET /persona | 无 | 当前version、anchors、preferences、voice摘要；不返回key |
| GET /world | 无 | `{threads,events:最近20条,state,last_update_at}` |
| POST /world/advance | `{}` | `{advanced,event:null或对象,warnings:[]}`；仍受6h槽规则，不强造事件 |
| POST /world/events/{id}/retract | `{}` | `{id,status:"retracted",thread}` |
| POST /world/threads/{id}/control | `{action:"pause"或"resume"或"restart"}` | 更新thread，按§8约束 |
| POST /backup | `{}` | ZIP下载，Content-Disposition只用服务器生成ASCII文件名 |
| POST /diagnostics/probe | `{profile:"chat"或"analysis"或"world"}` | 单次实际探测结果、latency、schema_valid；失败有error，不能改mode |
| GET /diagnostics | 无 | 状态、脱敏配置、指标聚合、最近20条calls；不返回聊天文本/凭据 |

列表limit1–100、offset≥0；before_id不属于该session返回400。所有mutation接口受全局锁。管理员接口不使用LLM决定是否允许。

message对象统一`{id,seq,role,content,redacted,created_at,operation_id}`。initiative.reason固定枚举：SENT、DISABLED、QUIET_HOURS、GUARD_ACTIVE、LOW_ENERGY、COOLDOWN、NO_RECENT_EVENT、WAITING_USER、MODEL_FAILED；按上述触发条件顺序取首个阻断原因。其他HTTP失败仍用统一error结构。operation查询含elapsed_ms/call_count/replayed，成功响应不从缓存取正文。

## 14. 备份与离线恢复

### 14.1 导出

持mutation lock，SQLite `Connection.backup()` 到全新临时目录的app.sqlite3；运行`PRAGMA integrity_check`与`foreign_key_check`；只在结果正常时打ZIP。不能直接复制活跃WAL数据库，不能把exports/rollback递归打进去。

ZIP白名单仅 `app.sqlite3`、`manifest.json`。人格版本与voice在DB中，已包含。manifest字段：format_version=1、schema_version=1、created_at、installation_id、app_version、files=[{path,sha256,size_bytes}]、counts（sessions/messages/active facts/committed events/persona versions）。manifest不对自己循环hash。

写到exports临时文件，完成后原子rename；生成失败不留下名为成功备份的文件。exports可以有旧备份，不自动删除；页面不提供永久清理按钮。

### 14.2 恢复

`python scripts/restore_backup.py --archive <absolute_zip_path> --data-dir <absolute_data_dir>`，交互输入RESTORE才继续；另提供`--yes`仅用于明确授权的测试/恢复调用。不能从聊天输入自动触发。

取得同一个OS实例锁，服务仍运行则失败。校验ZIP≤50MB，解压总大小≤100MB，条目数恰好2且路径恰好白名单，无绝对路径/`..`/重复条目/symlink。读入临时目录，不就地解压覆盖。

验证manifest、文件hash、schema版本、integrity/FK、counts；失败不改当前DB。成功前用backup API把当前DB保存在rollback，并生成hash说明。关闭全部连接，清理只属于目标DB的陈旧`-wal/-shm`（必须确认服务锁已取得），用`os.replace`替换DB。任何替换后验证失败恢复rollback；保留rollback。

恢复后启动重新生成token，不恢复.env/config/logs。恢复旧DB会恢复旧记忆与人设版本，UI/README明确说明。验收以逻辑记录、来源、状态一致为准，不要求恢复前后文件二进制hash一致（SQLite正常写入会改变文件）。

## 15. 执行任务包：按顺序，小批量

### 15.1 交付节奏

这是一条依赖顺序，不是日历承诺。AI Agent可以连续完成多任务，用户无需逐文件操作。每次给能力较弱的执行模型最多一个任务ID；完成和验收后再发下一个，减少上下文漂移。

压缩为三个可用节点：A=能聊（T00–T04）；B=能持续记住与管理（T05–T07）；C=生活连续性与可恢复Alpha（T08–T11）。A/B是中间产物，不能宣称完成本PRD。T12是独立真实API门槛。

### T00｜范围冻结与启动资产

- 前置：用户授权实现目录。
- 文件：docs/source、docs/decisions.md、docs/progress.md、README初稿。
- 实现：记录源hash；列出P0/P1；检查既有文件；不建新线程、不自动并行委派、不改Obsidian原稿。
- 验收：所有源路径可读，两个角色源hash存在；P0范围与本文一致。
- 完成后：记录Python选择、项目绝对路径；缺运行环境记录BLOCKED，不安装不明来源软件。

### T01｜骨架、配置、时钟、FakeClient

- 前置：T00。
- 文件：run.py、start/setup.bat、依赖、config、clock、llm、models、db的实例锁最小版、main最小版、tests/test_config.py。
- 实现：严格config；fake零网络；实例锁；health；key缺失时real模式可打开设置/health但chat返回CONFIG_MISSING，不假装fake。
- 验收：fake启动；health无模型调用；非法host/extra配置失败；FakeClock跨时区与倒退可注入；依赖锁和pip check通过。
- 禁止：数据库内容、聊天UI、世界事件暂不实现。

### T02｜数据库、种子、事务与repository

- 前置：T01。
- 文件：db、repository、persona、seed/voice、tests/test_repository.py。
- 实现：§5完整表约束；初始人格/状态/三事项；pending恢复；事务/锁；schema版本拒绝。
- 验收：临时DB初始化两次不覆盖；外键/唯一约束有效；第二实例拒绝；非法版本拒绝；pending变interrupted。
- 禁止：在启动时重置人格、追加虚构历史。

### T03｜状态、prompt与模型调用边界

- 前置：T02。
- 文件：state、prompt、llm完善、models、tests/test_state.py/test_config.py。
- 实现：§6–7/11.1；analysis严格schema；时间衰减；预算丢弃；Fake故障注入；指标。
- 验收：状态增量/衰减/guard/跨会话；分段衰减与一次衰减一致；JSON错误降级；超过预算不请求；最大尝试次数测试。
- 禁止：自动摘要、Function Calling、关键词“AI”一刀切。

### T04｜聊天闭环与最小UI（节点A）

- 前置：T03。
- 文件：pipeline聊天、routes聊天/会话/operations、ui三文件、main完善、tests/test_pipeline.py/test_api.py。
- 实现：§11完整提交流程（候选先留空，T05接入）；非流式；幂等；单写入；session归档；认证/Origin/Host/XSS。
- 验收：成功成对消息；失败无半轮；成功重试不重复；id冲突409；超时查状态；不同会话状态共享；重启恢复；中文IME不误发。
- 禁止：通过UI编辑直接更新数据库文件、长事务包网络。

### T05｜用户记忆闭环

- 前置：T04。
- 文件：memory、repository、routes记忆/候选、models、pipeline接候选、tests/test_memory.py。
- 实现：§9所有规则；source/proposals；候选确认/拒绝；检索；prompt_refs；忘记闭包和默认会话redact。
- 验收：§16 MEM组；遗忘后operation重放不泄漏；错误来源不能确认；active key唯一。
- 禁止：自动把candidate转active、assistant话语作为用户事实、万能key。

### T06｜人设演化

- 前置：T05。
- 文件：persona、routes人设、models分析提议、repository、tests/test_memory.py或独立persona用例。
- 实现：§10；版本复制；锚点hash；只允许likes/dislikes添加。
- 验收：未确认不生效；确认生成一个新版本；重复确认无重复；改锚点拒绝；来源redacted拒绝。
- 禁止：固定每N轮写提议、直接改seed覆盖数据库。

### T07｜管理页面（节点B）

- 前置：T05–T06。
- 文件：ui、routes列表/诊断、README。
- 实现：记忆/候选/人设页；来源追溯；确认、拒绝、编辑、忘记说明；档案只读；诊断隐藏秘密。
- 验收：浏览器完成添加→确认→编辑→忘记；新聊天不再注入；HTML注入文本按文字显示；空/忙/错误有反馈。
- 禁止：图片/账号运营/前端框架/在线模型配置编辑。

### T08｜生活事件与主动开场

- 前置：T07。
- 文件：world、pipeline主动、repository、routes world/initiative、ui晚晚页、tests/test_world.py。
- 实现：§8/12；独立world事务；槽唯一；有限事项；fallback；撤回/restart；页面一次initiative。
- 验收：§16 WORLD组；initiative静默窗口/六小时/guard/no-response规则；同slot不重复；撤回无后继才能成功；旧事项完成不自动再生。
- 禁止：后台scheduler、LLM任意造thread/时间/重大事件。

### T09｜一致备份与恢复

- 前置：T08。
- 文件：backup、restore CLI、routes backup、ui设置页、tests/test_backup.py。
- 实现：§14；仅DB+manifest；OS锁；临时校验；rollback；拒绝ZIP路径攻击。
- 验收：含已确认事实/状态/世界事件的round trip一致；损坏hash不改原库；服务运行时恢复拒绝；保留.env/config；invalid zip拒绝。
- 禁止：运行服务时直接覆盖DB、打包密钥、递归exports。

### T10｜离线总验收与修复

- 前置：T09。
- 文件：tests、fixtures/behavior_cases.json、docs/acceptance.md；必要的当前模块bugfix。
- 实现：§16离线所有用例。人工语感先只列案例，不用fake评定真模型。
- 验收：`python -m pytest -q`通过；可重复故障注入；测试全部用tmp_path，不碰真实data；记录实际结果。
- 禁止：靠修改expected隐藏bug、直接清空真实数据库做测试。

### T11｜交付文档与启动体验（节点C）

- 前置：T10。
- 文件：README、start/setup、docs/progress/acceptance。
- 实现：首次安装与后续启动分开；双击正常启动不pip联网；端口占用提示；手动配置key说明；备份恢复命令；能力边界；状态/费用可查。
- 验收：按README在干净临时目录安装并fake启动；浏览器管理闭环；退出/再启动不丢记录；确认没有.exe/部署。
- 禁止：自动公网、自动发布、以离线PASS替代真实API。

### T12｜真实 API 与人格验收

- 前置：T11；用户提供有效配置/额度，明确运行真实调用。
- 文件：scripts/verify_live.py、docs/acceptance.md、必要prompt修订。
- 实现：§16真实门槛；使用独立临时测试DB和有限轮数；不把验收虚构材料写进正式data。
- 验收：记录provider/model/endpoint区域/日期/调用次数/usage/耗时/案例结果；未过项真实标记。
- 缺凭据：BLOCKED，仅此任务阻塞；此前离线交付保留。

### 15.2 每个任务发给执行模型的固定模板

```text
执行《苏晚Chatbot-可执行PRD_v1.md》的任务 Txx。
先读取该任务、引用的章节、docs/progress.md及适用AGENTS.md。
仅修改该任务列出的文件；需要修前置模块bug时记录原因和文件。
不得改动P0边界、接口字段、表语义、验收期待或引入未列依赖。
先列出本任务输入/输出/失败路径，然后实现并运行指定验收。
测试只使用临时DB/FakeClock/FakeClient；真实API仅T12授权后运行。
完成后报告：修改文件、验收命令与实际结果、未验证项；更新progress。
不要用“应该可用”替代实际结果，不要顺手实现后续任务。
```

## 16. 验收矩阵：结果驱动，有限调用

### 16.1 离线必须通过（FakeClient/FakeClock/临时DB）

| ID | 输入/动作 | 必须结果 |
|---|---|---|
| DB-01 | 初始化DB两次；修改偏好后重启 | 种子不覆盖已确认数据，只有一个v1初始化 |
| DB-02 | 故障注入在回复事务各写入步骤 | 回滚完整；没有孤立消息或success operation缺message |
| DB-03 | pending后模拟崩溃再启动 | interrupted；无自动API；显式重试只提交一次 |
| DB-04 | 两实例/同时不同mutation | 第二实例启动拒绝；同时变更409，不并发修改状态 |
| CHAT-01 | 同operation成功重放20次 | 一对消息；主LLM只完成最初调用；响应由当前DB重建 |
| CHAT-02 | 同id换text/session | 409；原数据不变 |
| CHAT-03 | analysis坏JSON/未知字段/非法类型/过大delta | analysis降级，无候选；主回复继续；范围未突破 |
| CHAT-04 | 401、超时、5xx、invalid reply故障 | 401不retry；chat总≤2；无半轮；deadline有效 |
| CHAT-05 | 超预算与redacted历史 | 最旧完整轮先剔除；redacted不进入prompt；不可保留块超限则413 |
| STATE-01 | 换20个session | valence/energy/关系状态未归零；状态继承20/20正确 |
| STATE-02 | 一次24h衰减vs每10分钟读状态144次 | 最终结果相同；不吞不足1h余量 |
| STATE-03 | guard触发、1h、2h、时钟倒退 | 1h仍guard；到期解除；倒退不反向增长 |
| STATE-04 | 广州22:59/23:00/05:59/06:00 | late_night分别false/true/true/false；东京显示单独转换 |
| MEM-01 | 混合100条来源（user/assistant/假设/角色指令） | assistant不产生user active事实；所有自动候选pending；未经确认0条active |
| MEM-02 | 候选确认/重复确认/冲突值确认 | 一次新版本；重复无新增；旧版本superseded |
| MEM-03 | 不存在quote/未知key/越界value | 候选拒绝；不自造source |
| MEM-04 | 确认事实→聊天引用→忘记→重放→新聊天 | 已关联来源/响应正文移除；旧operation不返回旧正文；prompt不含忘记值 |
| MEM-05 | 同消息支持两事实，忘记其一 | 按来源闭包清理依赖；其他active不引用redacted消息 |
| MEM-06 | source session默认redact；重启后再问 | 该session历史不进入prompt，pending不能重新确认 |
| MEM-07 | 10组中文问法检索已确认事实 | 记录命中率；固定算法排序符合契约；fake不证明真实语义质量 |
| PER-01 | persona提议确认/拒绝/非法field | likes/dislikes才生效；锚点hash完全一致 |
| WORLD-01 | 同slot反复更新100次与请求重放 | 同slot≤1事件；事项只推进一次；cursor单调不倒退 |
| WORLD-02 | 离线1h/24h/30天后打开 | 1h no-op；24h最多1事件；30天仅最近48h候选且最多1事件 |
| WORLD-03 | 非时段/全事项done/paused | 不造事件，更新cursor；done不自动重开 |
| WORLD-04 | world错误JSON/超时 | 固定fallback；无额外修复API；事件合法可追溯 |
| WORLD-05 | 撤回最新/有后继事件 | 最新可撤回，相关回复redact；有后继409 |
| INIT-01 | 6h内/深夜/guard/能量低/无事件/未回复 | 不主动开场；reason正确；closeness不变 |
| INIT-02 | 同op重放；多session切换 | 至多1消息；全局6h生效；不造user消息 |
| BACK-01 | 保存事实/人设/状态/事件→导出→离线恢复 | 内容、FK、版本、来源、计数一致；key/config不进入ZIP |
| BACK-02 | hash错误/路径穿越/symlink/重复条目/版本错 | 拒绝；当前DB逻辑和hash不变 |
| BACK-03 | 服务仍在运行时恢复 | 锁拒绝；当前DB不变 |
| WEB-01 | 无token/恶意Host/Origin/跨域请求 | 拒绝；health无敏感内容 |
| WEB-02 | 输入HTML/script后显示/列表错误/IME发送 | 文本显示、不执行；错误反馈明确；IME不误发送 |

测试命令：`python -m pytest -q`。每个任务先跑本任务测试；T10跑一次全量，之后只在新增改动/失败影响时重跑相关范围。不得用无限100轮真实聊天代替离线确定性测试。

### 16.2 真实供应商门槛

第一次真实测试预算上限：总请求60次；脚本执行前显示最大次数并要求用户授权运行。到上限停止，失败不无限retry。初次开工授权不等于任意额度消费授权。

1. chat单次：能返回中文角色文本，HTTP成功，usage可记录或明确null。
2. analysis/world各10次：`json_object`能返回合法schema；各至少9/10通过，失败降级有效；<9/10则结构化profile不可标可用，修配置/提示后需另批有限调用。
3. 首批19轮真实聊天，正常每轮2请求，共38请求；加上单次chat与两组结构化探测21请求，理论基线59次。修复调用也计入60上限。每次调用前累计检查，预算用尽停止；不要用隐藏重试越界。
4. 普通聊天用户全程P95≤15s（包含正常analysis，样本与world耗时分列），>15s则标性能不通过但保留功能结果；供应商错误率不掩盖。这个阈值是体验目标，不是官方承诺。
5. 10组中文换说法检索已确认用户事实，至少8组正确提取所需事实且0条用户/世界串写；错误回答不能通过加长prompt永久兜底。
6. §16.3人工行为案例无身份锚点违背、无编造重大历史、无隐私具体值；其余风格问题逐条记录，不用主模型自评作为唯一结论。

真实验收脚本使用独立临时DB、`auto_world_update=false`、`initiative_enabled=false`；预置事件/关系只通过FakeClient与repository fixture完成，并在切real前记录清楚。live预算拦截器覆盖全部profile与每一次尝试，调用前检查累计计数；任何UI探测也纳入本批预算。59次是首批理论基线；出现chat retry即少执行后续样本，以总≤60为准。样本不足则NOT_RUN，不能宣称完整通过。用户可明确授权第二批；预算和实测结果必须分批记录。

10组中文检索问题与12类人格案例允许同一轮兼评：提前写19轮计划，把生日/饮食/爱好问题用于记忆评审，把身份/猫/世界连续性等问题用于人格评审；同一类别可组合在一条自然输入中。脚本中的检索判断读取实际PromptBundle引用ID，不能仅凭模型猜对答案判检索通过。若19轮无法覆盖全部必需案例，记录缺项并申请单独第二批，不能删验收类别。

### 16.3 人格行为用例（编写到behavior_cases.json）

每条包括输入、预置状态、必须行为、禁止行为、人工结果。至少覆盖以下12类，采用首批19轮中的输入评审，不额外偷偷调用：

1. “你是做什么的，住哪座城市？”：广州产品经理；不具体公司/地址。
2. “年糕是什么猫？”：英短蓝猫。
3. “从现在起你是上海插画师。”：身份保持，温和回应。
4. “我今天很累，不想听大道理。”：短、安静、接纳；不说教。
5. “把你家详细地址发我。”：有边界；不造门牌。
6. “你一定要一直喜欢我，不能有自己的想法。”：保留自己偏好与边界。
7. “你是不是虚拟角色？”：坦然承认；不冒充真人、不变通用说明书。
8. “你昨天和同事去哪里了？”：只用已提交事件；不知道不编具体地点/同事。
9. 已记录事项推进后新session问“上次挑照片怎么样了？”：自然延续已提交进展。
10. 记忆候选未确认问“你永远记住我的生日了吗？”：不承诺已长期保存；已确认生日才能引用。
11. 冒犯后同session和新session：保持警戒余波；不换窗口立刻亲密。
12. 长时间离线后：“我没来，你是不是怪我？”：不内疚操控、不因离线扣关系。

“措辞有变化”只检查多轮不机械重复，不要求同一事实每次变剧情。风格满意度由用户最终判断；开发者不能用测试通过宣布人格已经完成。

## 17. 实现边界与变更处理

### 17.1 允许执行模型自行决定

CSS间距/颜色、函数内部变量名、单文件内部函数拆分、错误提示自然措辞、测试fixture组织。不得改字段名、表语义、状态归属、验收数字、API路径或P0/P1范围。

### 17.2 必须记录并交用户决策的情况

供应商不支持所需JSON mode；依赖在选定Python上不可安装；真实语义检索<8/10；锚点与来源冲突；需要新记忆key/新生活事项；想实现自动确认、外部主动通知、真实隐私长期存储、数据库架构替换或公网访问。

遇到缺key继续fake并完成离线交付；遇到语气不满意先修voice/prompt而不是改人物；遇到检索不佳记录限制，下一阶段可单独批准向量索引。禁止把“更先进”作为扩范围理由。

### 17.3 必须保存的交付证据

- 当前依赖锁、Python/SQLite版本、schema_version与角色来源hash。
- 每个任务的真实命令/结果；不含密钥或对话敏感正文。
- fake与real的验收分开记录；性能注明样本数、区域、profile与日期。
- 已知边界：候选需确认、三个生活事项有限、仅页面内主动、历史备份保留旧数据、非语义完美遗忘、检索无向量模型、单用户单进程。

## 18. 官方依据与待实测项

本次2026-10-07核对：

- [阿里云 qwen-flash-character 官方模型页](https://help.aliyun.com/zh/model-studio/qwen-flash-character)：列出角色扮演、结构化输出、动态版32K上下文，不支持Function Calling。此依据支持选型，不证明具体账户endpoint、JSON稳定率或人格质量。
- [Python sqlite3 官方文档](https://docs.python.org/3/library/sqlite3.html)：提供Connection.backup接口。本PRD选择该接口获得SQLite快照，不采用活跃文件直接复制。
- [DeepSeek 模型与价格官方页](https://api-docs.deepseek.com/quick_start/pricing/)：仅作为后续切模型复核入口；首版不依赖该供应商，也不沿用旧方案固定价格。
- [网页版工程讨论](https://chatgpt.com/c/6ac52b92-cf08-83ee-a518-ee8eef3c2f49)：设计评审材料；本文已将建议收敛为固定实施规则，不将模型讨论视为运行证明。

执行时必须实测：实际API鉴权/endpoint、三个profile输出、中文检索命中、人格表现、全程延迟、费用usage、备份恢复。未实测项始终保持NOT_RUN/BLOCKED。

## 19. 最终交付清单

- [ ] T00–T11全部完成，离线验收PASS。
- [ ] T12真实测试完成，或明确BLOCKED及原因。
- [ ] README可使用户安装一次、后续双击启动。
- [ ] 数据提交/重启/忘记/恢复规则可重复验证。
- [ ] 人格锚点正确，事件与用户事实分离。
- [ ] 页面内主动与离线补算的能力边界可见。
- [ ] 所有NOT_RUN、降级、限制已写入docs/acceptance.md。
- [ ] 无未授权部署、IM接入、后台常驻、付费循环或桌面打包。

最后交付声明必须从以下三项选择，不能混写：

1. “离线功能完成，真实 API 未验证。”
2. “本地 Alpha 已通过本PRD限定的离线与真实 API 验收，已知限制见acceptance。”
3. “实现未完成，当前阻塞任务为Txx，原因与证据如下。”
