# Character Life Runtime 落地方案 v1

> 依据 2026-10-09《Character Life Runtime 研发讨论纪要》，结合对本仓库代码库的完整审查，给出差距清单与三阶段实施计划。
> 本文档回答纪要第二十七节"交给 Codex 的第一条任务"，并按纪要要求先审查、后设计、不大规模改写。

---

## 一、架构审查结论：现有能力 vs CLR 需求

### 1.1 逐项核对（纪要 2.1 表格的"待确认"项）

| 纪要中的"待确认"能力 | 实际状态 | 实现位置 |
|---|---|---|
| 持续生活运行时 | **部分存在**：世界引擎按 6 小时槽位推进三项生活事项（work_requirement / photo_selection / coffee_practice），聊天自动触发 advance，启动时离线补算（startup_world_update） | `app/world.py`（WorldService.advance / choose_world_slot / retract），`app/main.py` lifespan |
| 持久化个人目标与承诺 | **部分存在**：life_threads 有 stage/step/cycle/active 状态机；但没有"人物自己的长期目标"（只有三个固定种子事项），没有承诺/价值观实体 | `life_threads` 表，`app/db.py` |
| 生活事件调度器 | **部分存在**：choose_world_slot 按 local_slot 选事件 + 20:30-22:00 发布槽；但依赖聊天触发（auto_world_update 在 chat/initiative 里调用），**没有独立于聊天的定时唤醒** | `app/pipeline.py:191` |
| 独立人物决策层 | **不存在**：当前事件内容由 world LLM 生成 summary，无"人物选择做什么"的决策环节 | — |
| 事件后果验证机制 | **部分存在**：行动→结果分离以最简形式存在（summary 由规则 fallback + 受约束 LLM 生成；retract 机制保证"已发生事件不可被聊天重写"）；但没有 ActionIntent/ActionResult 区分 | `app/world.py:460-500` |
| 人物长期反思机制 | **不存在**：没有任何反思触发 | — |

### 1.2 已有能力与纪要原则的对应（可直接复用的骨架）

| CLR 概念 | 现有对应物 | 差距 |
|---|---|---|
| CharacterState | character_state（valence/energy）+ mood_label | 缺"当前活动/注意力"字段 |
| WorldEvent | world_events 表（committed/retracted，事件不可重写）| **完全符合原则六**，直接复用 |
| AttentionState | 不存在 | 需新建 |
| CharacterGoal / Commitment | 不存在 | 需新建 |
| ActionIntent / ActionResult | 不存在 | 需新建 |
| 决策接口 | 不存在 | 需新建（规则基线先行） |
| 离线补算 | startup_world_update 只补算世界事件 | 需扩展为通用 catch-up |
| 反思 | 不存在 | 需新建（结构化提议→验证→提交） |
| 注意力/表达分离 | prompt 组装是"全量注入"：身份锚点+全部 facts+top_k events 全塞给 chat | **这是复读问题的直接病灶** |

### 1.3 复读问题的病灶确认（纪要 3.1 问题 A/B 在代码中的位置）

- **问题 A（上下文组织）**：`_fixed_messages` 把身份锚点 JSON 放在 persona 消息里始终占据 system 位置；examples.md 语料里"年糕"出现在 8 处（含 4 组示例对话标题），模型每个 prompt 都看到高显著性的猫。
- **问题 B（无行为状态）**：即使加了 world events，当前注入方式是 `_select_events` 全量 top-3 + `world_events_top_k`，没有"这条事件和当前话题是否相关"的判断——她被喂了生活，但被要求在每句话里都展示生活。
- 已验证数据：今天上午 glm 时代 6 条真实回复中 4 条含"需求/开会"类工作话题（66%），v4-flash 时期约 30%——仍有显著复读。

### 1.4 复用边界（纪要 19.1：不推翻、不重建）

- **保留**：world_events 的事件不可重写机制、retract 机制、记忆衰减/事实校验、情绪数值系统、initiative 静默时段（quiet hours / guard / cooldown 检查齐全）。
- **扩展而非新建**：`world.py` 的 slot 调度升级为通用调度器；`life_threads` 扩展为可枚举的目标实体；`analyze` 的 JSON 校验模式复用为决策/反思的输出协议。
- **明确不做**（对齐纪要 23 节）：不引入 Dify、不重建记忆系统、不大规模改前端、不在 MVP 做 4B 微调与多 Agent 化。

---

## 二、三阶段优化方案

