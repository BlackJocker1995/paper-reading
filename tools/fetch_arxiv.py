#!/usr/bin/env python3
"""抓取 arXiv 某分类在最近公告窗口（或某月）的全部论文，并给每篇标上公告日。

两个后端：
  --via listing  走 arxiv.org 的列表页：recent?skip=0&show=2000 按公告日分組（地面真值，
                 跨榜与新版本都算当批），再逐篇抓 abs 页补摘要、分类、comment。
                 不消耗 export API 配额，新论文覆盖最全。默认。
  --via api      走 export.arxiv.org 的公开 API，按 submittedDate 拉整月，
                 公告日由「赶上哪个工作日 14:00（美东）截止线」推导，
                 可用 --check-recent 和列表页分日核对。API 限流很凶，备选。

用法：
    python3 tools/fetch_arxiv.py --category cs.CR --days-window
    python3 tools/fetch_arxiv.py --category cs.CR --month 2026-09 --via api --check-recent

输出：.cache/fetch/<slug>.raw.json（gitignore，不进仓库），每篇含 day 字段。
"""
from __future__ import annotations

import argparse, json, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = "https://export.arxiv.org/api/query"
WWW = "https://arxiv.org"
UA = "paper-reading-static-site/1.0 (site builder; contact via GitHub issues)"

EDT = timezone(timedelta(hours=-4))  # 9 月处于夏令时；跨到冬令时后需改成 -5


def http_get(url: str, tries: int = 8, min_wait: float = 3.0) -> str:
    """429 / “Rate exceeded” 一律指数退避重试。"""
    delay = min_wait
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 120)
    raise RuntimeError(f"{url}: {last}")


def polite(seconds: float = 1.3) -> None:
    time.sleep(seconds)


# ---------------------------------------------------------------- listing 后端

def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def fetch_listing_days(category: str) -> dict[str, list[str]]:
    """recent?skip=0&show=2000：每个公告日一组，id 顺序即列表顺序。"""
    html = http_get(f"{WWW}/list/{category}/recent?skip=0&show=2000")
    parts = re.split(r"<h3[^>]*>\s*([^<]*?)\s*\((showing[^)]*)\)\s*</h3>", html)
    days: dict[str, list[str]] = {}
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for i in range(1, len(parts) - 1, 3):
        label, showing, body = parts[i], parts[i + 1], parts[i + 2]
        m = re.search(r"([A-Z][a-z]{2}), (\d+) ([A-Z][a-z]{2}) (\d{4})", label)
        if not m:
            continue
        mon = months.index(m.group(3)) + 1
        day = f"{m.group(4)}-{mon:02d}-{int(m.group(2)):02d}"
        # 条目主链接带 [N] 锚点；Comments 等字段里也会出现别的 arXiv 链接，不能算数
        items = re.findall(
            r"<a name='item\d+'>\[\d+\]</a>\s*<a\s+href\s*=\s*\"/abs/([0-9]{4}\.[0-9]{4,5})(v\d+)?\"", body)
        ids = [base + (ver or "") for base, ver in items]
        total = re.search(r"of (\d+) entries", showing)
        print(f"  {day}: 展示 {len(ids)} / {total.group(1) if total else '?'} 篇", file=sys.stderr)
        if total and len(ids) != int(total.group(1)):
            sys.exit(f"{day} 没展开全（{len(ids)}/{total.group(1)}），调大 show 或换后端")
        days[day] = ids
    return days


