# -*- coding: utf-8 -*-
"""
中国宏观经济仪表盘 · 数据管线 Runner（Step 1: 取数快照）
用法：
    python dashboard_runner.py                 # 全量取数，生成快照目录
    python dashboard_runner.py --only gdp_quarter,cpi_latest   # 仅跑指定查询
输出：
    outputs/pipeline/data/YYYY-MM-DD_HHMM/raw_<id>.json   各查询原始返回
    outputs/pipeline/data/YYYY-MM-DD_HHMM/snapshot.json   关键指标抽取摘要
退出码：0=全部成功  2=凭证失效(需重新获取)  3=部分查询失败
说明：
    - 单条查询为空自动换措辞重试 ≤2 次；
    - 凭证由 ~/.workbuddy/skills/neodata-financial-search 的 token 缓存提供，
      若提示 TOKEN_EXPIRED/TOKEN_MISSING，请让助手重新走 connect_cloud_service 流程。
"""
import json, os, re, subprocess, sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
SKILL_DIR = Path.home() / ".workbuddy" / "skills" / "neodata-financial-search"
QUERY_PY = SKILL_DIR / "scripts" / "query.py"
QUERIES = BASE / "queries.json"

# 每条查询的备用措辞（为空时重试）
REPHRASES = {
    "gdp_quarter": ["中国最新季度GDP同比增速和当季GDP总量"],
    "cpi_latest": ["居民消费价格指数CPI同比涨幅最新月度数据"],
    "m2_social": ["最新月份金融统计数据报告 M2 社会融资规模 人民币贷款"],
    "unemployment": ["城镇调查失业率 统计局发布 最新月份"],
    "housing_price": ["70个大中城市商品住宅销售价格 最新月份 变动情况"],
}


def run_query(text):
    """调用 NeoData 查询，返回 (是否成功, 原始dict或错误字符串)"""
    try:
        p = subprocess.run(
            [sys.executable, str(QUERY_PY), "--query", text],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            cwd=str(SKILL_DIR),
        )
        out = p.stdout.strip()
    except Exception as e:
        return False, f"subprocess error: {e}"
    if "TOKEN_EXPIRED" in out or "TOKEN_MISSING" in out:
        return "TOKEN", out
    try:
        data = json.loads(out[out.index("{"):])
    except Exception:
        return False, out[:500]
    if data.get("code") in ("401", "403", "40101"):
        return "TOKEN", out[:300]
    return True, data


def extract_tables(data):
    """从 apiRecall 提取 markdown 表格文本"""
    rec = (data.get("data") or {}).get("apiData", {}).get("apiRecall", []) or []
    return [r.get("content", "") for r in rec if r.get("content")]


def extract_docs(data, limit=3):
    """从 docRecall 提取标题+摘要"""
    docs = (data.get("data") or {}).get("docData", {}).get("docRecall", []) or []
    out = []
    for g in docs:
        for d in g.get("docList", []):
            out.append({"title": d.get("title", ""), "excerpt": (d.get("content", "") or "")[:300]})
            if len(out) >= limit:
                return out
    return out


def main():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))

    conf = json.loads(QUERIES.read_text(encoding="utf-8"))
    ts = datetime.now()
    out_dir = BASE / "data" / ts.strftime("%Y-%m-%d_%H%M")
    out_dir.mkdir(parents=True, exist_ok=True)

    snapshot = {"run_time": ts.isoformat(timespec="seconds"), "queries": {}}
    token_problem, failures = False, 0

    for q in conf["queries"]:
        qid = q["id"]
        if only and qid not in only:
            continue
        texts = [q["query"]] + REPHRASES.get(qid, [])
        result, status = None, None
        for attempt, text in enumerate(texts[:3]):
            ok, res = run_query(text)
            if ok == "TOKEN":
                token_problem = True
                break
            if ok:
                tables = extract_tables(res)
                docs = extract_docs(res)
                if tables or docs:
                    result = res
                    (out_dir / f"raw_{qid}.json").write_text(
                        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
                    status = "ok"
                    break
            status = "empty"
        if token_problem:
            break
        entry = {"status": status}
        if result:
            entry["tables"] = len(extract_tables(result))
            entry["docs"] = len(extract_docs(result))
        snapshot["queries"][qid] = entry
        print(f"[{status:>5}] {qid}")
        if status != "ok":
            failures += 1

    (out_dir / "snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")

    if token_problem:
        print("\n!! 凭证失效：请重新获取凭证后重试（connect_cloud_service → --save-token）。")
        return 2
    ok_n = sum(1 for v in snapshot["queries"].values() if v["status"] == "ok")
    print(f"\n快照目录: {out_dir}\n成功 {ok_n}/{len(snapshot['queries'])}")
    return 0 if failures == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
