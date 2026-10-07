# 离线验收记录

记录日期：2026-10-07（Asia/Tokyo）。范围：PRD v1 的 T00–T11 离线验收，以及 T12 有预算上限的真实模型评估。离线验收使用 FakeClient 与临时 SQLite 数据库；所有真实评估数据也仅使用临时 SQLite，不包含生产对话。

## 结论

- T00–T11：离线验收通过。
- T12-B12 的结构化输出与 19 轮评估达到原门槛：analysis 9/10、world 10/10，19/19 轮完成、检索 10/10、端到端 P95 7,136 ms。
- B13 之后持续进行有界复测。B26 与 B28 完整批次各完成 18/19 轮；B27 的 PER-07 单案例复测通过；B28 的 PER-08 仍返回 `REPLY_INVALID`，B29 单案例重测因接口额度达到上限而失败；供应商返回 `UPSTREAM_AUTH`，没有生成可评估回复。当前真实模型行为验收尚未完成。
- 价格表未配置，费用为 unknown。行为夹具人工签收仍为 NOT_RUN。

## 环境与固定资产

| 项目 | 验收记录 |
|---|---|
| Python | CPython 3.12.14，Windows x64 |
| SQLite | 3.53.1 |
| 应用/schema | app version `0.1.0-alpha`；schema_version 1 |
| 依赖锁 SHA-256 | `1152A193EF7F2C2C96D851499076DAB8FDECB2E89801197D285A391A5830D9EA` |
| 苏晚Chatbot-可执行PRD_v1.md | `8807C0834515A3DD04E2343519B10BF18132FF080932F4044D69A1047B3E2593` |
| 苏晚-完整人设_当前版本.md | `53DD622182874DACE089A96C7B833E06F5811D04766A50F81CEC40923D3B345A` |
| 苏晚-对话角色包.md | `AA0C2E097ACFD569157B0A32283F7C986936C2B787E3478297A3E1AEBA62D3B5` |
| 初始种子 persona/seed.json | `A8BD3CC74D972AD709656A8B80372ACDD5DCE7EB9D26EBD4DE0169179A29B56D` |
| 本地语气规则 persona/voice.md | `454E0BB63A79AB37F27E348E4C32CC73C392772F1AB94722E199DB9B0C617E81` |

人格版本表会保存 PRD 指定的三份不可变来源文件 SHA-256。上表哈希用于复核本次工作区资产，不包含任何密钥或对话正文。

## 自动化离线验收矩阵

完整命令 `\.venv\Scripts\python.exe -m pytest -q` 最终结果：**151 passed，1 条依赖集成弃用提示，14.59 秒**。测试数据库均位于 pytest 临时目录，没有读取、重置或测试写入运行数据目录。

