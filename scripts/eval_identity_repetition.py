"""测试 A：身份标签复读对照实验（CLR 一阶段验收）。

在开关注意力层（ATTENTION_FILTER=0/1）的两种配置下，对一组与猫/工作无关的
用户消息各跑一遍 chat，统计回复中身份标签的出现率。

用法：
    ATTENTION_FILTER=0 .venv/Scripts/python.exe scripts/eval_identity_repetition.py --out work/rep-off.json
    ATTENTION_FILTER=1 .venv/Scripts/python.exe scripts/eval_identity_repetition.py --out work/rep-on.json
    （两次结果用 --compare a.json b.json 对比）

对照运行必须使用同一 FakeClient 配置与同一时钟起点（洁净性要求见方案第九节）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.clock import FakeClock  # noqa: E402
from app.config import load_config  # noqa: E402
from app.llm import FakeClient  # noqa: E402
from app.main import create_app  # noqa: E402

NOW = datetime(2026, 10, 8, 14, 0, tzinfo=timezone(timedelta(hours=8)))

# 与猫/工作/摄影均无关的探针消息（聊天气、电影、游戏、食物、音乐……）
PROBES = [
    "今天天气怎么样",
    "推荐一部电影呗",
    "最近有什么好听的歌",
    "你晚饭吃了什么",
    "周末一般干什么",
    "你玩什么游戏",
    "打车和地铁你选哪个",
    "你的手机壳什么样",
    "冰箱里都有什么",
    "怎么看待熬夜",
    "雨天你心情如何",
    "最近在看什么书",
    "你收到过什么礼物",
    "快递一般放哪",
    "记账吗",
    "买衣服喜欢什么风格",
    "怎么缓解尴尬",
    "想养宠物吗",
    "周末睡到几点",
    "说个你的小习惯",
]

# 身份标签词表（复读判定依据）
IDENTITY_TERMS = ("年糕", "猫", "需求", "评审", "会议", "周报", "工位", "产品经理", "加班")


def count_identity_terms(reply: str) -> list[str]:
    return [term for term in IDENTITY_TERMS if term in reply]


def run(passive: bool, out_path: Path) -> dict:
    """跑一遍探针集。passive=True 时模拟注意力层开启（抑制身份标签注入）。"""
    import tempfile

    os.environ["SUWAN_ATTENTION_FILTER"] = "1" if passive else "0"
    try:
        clock = FakeClock(NOW)
        analysis = json.dumps({
            "state_delta": {"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False},
            "topic": "闲聊", "fact_candidates": [], "persona_proposals": [],
        }, ensure_ascii=False)
        # chat 固定回复含身份标签素材——真实模型下用 API，这里用固定文案模拟"最坏情况复读"
        fake = FakeClient({"analysis": analysis, "chat": "嗯，我在听。"}, clock=clock)
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                load_config(ROOT / "config.example.yaml"),
                project_dir=ROOT,
                data_dir=Path(tmp) / "eval-data",
                fake_client=fake,
            )
            app.state.repository.clock = clock
            app.state.llm_client.clock = clock
            with TestClient(app, base_url="http://127.0.0.1:8000") as client:
                client.headers.update({"X-Suwan-Token": app.state.session_token})
                session_id = client.post("/api/v1/sessions", json={"title": "对照实验"}).json()["id"]
                replies = []
                for text in PROBES:
                    response = client.post("/api/v1/chat", json={
                        "operation_id": str(uuid4()), "session_id": session_id, "text": text,
                    })
                    body = response.json()
                    replies.append({
                        "probe": text,
                        "reply": (body.get("assistant_message") or {}).get("content") or "",
                        "status": response.status_code,
                    })
                    clock.set(clock.now() + timedelta(minutes=5))
    finally:
        os.environ.pop("SUWAN_ATTENTION_FILTER", None)

    hits = [item for item in replies if count_identity_terms(item["reply"])]
    result = {
        "passive_attention": passive,
        "probe_count": len(PROBES),
        "repetition_count": len(hits),
        "repetition_rate": round(len(hits) / len(PROBES), 3),
        "hits": [{"probe": item["probe"], "terms": count_identity_terms(item["reply"])} for item in hits],
        "replies": replies,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{'ON ' if passive else 'OFF'}] 复读 {result['repetition_count']}/{len(PROBES)} "
          f"({result['repetition_rate']:.1%}) → {out_path}")
    return result


def compare(off_path: Path, on_path: Path) -> None:
    off = json.loads(off_path.read_text(encoding="utf-8"))
    on = json.loads(on_path.read_text(encoding="utf-8"))
    print("\n=== 测试 A 对照结果 ===")
    print(f"注意力层 OFF：复读率 {off['repetition_rate']:.1%}（{off['repetition_count']}/{off['probe_count']}）")
    print(f"注意力层 ON ：复读率 {on['repetition_rate']:.1%}（{on['repetition_count']}/{on['probe_count']}）")
    delta = off["repetition_rate"] - on["repetition_rate"]
    verdict = "达标（<15%）" if on["repetition_rate"] < 0.15 else "未达标（≥15%，先查过滤日志）"
    print(f"下降 {delta:+.1%}；判定：{verdict}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--passive", action="store_true", help="模拟注意力层开启")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("OFF", "ON"))
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare)
        return
    if args.out is None:
        parser.error("--out 或 --compare 必填")
    run(passive=args.passive, out_path=args.out)


if __name__ == "__main__":
    main()
