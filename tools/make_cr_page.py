#!/usr/bin/env python3
"""从 cs.SE 合集页生成 cs.CR 合集页：同一套模板，替换分类相关的文案与配置。

每条替换都必须恰好命中一次，否则报错退出——防止模板悄悄变了没被发现。
"""
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "collections/arxiv-se-2026-09/index.html"
DST = REPO / "collections/arxiv-cr-2026-09/index.html"

REPLACEMENTS = [
    # 页头
    ("<title>arXiv cs.SE · 2026-09</title>",
     "<title>arXiv cs.CR · 2026-09</title>"),
    ('<meta name="description" content="2026 年 9 月 arXiv cs.SE 论文的中文摘要与客观评价：贡献类型、证据强度、新颖性、适用边界与局限。">',
     '<meta name="description" content="2026 年 9 月 arXiv cs.CR（密码学与安全）论文的中文摘要与客观评价：贡献类型、证据强度、新颖性、适用边界与局限。">'),
    ('<span class="brand-mark">arXiv</span><span class="brand-name">cs.SE</span>',
     '<span class="brand-mark">arXiv</span><span class="brand-name">cs.CR</span>'),
    # localStorage：每个合集一份进度
    ('var LS = "paper-reading:arxiv-se-2026-09";',
     'var LS = "paper-reading:arxiv-cr-2026-09";'),
    # 主题分类体系：cs.CR 的安全子领域
    ('''var TOPIC_RANK = ["AI Coding Agent","LLM4SE","Software Testing","Bug & Defect","Code Analysis",
  "MSR & Empirical","Maintenance & Evolution","Requirements & Architecture","Security & Vulnerability",
  "ML/AI Security & Quality","Human & Process","Systems & Performance","Other"];''',
     '''var TOPIC_RANK = ["AI for Security","Security of AI","Vulnerability & Exploitation",
  "Malware & Attacks","Systems & Network Security","Crypto & Protocols","Privacy",
  "Measurement & Empirical","Formal Methods & Theory","Other"];'''),
    # 日榜导语
    ("""return '<section class="intro"><h1>' + esc(state.day) + " " + wd(state.day) + ' · cs.SE 日榜</h1>' +
      '<p>arXiv 当天公告的 <strong>' + n + '</strong> 篇 cs.SE 论文，默认按<strong>证据强度 + 新颖性</strong>排序。' +
      '这两项<strong>只依据论文自身内容</strong>：前者看样本规模、baseline 与对照、统计检验，' +
      '后者看相对已有工作的新意。排序不代表「值不值得读」——那取决于你在做什么。</p>' +""",
     """return '<section class="intro"><h1>' + esc(state.day) + " " + wd(state.day) + ' · cs.CR 日榜</h1>' +
      '<p>arXiv 当天公告的 <strong>' + n + '</strong> 篇 cs.CR（密码学与安全）论文，默认按<strong>证据强度 + 新颖性</strong>排序。' +
      '这两项<strong>只依据论文自身内容</strong>：前者看样本规模、baseline 与对照、统计检验，' +
      '后者看相对已有工作的新意。排序不代表「值不值得读」——那取决于你在做什么。</p>' +"""),
    # 全月导语
    ("""  return '<section class="intro"><h1>' + esc(META.title || "arXiv cs.SE") + '</h1>' +
    '<p>这一页收录 ' + (META.count||0) + ' 篇 2026 年 9 月 arXiv <code>cs.SE</code> 论文的中文标题与摘要，' +
    '以及一份<strong>只依据论文自身内容</strong>的评价：贡献类型、证据强度、新颖性、适用边界、局限、artifact 是否公开。' +
    '评分不代表「值不值得读」，只描述这篇论文<em>拿出了什么证据</em>、<em>新在哪里</em>、<em>结论在什么范围内成立</em>。</p>' +""",
     """  return '<section class="intro"><h1>' + esc(META.title || "arXiv cs.CR") + '</h1>' +
    '<p>这一页收录 ' + (META.count||0) + ' 篇 2026 年 9 月 arXiv <code>cs.CR</code>（密码学与安全）论文的中文标题与摘要，' +
    '以及一份<strong>只依据论文自身内容</strong>的评价：贡献类型、证据强度、新颖性、适用边界、局限、artifact 是否公开。' +
    '评分不代表「值不值得读」，只描述这篇论文<em>拿出了什么证据</em>、<em>新在哪里</em>、<em>结论在什么范围内成立</em>。</p>' +"""),
    # 全月导语第三段：cs.CR 首批先覆盖最近几个公告日的标注
    ("""'<p class="hint">其中 ' + (META.count - (META.with_day||0)) + ' 篇是月初批量归档进来的，' +
    'arXiv 不保留历史公告日，所以它们只出现在「全月」里，不进日榜。</p>' +""",
     """'<p class="hint">本合集先从 ' + (META.annotated_days||[]).length + ' 个公告日（' + (META.annotated_days||[]).join("、") +
    '）开始做中文摘要与逐篇评价；其余论文先提供原文与全文检索，标注随公告日逐步补齐。</p>' +"""),
    # BibTeX 兜底分类
    ('(p.primary_category||"cs.SE")', '(p.primary_category||"cs.CR")'),
    # Markdown 导出的元信息
    ('"tags: [arxiv, cs.SE]"', '"tags: [arxiv, cs.CR]"'),
    ('"# arXiv cs.SE · " + scope', '"# arXiv cs.CR · " + scope'),
    ('a.download = "arxiv-cs-se-"', 'a.download = "arxiv-cs-cr-"'),
]

