# 苏晚晚 Chatbot

苏晚晚是一个只在本机运行的单用户网页聊天应用。固定技术路线是 Python 3.12、FastAPI、原生 HTML/CSS/JavaScript 和 SQLite。默认配置为离线 FakeClient，不会连接模型服务。

## 首次安装

1. 安装 CPython 3.12。
2. 双击 `setup.bat`。脚本创建项目自己的 `.venv`、安装锁定的 Python 依赖，并在缺少时从样例创建 `config.yaml` 和 `.env`。
3. 等待命令窗口显示 `Setup complete`。

安装需要网络来获取 Python 依赖。脚本不会安装 Python 本体，也不会覆盖已有的 `.venv`、`config.yaml` 或 `.env`。如果 Python 3.12 不在系统搜索路径，可先在命令提示符设置 `SUWAN_PYTHON` 为 Python 3.12 的 `python.exe` 绝对路径，再运行 `setup.bat`。

## 后续启动

双击 `start.bat`。它使用已有 `.venv` 启动本机服务，不运行 pip，也不访问模型服务；浏览器会打开设置页。选择“对话”开始聊天。服务地址固定为 `http://127.0.0.1:8000`。

如果提示端口 8000 已被占用，先确认是否已有苏晚晚窗口在运行。若有，继续使用该窗口；否则关闭占用该端口的程序后重试。不要同时启动第二个实例。关闭服务命令窗口即可停止应用。

## 模型配置与费用

- 默认 `config.yaml` 中的 `mode` 是 `fake`。此模式使用本地 FakeClient，结果仅用于确定性离线验收。
- `.env` 中的 `SUWAN_API_KEY` 默认为空。页面不展示或修改密钥；如未来获得单独授权，可手动编辑 `.env` 和 `config.yaml` 并重启。
- “设置与诊断”显示模式、模型标识、调用次数和脱敏指标，不读取密钥。FakeClient 探测不会访问网络；真实模式的模型探测会发出一次请求，可能产生费用。
- 历史 B12 真实模型批次达到原始门槛；后续 B26/B28 完整批次各完成 18/19 轮，B27 的 PER-07 单案例通过，B29 的 PER-08 复测因上游认证失败而未完成。T12 行为验收尚未完成，费用未知（未配置价格表），用户主观签收仍待实际使用；详见 [docs/acceptance.md](docs/acceptance.md)。

## 记忆和对话数据

运行数据保存在 `data/app.sqlite3`。对话、人设版本、用户确认的记忆、角色状态、生活事项和事件均以这份 SQLite 数据库为准。

自动识别的用户事实和人设偏好先进入待确认列表，只有明确确认后才会生效。忘记操作会清除事实内容、来源消息及引用它的晚晚回复；默认还会移除来源会话的全部消息。已经导出的旧备份仍可能含有这些内容，恢复旧备份也会恢复当时的记忆和人设版本。

人设固定为 PRD 中定义的苏晚，不会因对话自动改变身份锚点。状态在会话间共享；长时间离线不会扣减亲密度。模型已返回但进程在本地提交前崩溃时，重试可能再次产生模型费用；应用保证本地消息至多提交一次，不保证供应商至多计费一次。启动时遗留的 pending 操作会变为 `interrupted`，不会自动再次请求模型。

## 生活连续性和主动开场

生活页只有三个有限的初始事项：整理工作需求、整理随手拍、练习手冲。它们是产品种子，不代表此前已经发生的经历。每次最多推进一个符合固定广州时间槽的事项；长期离线只检查最近 48 小时且最多补一条。模型写描述失败时会使用固定备用文本。完成事项不会自动重开，页面可由管理员重开。

主动开场只在进入一个会话时尝试一次；需要处在广州 06:00–23:00、没有关系警戒、能量足够、有最近事件且全局六小时冷却期已结束。应用关闭时不会运行，也不会发送通知。

## 备份与恢复

在“设置与诊断”页选择“导出数据库备份”。应用通过 SQLite `Connection.backup()` 生成一致快照，验证完整性与外键后下载 ZIP。ZIP 仅包含 `app.sqlite3` 和 `manifest.json`；不包含 `.env`、`config.yaml`、日志或 `exports` 目录。导出的备份留在 `data/exports`，应用不会自动删除旧备份。

恢复必须在服务停止后执行。在项目目录打开 PowerShell，使用两个绝对路径运行：

```powershell
python scripts\restore_backup.py --archive "C:\完整路径\suwan-backup.zip" --data-dir "C:\完整路径\wanwan-chatbot\data"
```

输入 `RESTORE` 后才会继续。恢复会校验 ZIP 白名单、大小、版本、SHA-256、SQLite 完整性、外键和记录计数；运行中的服务持有同一实例锁时会拒绝恢复。当前数据库会先以 SQLite 快照方式保存在 `data/rollback`，并附带 SHA-256 说明。恢复只替换数据库，不覆盖 `.env`、`config.yaml` 或日志。重新启动后会生成新的本机会话令牌。

`--yes` 会跳过交互确认，只用于已明确授权的测试或恢复操作；不要在聊天内容或自动化输入中触发恢复。

## 数据边界与未实现功能

SQLite 是唯一运行时状态来源；没有后台调度器，页面外不会主动联系用户。首版不实现多用户、账号、公网访问、IM、语音、图片、自动总结、向量记忆、复杂世界模拟、插件/Agent 工具、社交发布、桌面安装包或 `.exe`。应用仅绑定 loopback 地址。

## 近期规划

1. 先恢复供应商认证，再重测 PER-08 和完整 19 轮计划；当前行为验收尚未达到 19/19。费用仍未知。
2. 已加强直接记忆回答、虚拟角色透明度、身份锚点、年糕归属及久未出现时的自然边界；保持聊天窗口以消息流和输入框为核心。
3. 参考 [Shinsekai](https://github.com/RachelForster/Shinsekai) 的聊天窗口/设置中心分工，以及[聊天主题指南](https://github.com/RachelForster/Shinsekai/blob/main/docs/CHAT_UI_THEME_GUIDE_zh-CN.md)中的对话、姓名、输入框与工具栏独立样式；只借鉴设计思路，不复用其代码或素材。继续让管理入口远离聊天主流程。

## 验证

在已安装的环境运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

完整离线验收记录、未运行项目和已知限制见 [docs/acceptance.md](docs/acceptance.md)；任务进度见 [docs/progress.md](docs/progress.md)，范围见 [docs/decisions.md](docs/decisions.md)。PRD 原文保存在 `docs/source/苏晚Chatbot-可执行PRD_v1.md`。