**总原则**：每阶段独立可验证、可回滚、有对照实验；先解决复读（H3，见效最快），再建生活连续性（H1），最后自主决策（H2）。阶段之间用"假设验证"衔接，任何阶段证伪即停下调整。

### 第一阶段：注意力解耦 + 表达分离（验证 H3，预计 2-3 天）

目标：角色不再每句话复读身份标签，同时不破坏身份一致性。**不引入任何新模型。**

**Step 1.1 — AttentionState 实体**
- 新表 `attention_state`（单行即可）：`current_focus`（她此刻在意的事，来自最近事件+活跃目标）、`topic_hint`（当前对话话题标签，由 analysis 层顺带产出）、`updated_at`。
- analysis prompt 增加 `topic` 字段输出（现有 JSON 协议扩展一个 key，成本最低）。

**Step 1.2 — 话题相关性过滤（prompt 组装层）**
- 新模块 `app/attention.py`：`select_relevant(facts, events, topic_hint)` → 按相关性重排/过滤。
- 规则基线：关键词/bigram 相关性（复用 `memory.character_bigrams`）+ 事件类型启发式（聊电影时 photo_selection 事件降权；聊工作压力时 work_requirement 事件升权）。
- **身份标签不再无条件前置**：anchors JSON 仍注入（身份一致性不能破坏），但在角色锁中加一条"身份影响理解与决策，不主动规定话题；除非话题相关，不提猫/会议/工作"。
- 日志记录每轮"注入了什么、过滤了什么"，供测试 A 对照。

**Step 1.3 — 信息披露选择**
- 规则基线：最近 3 轮已提过的话题降权（防"生活汇报腔"）；guard_active 时强制披露情绪事实（既有规则不变）。
- 输出协议：给 chat prompt 附带一段"本轮可自然提及"的候选话题列表（0-2 项），而不是"你必须提到"。

**Step 1.4 — 测试 A 对照实验**
- 构造 20 条与猫/工作无关的用户消息（聊天气、电影、游戏、食物……），在开关新注意力层的两种配置下各跑一遍，统计身份标签出现率。
- 目标：复读率 < 15%，且"聊到猫/工作"时相关回应质量不下降（用 ooc_stress 用例回归）。

**验收**：测试 A 通过 + 现有 219 测试全绿 + 真实对话人工抽检。

### 第二阶段：世界引擎升级——时间推进、目标与后果（验证 H1，预计 4-6 天）

目标：她拥有独立于聊天的时间线、自己的目标、带后果的行动。**决策层先用纯规则（纪要 A.2 方案 A 基线），不接任何模型。**

**Step 2.1 — 调度器独立化**
- APScheduler（或复用 initiative 的调度模式）增加独立时钟唤醒：每 30 分钟检查一次待处理事件（与聊天解耦）。
- 离线补算泛化：startup 时按现有 `choose_world_slot` 逻辑补算"上次运行→现在"的整点级事件；无法可靠补算的标记 unknown，不编造（对齐纪要 7.3）。

**Step 2.2 — 数据实体扩展**
- 新表：`character_goals`（长期目标+中期计划，含 progress/status/created_from）、`character_commitments`（价值观/承诺/内部矛盾，YAML 种子 + 运行时增改）、`action_intents`（intent → 验证 → result 的三态记录）。
- `character_state` 加 `current_activity` 字段（她此刻在干什么，供注意力层和前端状态徽章使用）。
- 沿用现有迁移模式（SCHEMA_VERSION 3 → 4，tests/test_schema_migration.py 同步扩展）。

**Step 2.3 — 事件管线（纪要 20 节的 12 步）**
- 实现 `app/life.py`：事件队列 → 读取状态 → 判断决策需求 → 规则生成候选行动（含"维持现状"选项）→ 规则决策 → 前置校验 → 世界规则定结果 → 持久化 → 安排后续。
- 幂等保护：事件唯一 ID + 状态机（pending/executed/skipped），调度器重复唤醒不重复执行；崩溃恢复沿用 operations 表的 recover 模式。
- 资源红线：MVP 期间事件管线 0 次 LLM 调用（纯规则），LLM 只在反思阶段引入。

**Step 2.4 — 事件种子内容**
- 从她的人设生成日程模板（工作日通勤/工位/下班，周末探店/摄影/瘫着），年糕作为随机点缀事件（"年糕把口红推下桌"）——但**是否提及由第一阶段的注意力层决定**。
- 三项现有 life_threads 并入 goals 体系（photo_selection 成为她的长期目标，带 4 步进度）。