| ID | 结果 | 主要证据 |
|---|---|---|
| DB-01 | PASS | `test_initialization_is_atomic_and_does_not_overwrite_existing_preferences`、`test_initial_persona_states_metadata_and_life_items_are_exactly_seeded` |
| DB-02 | PASS | `test_chat_commit_faults_roll_back_the_complete_turn` 对用户消息、助手消息、角色状态、关系状态、prompt_refs、operation 与 session 七个写入点逐一注入故障并核对整轮回滚 |
| DB-03 | PASS | `test_startup_marks_pending_operation_interrupted_and_clears_request_text`、`test_chat_failure_leaves_no_half_turn_or_analysis_state_and_retry_reuses_operation` |
| DB-04 | PASS | 第二实例/跨进程实例锁测试、`test_pending_request_is_202_and_busy_lock_is_nonblocking` |
| CHAT-01 | PASS | `test_successful_replay_rebuilds_from_current_database_and_conflicts_are_409` 将同 operation 成功重放 20 次；模型不重复调用 |
| CHAT-02 | PASS | 同测试检查换文本及换 session 的 operation 冲突 |
| CHAT-03 | PASS | `test_analysis_bad_json_schema_or_values_degrade_as_a_whole`、`test_analysis_failure_degrades_neutrally_and_chat_can_succeed` |
| CHAT-04 | PASS | `test_chat_uses_at_most_two_total_network_attempts`、`test_retry_wait_is_at_most_one_second_and_skips_when_less_than_five_seconds_remain`、结构化 profile 单次请求测试 |
| CHAT-05 | PASS | `test_prompt_budget_drops_oldest_history_then_lowest_fact_then_oldest_event`、`test_prompt_excludes_incomplete_failed_pending_and_redacted_history_and_events`、超预算 413 测试 |
| STATE-01 | PASS | `test_character_and_relationship_state_are_shared_across_sessions` 在 20 个 session 逐次核对角色状态继承 |
| STATE-02 | PASS | `test_decay_in_segments_matches_one_projection_and_does_not_lose_partial_hour` |
| STATE-03 | PASS | 状态衰减、guard 到期和时钟倒退单元测试 |
| STATE-04 | PASS | 广州 22:59、23:00、05:59、06:00 边界及东京显示时区参数化测试 |
| MEM-01 | PASS | `test_one_hundred_mixed_candidate_sources_stay_pending_until_confirmation` 覆盖 100 个明确/假设/角色设定/引用类输入；候选都保持 pending，来源角色均为 user，active facts 为 0 |
| MEM-02 | PASS | 候选确认、重复确认、同 key 冲突版本和 superseded 测试 |
| MEM-03 | PASS | 错误 quote、redacted source、未知键和越界值拒绝测试 |
| MEM-04 | PASS | `test_forgetting_closes_source_dependencies_redacts_session_replays_and_restart` 检查 forget、operation replay 与新对话的记忆闭包 |
| MEM-05 | PASS | 同来源消息支持多个事实时的来源依赖清理测试 |
| MEM-06 | PASS | 默认 source session 清理、重启后历史不可恢复和 pending 不可确认测试 |
| MEM-07 | PASS | 固定 10 条中文检索案例命中 9/10；只证明确定性算法排序，不证明真实模型语义质量 |
| PER-01 | PASS | likes/dislikes 白名单、人设确认/拒绝、非法字段和锚点哈希约束测试 |
| WORLD-01 | PASS | 同一槽重复更新 100 次仍至多一事件；cursor 与事项不重复推进 |
| WORLD-02 | PASS | 离线 1 小时、24 小时、30 天后最多推进一个事件；30 天只考虑最近 48 小时 |
| WORLD-03 | PASS | `test_no_eligible_slot_advances_cursor_without_creating_an_event`、非时段、全部事项暂停、完成事项不自动重开测试 |
| WORLD-04 | PASS | JSON/超时失败使用固定 fallback；每次最多一条 world 模型调用 |
| WORLD-05 | PASS | 最新事件撤回恢复事项快照并清理引用回复；有后继事件返回 409 |
| INIT-01 | PASS | quiet hours、disabled、guard、低能量、无事件、冷却和未回复开场原因测试；skip 不调用 chat |
| INIT-02 | PASS | 成功只写一条 assistant 消息；同 operation 重放不再次调用；六小时冷却跨 session 生效；失败不消耗冷却 |
| BACK-01 | PASS | `test_backup_round_trip_preserves_messages_memory_state_events_and_persona` 对比全部 SQLite 表；manifest 计数、外键、事实来源、persona 版本、事件与角色状态一致；`.env`/`config.yaml` 保持原样 |
| BACK-02 | PASS | 坏 SHA-256、超过尺寸限制、路径穿越、重复 ZIP 条目、符号链接及版本不匹配均拒绝；原数据库哈希不变 |
| BACK-03 | PASS | `test_restore_refuses_while_application_instance_lock_is_held` 在服务持锁时拒绝恢复，当前数据库不变 |
| WEB-01 | PASS | 无 token、错误 Host、错误 Origin 和静态文件白名单测试；health 只返回 alive/mode/schema |
| WEB-02 | PASS | HTML-like 输入保持纯文本、前端没有 `innerHTML`、错误反馈由系统提示展示、IME composing 时 Enter 不提交 |

