# paper-reading — 给 Claude Code 的项目说明

公开的论文合集网页，纯静态托管在 GitHub Pages。
线上：https://yunbolyu.github.io/paper-reading/

## 目录关系（重要）

这个仓库和数据来源是**两个独立的 git 仓库，同级目录，不要嵌套**：

```
~/Desktop/
├── Obsidian Vault/   → yunbo-notes 仓库。私人笔记与研究资料，抓取脚本在这里。
└── paper-reading/    → 本仓库。只放可公开的数据与页面。
```

**绝不要把 vault 的内容加进这个仓库**，也不要从 vault 目录部署 Pages——那边有未公开的研究资料。

导出脚本需要读 vault，路径可用环境变量覆盖：

```bash
VAULT=/path/to/vault python3 tools/build_site.py --month 2026-09
```

## 合集

| 目录 | 内容 | 评价口径 |
|---|---|---|
| `collections/arxiv-se-<YYYY-MM>/` | arXiv cs.SE 日榜，按公告日分榜，每月一个合集（2026-09 起） | 证据强度 / 新颖性 / 贡献类型 |
| `collections/arxiv-cr-<YYYY-MM>/` | arXiv cs.CR 日榜，同上结构 | 同上 |
| `collections/se-venues/` | ICSE·FSE·ASE·ISSTA 2026 研究轨道 1,005 篇 | 无评分，只有主题体系与中译 |
| `collections/ai-venues/` | ICLR·ICML·NeurIPS 九届 36,237 篇 | T1–T5 分级 / A1–A12 原型 |

**除 `arxiv-se-2026-09` 外的 arXiv 合集页都是生成的，不要手改。** 模板是
`collections/arxiv-se-2026-09/index.html`：cs.CR 9 月页由 `tools/make_cr_page.py` 生成（16 处替换，
每处必须恰好命中一次），其他月份的两个页由 `tools/make_month_page.py --month YYYY-MM` 生成。
改页面只改模板，然后重跑生成器（每个已有月份都跑一次）。分别手改一定会漂移——已经发生过一次。

**按月发布（2026-10-01 用户定的）**：arXiv 日榜从 2026-09 起每月一个合集，更早的月份不发。
换月步骤见 README「换月」。上个月最后几天要先按上个月跑完再切。

**se-venues 的源数据带个人相关度字段。** 它来自 Conference Paper Atlas（SE 四大会 + AI 三会合在一起
的那个 artifact），每篇有 `rel` / `rn`——按个人研究方向打的相关度与理由。`tools/build_se_venues.py`
用白名单把它们挡在外面，SE 这半的 `rel` 还恒为 `core`，本来也没有信息量。
注意 **ai-venues 的 `rel` / `rn` 是另一回事**：那是「这篇 AI 论文对软工这个领域的相关度」，
是有意发布的标注，总览页上也写明了，不要顺手把它一起删掉。

**三套口径不要强行统一。** cs.SE 那套在 `Scripts/arxiv_objective.py`（vault 里）；
AI 合集的 T1–T5 定义内嵌在它自己的数据里，是独立构建的一套体系，合并会毁掉它；
se-venues 干脆没有评分维度，只有主题归类与翻译，也不要给它硬加一套。

## 评价必须客观（硬要求）

站上所有评分**只依据论文自身内容**，不依据任何特定读者的研究方向：

- 证据强度 = 样本与数据规模、有无 baseline 与对照、有无统计检验、是否多设置复现
- 新颖性 = 相对已有工作
- 另有适用边界、局限、artifact 是否公开

提示词里明确禁止「值得读」「必读」「对读者重要」这类措辞。
**不要把个人相关度打分引入公开站**——vault 那套流水线里有一个按个人研究方向打的
`relevance` / `why`，那是私人视图专用的，导出时被白名单挡掉。

## 隐私防线（两道，别拆）

1. `tools/build_site.py` 用字段白名单导出，并显式拦截
   `relevance` / `why` / `headline` / `overview_cn` / `picks` / `skip`，命中就直接退出。
2. `.github/workflows/deploy.yml` 部署前再查一遍，泄漏就让构建失败。

读者的阅读进度只存在读者浏览器 localStorage，或（可选）读者自己账号下的 private gist。
**本站没有后端，不收集任何数据**，不要引入需要服务端的方案。

## 日常更新

抓取和 AI 标注在本地跑（不往 GitHub 放 API key），推上来后 Actions 自动部署。
前两步在 vault 里，后两步在本仓库：

```bash
cd ~/Desktop/Obsidian\ Vault
python3 Scripts/arxiv_se_web.py --weekly            # 抓取 + 翻译分类
python3 Scripts/arxiv_objective.py --month 2026-09  # 只给新论文补客观评价（增量）

cd ~/Desktop/paper-reading
python3 tools/build_site.py --month 2026-09
python3 tools/fetch_institutions.py --slug arxiv-se-2026-09 --since 2026-09-22
python3 tools/build_site.py --month 2026-09          # 再导一次，把机构并进去
git add -A && git commit -m "update" && git push
```

