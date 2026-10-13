"""Validate and apply Niko's final finance score receipt."""
import hashlib
import json
import math
from pathlib import Path


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _hash(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def load_packet(path):
    packet = json.loads(Path(path).read_text(encoding="utf-8"))
    supplied = packet.pop("packet_hash", None)
    if not supplied or supplied != _hash(packet):
        raise ValueError("评分交接包 hash 校验失败")
    packet["packet_hash"] = supplied
    if packet.get("schema_version") != "niko-finance-decision-v1":
        raise ValueError("不支持的评分交接包版本")
    return packet


def validate_receipt(packet, receipt):
    if receipt.get("schema_version") != "niko-finance-decision-v1":
        raise ValueError("Niko 回执版本不匹配")
    if receipt.get("packet_hash") != packet["packet_hash"]:
        raise ValueError("Niko 回执未引用当前评分交接包")
    if receipt.get("analyst") != "niko":
        raise ValueError("最终评分人必须是 niko")
    candidates = {
        str(item.get("code")): item
        for item in (packet.get("baseline_candidates") or {}).get("top", [])
    }
    decisions = receipt.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("Niko 回执没有 decisions")
    seen = set()
    for item in decisions:
        code = str(item.get("code", ""))
        if code not in candidates or code in seen:
            raise ValueError("Niko 回执包含未知或重复候选")
        seen.add(code)
        score = item.get("final_score")
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not math.isfinite(float(score)) or not 0 <= float(score) <= 100):
            raise ValueError(f"Niko 最终分数无效: {code}")
        if item.get("decision") not in ("priority", "observe", "watch", "avoid"):
            raise ValueError(f"Niko 结论无效: {code}")
        if not isinstance(item.get("reasons"), list) or not item["reasons"]:
            raise ValueError(f"Niko 缺少评分理由: {code}")


def apply_receipt(packet_path, receipt_path):
    packet = load_packet(packet_path)
    receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
    validate_receipt(packet, receipt)
    report_path = Path(packet["report_path"])
    if not report_path.is_file():
        raise FileNotFoundError(f"报告不存在: {report_path}")
    marker = f"<!-- niko-final-score:{packet['packet_hash']} -->"
    text = report_path.read_text(encoding="utf-8")
    if marker in text:
        return report_path
    decisions = sorted(
        receipt["decisions"],
        key=lambda item: (-float(item["final_score"]), str(item["code"])),
    )
    lines = [
        "", "---", "", "## 九、Niko最终评分", "", marker, "",
        "> DeepSeek 只负责新闻语义分析；以下最终分数由 Niko 结合新闻信号、BaoStock 行情特征和数据质量独立决定。上方固定公式分数仅作为客观特征基线。",
        "", "| 排名 | 股票 | Niko最终分数 | 结论 | 新闻调整 | 置信度 | 理由 | 风险 | 开盘验证 |",
        "|---:|---|---:|---|---:|---|---|---|---|",
    ]
    for rank, item in enumerate(decisions, 1):
        def cell(value):
            if isinstance(value, list):
                value = "；".join(str(v) for v in value)
            return str(value or "").replace("|", "\\|").replace("\n", " ")
        lines.append("| {} | {}（{}） | {:.1f} | {} | {} | {} | {} | {} | {} |".format(
            rank, cell(item.get("name")), cell(item.get("code")),
            float(item["final_score"]), cell(item.get("decision")),
            cell(item.get("news_adjustment", "未提供")),
            cell(item.get("confidence", "未提供")), cell(item.get("reasons")),
            cell(item.get("risks", "未提供")),
            cell(item.get("verification_conditions", "未提供")),
        ))
    lines.extend([
        "",
        "Niko 回执路径：" + str(receipt_path)
        + "；交接包路径：" + str(packet_path) + "。",
    ])
    temporary = report_path.with_suffix(".niko.tmp")
    temporary.write_text(text.rstrip() + "\n" + "\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from preopen_runs import _connect
        conn = _connect()
        conn.execute(
            "UPDATE preopen_runs SET status=?, delivery_status='pending' WHERE id=?",
            (packet.get("base_status", "complete"), packet.get("run_id")),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Report application remains verifiable even when the local run ledger is unavailable.
        pass
    return report_path