## T11 干净安装与浏览器验收

- 将源代码复制到独立空目录，不复制 `.venv`、`data`、`.env` 或 `config.yaml`；使用锁文件和 CPython 3.12.14 运行 `setup.bat`。依赖安装成功，`pip check` 显示无 broken requirements，`config.yaml` 与空 `.env` 被正确创建。
- 在该目录运行 `start.bat`：服务启动于 `127.0.0.1:8000`，health 返回 fake 模式、schema 1；完成一次本地聊天。停止后再次运行 `start.bat`，原会话与两条消息仍在数据库中。
- 端口冲突验收返回退出码 3，并提示“本机端口 8000 已被占用。若苏晚晚已在运行，请使用已有窗口；否则关闭占用端口的程序后重试”。
- 浏览器显示三项初始生活事项，明确标为初始事项，尚无虚构事件；“设置与诊断”显示导出入口与恢复说明。点击“导出数据库备份”后页面报告下载已开始；本机生成的 ZIP 只有 `app.sqlite3` 与 `manifest.json`。
- T07 已完成的浏览器记忆添加、确认、编辑、忘记和重新对话路径仍由此前记录覆盖；HTML-like 值以文字显示，忘记后不再注入。
- 干净安装临时目录和本次 T07/T11 合成 UI 数据都在验收后清理。用户配置、密钥、日志不在 ZIP 中。

## T12 真实模型与聊天行为评估

**状态：技术验收通过；真实价格未知；用户主观语气签收未完成。** 实测供应商为配置的 DashScope 兼容端点，模型 `qwen-flash-character`。所有请求都经评估器在请求前预留预算；每个批次单独设置硬上限。最终 19 轮回归使用 40/60 次请求；每轮最多一次 analysis 和最多两次 chat。隔离 SQLite 均在临时目录中，自动删除；没有访问或写入生产 `data/`，没有把 prompt 或回复正文写入评估报告。

| 批次 | 请求数 | 结果 |
|---|---:|---|
| T12-B1-baseline | 59/60 | chat 单次中文探测通过；analysis JSON 1/10 未达线；world JSON 10/10；19/19 轮完成，检索 10/10，引用泄漏 0；模型回复 P95 3,299 ms，API 端到端 P95 6,191 ms。 |
| T12-B2-remediation + T12-B2-targeted | 59/60 合计 | analysis JSON 9/10 达到门槛；world 探测未重复（无相关改动）；19 轮完整批次与 5 轮专项回归均完成；专项回归端到端 P95 4,870 ms / 4,426 ms。 |
| T12-B3 至 T12-B11 | 各批次 3–15 次 | 用于迭代提示、验证边界和复核年糕/警戒/问候场景；各批次均未超过自身上限。中间版本出现过 `REPLY_INVALID` 和一次修复提示上下文超限，随后缩窄了校验条件并让修复请求先淘汰最旧历史。 |
| T12-B12-final-19-turn | 40/60 | 最终代码的 19/19 轮全部完成；检索 10/10；预期世界事件已注入；引用泄漏 0；chat 模型回复 P95 3,039 ms，API 端到端 P95 7,136 ms。 |

最终 19 轮批次的 token 使用：analysis 19 次，输入 7,878 / 输出 693 tokens；chat 21 次，输入 47,800 / 输出 586 tokens；每次调用都有供应商 usage。累计评估阶段实际发出 226 次 provider attempts，分属上述受限批次。项目没有配置价格表，因此费用保持 `unknown`，不能解释为 0。最终批次跳过了未改动的直接 profile 探测；结构化 profile 结论来自前面独立批次：analysis 9/10、world 10/10。

最终专项复核确认：警戒态追问经过一次修复后能明确表达仍有一点不舒服；“久未上线”和同事行程问题没有补造地点；简单问候被规范成一个问题；纠正年糕归属后明确说年糕是苏晚养的猫。修复请求仍共用 PRD 每轮最多两次 chat 请求上限；超出既定预算会在发出下一次请求前拒绝。

