from __future__ import annotations


class APIProblem(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        operation_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.operation_id = operation_id


def problem_for_code(code: str, operation_id: str | None = None) -> APIProblem:
    known: dict[str, tuple[int, str, bool]] = {
        "CONFIG_MISSING": (400, "请先在本地 .env 中配置模型密钥，再重启应用。", False),
        "SESSION_NOT_FOUND": (404, "找不到这段对话。", False),
        "OPERATION_NOT_FOUND": (404, "找不到这次操作。", False),
        "OPERATION_CONFLICT": (409, "操作编号已用于不同请求。", False),
        "APP_BUSY": (409, "当前操作尚未结束，请稍后重试。", True),
        "SESSION_ARCHIVED": (409, "这段对话已归档，恢复后才能继续发送。", False),
        "FACT_NOT_FOUND": (404, "找不到这条记忆。", False),
        "PROPOSAL_NOT_FOUND": (404, "找不到这条待确认建议。", False),
        "SOURCE_REDACTED": (409, "来源内容已被移除，不能再确认这条建议。", False),
        "SOURCE_INVALID": (409, "来源消息不支持这条记忆。", False),
        "PROPOSAL_INVALID": (409, "这条建议的数据无效，不能确认。", False),
        "FACT_NOT_ACTIVE": (409, "只有当前有效的记忆可以修改。", False),
        "PROPOSAL_NOT_PENDING": (409, "这条建议已失效或已处理。", False),
        "PROPOSAL_CONFLICT": (409, "这条建议已经作出相反处理。", False),
        "ANCHOR_CHANGE_FORBIDDEN": (409, "固定人设锚点不能通过建议修改。", False),
        "PERSONA_LIMIT_REACHED": (409, "这个偏好列表已达到上限。", False),
        "WORLD_EVENT_NOT_FOUND": (404, "找不到这条生活经历。", False),
        "WORLD_THREAD_NOT_FOUND": (404, "找不到这项生活事项。", False),
        "EVENT_HAS_SUCCESSOR": (409, "这条经历已有后续记录，不能撤回。", False),
        "THREAD_NOT_RESTARTABLE": (409, "只有已完成的事项可以重新开始。", False),
        "THREAD_COMPLETED": (409, "已完成的事项不能通过恢复重新开启。", False),
        "FACT_VALUE_INVALID": (422, "记忆键或内容不符合允许范围。", False),
        "CONTEXT_TOO_LARGE": (413, "当前对话上下文超过模型输入限制。", False),
        "UPSTREAM_AUTH": (502, "模型服务认证失败，请检查本地配置。", False),
        "REPLY_INVALID": (502, "模型回复无法使用，请稍后重试。", True),
        "ANALYSIS_INVALID": (502, "状态分析回复无法使用，本轮已停止。", True),
        "UPSTREAM_TIMEOUT": (504, "模型请求超时。", True),
        "DEADLINE_EXCEEDED": (504, "本轮处理超时。", True),
        "DATA_CORRUPT": (503, "本地数据无法读取，请检查数据文件。", False),
        "DATABASE_ERROR": (503, "本地数据暂时不可用。", True),
        "BACKUP_FAILED": (503, "备份未能通过本地校验或写入。", False),
        "PROCESS_INTERRUPTED": (503, "应用重启时中断了这次操作；可使用相同内容明确重试。", True),
        "UPSTREAM_NETWORK": (503, "暂时无法连接模型服务。", True),
        "UPSTREAM_ERROR": (503, "模型服务暂时不可用。", True),
        "INPUT_BUDGET_EXCEEDED": (503, "分析请求超过配置的输入限制。", False),
    }
    status, message, retryable = known.get(code, (503, "本轮处理失败，请稍后重试。", True))
    return APIProblem(status, code, message, retryable=retryable, operation_id=operation_id)
