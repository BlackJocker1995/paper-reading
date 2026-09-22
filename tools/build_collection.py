#!/usr/bin/env python3
"""把 fetch_arxiv.py 的原始抓取 + 逐篇标注合成合集的公开数据。

输入：
  .cache/fetch/<slug>.raw.json              原始元数据（gitignore，不进仓库）
  annotations/<slug>/<pid>.json             逐篇标注（进仓库，可复核可增量）

输出：
  collections/<slug>/data/papers.json       公开数据（脚本生成，勿手改）
  collections/<slug>/data/meta.json         统计与筛选项

公开字段沿用 arxiv-se 合集的白名单；私人字段白名单之外的一律拦下，
BLOCKED 里的字段出现即失败（和 deploy.yml 里的检查是同一份清单）。

用法：
    python3 tools/build_collection.py --category cs.CR --month 2026-09
"""
from __future__ import annotations

import argparse, json, sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 与 collections/arxiv-se-2026-09 完全一致的字段面：翻译 + 客观分类 + 客观评价
PUBLIC_PAPER_FIELDS = ["id", "title", "title_cn", "abstract", "abstract_cn", "summary_cn",
                       "topic", "tags", "authors", "author_count", "categories",
                       "primary_category", "published", "venue", "comment", "url", "pdf"]
PUBLIC_OBJ_FIELDS = ["objective_summary", "contribution_type", "evidence_strength",
                     "evidence_note", "novelty", "novelty_note", "scope",
                     "limitations", "artifact"]
BLOCKED = {"relevance", "why", "headline", "overview_cn", "picks", "skip"}
REQUIRED_OBJ = ["topic", "tags", "contribution_type", "evidence_strength", "evidence_note",
                "novelty", "novelty_note", "scope", "limitations"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True)
    ap.add_argument("--month", default="2026-09")
    args = ap.parse_args()

    slug = f"arxiv-{args.category.split('.')[-1].lower()}-{args.month}"
    raw_f = REPO / ".cache/fetch" / f"{slug}.raw.json"
    ann_dir = REPO / "annotations" / slug
    out = REPO / "collections" / slug / "data"
    raw = json.loads(raw_f.read_text(encoding="utf-8"))
    if not raw:
        sys.exit(f"原始数据是空的：{raw_f}，先跑 tools/fetch_arxiv.py")

    # 标注按公告日成批做；只有整日齐了才算数，避免日榜里混着半成品
    papers, annotated_ids = [], set()
    for ann_f in sorted(ann_dir.glob("*.json")):
        a = json.loads(ann_f.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED_OBJ if not a.get(k)]
        if missing:
            sys.exit(f"{ann_f.name} 缺字段：{missing}")
        if not a.get("abstract_cn") or not a.get("title_cn") or not a.get("summary_cn"):
            sys.exit(f"{ann_f.name} 缺中文标题/摘要/一句话")
        annotated_ids.add(a["id"])

    papers = []
    for p in raw:
        pid = p["id"].split("v")[0]
        rec = {k: p.get(k) for k in PUBLIC_PAPER_FIELDS if p.get(k) not in (None, "")}
        ann_f = ann_dir / f"{pid}.json"
        if annotated_ids and ann_f.exists():
            a = json.loads(ann_f.read_text(encoding="utf-8"))
            for k in PUBLIC_PAPER_FIELDS + PUBLIC_OBJ_FIELDS:
                if k in ("id", "url", "pdf") or k in rec:
                    continue
                if a.get(k) not in (None, ""):
                    rec[k] = a[k]
        leaked = BLOCKED & set(rec)
        if leaked:
            sys.exit(f"内部错误：{pid} 带出了私人字段 {leaked}")
        papers.append(rec)

    # 公告日：抓取时已落在每篇上（listing 后端=官方分日；api 后端=按截止线推导）。
    # 只有整日全部标完的公告日才进日榜，半齐的日子一篇都不标 day，避免半成品的榜
    day_pids: dict[str, list[str]] = {}
    for p in raw:
        if p.get("day"):
            day_pids.setdefault(p["day"], []).append(p["id"].split("v")[0])
    done_days = sorted((d for d, ids in day_pids.items()
                        if ids and all(i in annotated_ids for i in ids)), reverse=True)
    id2day = {pid: d for d, ids in day_pids.items() for pid in ids}
    for p in papers:
        pid = p["id"].split("v")[0]
        if pid in annotated_ids and id2day.get(pid) in done_days:
            p["day"] = id2day[pid]

    papers.sort(key=lambda x: x["id"], reverse=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "papers.json").write_text(
        json.dumps(papers, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    topics: Counter = Counter(p.get("topic", "Other") for p in papers if p.get("topic"))
    types: Counter = Counter(p["contribution_type"] for p in papers if p.get("contribution_type"))
    meta = {
        "slug": slug,
        "title": f"arXiv {args.category} · {args.month}",
        "month": args.month,
        "category": args.category,
        "count": len(papers),
        "with_objective": len(annotated_ids),
        "annotated_days": done_days,
        "days": done_days,
        "topics": dict(topics.most_common()),
        "contribution_types": dict(types.most_common()),
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    size = (out / "papers.json").stat().st_size
    print(f"✅ {slug}: {len(papers)} 篇 · papers.json {size/1024:.0f} KB · "
          f"已标注 {len(annotated_ids)} 篇，覆盖公告日 {done_days}")


if __name__ == "__main__":
    main()