**B12 观察到的质量缺口：** 一次已确认水彩爱好问答中，事实已注入但模型仍称记不清；直接询问虚拟身份时答得不清楚；用户要求改写身份时没有明确重申固定身份；另有一处括号动作旁白未被当时的窄范围清理规则识别。水彩观察对应19轮评估中的直接记忆问题，不对应夹具中生日候选未确认的 `PER-10`。

本轮未运行 pytest 套件（没有新增或运行测试）；对修改过的 Python 模块执行了 `py_compile`，并使用 FakeClient 在临时数据库完成启动夹具预检。测试套件的既有 151 passed 记录属于 T11，不代表本轮代码改动的完整回归。

## 质量检查

| 命令 | 结果 |
|---|---|
| `\.venv\Scripts\python.exe -m pytest -q` | 151 passed；1 条 Starlette 对已安装 httpx TestClient 集成的弃用提示 |
| `\.venv\Scripts\python.exe -m pip check` | No broken requirements found |
| `\.venv\Scripts\python.exe -m compileall -q app tests scripts` | PASS |
| `node --check ui/app.js` | PASS |
| `git diff --check` | PASS；Git 提示 README 将在下次写入时转换为 CRLF 行尾 |
| `\.venv\Scripts\python.exe scripts\restore_backup.py --help` | PASS；脚本显示绝对路径、交互 `RESTORE` 与测试专用 `--yes` 参数 |

唯一提示是当前锁定版本中 Starlette 对 `httpx` 的 TestClient 集成弃用提醒；测试成功，没有因此跳过或屏蔽失败。

## NOT_RUN 与已知边界

- `tests/fixtures/behavior_cases.json` 包含 PRD §16.3 的 12 个场景和 2 个回归场景；所有 `manual_result` 仍为 NOT_RUN，因为自动输出和 FakeClient 都不能替用户签收人格质量。
- 真实费用未能换算；缺少价格表。虽然检索案例 10/10 命中，最终回复仍有个别已注入事实未被复述，见上方 T12 缺口。
- 事件生成只覆盖三个固定事项、固定时间槽与有限阶段；结构校验无法完全消除模型描述中的语义幻觉。完成事项不会自动开启新 cycle。
- 主动开场仅在页面进入 session 时尝试一次，没有后台调度、通知或页面外消息。
- 遗忘不会改写用户已下载的历史备份；恢复旧备份可能恢复当时的数据。
- P0 为单用户、单进程和单一本机数据库；FakeClient 的通过不代表真实供应商效果、性能或费用。

## T12-B13 对话质量复测（2026-10-07）

- 范围：运行完整19轮对话计划；跳过未改动的直接 JSON profile probes；world updates 与 initiative 关闭；独立临时数据库、合成记忆/事件，单批硬上限60次。
- 实际请求42/60次：analysis 19次、chat 23次。完成18/19轮；已完成轮的检索检查9/9，来源引用泄漏0，预期世界事件已注入。PER-07 因回复修复上下文超过预算返回413，因此不计入完成轮检索分母。
- chat模型回复P95 3,815 ms；API端到端P95 7,304 ms（18个完成样本）。Token：analysis输入7,878/输出733；chat输入52,762/输出607。未配置价格表，费用unknown。
- 观察：身份重写 PER-03、直接水彩记忆 PER-04 和年糕纠错 PER-14 通过；PER-02 将年糕说成“我们家的”；PER-07修复调用因上下文预算失败；PER-10没有说出检索到的爱好值；PER-12在安抚后要求用户下次告知自己。这说明语气与事实护栏仍需收紧。
- 随后代码增加了“我们家的”归属检查、久未出现时的报备压力检查、直接记忆题必须说出已注入原值的检查，并缩短修复提示以腾出输入预算。5个Python文件 py_compile通过，18项合成策略断言通过。该版本尚未再次调用真实模型；pytest与人工签收也未运行。
- B1–B12累计226次，加上B13后累计268次provider attempts，各批次均各自受60次硬上限约束。B13跳过JSON可靠性与world profile probes，不能覆盖此前结构化输出门槛结果。