def fetch_abs(aid: str) -> dict:
    html = http_get(f"{WWW}/abs/{aid}")
    g = lambda pat: (re.search(pat, html, re.S) or [None, ""])[1].strip()

    title = g(r'<h1 class="title[^"]*">(?:<span[^>]*>[^<]*</span>)?\s*(.*?)</h1>')
    abstract = g(r'<meta name="citation_abstract" content="(.*?)"\s*/?>')
    if not abstract:
        abstract = g(r'<blockquote class="abstract[^"]*">(?:<span[^>]*>[^<]*</span>)?\s*(.*?)</blockquote>')
    authors = re.findall(r'<a href="/a/[^"]*">([^<]+)</a>', html)
    if not authors:  # 无链接的作者行
        m = re.search(r'<div class="authors">(?:<span[^>]*>[^<]*</span>)?\s*(.*?)</div>', html)
        if m:
            authors = [a.strip() for a in re.sub(r"<[^>]+>", "", m.group(1))
                       .replace("Authors:", "").split(",")]
    subj = g(r'<td class="tablecell subjects">(.*?)</td>')
    cats = re.findall(r"\(([a-z.-]+\.[A-Z]{2})\)", subj)
    prim = re.search(r'<span class="primary-subject">[^<]*\(([a-z.-]+\.[A-Z]{2})\)', subj)
    comment = g(r'<td class="tablecell comments[^"]*">(.*?)</td>')
    # v1 提交日期（与 cs.SE 合集同为 date-only；提交行如 “Fri, 18 Sep 2026 17:41:39 UTC”）
    published = ""
    sub = g(r'<div class="submission-history">(.*?)</div>')
    m1 = re.search(r"\[v1\]</strong>\s*[A-Za-z]{3},\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", sub)
    if m1:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        published = f"{m1.group(3)}-{months.index(m1.group(2)) + 1:02d}-{int(m1.group(1)):02d}"

    return {
        "id": aid,
        "title": strip_tags(title),
        "abstract": strip_tags(abstract),
        "authors": [strip_tags(a) for a in authors],
        "author_count": len(authors),
        "categories": sorted(set(cats)) or None,
        "primary_category": prim.group(1) if prim else (cats[0] if cats else None),
        "published": published or None,
        "comment": strip_tags(comment) or None,
        "url": f"{WWW}/abs/{aid}",
        "pdf": f"{WWW}/pdf/{aid}",
    }


def fetch_via_listing(category: str) -> tuple[list[dict], dict[str, list[str]]]:
    days = fetch_listing_days(category)
    papers: dict[str, dict] = {}
    todo = [aid for ids in days.values() for aid in ids]
    print(f"共 {len(todo)} 篇，逐篇抓 abs 页（约 {len(todo)*1.5/60:.0f} 分钟）…", file=sys.stderr)
    for i, aid in enumerate(todo, 1):
        papers[aid] = fetch_abs(aid)
        if i % 25 == 0:
            print(f"  … {i}/{len(todo)}", file=sys.stderr)
        polite()
    for day, ids in days.items():
        for aid in ids:
            papers[aid]["day"] = day
    return list(papers.values()), days


# ---------------------------------------------------------------- api 后端

def parse_feed(xml: str) -> list[dict]:
    out = []
    for rec in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        get = lambda tag: (re.search(rf"<{tag}>([^<]*)</{tag}>", rec) or [None, ""])[1].strip()
        authors = re.findall(r"<name>([^<]*)</name>", rec)
        cats = re.findall(r'term="([^"]+)"', rec)
        prim = re.search(r'primary_category[^>]*term="([^"]+)"', rec)
        aid = re.search(r"<id>https?://arxiv\.org/abs/([^<]+)</id>", rec).group(1)
        entry = {
            "id": aid,
            "title": re.sub(r"\s+", " ", get("title")),
            "abstract": re.sub(r"\s+", " ", get("summary")),
            "authors": authors,
            "author_count": len(authors),
            "categories": cats,
            "primary_category": prim.group(1) if prim else (cats[0] if cats else ""),
            "published": get("published"),
            "updated": get("updated"),
            "url": f"{WWW}/abs/{aid}",
            "pdf": f"{WWW}/pdf/{aid}",
        }
        cm = re.search(r"<arxiv:comment[^>]*>(.*?)</arxiv:comment>", rec, re.S)
        if cm:
            entry["comment"] = re.sub(r"\s+", " ", cm.group(1)).strip()
        out.append(entry)
    return out


