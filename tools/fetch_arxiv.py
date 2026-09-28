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
    python3 tools/fetch_arxiv.py --category cs.CR --month 2026-09
    python3 tools/fetch_arxiv.py --category cs.CR --month 2026-09 --catchup 2026-09-16,2026-09-17
    python3 tools/fetch_arxiv.py --category cs.CR --month 2026-09 --via api --check-recent

输出：.cache/fetch/<slug>.raw.json（gitignore，不进仓库），每篇含 day 字段；
      annotations/days/<slug>.json（进仓库）：每个公告日的完整成员名单 {day: [id, ...]}。

listing 后端是**增量**的：
  - 已在 raw 里的论文不重抓，新论文并进去，旧论文原样保留
    （recent 页只有最近 5 个公告日，覆盖写会把更早的论文整批丢掉）。
  - 分日名单单独进仓库。公开的 papers.json 只给整日标齐的论文带 day，
    靠它还原会把没标齐那几天的归属丢掉；有了名单，换机器也能还原。
  - 本机还没有 raw 时，从已发布的 collections/<slug>/data/papers.json 还原
    （换一台机器、或别人抓的合集，都走这条）。
  - 已滚出 recent 窗口的日子用 --catchup 补：arxiv.org/catchup/<cat>/<day> 能取任意一天的
    New + Cross-list（和 recent 页同口径，不含 Replacements）。
  - 只收 --month 当月的公告日；窗口里别的月份的日子如果那个月的名单里还没有，会报错提醒去补跑。
