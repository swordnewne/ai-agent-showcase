#!/usr/bin/env python3
"""Generate once, reuse complete reports, and confirm delivery to Niko's main chat."""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = os.environ.get("NIKO_WORKSPACE") or str(Path(__file__).resolve().parents[3])
sys.path.insert(0, str(Path(WORKSPACE) / "showcase" / "src" / "financial"))

from preopen_runs import CST, completed_run, get_run, get_today_runs, mark_delivery, record_run, today_str
import preopen_report
from niko_finance_handoff import apply_receipt, load_packet

LOCK_PATH = Path(WORKSPACE) / "data" / ".preopen_guard.lock"
SESSION_KEY = "agent:main:main"


def _gateway_call(method: str, params: dict) -> dict:
    proc = subprocess.run(
        ["openclaw", "gateway", "call", method, "--params", json.dumps(params, ensure_ascii=False), "--json"],
        cwd=WORKSPACE, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"{method} failed")
    return json.loads(proc.stdout)


def _text(message: dict) -> str:
    return "\n".join(item.get("text", "") for item in message.get("content", []) if item.get("type") == "text")


def _message_id(message: dict) -> str:
    return message.get("__openclaw", {}).get("id") or message.get("id")


def deliver(row: dict, recovery: bool = False) -> str:
    path = Path(row["report_path"]) if row.get("report_path") else None
    report = path.read_text(encoding="utf-8") if path else "报告生成失败，晚间任务将允许重试。"
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
    marker = f"finance-report:{row['id']}:{digest}"
    message_id = row.get("delivery_message_id")

    # Recover an acknowledged write or a write interrupted before its local receipt.
    if recovery and not message_id:
        history = _gateway_call("chat.history", {"sessionKey": SESSION_KEY, "limit": 100})
        for message in history.get("messages", []):
            if message.get("role") == "assistant" and marker in _text(message):
                message_id = _message_id(message)
                if message_id:
                    break

    if not message_id:
        sections = report.split("\n## ")
        summary = sections[0]
        for section in sections[1:]:
            if section.startswith(("二、", "四、", "五、", "七、", "八、")):
                summary += "\n## " + section
        message = f"<!-- {marker} -->\n状态：**{row['status']}**\n\n{summary}\n\n报告：{path or '(未生成文件)'}"
        response = _gateway_call("chat.inject", {
            "sessionKey": SESSION_KEY, "message": message, "label": "金融任务报告",
        })
        if not response.get("ok") or not response.get("messageId"):
            raise RuntimeError("chat.inject 未确认写入")
        message_id = response["messageId"]
        mark_delivery(row["run_date"], "pending", run_id=row["id"], message_id=message_id)

    readback = _gateway_call("chat.message.get", {
        "sessionKey": SESSION_KEY, "messageId": message_id, "maxChars": 600,
    })
    message = readback.get("message", {})
    if not readback.get("ok") or _message_id(message) != message_id or marker not in _text(message):
        raise RuntimeError(f"主会话消息读回未确认: {message_id}")
    mark_delivery(row["run_date"], "delivered", run_id=row["id"], message_id=message_id)
    print(f"[DELIVERED] run_id={row['id']} message_id={message_id} path={path}")
    return message_id


def cmd_status() -> int:
    date = today_str()
    row = completed_run(date)
    print(f"日期: {date}\n已完成: {bool(row)}\n报告路径: {row['report_path'] if row else '(无)'}")
    for slot, status, path, delivery_status, created in get_today_runs(date):
        print(f"  [{slot}] {status} / 投递={delivery_status} @ {created} / {path}")
    return 0


def cmd_apply_niko(args) -> int:
    packet = load_packet(args.packet)
    path = apply_receipt(args.packet, args.receipt)
    row = get_run(packet.get("run_id"))
    if row is not None:
        mark_delivery(row["run_date"], "pending", run_id=row["id"])
    if row is not None:
        deliver(get_run(row["id"]), recovery=False)
    print(f"[NIKO_APPLIED] report={path}")
    return 0


def execute(args) -> int:
    date = today_str()
    row = get_run(args.run_id) if args.deliver_only else completed_run(date)
    if args.deliver_only and not row:
        raise ValueError(f"运行记录不存在: {args.run_id}")
    if row and not args.deliver_only and args.slot == "08:15" and row.get("delivery_status") == "delivered":
        cutoff = datetime.fromisoformat(f"{date}T08:15:00").replace(tzinfo=CST)
        created = datetime.fromisoformat(row["created_at"]).replace(tzinfo=timezone.utc)
        if datetime.now(CST) >= cutoff and created < cutoff:
            print(f"[REFRESH] run_id={row['id']} 早于今日早间时点，重新采集")
            row = None
    reused = bool(row) and not args.force
    if not reused:
        print(f"[RUN] 生成报告 ({date}, slot={args.slot})")
        try:
            run_kwargs = {"slot": args.slot, "ignore_calendar": args.ignore_calendar}
            if getattr(args, "prepare_for_niko", False):
                run_kwargs["defer_niko"] = True
            result = preopen_report.run(**run_kwargs)
        except Exception as exc:
            run_id = record_run(date, args.slot, "failed")
            row = get_run(run_id)
            print(f"[GENERATION_FAILED] {type(exc).__name__}: {exc}", file=sys.stderr)
        else:
            if result.get("status") == "skipped":
                print(f"[SKIP] {result.get('reason', '非交易日')}")
                return 0
            row = get_run(result["run_id"])
            if not row:
                raise RuntimeError("报告运行记录未保存")
            print(f"[DONE] status={row['status']} path={row.get('report_path')}")
            if result.get("reason"):
                print(f"[NOTE] {result['reason']}")
            if getattr(args, "prepare_for_niko", False):
                print(f"[WAITING_NIKO] packet={result.get('packet_path')} hash={result.get('packet_hash')}")
                return 0
    else:
        print(f"[REUSE] run_id={row['id']} path={row.get('report_path')}")

    try:
        deliver(row, recovery=reused)
    except Exception as exc:
        mark_delivery(row["run_date"], "failed", run_id=row["id"])
        print(f"[DELIVERY_FAILED] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0 if row["status"] == "complete" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="金融报告生成与投递")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--slot", default="auto")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--ignore-calendar", action="store_true")
    parser.add_argument("--deliver-only", action="store_true")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--prepare-for-niko", action="store_true")
    parser.add_argument("--apply-niko", action="store_true")
    parser.add_argument("--packet")
    parser.add_argument("--receipt")
    args = parser.parse_args(argv)
    if args.status:
        return cmd_status()
    if args.apply_niko:
        if not args.packet or not args.receipt:
            parser.error("--apply-niko 需要 --packet 和 --receipt")
        return cmd_apply_niko(args)
    if args.deliver_only != (args.run_id is not None) or (args.deliver_only and args.force):
        parser.error("--deliver-only 与 --run-id 必须同时使用，且不能与 --force 同用")

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[LOCK] 已有金融任务运行，跳过重复执行")
            return 0
        return execute(args)


if __name__ == "__main__":
    sys.exit(main())
