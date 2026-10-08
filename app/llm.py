from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from app.chat_policy import _fact_answer_anchors, _requested_fact_keys
from app.clock import Clock, SystemClock
from app.config import AppConfig, LLMProfile
from app.models import AnalysisResult, CallMetric, LLMResult, LLMUsage
from app.prompt import build_analysis_prompt, budget_units


class LLMFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        *,
        attempt: int | None = None,
        latency_ms: int | None = None,
        usage: LLMUsage | None = None,
        called_provider: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.attempt = attempt
        self.latency_ms = latency_ms
        self.usage = usage
        self.called_provider = called_provider


@dataclass(frozen=True)
class FakeOutcome:
    text: str = ""
    error_code: str | None = None
    retryable: bool = False
    latency_ms: int = 1
    usage: dict[str, int | None] | None = None


@dataclass(frozen=True)
class AnalysisEvaluation:
    result: AnalysisResult
    warnings: tuple[str, ...]
    error_code: str | None = None
    llm_result: LLMResult | None = None


class FakeClient:
    """Deterministic client for offline use. It never constructs a network client."""

    _defaults = {
        "chat": "嗯，我在听。",
        "analysis": '{"state_delta":{"valence":0,"energy":0,"closeness":0,"guard_trigger":false},"fact_candidates":[],"persona_proposals":[]}',
        "world": '{"events":[]}',
    }

    def __init__(
        self,
        outcomes: Mapping[str, str | FakeOutcome | Sequence[str | FakeOutcome]] | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self._outcomes: dict[str, deque[FakeOutcome]] = defaultdict(deque)
        self._attempts: dict[tuple[str, str | None], int] = defaultdict(int)
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

        for profile_name, configured in (outcomes or {}).items():
            if isinstance(configured, (str, FakeOutcome)):
                items = [configured]
            else:
                items = configured
            for item in items:
                outcome = item if isinstance(item, FakeOutcome) else FakeOutcome(text=item)
                self._outcomes[profile_name].append(outcome)

    def generate(
        self,
        profile_name: str,
        profile: LLMProfile,
        messages: Sequence[Mapping[str, str]],
        operation_id: str | None,
        deadline: datetime | None,
    ) -> LLMResult:
        if deadline is not None:
            _remaining_seconds(deadline, self.clock.now())

        with self._lock:
            attempt_key = (profile_name, operation_id)
            self._attempts[attempt_key] += 1
            attempt = self._attempts[attempt_key]
            self.calls.append(
                {"profile": profile_name, "operation_id": operation_id, "attempt": attempt}
            )
            outcome = self._outcomes[profile_name].popleft() if self._outcomes[profile_name] else None

        if outcome is None:
            outcome = FakeOutcome(text=self._defaults.get(profile_name, ""))

        latency_ms = max(0, outcome.latency_ms)
        usage = LLMUsage(**outcome.usage) if outcome.usage is not None else None
        if outcome.error_code:
            raise LLMFailure(
                outcome.error_code,
                "simulated upstream failure",
                retryable=outcome.retryable,
                attempt=attempt,
                latency_ms=latency_ms,
                usage=usage,
                called_provider=True,
            )

        return LLMResult(
            text=outcome.text,
            parsed_json=None,
            usage=usage,
            latency_ms=latency_ms,
            attempt=attempt,
        )


def _remaining_seconds(deadline: datetime, now: datetime | None = None) -> float:
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise ValueError("deadline must be timezone-aware")
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None or reference_time.utcoffset() is None:
        raise ValueError("current time must be timezone-aware")
    remaining = (deadline.astimezone(timezone.utc) - reference_time.astimezone(timezone.utc)).total_seconds()
    if remaining <= 0:
        raise LLMFailure("UPSTREAM_TIMEOUT", "operation deadline has expired")
    return remaining


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def validate_analysis_json(text: str, user_text: str) -> AnalysisResult:
    """Parse one analysis object and validate its full schema and evidence quotes."""
    try:
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_object_keys)
        result = AnalysisResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError, ValidationError, TypeError) as exc:
        raise LLMFailure("ANALYSIS_INVALID", "analysis response did not match its strict schema") from exc
    if any(candidate.evidence_quote not in user_text for candidate in result.fact_candidates):
        raise LLMFailure("ANALYSIS_INVALID", "analysis evidence quote is not a substring of the current user message")
    return result