"""
from __future__ import annotations

import argparse, json, re, sys, time, urllib.request
from html import unescape  # 函数里有叫 html 的局部变量，别用模块名
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = "https://export.arxiv.org/api/query"
WWW = "https://arxiv.org"
UA = "paper-reading-static-site/1.0 (site builder; contact via GitHub issues)"

EDT = timezone(timedelta(hours=-4))  # 9 月处于夏令时；跨到冬令时后需改成 -5


PACE = 3.5   # arXiv 要求 >= 3 秒一个请求
_last = 0.0


def http_get(url: str, tries: int = 8, min_wait: float = 3.0) -> str:
    """任意两次请求至少隔 PACE 秒；429 / “Rate exceeded” 一律指数退避重试。"""
    global _last
    delay = min_wait
    last = None
    for i in range(tries):
        wait = _last + PACE - time.time()
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
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


# ---------------------------------------------------------------- listing 后端

def strip_tags(s: str) -> str:
    # abs 页的 citation_* meta 里是 HTML 实体（&#39; 等），不反转义就会原样显示在页面上
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", s))).strip()


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


def fetch_catchup_day(category: str, day: str) -> list[str]:
    """arxiv.org/catchup/<cat>/<day>：任意一天的公告，取 New + Cross（与 recent 页同口径，不含 Replacements）。"""
    html = http_get(f"{WWW}/catchup/{category}/{day}")
    parts = re.split(r"<h3[^>]*>\s*([^<]*?)\s*\((showing[^)]*)\)\s*</h3>", html)
    ids: list[str] = []
    for i in range(1, len(parts) - 1, 3):
        label, showing, body = parts[i], parts[i + 1], parts[i + 2]
        if not re.match(r"(New|Cross)", label.strip()):
            continue
        items = re.findall(
            r"<a name='item\d+'>\[\d+\]</a>\s*<a\s+href\s*=\s*\"/abs/([0-9]{4}\.[0-9]{4,5})(v\d+)?\"", body)
        total = re.search(r"of (\d+) entries", showing)
        if total and len(items) != int(total.group(1)):
            sys.exit(f"catchup {day} {label.strip()} 没展开全（{len(items)}/{total.group(1)}）")
        ids += [base for base, _ in items]
    if not ids:
        sys.exit(f"catchup {day} 一篇都没解析出来（那天没有公告，或页面结构变了）")
    print(f"  {day}: catchup {len(ids)} 篇（New + Cross）", file=sys.stderr)
    return list(dict.fromkeys(ids))


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


RAW_FIELDS = ["id", "title", "abstract", "authors", "author_count", "categories",
              "primary_category", "published", "venue", "comment", "url", "pdf", "day"]


def days_file(slug: str) -> Path:
    # 不能放在 annotations/<slug>/ 下：build_collection.py 会把那里的 *.json 全当标注校验
    return REPO / "annotations" / "days" / f"{slug}.json"


def load_days(slug: str) -> dict[str, list[str]]:
    f = days_file(slug)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def save_json(f: Path, obj, **kw) -> None:
    """先写临时文件再改名，写到一半被打断也不会留下半截文件。"""
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, **kw), encoding="utf-8")
    tmp.replace(f)


def load_known(slug: str, raw_f: Path) -> dict[str, dict]:
    """已有的原始数据；本机没有就从已发布的 papers.json 还原（丢掉标注字段，只留原始字段）。"""
    if raw_f.exists():
        return {p["id"].split("v")[0]: p for p in json.loads(raw_f.read_text(encoding="utf-8"))}
    pub = REPO / "collections" / slug / "data" / "papers.json"
    if not pub.exists():
        return {}
    print(f"本机没有 {raw_f.name}，从已发布的 {pub.relative_to(REPO)} 还原", file=sys.stderr)
    return {p["id"].split("v")[0]: {k: p[k] for k in RAW_FIELDS if k in p}
            for p in json.loads(pub.read_text(encoding="utf-8"))}


def fetch_via_listing(category: str, month: str, slug: str, known: dict[str, dict],
                      catchup: list[str], save=None) -> tuple[list[dict], dict[str, list[str]], list[str]]:
    """增量：recent 窗口（+ --catchup 指定的日子）的分日名单并进 days 文件，缺的论文按 id 抓 abs，
    再按名单给论文标公告日（整日齐了才标）。"""
    days = load_days(slug)
    if not days:  # 第一次：从 raw 里已有的 day 起一份名单
        for p in known.values():
            if p.get("day", "").startswith(month):
                days.setdefault(p["day"], []).append(p["id"].split("v")[0])
    window = fetch_listing_days(category)
    other_month_gaps = []
    for d, ids in window.items():
        base = [a.split("v")[0] for a in ids]
        if d.startswith(month):
            days[d] = base
        else:
            other = f"arxiv-{category.split('.')[-1].lower()}-{d[:7]}"
            if sorted(load_days(other).get(d, [])) != sorted(base):
                other_month_gaps.append(d)
    for d in catchup:
        if not d.startswith(month):
            sys.exit(f"--catchup {d} 不在 --month {month} 里")
        days[d] = fetch_catchup_day(category, d)

    papers = dict(known)
    todo = [a for d in sorted(days) for a in days[d] if a not in papers]
    todo = list(dict.fromkeys(todo))
    print(f"{month} 名单里 {len(days)} 个公告日，缺 {len(todo)} 篇，逐篇抓 abs 页"
          f"（约 {len(todo) * PACE / 60:.0f} 分钟）…", file=sys.stderr)
    failed = []
    for i, aid in enumerate(todo, 1):
        try:
            papers[aid] = fetch_abs(aid)
        except Exception as e:
            failed.append(aid)
            print(f"  ✗ {aid}: {e}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  … {i}/{len(todo)}", file=sys.stderr)
            if save:
                save(list(papers.values()), days)  # 中途断了，已抓的不用重来
    for day, ids in days.items():
        # 有一篇没抓到，这一天就先不标：日榜只放完整的一天。名单已落盘，下次按 id 补
        missing = [a for a in ids if a not in papers]
        if missing:
            print(f"  ⚠ {day} 还缺 {len(missing)} 篇，先不标公告日，重跑会补", file=sys.stderr)
            continue
        for aid in ids:
            p = papers[aid]
            if p.get("day") and p["day"] != day:
                print(f"  ⚠ {aid} 原公告日 {p['day']}，名单给 {day}", file=sys.stderr)
            p["day"] = day
    if other_month_gaps:
        print(f"  ⚠ recent 窗口里还有别的月份的公告日 {other_month_gaps} 没进对应月的名单——"
              f"这次只收 {month}，那几天要用对应的 --month 再跑一次，滚出窗口就只能靠 --catchup 了",
              file=sys.stderr)
        failed.append(f"其他月份未收: {other_month_gaps}")
    return list(papers.values()), days, failed


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
    ap.add_argument("--catchup", help="listing 后端：再用 catchup 页补这些公告日（逗号分隔），用于已滚出 recent 窗口的日子")
    args = ap.parse_args()

    slug = f"arxiv-{args.category.split('.')[-1].lower()}-{args.month}"
    f = REPO / ".cache" / "fetch" / f"{slug}.raw.json"

    def save(ps: list[dict], days: dict | None = None) -> None:
        save_json(f, ps)
        if days is not None:
            save_json(days_file(slug), dict(sorted(days.items())), indent=1)

    failed: list[str] = []
    if args.via == "listing":
        catchup = [d.strip() for d in (args.catchup or "").split(",") if d.strip()]
        papers, days, failed = fetch_via_listing(args.category, args.month, slug,
                                                 load_known(slug, f), catchup, save)
        save(papers, days)
    else:
        papers, days = fetch_via_api(args.category, args.month)
        if args.check_recent:
            check_recent(args.category, days)
        save(papers)

    for d in sorted(days, reverse=True):
        print(f"  {d}: {len(days[d])} 篇")
    print(f"✅ {slug}: {len(papers)} 篇 → {f.relative_to(REPO)}")
    if failed:
        sys.exit(f"❌ 没办完：{failed[:5]}…。没抓到的论文所在公告日暂不进日榜，重跑本脚本会只补缺的")


if __name__ == "__main__":
    main()
