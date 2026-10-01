#!/usr/bin/env python3
"""生成某个月的 arXiv 合集页：collections/arxiv-<se|cr>-<YYYY-MM>/index.html。

模板永远是 collections/arxiv-se-2026-09/index.html（改页面只改它）。
cs.CR 先过 make_cr_page.transform()，再统一换月份；每条替换都校验命中次数，模板变了会直接报错。

顺带把月份加进总览页 index.html 的 ARXIV_MONTHS（已有就不动），首页就会多出这个月的两张卡片。
**等这个月第一个公告日的数据出来再跑**，否则首页卡片会显示「数据未载入」。

用法：
    python3 tools/make_month_page.py --month 2026-10            # 两个分类都生成
    python3 tools/make_month_page.py --month 2026-10 --category cs.CR
"""
from __future__ import annotations

import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_cr_page  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
TEMPLATE_MONTH = "2026-09"
TEMPLATE = REPO / f"collections/arxiv-se-{TEMPLATE_MONTH}/index.html"

# 全月导语里「月初批量归档」那句在模板里是无条件显示的（9 月确实有 60 篇）；别的月份没有就别显示「其中 0 篇」
SE_BACKLOG_OLD = """'<p class="hint">其中 ' + (META.count - (META.with_day||0)) + ' 篇是月初批量归档进来的，' +
    'arXiv 不保留历史公告日，所以它们只出现在「全月」里，不进日榜。</p>' +"""
SE_BACKLOG_NEW = """((META.count - (META.with_day||0)) > 0 ? '<p class="hint">其中 ' + (META.count - (META.with_day||0)) + ' 篇是月初批量归档进来的，' +
    'arXiv 不保留历史公告日，所以它们只出现在「全月」里，不进日榜。</p>' : '') +"""


def swap(h: str, old: str, new: str, times: int) -> str:
    n = h.count(old)
    if n != times:
        sys.exit(f"模板里「{old[:40]}」命中 {n} 次（应为 {times}），模板变了，先更新本脚本")
    return h.replace(old, new)


def month_page(category: str, month: str) -> Path:
    y, m = month.split("-")
    h = TEMPLATE.read_text(encoding="utf-8")
    if category == "cs.CR":
        h = make_cr_page.transform(h)
    else:
        h = swap(h, SE_BACKLOG_OLD, SE_BACKLOG_NEW, 1)
    # 标题、品牌角标、localStorage 键（每个合集一份阅读进度）；描述与全月导语里的「2026 年 9 月」
    h = swap(h, TEMPLATE_MONTH, month, 3)
    ty, tm = TEMPLATE_MONTH.split("-")
    h = swap(h, f"{ty} 年 {int(tm)} 月", f"{y} 年 {int(m)} 月", 2)
    slug = f"arxiv-{category.split('.')[-1].lower()}-{month}"
    dst = REPO / "collections" / slug / "index.html"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(h, encoding="utf-8")
    return dst


def add_to_index(month: str) -> bool:
    import re
    f = REPO / "index.html"
    h = f.read_text(encoding="utf-8")
    m = re.search(r"var ARXIV_MONTHS = \[(.*?)\];", h)
    if not m:
        sys.exit("index.html 里没找到 ARXIV_MONTHS")
    months = re.findall(r'"(\d{4}-\d{2})"', m.group(1))
    if month in months:
        return False
    months = sorted(set(months + [month]), reverse=True)
    h = h[:m.start()] + "var ARXIV_MONTHS = [" + ", ".join(f'"{x}"' for x in months) + "];" + h[m.end():]
    f.write_text(h, encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True)
    ap.add_argument("--category", choices=["cs.SE", "cs.CR"], help="不给就两个都生成")
    a = ap.parse_args()
    if a.month == TEMPLATE_MONTH:
        sys.exit(f"{TEMPLATE_MONTH} 的 cs.SE 页就是模板本身；cs.CR 用 tools/make_cr_page.py")
    for cat in [a.category] if a.category else ["cs.SE", "cs.CR"]:
        print(f"✅ 生成 {month_page(cat, a.month).relative_to(REPO)}")
    if add_to_index(a.month):
        print(f"✅ index.html 的 ARXIV_MONTHS 加入 {a.month}")


if __name__ == "__main__":
    main()
