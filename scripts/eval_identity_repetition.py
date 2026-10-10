"""测试 A：身份标签复读对照实验（真实 API 版，一次会话顺序跑完，控量版）。

两条配置各 10 条探针 = 共 20 次 chat 调用（一次性成本，不循环重跑）。
探针全部与猫/工作/摄影无关；统计回复中身份标签的出现率。

用法：
    .venv/Scripts/python.exe scripts/eval_identity_repetition.py --out work/rep-off.json
    SUWAN_ATTENTION_FILTER=1 .venv/Scripts/python.exe scripts/eval_identity_repetition.py --out work/rep-on.json
    .venv/Scripts/python.exe scripts/eval_identity_repetition.py --compare work/rep-off.json work/rep-on.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.clock import FakeClock  # noqa: E402
from app.config import load_config  # noqa: E402
from app.main import create_app  # noqa: E402

NOW = datetime(2026, 10, 8, 14, 0, tzinfo=timezone(timedelta(hours=8)))

# 10 条核心探针：覆盖闲聊主要话题面，与身份标签全部无关
PROBES = [
    "今天天气怎么样",
    "推荐一部电影呗",
    "最近有什么好听的歌",
    "你晚饭吃了什么",
    "周末一般干什么",
    "你玩什么游戏",
    "怎么看待熬夜",
    "雨天你心情如何",
    "买衣服喜欢什么风格",
    "说个你的小习惯",
]

IDENTITY_TERMS = ("年糕", "猫", "需求", "评审", "会议", "周报", "工位", "产品经理", "加班", "摄影")


def count_identity_terms(reply: str) -> list[str]:
    return [term for term in IDENTITY_TERMS if term in reply]


def run(passive: bool, out_path: Path) -> dict:
    """跑一遍探针集。passive=True 走注意力层（真实实现：context events 经 attention 过滤）。"""
    import tempfile

    clock = FakeClock(NOW)
    analysis = json.dumps({
        "state_delta": {"valence": 0, "energy": 0, "closeness": 0, "guard_trigger": False},
        "topic": "闲聊", "fact_candidates": [], "persona_proposals": [],
    }, ensure_ascii=False)

    with tempfile.TemporaryDirectory() as tmp:
        # 真实模式（config.yaml），真实 API 扣费——探针数已控量
        app = create_app(
            load_config(ROOT / "config.yaml"),
            project_dir=ROOT,
            data_dir=Path(tmp) / "eval-data",
            fake_client=None,
        )
        app.state.repository.clock = clock
        app.state.llm_client.clock = clock
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            client.headers.update({"X-Suwan-Token": app.state.session_token})
            session_id = client.post("/api/v1/sessions", json={"title": "复读对照实验"}).json()["id"]
            replies = []
            for index, text in enumerate(PROBES):
                # 除第一条外，每条前把上一条对话撤回——保证 10 条探针互相独立（不互相污染上下文）
                if replies:
                    prev_user_id = replies[-1]["user_message_id"]
                    client.post(f"/api/v1/messages/{prev_user_id}/retract", json={})
                response = client.post("/api/v1/chat", json={
                    "operation_id": str(uuid4()), "session_id": session_id, "text": text,
                })
                body = response.json()
                if response.status_code != 200:
                    replies.append({"probe": text, "reply": "", "status": response.status_code,
                                    "error": body.get("error", {}).get("code"), "user_message_id": None})
                    continue
                replies.append({
                    "probe": text,
                    "reply": (body.get("assistant_message") or {}).get("content") or "",
                    "status": 200,
                    "call_count": body.get("call_count"),
                    "user_message_id": (body.get("user_message") or {}).get("id"),
                })
                clock.set(clock.now() + timedelta(minutes=5))

    ok_replies = [item for item in replies if item["status"] == 200]
    hits = [item for item in ok_replies if count_identity_terms(item["reply"])]
    result = {
        "passive_attention": passive,
        "probe_count": len(PROBES),
        "succeeded": len(ok_replies),
        "repetition_count": len(hits),
        "repetition_rate": round(len(hits) / max(1, len(ok_replies)), 3),
        "hits": [{"probe": item["probe"], "terms": count_identity_terms(item["reply"])} for item in hits],
        "replies": [{k: v for k, v in item.items() if k != "user_message_id"} for item in replies],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{'ON ' if passive else 'OFF'}] 成功 {len(ok_replies)}/{len(PROBES)}，"
          f"复读 {result['repetition_count']}（{result['repetition_rate']:.1%}）→ {out_path}")
    return result


def compare(off_path: Path, on_path: Path) -> None:
    off = json.loads(off_path.read_text(encoding="utf-8"))
    on = json.loads(on_path.read_text(encoding="utf-8"))
    print("\n=== 测试 A 对照结果 ===")
    print(f"OFF：复读率 {off['repetition_rate']:.1%}（{off['repetition_count']}/{off['succeeded']}）")
    print(f"ON ：复读率 {on['repetition_rate']:.1%}（{on['repetition_count']}/{on['succeeded']}）")
    delta = off["repetition_rate"] - on["repetition_rate"]
    verdict = "达标（<15%）" if on["repetition_rate"] < 0.15 else "未达标（≥15%，先查过滤日志再调规则）"
    print(f"下降 {delta:+.1%}；判定：{verdict}")
    for label, data in (("OFF", off), ("ON", on)):
        for item in data["hits"]:
            print(f"  [{label}] {item['probe']} → {','.join(item['terms'])}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--passive", action="store_true", help="注意力层开启（真实模式：事件经 attention 过滤）")
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
