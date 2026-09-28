#!/usr/bin/env python3
"""给合集里的论文标一作机构，写 annotations/institutions/<slug>.json。

arXiv 元数据没有机构字段，OpenAlex / Semantic Scholar 继承的也是空的，只能从论文首页取：
  1. arxiv.org/html/<id> 的作者块（LaTeXML 的 ltx_authors），优先
  2. 没有 HTML 版就退回 PDF 首页（pdftotext -f 1 -l 1），截到 Abstract 之前
再由模型（默认 opus）定位**第一作者**那一条。正则做不了：同一作者挂系/大学/邮编三个并列条目、
上标 34 是 3 和 4、「N Corresponding author.」长得和编号机构一样、邮箱域名会指向别的作者。

只标有公告日（日榜上）且还没有记录的论文；--all 连没有公告日的也标。
首页文本缓存在 .cache/institutions/<slug>/（gitignore），重跑不重抓。
build_site.py / build_collection.py 只发 high / medium，低置信度留空——机构挂错比空着严重。

用法：
    python3 tools/fetch_institutions.py --slug arxiv-cr-2026-09
    python3 tools/fetch_institutions.py --slug arxiv-se-2026-09 --since 2026-09-22
        # cs.SE 读已发布的 papers.json：先 build_site.py，标完再 build_site.py 一次
"""
from __future__ import annotations

import argparse, json, re, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotate_collection import call_claude, parse_json_array  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
UA = "paper-reading-static-site/1.0 (site builder; contact via GitHub issues)"
PACE = 3.5          # arXiv 要求 >= 3 秒一个请求
BATCH, WORKERS = 8, 4
EVIDENCE = {"explicit_affiliation", "superscript_mapping", "footnote_or_thanks", "absent"}
CONF = {"high", "medium", "low"}

try:  # macOS 自带 Python 没有根证书，每个请求都会 CERTIFICATE_VERIFY_FAILED
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    CTX = ssl.create_default_context()

_last = 0.0