h = SRC.read_text(encoding="utf-8")
for old, new in REPLACEMENTS:
    n = h.count(old)
    if n != 1:
        sys.exit(f"替换源文命中 {n} 次（应为 1）：{old[:70]}…")
    h = h.replace(old, new)

# 日榜导语里补一句标注覆盖提示：未标注的公告日先给原文
OLD_STAT = """'<span class="stat"><b>' + DAYS.length + '</b>个公告日可切换</span>' +"""
NEW_STAT = """'<span class="stat"><b>' + DAYS.length + '</b>个公告日可切换</span>' +
      ((META.annotated_days||[]).indexOf(state.day) < 0 &&
        '<span class="stat">这一天的中文摘要与评价还没做，先按原文阅读</span>' || "") +"""
assert h.count(OLD_STAT) == 1
h = h.replace(OLD_STAT, NEW_STAT)

# 未标注论文没有 topic 字段：计数、筛选、徽章三处都归一到「未标注」，
# 否则主题筛选里会出现 "undefined" 这一栏
PAIRS = [
    ("""  PAPERS.forEach(function(p){
    tc[p.topic] = (tc[p.topic]||0)+1;""",
     """  PAPERS.forEach(function(p){
    var t0 = p.topic || "未标注";
    tc[t0] = (tc[t0]||0)+1;"""),
    ("""  var t = keys(state.topics); if(t.length && t.indexOf(p.topic) < 0) return false;""",
     """  var t = keys(state.topics); if(t.length && t.indexOf(p.topic || "未标注") < 0) return false;"""),
    ("""  h += '<span class="badge" data-act="topic" data-topic="' + esc(p.topic) + '">' + esc(p.topic) + "</span>";""",
     """  var tp = p.topic || "未标注";
  h += '<span class="badge" data-act="topic" data-topic="' + esc(tp) + '">' + esc(tp) + "</span>";"""),
]
for old, new in PAIRS:
    n = h.count(old)
    if n != 1:
        sys.exit(f"替换源文命中 {n} 次（应为 1）：{old[:70]}…")
    h = h.replace(old, new)

DST.parent.mkdir(parents=True, exist_ok=True)
DST.write_text(h, encoding="utf-8")
print(f"✅ 生成 {DST.relative_to(REPO)}（{len(REPLACEMENTS)+1+len(PAIRS)} 处替换）")
