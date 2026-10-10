"""CLR 展示层：她独立生活的摘要 API（供"晚晚的生活"面板）。"""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import JSONResponse

from app.routes import router

_SHANGHAI = ZoneInfo("Asia/Shanghai")


@router.get("/life/summary")
def life_summary(request: Request) -> dict:
    """她在不聊天的日子里做了什么的摘要。

    只读聚合，不改任何状态：当前活动、今日时间线（含已过/进行/未到）、
    近期自由决策、目标进度。失败静默降级为空骨架。
    """
    repository = request.app.state.repository
    config = request.app.state.config
    try:
        now = repository.clock.now()
        local_now = now.astimezone(_SHANGHAI)
        day_type = "weekend" if local_now.weekday() >= 5 else "workday"
        schedule = repository.list_schedule(day_type)

        minutes_now = local_now.hour * 60 + local_now.minute
        timeline = []
        for row in schedule:
            start = _hhmm(row["start_time"])
            end = _hhmm(row["end_time"])
            if end <= start:  # 跨午夜段（如 23:30-07:10）
                if minutes_now >= end and minutes_now < start:
                    state = "past"
                elif minutes_now >= start or minutes_now < end:
                    state = "current"
                else:
                    state = "upcoming"
            elif minutes_now >= end:
                state = "past"
            elif minutes_now >= start:
                state = "current"
            else:
                state = "upcoming"
            timeline.append({
                "start": row["start_time"],
                "end": row["end_time"],
                "activity": row["activity"],
                "state": state,
            })

        with repository.connection() as connection:
            activity_row = connection.execute(
                "SELECT current_activity FROM character_state WHERE character_id = 'suwan'"
            ).fetchone()
        current_activity = activity_row["current_activity"] if activity_row else None

        intents = repository.recent_action_intents(limit=10)
        for intent in intents:
            intent["label"] = _ACTION_LABELS.get(intent["chosen_action"], intent["chosen_action"])

        goals = [
            {
                "title": goal["title"],
                "detail": goal.get("detail"),
                "progress_step": goal["progress_step"],
                "status": goal["status"],
            }
            for goal in repository.list_goals(status=None)
        ]
        return {
            "current_activity": current_activity,
            "local_time": local_now.isoformat(),
            "day_type": day_type,
            "timeline": timeline,
            "recent_intents": intents,
            "goals": goals,
        }
    except Exception:
        return JSONResponse(
            status_code=200,
            content={"current_activity": None, "local_time": None, "day_type": None,
                     "timeline": [], "recent_intents": [], "goals": [], "degraded": True},
        )


_ACTION_LABELS = {
    "go_photo_walk": "出门拍照了",
    "edit_photos": "在整理照片",
    "watch_old_movie": "在看老电影",
    "brew_coffee": "手冲了一次咖啡",
    "read": "窝着看书",
    "rest": "瘫着休息",
    "noop": "哪儿也没去，就待着",
}


def _hhmm(value: str) -> int:
    hours, _, minutes = value.partition(":")
    return int(hours) * 60 + int(minutes)


# ── 年糕快报（她不在你身边的时侯，本喵都看见了）──────────────

_NIANBAO_ACTIONS = {
    "go_photo_walk": ("出门拍照了，扔下本喵看家", "尾巴出镜率 +1"),
    "edit_photos": ("整理照片整理到一半就发呆", "哼，说好的作品集呢"),
    "watch_old_movie": ("看老电影看到一半睡着了", "本喵占了半个沙发，她没意见"),
    "brew_coffee": ("又手冲失败了", "速溶之神的宿命"),
    "read": ("窝着看书没理本喵", "书有那么好看吗"),
    "rest": ("瘫着休息了一整天", "瘫着倒是带上本喵一起"),
}

_SCHEDULE_TONE = {
    "需求": ("被需求折磨的一天", "本喵蹲她键盘上都没敢动"),
    "开会": ("开了一天会", "本喵听见她叹气叹了三次"),
    "翻车": ("厨房又传来了不妙的动静", "本喵选择了战略撤退"),
    "深夜电台": ("深夜又开始碎碎念", "本喵在旁边打着呼噜监工"),
    "通勤": ("一大早就挤地铁去了", "本喵还在被窝里"),
    "陪年糕": ("回家陪本喵了", "本喵勉为其难地蹭了她一下"),
    "拍照": ("背着相机出门溜达", "本喵在窗边目送"),
    "赖床": ("赖床赖到本喵都看不下去了", "本喵用尾巴扫她脸叫服务，无效"),
    "磨蹭": ("磨磨蹭蹭一上午才吃上第一顿", "本喵饿的呼噜声她装没听见"),
    "老电影": ("看老电影看一半睡着了", "本喵接手了她的暖腿位"),
    "手冲": ("又摆弄她的手冲家伙事儿了", "十次里六七次，本喵都替豆子心疼"),
    "画画": ("窝着画画没理本喵", "本喵踩了两脚画纸表示抗议"),
    "外卖": ("点的又是那几样外卖", "黄焖鸡的味道本喵都背下来了"),
    "睡觉": ("睡觉去了", "本喵负责夜巡"),
}


def _tone_for(text: str) -> tuple[str, str] | None:
    for needle, pair in _SCHEDULE_TONE.items():
        if needle in text:
            return pair
    return None


def nianbao_report(summary: dict) -> dict | None:
    """把生活摘要翻译成一条年糕视角的快报；没料可报时返回 None。

    原则：猫只报告"看得见的行为"，不解释她的内心（猫不懂人的心思）。
    """
    intents = summary.get("recent_intents") or []
    timeline = [row for row in (summary.get("timeline") or []) if row.get("state") == "past"]
    goals = [goal for goal in (summary.get("goals") or []) if goal.get("status") == "active"]
    if not intents and not timeline:
        return None

    lines: list[str] = []
    # 自由决策优先（最有"生活感"的料）
    for intent in intents[:2]:
        pair = _NIANBAO_ACTIONS.get(intent.get("chosen_action"))
        if pair:
            lines.append(f"{pair[0]}，{pair[1]}。")
        elif intent.get("goal_title"):
            lines.append(f"又在折腾「{intent['goal_title']}」。")
    # 日程活动补齐（最多取今天已过的 2 段不同 tone）
    seen_pairs = set()
    for row in reversed(timeline):
        if len(lines) >= 2:
            break
        if row.get("state") != "past":  # 只播报今天已发生的时段
            continue
        pair = _tone_for(row.get("activity") or "")
        if pair and pair[0] not in seen_pairs:
            seen_pairs.add(pair[0])
            lines.append(f"{pair[0]}，{pair[1]}。")
    # 目标进度压轴
    photo = next((goal for goal in goals if "作品集" in goal.get("title", "")), None)
    if photo and photo.get("progress_step", 0) > 0:
        lines.append(f"作品集推进到第 {photo['progress_step']} 步了。本喵的尾巴贡献了出镜率。")
    if not lines:
        return None
    return {"lines": lines[:3]}


@router.get("/life/nianbao")
def nianbao_report_api(request: Request) -> dict:
    """年糕快报：会话首条消息后前端拉取；无料时 available=false。"""
    try:
        summary = life_summary(request)
        report = nianbao_report(summary)
        if not report:
            return {"available": False}
        return {"available": True, "lines": report["lines"]}
    except Exception:
        return {"available": False}