def get(url: str, tries: int = 3) -> bytes | None:
    """404 直接返回 None（没有 HTML 版很常见，别白等重试）；其余错误退避重试。"""
    global _last
    delay = 10
    for t in range(tries):
        wait = _last + PACE - time.time()
        if wait > 0:
            time.sleep(wait)
        _last = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90, context=CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = e
        except Exception as e:
            err = e
        if t < tries - 1:
            print(f"    retry {t + 1}/{tries}（{delay}s）: {err}", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    print(f"    放弃 {url}: {err}", file=sys.stderr)
    return None


def _text(html: str) -> str:
    html = re.sub(r"<sup[^>]*>(.*?)</sup>", r"^{\1}", html, flags=re.S)
    html = re.sub(r"<br\s*/?>|</(?:span|p|div|td|tr)>", "\n", html)
    txt = unescape(re.sub(r"<[^>]+>", "", html))
    return re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", txt)).strip()


def header_from_html(pid: str) -> str | None:
    raw = get(f"https://arxiv.org/html/{pid}", tries=1)
    if not raw:
        return None
    html = raw.decode("utf-8", "replace")
    s = html.find('<div class="ltx_authors">')
    if s < 0:
        return None
    ends = [i for i in (html.find('class="ltx_abstract"', s), html.find("<section", s)) if i > 0]
    e = html.rfind("<", s, min(ends)) if ends else s + 12000   # 退到那个标签的开头，别留半截标签
    block = html[s:e]
    txt = _text(block)
    return txt[:3000] if len(txt) > 10 else None


def header_from_pdf(pid: str) -> str | None:
    raw = get(f"https://arxiv.org/pdf/{pid}")
    if not raw or not raw.startswith(b"%PDF"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(raw)
        f.flush()
        try:
            txt = subprocess.run(["pdftotext", "-f", "1", "-l", "1", f.name, "-"],
                                 capture_output=True, text=True, timeout=60).stdout
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"    pdftotext 不可用：{e}", file=sys.stderr)
            return None
    # 标题本身可能以 Abstract 开头（Abstract Interpretation…），所以跳过开头那一段再找摘要标题
    m = next((m for m in re.finditer(r"\n\s*(?:A\s?BSTRACT|Abstract)\b", txt) if m.start() > 150), None)
    head = txt[:m.start()] if m else txt[:2500]
    return re.sub(r"\n\s*\n+", "\n", head).strip()[:3000] or None


def prompt(batch: list[dict]) -> str:
    items = [{"arxiv_id": p["id"], "first_author_in_metadata": (p.get("authors") or [""])[0],
              "source": p["_src"], "header": p["_hdr"]} for p in batch]
    return f"""下面是 {len(batch)} 篇 arXiv 论文首页的作者块文本（source=html 来自 arXiv HTML 版，pdf 来自 PDF 首页 pdftotext）。
为每篇找出**第一作者**的所属机构。只依据给出的文本，不要用你对作者的外部了解。

注意：
- 上标写成 ^{{1}} 这样；^{{34}} 可能是 3 和 4 两个编号，也可能是 34，要结合机构列表的编号判断。
- 同一作者可能挂多个并列条目（实验室 / 系 / 大学 / 邮编地址），机构取**最上层的组织**（大学、公司、研究院），
  如「State Key Laboratory for Novel Software Technology, Nanjing University」→「Nanjing University」。
- 一作有多个不同机构时取第一个列出的，raw_affiliation 里保留全部。
- 「Corresponding author」「Equal contribution」这类脚注不是机构。
- 邮箱域名可能属于别的作者，不能单凭邮箱定机构；只能靠邮箱推断时 confidence 给 low。
- 缩写只在文本里能确定全称时展开（如 FDU 与 Fudan 同时出现）。

每篇输出一个对象：
- "id": 原样抄 arxiv_id
- "first_author": 文本里第一作者的名字
- "institution": 一作机构的规范英文名（最上层组织）；找不到就空字符串
- "country": 文本里**明确写出**的国家（英文，如 China、United States）；没写就空字符串，不要推断
- "raw_affiliation": 一作机构在文本里的原样片段（可多条用分号连接）；找不到就空字符串
- "evidence": 严格取其一：explicit_affiliation（机构直接写在作者名下）/ superscript_mapping（靠上标编号对应）/
  footnote_or_thanks（在脚注或致谢里）/ absent（文本里没有一作机构）
- "confidence": high / medium / low。high=对应关系明确；medium=机构名本身或对应关系有少许不确定；
  low=靠猜（含只靠邮箱）。absent 且确实没写时给 high。

只输出一个 JSON 数组，长度 {len(batch)}，顺序与输入一致，不要任何解释文字。

输入：
{json.dumps(items, ensure_ascii=False)}"""


def _surname_ok(meta_name: str, got: str) -> bool:
    """模型说的一作要和 arXiv 元数据的第一作者对得上，否则宁可不发。"""
    a = {t for t in re.findall(r"\w+", meta_name.lower()) if len(t) > 1}
    b = {t for t in re.findall(r"\w+", got.lower()) if len(t) > 1}
    return bool(a & b) or not a


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="如 arxiv-cr-2026-09 / arxiv-se-2026-09")
    ap.add_argument("--all", action="store_true", help="没有公告日的论文也标")
    ap.add_argument("--since", help="只标这一天及之后的公告日，如 2026-09-22")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", default="opus", help="抽取用的模型，默认 opus")
    a = ap.parse_args()

    raw_f = REPO / ".cache/fetch" / f"{a.slug}.raw.json"
    pub_f = REPO / "collections" / a.slug / "data/papers.json"
    src_f = raw_f if raw_f.exists() else pub_f
    if not src_f.exists():
        sys.exit(f"找不到 {a.slug} 的论文列表（{raw_f.name} 或 {pub_f.relative_to(REPO)}）")
    papers = [dict(p, id=p["id"].split("v")[0]) for p in json.loads(src_f.read_text(encoding="utf-8"))]
    out_f = REPO / "annotations/institutions" / f"{a.slug}.json"
    have = json.loads(out_f.read_text(encoding="utf-8")) if out_f.exists() else {}
    cache = REPO / ".cache/institutions" / a.slug
    cache.mkdir(parents=True, exist_ok=True)

    def src_of(pid: str) -> str | None:
        r = have.get(pid) or {}
        if r.get("source"):
            return r["source"]
        cf = cache / f"{pid}.json"
        return json.loads(cf.read_text(encoding="utf-8")).get("src") if cf.exists() else None

    def need_pdf(pid: str) -> bool:
        """HTML 版作者块里没找到机构（LaTeXML 常丢 \\affiliation 或脚注）：再用 PDF 首页问一次。"""
        r = have.get(pid) or {}
        return r.get("evidence") == "absent" and src_of(pid) == "html"

    def needs(pid: str) -> bool:
        r = have.get(pid)
        if r is None:
            return True
        if r.get("attempts", 1) >= 2:
            return False
        return r.get("confidence") == "low" or need_pdf(pid)

    todo = [p for p in papers if (a.all or p.get("day")) and needs(p["id"])
            and not (a.since and (p.get("day") or "") < a.since)]
    todo.sort(key=lambda p: (p.get("day") or "", p["id"]))
    if a.limit:
        todo = todo[:a.limit]
    retry = sum(p["id"] in have for p in todo)
    print(f"{a.slug}：待标一作机构 {len(todo)} 篇（其中重试 {retry}；来源 {src_f.relative_to(REPO)}）",
          file=sys.stderr)
    if not todo:
        return

    for i, p in enumerate(todo, 1):
        pdf_only = need_pdf(p["id"])
        cf = cache / f"{p['id']}{'.pdf' if pdf_only else ''}.json"
        if cf.exists():
            c = json.loads(cf.read_text(encoding="utf-8"))
        else:
            hdr, src = (None, "html") if pdf_only else (header_from_html(p["id"]), "html")
            if not hdr:
                hdr, src = header_from_pdf(p["id"]), "pdf"
            c = {"src": src, "hdr": hdr}
            if hdr:  # 抓不到的不缓存，下次再试
                cf.write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
        p["_src"], p["_hdr"] = c["src"], c["hdr"]
        if i % 20 == 0:
            print(f"  首页 {i}/{len(todo)}", file=sys.stderr)
    ready = [p for p in todo if p.get("_hdr")]
    print(f"  拿到首页 {len(ready)}/{len(todo)} 篇（html {sum(p['_src'] == 'html' for p in ready)}，"
          f"pdf {sum(p['_src'] == 'pdf' for p in ready)}）", file=sys.stderr)

    by_id = {p["id"]: p for p in ready}
    results: dict[str, dict] = {}

    def work(batch: list[dict]) -> dict[str, dict]:
        got = {}
        arr = parse_json_array(call_claude(prompt(batch), a.model) or "")
        ids = [str(x.get("id", "")).split("v")[0] for x in arr]
        for pid, x in zip(ids, arr):
            if pid not in by_id or ids.count(pid) != 1:   # 重复的 id 分不清是哪篇的，不要
                continue
            rec = {"first_author": str(x.get("first_author") or "").strip(),
                   "institution": str(x.get("institution") or "").strip(),
                   "country": str(x.get("country") or "").strip(),
                   "raw_affiliation": str(x.get("raw_affiliation") or "").strip(),
                   "evidence": x.get("evidence") if x.get("evidence") in EVIDENCE else "absent",
                   "confidence": x.get("confidence") if x.get("confidence") in CONF else "low",
                   "source": by_id[pid]["_src"]}
            if rec["institution"] and rec["evidence"] == "absent":
                rec["confidence"] = "low"
            if not _surname_ok((by_id[pid].get("authors") or [""])[0], rec["first_author"]):
                rec["confidence"] = "low"   # 模型认错了一作
            got[pid] = rec
        return got

    def commit(got: dict[str, dict]) -> None:
        """每批一落盘（先写临时文件再改名），断了不丢已经问过模型的结果。"""
        for pid, rec in got.items():
            old = have.get(pid)
            # 重试没问出更好的结果，就留着原来的
            if old and old.get("institution") and not rec["institution"]:
                rec = dict(old, source=old.get("source") or src_of(pid))
            rec["attempts"] = (old.get("attempts", 1) if old else 0) + 1
            have[pid] = rec
        out_f.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_f.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(have, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out_f)

    for rnd in range(3):
        left = [p for p in ready if p["id"] not in results]
        if not left:
            break
        batches = [left[i:i + BATCH] for i in range(0, len(left), BATCH)]
        print(f"  [{a.model}] 第 {rnd + 1} 轮：{len(left)} 篇 / {len(batches)} 批", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for f in as_completed([ex.submit(work, b) for b in batches]):
                try:
                    got = f.result()
                except Exception as e:
                    print(f"    批失败: {e}", file=sys.stderr)
                    continue
                results.update(got)
                commit(got)

    pub = sum(1 for pid in results if have[pid]["institution"] and have[pid]["confidence"] in ("high", "medium"))
    miss = [p["id"] for p in todo if p["id"] not in results]
    print(f"✅ {a.slug}：本次 {len(results)} 条（可发布 {pub}，无机构或低置信 {len(results) - pub}）→ "
          f"{out_f.relative_to(REPO)}" + (f"；{len(miss)} 篇没拿到，重跑会补：{miss[:5]}" if miss else ""))


if __name__ == "__main__":
    main()