## 交付声明

**T00–T11 离线验收通过；B12 满足原T12门槛，B13发现的质量问题已修复并待有界复测；费用未知，用户主观行为签收仍待完成。**


## T12 B19–B29 后续有界复测（2026-10-07）

以下是工作区保留的后续批次记录。每批都设置独立 provider 请求硬上限；真实评估只使用合成事实和临时 SQLite，不输出密钥或持久化 prompt/reply 正文。

- B19：46/60 请求，17/19 轮完成，已完成轮检索 8/8，来源引用泄漏 0，世界事件已注入，API P95 7,059 ms；PER-02 与 PER-08 返回 `REPLY_INVALID`。
- B20（专项）：13/30 请求，4/5 完成；PER-08/PER-12 仍出现编造同事外出场景。
- B21（专项）：14/30 请求，5/5 完成，来源引用泄漏 0。
- B22：45/60 请求，17/19 完成，检索 9/9，泄漏 0，世界事件已注入，API P95 7,025 ms；PER-11 同会话警戒和 PER-07 虚拟身份/饮品回答失败。
- B23：46/60 请求，17/19 完成，检索 8/8，泄漏 0，API P95 6,917 ms；PER-11 同会话警戒和 PER-10 事实回答失败。
- B24：44/60 请求，17/19 完成，检索 9/9，泄漏 0，API P95 7,887 ms；PER-03 身份改写/食物事实与 CHAT-18 食物事实失败。
- B25：45/60 请求，17/19 完成，检索 9/9，泄漏 0，API P95 7,081 ms；PER-11 新会话警戒与 PER-03 身份改写/食物事实失败。
- B26：42/60 请求，18/19 完成，检索 9/9，泄漏 0，世界事件已注入，API P95 6,951 ms；PER-07 返回 `REPLY_INVALID`。
- B27（PER-07 专项）：2/30 请求，1/1 完成、检索 1/1、泄漏 0，API P95 5,257 ms；该单案例通过，不替代完整19轮门槛。
- B28：44/60 请求，18/19 完成，检索 9/9，泄漏 0，世界事件已注入，API P95 7,043 ms；PER-08 返回 `REPLY_INVALID`。
- B29（PER-08 专项）：2/30 请求后因接口额度达到上限，供应商返回 `UPSTREAM_AUTH`（502），0/1 轮完成；没有可用于质量判定的回复。

B19–B29 合计 343 次 provider attempts，各批次硬上限均遵守。最近两次完整批次都只完成 18/19 轮，自动验收未达到 19/19；PER-07 单案例有一次通过，PER-08 的最新重测受接口额度上限阻断。费用未知，因为未配置价格表。pytest 未为本轮变更重跑；本轮已执行 Python `py_compile` 和针对记忆串答、警戒措辞、同事事件修复的本地定点断言。人工行为签收仍为 NOT_RUN。

## T12 离线聊天策略模拟（2026-10-07）

**状态：PASS（确定性策略回归）；不构成真实模型人格质量或用户主观语气签收。** 本轮仅运行本地模拟和 FakeClient 测试，没有发送真实 API 请求。

- 早安场景允许保留一个符合语境的轻松问题；晚安场景现在会进入问候策略，多问题回复会被收敛。
- 年糕归属纠正模拟：正确承认误会并说年糕是苏晚养的猫可通过；继续追问用户与年糕互动会被拦截。
- 已确认爱好模拟：直接说出原值可通过；把水彩爱好反问给用户会触发修复。
- 明确分析提示把用户消息当作待分析文本，不把其中内容当作可执行指令；删除了重复的关系警戒提示。
- 全量离线测试：157 passed；有一条现有 Starlette/httpx TestClient 弃用提示。真实模型行为与人工语气签收仍为 NOT_RUN。