顺序不能反：客观评分依赖翻译分类已完成。

cs.CR 全在本仓库（README「日常更新」）：`fetch_arxiv.py` → `annotate_collection.py` →
`fetch_institutions.py` → `build_collection.py`。

- `fetch_arxiv.py` 是**增量**的：本机没有 `.cache/fetch/<slug>.raw.json` 时从已发布的 `papers.json` 还原，
  不要删了 raw 重抓——recent 只有最近 5 个公告日，覆盖写会把更早的论文整批丢掉。只收 `--month` 当月的公告日。
- `annotate_collection.py`：翻译用 sonnet、分类评级用 opus（用户定的分工），之后再过一道 opus **复核**
  （对照英文原文挑错，只改有问题的字段）。**复核别关**：2026-10-01 抽查不复核的 45 篇，42 篇有问题——
  评语里编造原文没有的事实（「首次」、没测过的防御）、术语误译（harness→测试框架、open-weight→开源）、
  倍数说错（1.32x increase→增加了 1.32 倍）。术语表在 `TAXONOMY[...]["terms"]`，硬规则在 `FACT_RULES`。
  评级提示里带着从已发布标注（同分类所有月份）现算的分数分布与样例，新标注对齐同一把尺子。
  改了口径要回头补：`--review-existing --days ...` 只对已有标注重新复核。
- **脚本的单次复核不够，每次更新后要再做一轮 agent 逐篇复核**（2026-10-04 起的做法）：
  每个 agent 约 12 篇，对照英文原文直接改标注文件；改完另派一个 agent 只读抽查量残余率。
  实测：复核前 cs.CR 144 篇里 83 篇、SE 55 篇里 35 篇有实质问题；复核后抽查 cs.CR 0/15、SE 1/10。
  代价约每 12 篇 20 万 token。SE 的标注在 vault 缓存里（同一天在一个大 JSON），要先拆成每篇一个文件给 agent 改，
  再合并回 `.web/<date>.json` 与 `.web/objective/<id>.json`，别让多个 agent 同时改同一个文件。
  topic 的边界口径写在脚本的 `TAXONOMY` 里，是对照已发布 323 篇归纳的，改之前先看已发布的怎么归。
- cs.SE 与 cs.CR 的抓取都打 arxiv.org，别同时跑。

本地预览：`python3 -m http.server 8000`

## 一作机构（2026-09-22 起）

`annotations/institutions/<slug>.json`，由 `tools/fetch_institutions.py` 标，
`build_site.py`（cs.SE）与 `build_collection.py`（cs.CR）合并进公开数据，只发 high/medium。

- **不要去 arXiv API / OpenAlex / Semantic Scholar 找机构**，那三家都是空的（实测 50 篇：1 / 0 / 0）。
  机构只在论文首页上：HTML 版作者块优先，没有就 PDF 首页 `pdftotext -f 1 -l 1`。
- 抽取要用模型。正则在这批样本上只对 9/22，而且错得很安静（输出 `Affiliation:`、把作者名当机构）。
- 覆盖：cs.SE 从 09-22 起（更早的日榜没标，要补就去掉 `--since`，约 460 篇），cs.CR 全部日榜。
- 脚本自己限速 3.5s/请求，404（没有 HTML 版）立刻退到 PDF，不重试。
- 模型认的一作和 arXiv 元数据第一作者对不上时，自动降为 low（不发布）。

## 已知坑

- **arXiv 限流**：`export.arxiv.org` 的 API 会按 IP 封配额（响应体 `Rate exceeded.`），
  惩罚是分钟级，重试十几分钟无效。抓取脚本已做全局限速（3.5~4s/请求，状态跨进程持久化）、
  撞 429 记 30 分钟冷却、元数据永久缓存、API 不通自动改走 `arxiv.org/abs/` 页面。
  `python3 Scripts/arxiv_se_web.py --status` 看状态。**不要把间隔调小。**
- **macOS Python 根证书**：`http()` 优先用 certifi，否则每个请求都 `CERTIFICATE_VERIFY_FAILED`。
- **标注批大小 `BATCH=6`**：12 篇时一个坏转义会让整批 JSON 解析失败。
  `extract_json` 整批失败会逐个抢救顶层对象，`annotate_batch` 按字段完整性重问缺的那几篇。
- **cs.SE 合集有 60 篇没有公告日**：月初批量归档进来的，arXiv 不保留历史公告日，
  所以只出现在「全月」视图、不进日榜。**不要拿 `published` 去凑公告日**，那是错的数据。
- **`ai-venues/data/ledger.js` 14.8MB**：刻意外置，别再内联回 HTML。
  该合集数据完整度不均匀（3.6 万篇里约 2000 篇有中译、5785 篇有分级），是原始状态。