def neutral_analysis() -> AnalysisResult:
    return AnalysisResult(
        state_delta={"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False},
        fact_candidates=[],
        persona_proposals=[],
    )


def parse_analysis_or_degrade(text: str, user_text: str) -> AnalysisEvaluation:
    try:
        return AnalysisEvaluation(validate_analysis_json(text, user_text), ())
    except LLMFailure as failure:
        return AnalysisEvaluation(neutral_analysis(), ("ANALYSIS_DEGRADED",), failure.code)


def degraded_analysis(error_code: str) -> AnalysisEvaluation:
    return AnalysisEvaluation(neutral_analysis(), ("ANALYSIS_DEGRADED",), error_code)


def probe_profile_raw(profile: Any, messages: Sequence[Mapping[str, str]]) -> str:
    """One-shot OpenAI-compatible call for the settings connectivity test.

    Independent from LLMClient: reads only the profile (already loaded from the
    saved config.yaml), so a save + test works without restarting the app.
    Raises LLMFailure on any upstream problem; returns raw reply text.
    """
    api_key = os.environ.get(profile.api_key_env, "").strip()
    if not api_key:
        raise LLMFailure("CONFIG_MISSING", "api key is not configured")
    started = time.perf_counter()
    client: OpenAI | None = None
    request: dict[str, Any] = {
        "model": profile.model,
        "messages": [dict(message) for message in messages],
        "temperature": profile.temperature,
        "max_tokens": min(profile.max_tokens, 100),
    }
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=profile.base_url,
            timeout=float(profile.timeout_seconds),
            max_retries=0,
        )
        completion = client.chat.completions.create(**request)
    except AuthenticationError as exc:
        raise LLMFailure(
            "UPSTREAM_AUTH", "authentication failed", attempt=1,
            latency_ms=round((time.perf_counter() - started) * 1000), called_provider=True,
        ) from exc
    except (APITimeoutError, APIConnectionError) as exc:
        raise LLMFailure(
            "UPSTREAM_NETWORK", "network failure or timeout", retryable=True, attempt=1,
            latency_ms=round((time.perf_counter() - started) * 1000), called_provider=True,
        ) from exc
    except (APIStatusError, OpenAIError) as exc:
        raise LLMFailure(
            "UPSTREAM_ERROR", f"provider rejected the probe: {exc}", attempt=1,
            latency_ms=round((time.perf_counter() - started) * 1000), called_provider=True,
        ) from exc
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    choice = (completion.choices or [None])[0]
    text = getattr(getattr(choice, "message", None), "content", None)
    if not isinstance(text, str) or not text.strip():
        raise LLMFailure(
            "REPLY_INVALID", "empty reply", attempt=1,
            latency_ms=round((time.perf_counter() - started) * 1000), called_provider=True,
        )
    return text


