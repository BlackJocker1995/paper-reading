#!/usr/bin/env python3
"""从 Obsidian vault 导出**公开版**数据到本仓库。

公开的只有：翻译（中文标题/摘要/一句话总结）、客观分类、以及 arxiv_objective.py 产出的
客观评价（贡献类型 / 证据强度 / 新颖性 / 适用边界 / 局限 / artifact）。

**刻意不导出**：
  - relevance / why —— 那是按个人研究方向打的分，不适合公开
  - 每日/每周综述 headline & overview —— 同样是以个人视角写的
  - 九月以外的任何月份，以及 vault 里其他一切内容

用法：
    python3 tools/build_site.py           # 默认导出 2026-09
    VAULT=/path/to/vault python3 tools/build_site.py --month 2026-09
"""
from __future__ import annotations

import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VAULT = Path(os.environ.get("VAULT", "/Users/lvyunbo/Desktop/Obsidian Vault"))
CACHE = VAULT / "Arxiv_Reading" / ".web"

# 允许出现在公开数据里的字段白名单——宁可漏，也不要把私人字段带出去
PUBLIC_PAPER_FIELDS = ["id", "title", "title_cn", "abstract", "abstract_cn", "summary_cn",
                       "topic", "tags", "authors", "author_count", "categories",
                       "primary_category", "published", "venue", "comment", "url", "pdf"]
PUBLIC_OBJ_FIELDS = ["objective_summary", "contribution_type", "evidence_strength",
                     "evidence_note", "novelty", "novelty_note", "scope",
                     "limitations", "artifact"]
BLOCKED = {"relevance", "why", "headline", "overview_cn", "picks", "skip"}


def collect(month: str) -> tuple[list[dict], dict[str, list[str]]]:
    """返回 (论文列表, {公告日: [id]})。月度归档 + 当月各公告日缓存，按 id 去重。"""
    papers: dict[str, dict] = {}
    by_day: dict[str, list[str]] = {}

    arch = CACHE / "months" / f"{month}.json"
    if arch.exists():
        for p in json.loads(arch.read_text(encoding="utf-8")).get("papers", []):
            papers.setdefault(p["id"].split("v")[0], p)

    for f in sorted(CACHE.glob(f"{month}-*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        date = d.get("date")
        if not date:
            continue
        ids = []
        for p in d.get("papers", []):
            pid = p["id"].split("v")[0]
            papers.setdefault(pid, p)
            ids.append(pid)
        if ids:
            by_day[date] = ids
    return list(papers.values()), by_day


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-09")
    ap.add_argument("--out", default=None, help="输出目录，默认 collections/arxiv-se-<month>/data")
    a = ap.parse_args()

    slug = f"arxiv-se-{a.month}"
    out = Path(a.out) if a.out else REPO / "collections" / slug / "data"
    out.mkdir(parents=True, exist_ok=True)

    raw, by_day = collect(a.month)
    if not raw:
        sys.exit(f"vault 里没找到 {a.month} 的数据：{CACHE}")

    objdir = CACHE / "objective"
    papers, no_obj = [], []
    for p in raw:
        pid = p["id"].split("v")[0]
        rec = {k: p.get(k) for k in PUBLIC_PAPER_FIELDS if p.get(k) not in (None, "")}
        of = objdir / f"{pid}.json"
        if of.exists():
            o = json.loads(of.read_text(encoding="utf-8"))
            for k in PUBLIC_OBJ_FIELDS:
                if o.get(k) not in (None, ""):
                    rec[k] = o[k]
        else:
            no_obj.append(pid)
        leaked = BLOCKED & set(rec)
        if leaked:                       # 兜底断言：白名单之外的东西绝不该出现
            sys.exit(f"内部错误：{pid} 带出了私人字段 {leaked}")
        papers.append(rec)

    papers.sort(key=lambda x: x["id"], reverse=True)
    day_of = {pid: d for d, ids in by_day.items() for pid in ids}
    for p in papers:
        d = day_of.get(p["id"].split("v")[0])
        if d:
            p["day"] = d

    (out / "papers.json").write_text(json.dumps(papers, ensure_ascii=False,
                                                separators=(",", ":")), encoding="utf-8")
    topics: dict[str, int] = {}
    types: dict[str, int] = {}
    for p in papers:
        topics[p.get("topic", "Other")] = topics.get(p.get("topic", "Other"), 0) + 1
        t = p.get("contribution_type")
        if t:
            types[t] = types.get(t, 0) + 1
    meta = {
        "slug": slug,
        "title": f"arXiv cs.SE · {a.month}",
        "month": a.month,
        "category": "cs.SE",
        "count": len(papers),
        "with_objective": len(papers) - len(no_obj),
        "days": sorted(by_day, reverse=True),
        "topics": dict(sorted(topics.items(), key=lambda kv: -kv[1])),
        "contribution_types": dict(sorted(types.items(), key=lambda kv: -kv[1])),
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    size = (out / "papers.json").stat().st_size
    print(f"✅ {slug}: {len(papers)} 篇 · papers.json {size/1024:.0f} KB · "
          f"客观评价覆盖 {meta['with_objective']}/{len(papers)}")
    if no_obj:
        print(f"⚠ {len(no_obj)} 篇还没有客观评价，先跑 Scripts/arxiv_objective.py: {no_obj[:5]}")


if __name__ == "__main__":
    main()
