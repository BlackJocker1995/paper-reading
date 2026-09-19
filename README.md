# paper-reading

论文合集网页：以**日榜**为主视图——arXiv 每个公告日一批论文，按证据强度 + 新颖性排序；
每篇配中文标题与摘要，以及**只依据论文自身内容**的客观评价。
纯静态，托管在 GitHub Pages，没有后端。

线上： https://yunbolyu.github.io/paper-reading/

## 这个仓库放什么

```
index.html                              合集总入口
collections/
  arxiv-se-2026-09/
    index.html                          合集页面
    data/papers.json                    论文数据（脚本生成，勿手改）
    data/meta.json                      统计与筛选项
  <以后的合集>/                          同样结构
tools/build_site.py                     从本地 Obsidian vault 导出公开数据
.github/workflows/deploy.yml            只负责部署
```

## 客观评价怎么来的

每篇论文除翻译外，还有六项**只看论文内容**的标注：

| 字段 | 含义 |
|---|---|
| `contribution_type` | 新方法 / 新工具 / 基准数据集 / 实证研究 / 综述 / 立场 / 理论 / 复现 |
| `evidence_strength` 1–5 | 只看样本与数据规模、有无 baseline 与对照、有无统计检验、是否多设置复现 |
| `evidence_note` | 打这个分的具体依据（写出规模、baseline、检验等事实） |
| `novelty` 1–5 | 相对已有工作的新颖程度 |
| `novelty_note` | 新在哪里，或为何不新 |
| `scope` | 结论在什么语言 / 数据集 / 模型 / 规模下成立 |
| `limitations` | 最主要的局限 |
| `artifact` | 代码或数据是否公开 |

评分**不代表「值不值得读」**——那取决于读者在做什么。提示词里明确禁止使用
「值得读」「必读」这类面向特定读者的措辞。

## 日常更新

抓取和 AI 标注在本地跑（不需要往 GitHub 放任何 API key），推上来后 Actions 自动部署。

```bash
# 1. 在 Obsidian vault 里抓当天论文 + 翻译分类
python3 "Scripts/arxiv_se_web.py" --weekly

# 2. 给新论文补客观评价（只跑还没评过的）
python3 "Scripts/arxiv_objective.py" --month 2026-09

# 3. 在本仓库导出公开数据
python3 tools/build_site.py --month 2026-09

# 4. 推上去，Actions 自动部署
git add -A && git commit -m "update 2026-09" && git push
```

本地预览：

```bash
python3 -m http.server 8000
# 打开 http://localhost:8000/
```

## 隐私

- **读者的阅读进度默认只存在自己浏览器的 localStorage 里。** 本站没有后端、没有数据库、不做统计。
- 想跨设备的读者可以在合集页点「同步」，贴一个自己的 GitHub fine-grained token
  （只需 `Gists: Read and write`），进度写进**读者自己账号下的一个 private gist**。
  token 只存在读者本机，只发往 `api.github.com`，随时可在 GitHub 吊销。
- 这个仓库**只包含 2026-09 的公开数据**。导出脚本用字段白名单，
  并且显式拦截 `relevance` / `why` / 每日每周综述等带个人视角的字段；
  部署流程里还有一道同样的检查，泄漏就直接让构建失败。

## 免责

翻译和评价由模型生成，可能有误或过时，请以论文原文为准。
数据来自 [arXiv cs.SE](https://arxiv.org/list/cs.SE/recent)，版权归原作者所有。