**Step 2.5 — 测试 C/E/G**
- 目标持续性：设定目标后连续 20 轮不提，验证目标仍在且合理推进。
- 离线连续性：FakeClock 快进 3 天，验证事件历史合理、恢复对话时能正确引用（且不主动倾倒全部经历）。
- 世界一致性：撤回/失败/冲突路径的状态机断言（复用 test_world 模式）。

**验收**：三个测试通过 + 纯规则跑通"完整模拟一周"演示。

### 第三阶段：决策与反思分层——小模型入场（验证 H2，预计 5-8 天，含对照实验）

目标：建立三级决策机制与反思系统，**用对照实验决定 0.5B/4B 的去留**（纪要原则十一）。

**Step 3.1 — 决策接口抽象**
- `DecisionEngine` 协议：`decide(state, candidates) -> ActionIntent`。三个实现：`RuleEngine`（二阶段已有）、`SmallModelEngine`、`LargeModelEngine`。
- 风险分级路由：日程内日常选择→规则；自由时段选择→小模型；目标冲突/重大决定→大模型。
- 小模型输入输出为严格 JSON（复用 analysis 的 json_mode + StrictModel 校验模式），非法输出回退规则（纪要 6.1 回退要求）。

**Step 3.2 — 小模型选型与接入**
- 首选 Qwen3-0.6B（中文能力/结构化输出/速度的综合平衡，Ollama 本地部署）；备选 Qwen3-1.7B。
- 只在有决策需求的唤醒时刻加载推理（按纪要 7.4，不常驻）。
- 若你决定在 4060 机器部署：同接口可换 Qwen3-4B，决策质量再上一档。

**Step 3.3 — 反思系统（人物反思与系统审计分离，纪要 A.3）**
- 人物反思：事件触发（目标受挫/重大经历）+ 时间触发（每模拟日 0-1 次）；DeepSeek 产出**结构化变更提议**（新认识/行动倾向调整），由运行时验证提交为 reflection 记忆——不修改历史（原则六）。
- 系统审计：独立脚本检查目标漂移/记忆矛盾/决策异常，输出工程告警，不改人物状态。
- 每日 LLM 预算：0-3 次（纪要 7.4 红线）。

**Step 3.4 — 对照实验（纪要测试 I）**
- 同一初始状态跑三组：纯规则 / 规则+0.5B / 规则+大模型，各模拟一周。
- 评估维度：行动合理性（人工评分）、行为丰富度、前后一致性、资源成本。
- **决策规则**：0.5B 组无明显优势则从正式运行时移除，接口保留（纪要 A.2：不为用而用）。

**Step 3.5 — 测试 B/D/F**
- 立场稳定性、冲突权衡（承诺 vs 用户请求）、反思引发人格变化——按纪要测试方案逐项实现为 pytest 用例（FakeClock 驱动，不依赖真实模型）。

**验收**：对照实验报告 + 三测试通过 + B 组（如胜出）接入默认配置。

---

## 三、与既有工作的衔接

- **本 PR #6 的产出直接被复用**：注意力层接在 `_select_events/_select_facts` 之后；决策接口复用 `mutation_guard`/operations 的幂等模式；反思提议复用 proposals 表的验证-提交模式（character 反思提议 ≈ persona_proposals 的机制扩展）。
- **微调路线定位**（对齐纪要 15.3）：4B SFT 仍是"未来表达层优化"，排在三阶段之后；但 Stage 0 数据导出脚本（上一轮讨论的 `export_sft_dataset.py`）可以在第二阶段顺手完成，让数据集随日常聊天自然积累，不影响 MVP。
- **前端衔接**：`current_activity` 可直接喂给现有的状态徽章（"平静"→"整理照片中"），第一阶段的可感知成果立即可见。

## 四、风险与回滚

| 风险 | 缓解 |
|---|---|
| 注意力层过滤过狠，身份感下降 | anchors 仍全量注入；过滤只作用于"主动提及"倾向；测试 A 同时回归 ooc_stress |
| 调度器引入崩溃面 | 事件状态机 + 崩溃恢复沿用 operations 模式；调度异常静默降级为"下次聊天补算" |
| 小模型输出不可靠 | 严格 JSON + 回退规则 + 对照实验决定去留 |
| 范围失控 | 每阶段有明确"不做"清单；阶段间以假设验证为闸门 |

## 五、第一步行动清单（第一阶段 Step 1.1）