def fetch_via_api(category: str, month: str) -> tuple[list[dict], dict[str, list[str]]]:
    y, m = map(int, month.split("-"))
    start = f"{y}{m:02d}010000"
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    end = f"{ny}{nm:02d}010000"
    q = f"search_query=cat:{category}+AND+submittedDate:[{start}+TO+{end}]"
    papers, page, seen = [], 0, set()
    while True:
        url = f"{API}?{q}&start={page * 200}&max_results=200&sortBy=submittedDate&sortOrder=ascending"
        batch = parse_feed(http_get(url, min_wait=5.0))
        if not batch:
            break
        for p in batch:
            key = p["id"].split("v")[0]
            if key not in seen:
                seen.add(key)
                papers.append(p)
        page += 1
        print(f"  … {len(papers)} 篇", file=sys.stderr)
        if len(batch) < 200:
            break
        time.sleep(4)

    for p in papers:
        p["day"] = announce_day(p["published"])
    days: dict[str, list[str]] = {}
    for p in papers:
        days.setdefault(p["day"], []).append(p["id"])
    return papers, days


def announce_day(published_utc: str) -> str:
    """论文赶上哪个公告日 14:00 美东的提交截止线，就属于哪个公告日。"""
    t = datetime.fromisoformat(published_utc.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(EDT)
    d = t.replace(hour=0, minute=0, second=0, microsecond=0)
    if t.hour > 14 or (t.hour == 14 and (t.minute or t.second)):
        d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.date().isoformat()


def check_recent(category: str, days: dict[str, list[str]]) -> None:
    """推导的公告日和 recent 页的分组核对（只核页面展示出来的成员）。"""
    html = http_get(f"{WWW}/list/{category}/recent")
    parts = re.split(r"<h3[^>]*>\s*([^<]*?)\s*\(showing[^)]*\)\s*</h3>", html)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    ok = True
    for i in range(1, len(parts) - 1, 2):
        m = re.search(r"([A-Z][a-z]{2}), (\d+) ([A-Z][a-z]{2}) (\d{4})", parts[i])
        if not m:
            continue
        mon = months.index(m.group(3)) + 1
        day = f"{m.group(4)}-{mon:02d}-{int(m.group(2)):02d}"
        listed = re.findall(r"/abs/([0-9]{4}\.[0-9]{4,5})(v\d+)?", parts[i + 1])
        ids = {b + (v or "") for b, v in listed}
        mine = set(days.get(day, []))
        wrong = [x for x in ids if x not in mine]
        print(f"  {day}: 页面 {len(ids)} 篇，推导 {len(mine)} 篇，不匹配 {len(wrong)}")
        for x in sorted(wrong)[:3]:
            print(f"    例：{x}")
        ok = ok and not wrong
    if not ok:
        sys.exit("❌ 公告日推导和 recent 页对不上，用 --via listing 或先修规则")
    print("  ✅ 推导与 recent 页一致")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True)
    ap.add_argument("--month", default="2026-09")
    ap.add_argument("--via", choices=["listing", "api"], default="listing")
    ap.add_argument("--days-window", action="store_true", default=True,
                    help="listing 后端：收 recent 页整个公告窗口（默认）")
    ap.add_argument("--check-recent", action="store_true")
    args = ap.parse_args()

    slug = f"arxiv-{args.category.split('.')[-1].lower()}-{args.month}"
    if args.via == "listing":
        papers, days = fetch_via_listing(args.category)
    else:
        papers, days = fetch_via_api(args.category, args.month)
        if args.check_recent:
            check_recent(args.category, days)

    for d in sorted(days, reverse=True):
        print(f"  {d}: {len(days[d])} 篇")

    out = REPO / ".cache" / "fetch"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{slug}.raw.json"
    f.write_text(json.dumps(papers, ensure_ascii=False), encoding="utf-8")
    print(f"✅ {slug}: {len(papers)} 篇 → {f.relative_to(REPO)}")


if __name__ == "__main__":
    main()
