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
| `collections/arxiv-se-2026-09/` | arXiv cs.SE 日榜，按公告日分榜 | 证据强度 / 新颖性 / 贡献类型 |
| `collections/arxiv-cr-2026-09/` | arXiv cs.CR 日榜，同上结构 | 同上 |
| `collections/se-venues/` | ICSE·FSE·ASE·ISSTA 2026 研究轨道 1,005 篇 | 无评分，只有主题体系与中译 |
| `collections/ai-venues/` | ICLR·ICML·NeurIPS 九届 36,237 篇 | T1–T5 分级 / A1–A12 原型 |

**cs.CR 的页面是生成的，不要手改。** `collections/arxiv-cr-2026-09/index.html` 由
`python3 tools/make_cr_page.py` 从 cs.SE 页整份生成（16 处替换，每处必须恰好命中一次）。
改页面只改 cs.SE 那份，然后跑一次生成器。两边分别手改一定会漂移——已经发生过一次。

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
git add -A && git commit -m "update" && git push
```

顺序不能反：客观评分依赖翻译分类已完成。

本地预览：`python3 -m http.server 8000`

## 一作机构（2026-09-22 起）

`annotations/institutions/<slug>.json`，`build_site.py` 合并进公开数据，只发 high/medium。

- **不要去 arXiv API / OpenAlex / Semantic Scholar 找机构**，那三家都是空的（实测 50 篇：1 / 0 / 0）。
  机构只在论文首页上：HTML 版作者块优先，没有就 PDF 首页 `pdftotext -f 1 -l 1`。
- 抽取要用模型。正则在这批样本上只对 9/22，而且错得很安静（输出 `Affiliation:`、把作者名当机构）。
- **目前只有 2026-09-22 这批标了，且抓取脚本是一次性的（在 scratchpad，没进仓库）。**
  要常态化得先把脚本提进 `tools/`。
- 复用 vault 的 `arxiv_se_web.http()` 抓，它是全局唯一出口带跨进程限速；但它**会把 404 也重试**
  （10s/20s/40s），探测有没有 HTML 版时必须传 `tries=1`，否则每篇没 HTML 的论文白等三分钟。

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