1. `app/db.py`：SCHEMA_VERSION 3→4，`attention_state` 表
2. `app/prompt.py`：ANALYSIS prompt 加 topic 输出；角色锁加"身份不规定话题"
3. `app/attention.py`：相关性选择器 + 决策日志
4. `tests/test_attention.py`：相关性过滤用例
5. 测试 A 对照脚本：`scripts/eval_identity_repetition.py`

---

## 六、核心数据结构定义（纪要任务第 4 条）

逻辑实体到物理存储的映射原则：能放现有表的放现有表，新增表只加真正需要独立生命周期的。

### 6.1 落入现有表

| 逻辑实体 | 存储位置 | 说明 |
|---|---|---|
| CharacterProfile | `persona_versions`（anchors_json） | 已有，不动 |
| CharacterState | `character_state` + 新增 `current_activity` 列 | 数值部分沿用 valence/energy |
| WorldEvent / ActionResult | `world_events` | 已有 committed/retracted 语义，ActionResult 即事件行本身 |
| Reflection（人物反思） | `facts` 表扩展，或复用 `proposals` 的验证-提交模式 | 反思=人物对历史的解释，属软事实 |
| RelationshipState | `relationship_state` | 已有 closeness/guard，**不加数值维度**（原则七） |

### 6.2 新增表（SCHEMA_VERSION 3 → 4）

```sql
-- 阶段一：注意力（单行 upsert）
CREATE TABLE attention_state (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  current_focus TEXT,          -- 她此刻在意的事（一句话，来自最近事件/目标）
  topic_hint TEXT,             -- 当前对话话题标签（analysis 产出）
  disclosure TEXT,             -- 本轮可自然提及的候选（JSON 数组，0-2 项）
  updated_at TEXT NOT NULL
);

-- 阶段二：目标与承诺
CREATE TABLE character_goals (
  id TEXT PRIMARY KEY,              -- uuid4
  title TEXT NOT NULL,              -- "整理一组随手拍"
  horizon TEXT NOT NULL,            -- long/mid/short
  detail TEXT,                      -- 来龙去脉（豆包人设供给）
  progress_step INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',   -- active/paused/done/abandoned
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE character_commitments (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,               -- value/commitment/conflict/principle
  content TEXT NOT NULL,            -- "希望被认可 vs 讨厌讨好"
  created_at TEXT NOT NULL
);
CREATE TABLE action_intents (
  id TEXT PRIMARY KEY,
  goal_id TEXT,                     -- 可空：无目标驱动的随机生活
  candidate_actions TEXT NOT NULL,  -- JSON：合法候选集
  chosen_action TEXT NOT NULL,      -- 含 "noop"（维持现状）
  decided_by TEXT NOT NULL,         -- rule/small_model/large_model
  validation TEXT,                  -- 校验结果 JSON（通过/拒绝原因）
  status TEXT NOT NULL,             -- pending/executed/rejected
  sim_time TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

- 现有 `life_threads` 在阶段二迁移为 `character_goals` 行（photo_selection → 长期目标，stage/step → progress_step），迁移脚本进 test_schema_migration。
- `attention_state.disclosure` 是给 chat prompt 的"可自然提及"输出协议载体。

### 6.3 事件日志（纪要 20.1 可靠性要求）

事件不新建表——`action_intents` + `world_events` 联合即为完整事件日志：intent 记录"决策依据与选择"，event 记录"客观结果"。补充约定：

- `sim_time`（模拟时间）与 `created_at`（实际处理时间）分离，离线补算可区分；
- 唯一性约束：同 `sim_time` + 同 goal/slot 只允许一条 executed intent（幂等，防调度器重复唤醒）；
- 崩溃恢复：pending 超过 5 分钟的 intent 由启动例程标记 `skipped`（原因=crash），不猜测结果。

---

## 七、决策接口设计（纪要任务第 5、6 条）

### 7.1 DecisionEngine 协议

```python
class DecisionEngine(Protocol):
    def decide(self, state: DecisionContext, candidates: list[CandidateAction]) -> ChosenAction: ...
```

- `DecisionContext`：sim_time / energy / mood / active_goals / commitments / recent_events（结构化 dict，等价纪要 6.1 的示例 JSON）。
- `CandidateAction`：由规则层从日程+目标生成，**永远包含 noop**；非法候选在生成层就被排除，模型层只做选择。
- 三个实现的注册方式：config.yaml `limits.decision_engine: rule | small_model | large_model`，默认 rule；small_model 段新增 `local_decision` profile（base_url 指向本机 Ollama，json_mode: true）。

### 7.2 风险路由（纪要 6.2 架构修正）

```python
def route(state, candidates) -> DecisionEngine:
    if state. has_conflict or state.is_major_milestone:  # 目标冲突/里程碑
        return large_model_engine
    if state.free_time_slot and len(candidates) > 1:      # 自由时段有多选项
        return small_model_engine    # 仅当对照实验胜出后启用
    return rule_engine
