#!/usr/bin/env python3
"""把 Conference Paper Atlas 里的 SE 四大会部分导出成公开合集数据。

来源是那个 artifact 里内嵌的 DATA（SE 四大会 + AI 三大会两条赛道合在一起）。
先把整份 DATA 存到 .cache/atlas/atlas.json（gitignore，不进仓库），再跑这个脚本
只取 SE 那一半。

**必须挡掉的字段**：`rel` / `rn` 是按个人研究方向打的相关度与理由，和 vault 那套
`relevance` / `why` 是同一类东西，不能进公开站（见 CLAUDE.md「评价必须客观」）。
SE 论文的 rel 还全都是 "core"，本来也没有信息量。

用法：
    python3 tools/build_se_venues.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / ".cache" / "atlas" / "atlas.json"
OUT = REPO / "collections" / "se-venues" / "data"

VENUES = ["ICSE 2026", "FSE 2026", "ASE 2026", "ISSTA 2026"]

# 公开字段白名单——和别的合集一个思路：宁可漏，也不要把私人字段带出去
PUBLIC = ["id", "v", "k", "t", "tz", "a", "az", "tl", "au", "top", "alt", "tg", "kw", "u", "pu", "aw"]
# 命中即失败。rel/rn 是这份数据自己的个人相关度字段，其余几个和别处的黑名单保持一致
BLOCKED = {"rel", "rn", "relevance", "why", "headline", "overview_cn", "picks", "skip"}


def main() -> None:
    if not SRC.exists():
        sys.exit(f"没有源数据：{SRC}\n先把 Conference Paper Atlas 里的 DATA 存成这个文件")
    D = json.loads(SRC.read_text(encoding="utf-8"))

    se = [p for p in D["papers"] if p.get("tr") == "SE"]
    if not se:
        sys.exit("源数据里没有 tr=SE 的论文")

    papers = []
    for p in se:
        rec = {k: p[k] for k in PUBLIC if p.get(k) not in (None, "", [])}
        leaked = BLOCKED & set(rec)
        if leaked:                      # 兜底断言：白名单之外的东西绝不该出现
            sys.exit(f"内部错误：{p.get('id')} 带出了私人字段 {leaked}")
        papers.append(rec)

    used = {p["top"] for p in papers} | {p["alt"] for p in papers if p.get("alt")}

    # 主题树只留下真有 SE 论文的 topic；整个 theme 都空了就整条去掉，
    # 否则侧栏会挂着一串 AI 专属主题，全是 0
    themes = []
    for th in D["themes"]:
        topics = [t for t in th["topics"] if t["id"] in used]
        if topics:
            themes.append({**th, "topics": topics})

    tags = [t for t in D.get("tags", []) if t["id"] in {g for p in papers for g in p.get("tg", [])}]

    OUT.mkdir(parents=True, exist_ok=True)
    ledger = {"papers": papers, "themes": themes, "tags": tags}
    (OUT / "ledger.js").write_text(
        "window.__SE_ATLAS__=" + json.dumps(ledger, ensure_ascii=False, separators=(",", ":")) + ";",
        encoding="utf-8")

    by_venue = Counter(p["v"] for p in papers)
    meta = {
        "slug": "se-venues",
        "title": "SE 四大会 · 2026",
        "venues": VENUES,
        "count": len(papers),
        "by_venue": {v: by_venue.get(v, 0) for v in VENUES},
        "with_abstract": sum(1 for p in papers if p.get("a")),
        "translated": sum(1 for p in papers if p.get("az")),
        "themes": len(themes),
        "topics": sum(len(t["topics"]) for t in themes),
        "awards": sum(1 for p in papers if p.get("aw")),
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    size = (OUT / "ledger.js").stat().st_size
    print(f"✅ se-venues: {len(papers)} 篇 · ledger.js {size/1024/1024:.2f} MB")
    print(f"   {dict(meta['by_venue'])}")
    print(f"   摘要 {meta['with_abstract']}/{len(papers)} · 中译 {meta['translated']} · "
          f"{meta['themes']} 主题 / {meta['topics']} 子题 · 杰出论文 {meta['awards']}")


if __name__ == "__main__":
    main()
