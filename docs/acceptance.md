# 离线验收记录

记录日期：2026-10-07（Asia/Tokyo）。范围：PRD v1 的 T00–T11 离线验收，以及一次独立的基础真实模型冒烟。自动化应用验收使用 FakeClient 与临时 SQLite 数据库；真实冒烟只使用临时数据，不包含用户对话。

## 结论

- T00–T11：离线验收通过。
- T12 基础冒烟：PASS（范围有限）。本机真实模式下，分析与聊天调用均成功并返回中文回复。
- T12 完整评估：NOT_RUN。一次基础冒烟不能证明供应商 JSON 稳定性、长对话连续性、真实检索语义、人格满意度、完整费用或端到端延迟。

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

- `tests/fixtures/behavior_cases.json` 包含 PRD §16.3 的 12 个场景和 1 个回归场景，新增项明确年糕是苏晚的猫，不得暗示用户与年糕互动；所有 `manual_result` 仍为 NOT_RUN，因为 FakeClient 不评人格质量。
- T12 的完整范围仍未运行：analysis/world JSON 多次探测、19 轮真实对话、P95、费用统计和真实中文语义检索。当前只确认了两次真实调用的基础冒烟，不能外推到完整行为或性能结论。
- 事件生成只覆盖三个固定事项、固定时间槽与有限阶段；结构校验无法完全消除模型描述中的语义幻觉。完成事项不会自动开启新 cycle。
- 主动开场仅在页面进入 session 时尝试一次，没有后台调度、通知或页面外消息。
- 遗忘不会改写用户已下载的历史备份；恢复旧备份可能恢复当时的数据。
- P0 为单用户、单进程和单一本机数据库；FakeClient 的通过不代表真实供应商效果、性能或费用。

## 交付声明

**离线功能完成；真实 API 基础调用冒烟通过；T12 完整质量、性能与费用评估仍未完成。**