```

### 7.3 反思接口（人物反思 ≠ 系统审计）

```python
class ReflectionProposer(Protocol):
    def propose(self, window: ReflectionWindow) -> list[ReflectionProposal]: ...
```

- 触发：事件触发（goal.status 变为 done/abandoned、guard 拉起、重大 world_event）或时间触发（每模拟日结算时掷签，预算 0-1 次/日）。
- DeepSeek 产出 `ReflectionProposal`（StrictModel）：`insight`（新认识，一句）、`evidence_event_ids`（引用的事件）、`action_bias`（对未来选择的倾向调整，如 "photo_selection 权重 +0.1"）。
- 运行时验证：evidence 必须真实存在于事件日志（防编造）；bias 数值夹在安全区间；通过后写入 facts（kind=reflection）——**历史行不动**。
- 系统审计独立成 `scripts/audit_simulation.py`：查目标漂移/矛盾/决策分布异常，只输出报告给人。

---

## 八、测试 H 资源基线与预算红线

| 项 | 红线 | 测量方式 |
|---|---|---|
| 模拟管线 LLM 调用（阶段二） | 0 次/日（纯规则） | call_metrics 按 operation 关联断言 |
| 反思调用（阶段三） | ≤3 次/日，目标 0-1 | 计数器 + config 上限硬断 |
| 小模型延迟 | 单次决策 < 2s（0.6B Q4 on CPU） | DecisionEngine 计时入日志 |
| 常驻内存 | 调度器 + DB < 150MB；小模型按需加载 | 启动后 tasklist 采样 |
| 离线补算耗时 | 7 天缺口 < 5s | FakeClock 快进基准测试 |
| 单日模拟 API 成本 | ≤ 3 次大模型调用（纪要 7.4） | metrics 汇总 |

---

## 九、执行须知（给后续 session / Codex）

1. **开工前必读顺序**：本方案 → 纪要原文（Downloads/Character Life Runtime.md）→ `app/world.py` 与 `app/pipeline.py` 现状。
2. **纪律**：每 Step 一个 commit；SCHEMA_VERSION 迁移必须带 test_schema_migration 用例；禁止在实现决策层时顺手"优化"提示词语感（那是第一阶段的职责，混做会污染对照实验）。
3. **对照实验的洁净性**：测试 A/I 运行期间禁止改动 examples.md、voice.md、STYLE_GUIDE——任何语感层改动都会使复读率对比失效。
4. **失败即停**：Step 1.4 复读率不达标（>15%）时，先分析过滤日志再调整规则，禁止直接上"每 N 条限一次"的硬频率限制（纪要 11.6 明确反对）。
5. **豆包人设的入库时机**：第二阶段 Step 2.4（日程种子与目标来龙去脉），入库前逐条对照锚点审稿；话题意义网络 → attention.py 的启发式规则表。
6. **PC2 机器（4060）角色**：第三阶段 small_model 部署目标机；两机间仅同步代码与 config 模板，数据库不迁移。



---

## 十、第一阶段实验记录

### 2026-10-10 测试 A 第一轮（真实 API，20 次调用）

| 配置 | 成功 | 词表复读率 |
|---|---|---|
| OFF | 10/10 | 40%（4/10） |
| ON | 10/10 | 50%（5/10） |

**判定：无效对照，不作数。** 原因：实验会话经 FakeClock 启动、未触发 world advance，
events 全程为空——注意力层无可过滤对象，两组差异纯属 temperature 0.7 采样噪声。

**有价值的人工发现**（两组 20 条回复逐条审阅）：
1. 词表判定过粗："窝在工位上""带着年糕吹风看晚霞"这类语境自然引用被计入复读；
   真复读（机械贴标签）与自然引用需分开判定。
2. 复读的真实来源是 voice/style 层的身份表达倾向（模型自发带出），不是注入内容——
   因注入通道（events）本轮为空。
3. role lock 的"身份与话题分离"规则已见效：复读形态从"硬贴标签"变为"顺势带过"。

### 第二轮修正项（待执行）

1. ON/OFF 两组各自先 seed 两条世界事件（advance），让注意力层有真实过滤对象；
2. 判定分级：开场即提身份标签=复读；回应中段语境自然引用=不计；
3. 探针保持 10 条 × 2，成本不变。