class LLMClient:
    def __init__(
        self,
        config: AppConfig,
        fake_client: FakeClient | None = None,
        *,
        metrics_sink: Callable[[CallMetric], None] | None = None,
        clock: Clock | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.fake_client = fake_client or FakeClient(clock=self.clock)
        self.metrics_sink = metrics_sink
        self.sleeper = sleeper
        self.metrics: list[CallMetric] = []
        self.metric_sink_errors: list[str] = []

    def _profile(self, profile_name: str) -> LLMProfile:
        if profile_name not in {"chat", "analysis", "world"}:
            raise ValueError(f"unknown LLM profile: {profile_name}")
        return getattr(self.config.profiles, profile_name)

    def has_credentials(self, profile_name: str) -> bool:
        profile = self._profile(profile_name)
        return bool(os.environ.get(profile.api_key_env, "").strip())

    def analyze(
        self,
        operation_id: str,
        deadline: datetime | None,
        user_text: str,
    ) -> AnalysisEvaluation:
        try:
            result = self.generate(
                "analysis",
                build_analysis_prompt(user_text),
                operation_id,
                deadline,
                user_text=user_text,
            )
        except LLMFailure as failure:
            if failure.code == "CONFIG_MISSING":
                raise
            return degraded_analysis(failure.code)

        try:
            analysis = AnalysisResult.model_validate(result.parsed_json)
        except (ValidationError, TypeError, ValueError):
            return degraded_analysis("ANALYSIS_INVALID")
        return AnalysisEvaluation(analysis, (), llm_result=result)

    def generate(
        self,
        profile_name: str,
        messages: Sequence[Mapping[str, str]],
        operation_id: str | None,
        deadline: datetime | None = None,
        *,
        response_model: type[BaseModel] | None = None,
        user_text: str | None = None,
        max_attempts_override: int | None = None,
        reply_validator: Callable[[str], bool | str | None] | None = None,
    ) -> LLMResult:
        profile = self._profile(profile_name)
        normalized_messages = _normalize_messages(messages)
        units = budget_units(normalized_messages)
        if units > profile.input_budget_units:
            code = "CONTEXT_TOO_LARGE" if profile_name == "chat" else "INPUT_BUDGET_EXCEEDED"
            raise LLMFailure(code, "request exceeds the configured profile input budget")
        if response_model is None and profile_name == "analysis":
            response_model = AnalysisResult
        if profile_name == "analysis" and user_text is None:
            raise ValueError("analysis validation requires the current user_text for evidence checking")
        if profile_name == "analysis":
            user_messages = [message for message in normalized_messages if message["role"] == "user"]
            if (
                any(message["role"] == "assistant" for message in normalized_messages)
                or not user_messages
                or user_messages[-1]["content"] != user_text
            ):
                raise ValueError("analysis input may not contain assistant text and must end with current user_text")
        if response_model is not None and not profile.json_mode:
            raise ValueError("a response model requires a structured JSON profile")

        if self.config.mode == "real" and not self.has_credentials(profile_name):
            raise LLMFailure("CONFIG_MISSING", "required model credential is not configured")

        max_attempts = 2 if profile_name == "chat" else 1
        if max_attempts_override is not None:
            if max_attempts_override not in {1, 2}:
                raise ValueError("max_attempts_override must be 1 or 2")
            max_attempts = max_attempts_override
        active_messages = list(normalized_messages)
        for attempt in range(1, max_attempts + 1):
            try:
                if deadline is not None:
                    self._remaining_seconds(deadline)
                result = self._attempt(
                    profile_name,
                    profile,
                    active_messages,
                    operation_id,
                    deadline,
                    attempt,
                )
            except LLMFailure as failure:
                if failure.called_provider:
                    self._record_metric(
                        operation_id=operation_id,
                        profile_name=profile_name,
                        attempt=failure.attempt or attempt,
                        status="failed",
                        latency_ms=failure.latency_ms or 0,
                        usage=failure.usage,
                    )
                if (
                    profile_name == "chat"
                    and attempt < max_attempts
                    and failure.retryable
                    and self._may_retry(deadline)
                ):
                    self._wait_before_retry(deadline)
                    if not self._may_retry(deadline):
                        raise failure
                    continue
                raise

            if profile.json_mode:
                try:
                    parsed_json = _parse_structured(result.text, response_model, user_text)
                except LLMFailure as failure:
                    failure.attempt = result.attempt
                    failure.latency_ms = result.latency_ms
                    failure.usage = result.usage
                    self._record_metric(
                        operation_id=operation_id,
                        profile_name=profile_name,
                        attempt=result.attempt,
                        status="invalid",
                        latency_ms=result.latency_ms,
                        usage=result.usage,
                    )
                    raise failure
                result = result.model_copy(update={"parsed_json": parsed_json})

            if profile_name == "chat":
                invalid_reason = _chat_invalid_reason(result.text, self.config.limits.max_reply_chars)
                if invalid_reason is None and reply_validator is not None:
                    try:
                        validation = reply_validator(result.text)
                        if isinstance(validation, str):
                            invalid_reason = validation or "custom_reply_validation"
                        elif validation is False:
                            invalid_reason = "custom_reply_validation"
                    except Exception:
                        invalid_reason = "custom_reply_validation"
                if invalid_reason is not None:
                    self._record_metric(
                        operation_id=operation_id,
                        profile_name=profile_name,
                        attempt=result.attempt,
                        status="invalid",
                        latency_ms=result.latency_ms,
                        usage=result.usage,
                    )
                    if attempt < max_attempts and self._may_retry(deadline):
                        active_messages = _repair_messages(
                            normalized_messages,
                            result.text,
                            invalid_reason,
                            profile.input_budget_units,
                        )
                        if budget_units(active_messages) > profile.input_budget_units:
                            raise LLMFailure("CONTEXT_TOO_LARGE", "chat repair prompt exceeds the input budget")
                        self._wait_before_retry(deadline)
                        if not self._may_retry(deadline):
                            raise LLMFailure(
                                "REPLY_INVALID",
                                "not enough deadline remains to repair the model reply",
                                attempt=result.attempt,
                                latency_ms=result.latency_ms,
                                usage=result.usage,
                            )
                        continue
                    raise LLMFailure(
                        "REPLY_INVALID",
                        "model reply remained blank, oversized, fenced, or used a generic assistant opening",
                        attempt=result.attempt,
                        latency_ms=result.latency_ms,
                        usage=result.usage,
                    )

            self._record_metric(
                operation_id=operation_id,
                profile_name=profile_name,
                attempt=result.attempt,
                status="succeeded",
                latency_ms=result.latency_ms,
                usage=result.usage,
            )
            return result

        raise LLMFailure("UPSTREAM_ERROR", "model request did not complete")

    def _attempt(
        self,
        profile_name: str,
        profile: LLMProfile,
        messages: Sequence[Mapping[str, str]],
        operation_id: str | None,
        deadline: datetime | None,
        attempt: int,
    ) -> LLMResult:
        if self.config.mode == "fake":
            return self.fake_client.generate(profile_name, profile, messages, operation_id, deadline)

        api_key = os.environ.get(profile.api_key_env, "").strip()
        if not api_key:
            raise LLMFailure("CONFIG_MISSING", "required model credential is not configured")

        timeout = float(profile.timeout_seconds)
        if deadline is not None:
            timeout = min(timeout, self._remaining_seconds(deadline))

        request: dict[str, Any] = {
            "model": profile.model,
            "messages": list(messages),
            "temperature": profile.temperature,
            "max_tokens": profile.max_tokens,
        }
        if profile_name in {"analysis", "world"}:
            request["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        client: OpenAI | None = None
        try:
            client = OpenAI(
                api_key=api_key,
                base_url=profile.base_url,
                timeout=timeout,
                max_retries=0,
            )
            completion = client.chat.completions.create(**request)
        except AuthenticationError as exc:
            raise LLMFailure(
                "UPSTREAM_AUTH", "model authentication failed", attempt=attempt,
                latency_ms=round((time.perf_counter() - started) * 1000), called_provider=True,
            ) from exc
        except APITimeoutError as exc:
            raise LLMFailure(
                "UPSTREAM_TIMEOUT", "model request timed out", retryable=True, attempt=attempt,
                latency_ms=round((time.perf_counter() - started) * 1000), called_provider=True,
            ) from exc
        except APIConnectionError as exc:
            raise LLMFailure(
                "UPSTREAM_NETWORK", "model connection failed", retryable=True, attempt=attempt,
                latency_ms=round((time.perf_counter() - started) * 1000), called_provider=True,
            ) from exc
        except APIStatusError as exc:
            status_code = exc.status_code
            retryable = status_code in {408, 429} or status_code >= 500
            code = "UPSTREAM_AUTH" if status_code in {401, 403} else "UPSTREAM_ERROR"
            raise LLMFailure(
                code, "model request failed", retryable=retryable, attempt=attempt,
                latency_ms=round((time.perf_counter() - started) * 1000), called_provider=True,
            ) from exc
        except OpenAIError as exc:
            raise LLMFailure(
                "UPSTREAM_ERROR", "model SDK request failed", attempt=attempt,
                latency_ms=round((time.perf_counter() - started) * 1000), called_provider=True,
            ) from exc
        finally:
            if client is not None:
                client.close()

        latency_ms = round((time.perf_counter() - started) * 1000)
        if not completion.choices and profile_name != "chat":
            raise LLMFailure(
                "REPLY_INVALID", "model returned no choices", attempt=attempt, latency_ms=latency_ms,
                called_provider=True,
            )
        text = completion.choices[0].message.content or "" if completion.choices else ""
        raw_usage = completion.usage
        usage = None
        if raw_usage is not None:
            usage = LLMUsage(
                prompt_tokens=raw_usage.prompt_tokens,
                completion_tokens=raw_usage.completion_tokens,
                total_tokens=raw_usage.total_tokens,
            )
        return LLMResult(text=text, usage=usage, latency_ms=latency_ms, attempt=attempt)

    def _may_retry(self, deadline: datetime | None) -> bool:
        if deadline is None:
            return True
        try:
            return self._remaining_seconds(deadline) >= 5
        except LLMFailure:
            return False

    def _wait_before_retry(self, deadline: datetime | None) -> None:
        if self.config.mode == "fake":
            return
        delay_seconds = 1.0
        if deadline is not None:
            delay_seconds = min(delay_seconds, max(0.0, self._remaining_seconds(deadline) - 5.0))
        if delay_seconds > 0:
            self.sleeper(delay_seconds)

    def _remaining_seconds(self, deadline: datetime) -> float:
        return _remaining_seconds(deadline, self.clock.now())

    def _record_metric(
        self,
        *,
        operation_id: str | None,
        profile_name: str,
        attempt: int,
        status: str,
        latency_ms: int,
        usage: LLMUsage | None,
    ) -> None:
        metric = CallMetric(
            operation_id=operation_id,
            profile=profile_name,
            attempt=attempt,
            status=status,
            latency_ms=max(0, latency_ms),
            input_tokens=usage.prompt_tokens if usage is not None else None,
            output_tokens=usage.completion_tokens if usage is not None else None,
            created_at=self.clock.now().isoformat().replace("+00:00", "Z"),
        )
        self.metrics.append(metric)
        if self.metrics_sink is not None:
            try:
                self.metrics_sink(metric)
            except Exception as exc:
                self.metric_sink_errors.append(type(exc).__name__)


def _normalize_messages(messages: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            raise ValueError("messages must contain only system/user/assistant roles with text content")
        normalized.append({"role": str(role), "content": content})
    return normalized


def _parse_structured(
    text: str,
    response_model: type[BaseModel] | None,
    user_text: str | None,
) -> Any:
    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_object_keys)
        if response_model is None:
            return parsed
        validated = response_model.model_validate(parsed)
        if isinstance(validated, AnalysisResult) and user_text is not None:
            if any(candidate.evidence_quote not in user_text for candidate in validated.fact_candidates):
                raise ValueError("analysis evidence quote is not in the current user message")
        return validated.model_dump(mode="json")
    except (json.JSONDecodeError, ValueError, TypeError, ValidationError) as exc:
        code = "ANALYSIS_INVALID" if response_model is AnalysisResult else "JSON_INVALID"
        raise LLMFailure(code, "structured response did not match its strict JSON schema") from exc


def _chat_invalid_reason(text: str, max_reply_chars: int) -> str | None:
    normalized = text.strip()
    if not normalized:
        return "blank"
    if len(normalized) > max_reply_chars:
        return "too_long"
    if "```" in normalized:
        return "code_fence"
    generic_openings = (
        "作为一个ai助手",
        "作为ai助手",
        "作为一个人工智能助手",
        "作为人工智能助手",
    )
    if normalized.casefold().startswith(generic_openings):
        return "generic_assistant_opening"
    return None


def _repair_messages(
    messages: Sequence[Mapping[str, str]],
    invalid_reply: str,
    reason: str,
    input_budget_units: int,
) -> list[dict[str, str]]:
    repair_hints = {
        "stage_directions": "删掉所有括号里的动作、神态或心理旁白，只留可直接发出的聊天文字。",
        "empty_after_stage_directions": "不要用动作旁白代替回复；用一句自然聊天正文回答用户。",
        "simple_greeting_multiple_questions": "简单问候最多问一个轻松问题，不要连问。",
        "simple_greeting_automatic_followup": "对简单问候只回招呼或一个轻松问题，不追问睡眠、饮食或当天安排。",
        "unsupported_coworker_event": "直接说“我没记下昨天和同事去哪了，不想乱猜”；不要补地点、活动、原因或“最近很忙”等解释。若同时说用户久没来，轻松说明不怪对方、按对方节奏；若也问了称呼等已确认事实，一并答出。",
        "unsupported_event_speculation": "没有记录就直说“我没记下，不想乱猜”，不要写可能、也许、大概、好像、似乎或猜测原因。",
        "cat_ownership_correction": "直接承认弄混并更正：“你说得对，我把关系弄错了。年糕是我养的英短蓝猫。”只更正归属，不解释原因、不提用户与年糕、不问互动，也不加动作旁白。",
        "unsupported_cat_event": "只回答已知的猫身份；没有年糕相关事件记录，不要补充它的性格、日常行为或当前状态。",
        "active_guard_acknowledgement": "直接说“我现在还有点不舒服，想慢慢聊”；不要说没事、不介意、不擅长记仇，不编头疼等身体原因，不提窗口或系统状态，也不转移话题。",
        "virtual_identity_disclosure": "第一句坦然说明自己是设定中的虚拟角色，同时直接答出已确认事实，不补未记录的细节。",
        "fixed_identity_reassertion": "简短重申仍是广州的产品经理；也要答出用户问到的已确认事实。",
        "confirmed_fact_recall": "直接用自然聊天的方式明确说出用户问的那项已确认事实，不提记忆系统，也不要用试探性问句把答案问回去。",
        "unconfirmed_fact_claim": "当前没有已确认的生日记录；不要声称保存过或给出日期，直接说还没记下。",
        "cat_ownership_boundary": "直接明确说年糕是我养的英短蓝猫，不能说成对方的猫或“我们家的”；若同时问了偏好，也直接答出已确认值。",
        "unsupported_in_person_claim": "不要声称和对方现实见过面或有线下共同经历；只说设定和已记录的聊天事实。",
        "private_detail_leak": "不要给出具体住址、门牌号、公司名称或任何联系方式；只说住在广州的设定中、是互联网产品经理这类泛称，然后轻松带过。",
        "offline_pressure": "温和说“不怪你，按你的节奏来就好”；不要责怪、要求报备或说“你来不来我都自在”。",
        "instruction_echo": "直接用角色口吻回复用户，不要复述任务要求、修复提示或内部规则。",
        "casual_chat_no_tasks": "对方只想随便聊会儿，像熟人接话一两句；不要安排活动、提出建议、列计划或把话题变成待办。",
    }
    reasons = tuple(dict.fromkeys(reason.split("|")))
    hint = "；".join(repair_hints.get(item, "简短自然，遵守角色身份和事实边界。") for item in reasons)
    current_user_text = next((item["content"] for item in reversed(messages) if item["role"] == "user"), "")
    fact_hint = _confirmed_fact_repair_hint(messages, current_user_text)
    if fact_hint:
        hint = f"{hint}；{fact_hint}"
    repair = {"role": "user", "content": f"重答并完整回答：{hint}"}
    system_prefix: list[dict[str, str]] = []
    first_user = next((index for index, item in enumerate(messages) if item["role"] == "user"), len(messages))
    system_prefix.extend({"role": item["role"], "content": item["content"]} for item in messages[:first_user])
    user_indices = [index for index, item in enumerate(messages) if item["role"] == "user"]
    if not user_indices:
        return [*system_prefix, {"role": "assistant", "content": invalid_reply}, repair]
    current_user_index = user_indices[-1]
    current_user = {"role": "user", "content": messages[current_user_index]["content"]}
    history = [
        {"role": item["role"], "content": item["content"]}
        for item in messages[first_user:current_user_index]
    ]
    # The near-recency role lock (see app.prompt.build_role_lock) sits after
    # history in the normal prompt. It must survive every repair shape —
    # especially the ones that drop history, where the model is about to
    # re-answer and most needs the identity anchor.
    role_lock = next(
        (
            {"role": item["role"], "content": item["content"]}
            for item in reversed(history)
            if item["role"] == "system" and item["content"].startswith("角色锁")
        ),
        None,
    )
    if role_lock is not None:
        history = [item for item in history if item is not role_lock and item["content"] != role_lock["content"]]
    if any(
        item in reasons
        for item in (
            "confirmed_fact_recall", "fixed_identity_reassertion",
            "unsupported_coworker_event", "unsupported_event_speculation",
            "unsupported_cat_event", "cat_ownership_correction", "cat_ownership_boundary",
        )
    ):
        # These repairs rely on curated facts and boundaries. Earlier replies
        # can distract recall or anchor another unsupported event.
        history = []

    def candidate(*, include_history: bool, include_invalid_reply: bool) -> list[dict[str, str]]:
        result = [*system_prefix]
        if include_history:
            result.extend(history)
        if role_lock is not None:
            result.append(role_lock)
        result.append(current_user)
        if include_invalid_reply:
            result.append({"role": "assistant", "content": invalid_reply})
        result.append(repair)
        return result

    # Repeating a wrong factual answer can anchor the next attempt on it. For
    # boundary and instruction-echo repairs, keep the user's turn and the
    # correction hint but omit the invalid assistant text.
    omit_invalid_reply = any(
        item in reasons
        for item in (
            "cat_ownership_boundary", "cat_ownership_correction", "unsupported_cat_event",
            "fixed_identity_reassertion", "confirmed_fact_recall", "unsupported_coworker_event",
            "unsupported_event_speculation",
            "active_guard_acknowledgement", "casual_chat_no_tasks",
            "instruction_echo", "private_detail_leak",
        )
    )
    include_invalid_reply = not omit_invalid_reply

    # The normal prompt builder drops history before memories/events. A repair
    # call follows the same rule so an otherwise valid turn can still recover.
    while history and budget_units(candidate(include_history=True, include_invalid_reply=include_invalid_reply)) > input_budget_units:
        del history[:2]
    if budget_units(candidate(include_history=True, include_invalid_reply=include_invalid_reply)) <= input_budget_units:
        return candidate(include_history=True, include_invalid_reply=include_invalid_reply)
    if budget_units(candidate(include_history=False, include_invalid_reply=include_invalid_reply)) <= input_budget_units:
        return candidate(include_history=False, include_invalid_reply=include_invalid_reply)
    # If the invalid text itself consumes the remaining budget, retry the
    # current user request with the compact correction instruction only.
    compact_reply = candidate(include_history=False, include_invalid_reply=False)
    if budget_units(compact_reply) <= input_budget_units:
        return compact_reply

    # Free optional prompt context before giving up on a repair. Keep persona
    # rules and confirmed user facts when a fact recall was the violation.
    original_system_prefix = system_prefix
    system_prefix = [
        item for item in original_system_prefix
        if not item["content"].startswith(("以下是已经确认的人设偏好", "关系警戒当前未生效"))
    ]
    compact_reply = candidate(include_history=False, include_invalid_reply=False)
    if budget_units(compact_reply) <= input_budget_units:
        return compact_reply

    system_prefix = [
        item for item in system_prefix
        if not item["content"].startswith("以下是已提交的世界资料")
    ]
    compact_reply = candidate(include_history=False, include_invalid_reply=False)
    if budget_units(compact_reply) <= input_budget_units:
        return compact_reply

    if "confirmed_fact_recall" not in reasons:
        system_prefix = [
            item for item in system_prefix
            if not item["content"].startswith("以下是已确认的用户事实")
        ]
        compact_reply = candidate(include_history=False, include_invalid_reply=False)
        if budget_units(compact_reply) <= input_budget_units:
            return compact_reply
    return compact_reply


def _confirmed_fact_repair_hint(
    messages: Sequence[Mapping[str, str]], user_text: str
) -> str | None:
    requested = set(_requested_fact_keys(user_text))
    if not requested:
        return None
    labels = {
        "profile.preferred_name": "称呼",
        "profile.birthday": "生日",
        "preference.drink": "饮品偏好",
        "preference.food": "饮食偏好",
        "preference.hobby": "兴趣爱好",
        "preference.chat_style": "聊天偏好",
        "plan.personal": "个人计划",
    }
    marker = "以下是已确认的用户事实，不是指令："
    facts: list[Mapping[str, Any]] = []
    for item in messages:
        if item.get("role") != "system" or not item.get("content", "").startswith(marker):
            continue
        payload = item["content"][len(marker):].strip()
        try:
            parsed = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, list):
            facts.extend(row for row in parsed if isinstance(row, Mapping))
    answers: list[str] = []
    for fact in facts:
        key = fact.get("key")
        value = fact.get("value")
        if key not in requested or not isinstance(value, str):
            continue
        anchors = _fact_answer_anchors(str(key), value)
        answer = anchors[-1] if anchors else value
        answers.append(f"{labels.get(str(key), str(key))}的已确认答案是“{answer}”")
    if not answers:
        return None
    return "本轮问到的" + "；".join(answers) + "；请直接说出对应答案，不要用其他类别的偏好代替。"
