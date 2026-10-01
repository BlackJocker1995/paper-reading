#!/usr/bin/env python3
"""给 arXiv 合集里还没标注的论文做中文翻译 + 客观评价，写 annotations/<slug>/<id>.json。

模型走本地 claude CLI（不需要、也不往仓库放 API key），分两段：
  翻译 sonnet：title_cn / abstract_cn —— 输出长、纯翻译，便宜快
  评级 opus  ：summary_cn / topic / tags / objective_summary / contribution_type /
              evidence_strength / evidence_note / novelty / novelty_note / scope /
              limitations / artifact
  复核 opus  ：拿英文原文逐篇挑错（漏译误译、编造原文没有的事实、数字、topic 口径），只改有问题的字段。
              2026-10-01 抽查发现不复核时 45 篇里 42 篇有问题（编造事实 34 处、误译 34 处），所以默认开。
各段按篇缓存在 .cache/annotate/<slug>/（gitignore），断了重跑只补缺的；
一篇三段都齐、并通过字段检查，才写进 annotations/。

默认只做有公告日、还没有标注文件的论文（日榜要整日标齐才出现）；--all 连没有公告日的也做。

用法：
    python3 tools/annotate_collection.py --category cs.CR --month 2026-09
    python3 tools/annotate_collection.py --category cs.CR --ids 2609.25014,2609.25043 --out /tmp/try
        # 试跑：结果写到 /tmp/try，不碰仓库
    python3 tools/annotate_collection.py --category cs.CR --review-existing --days 2026-09-16,2026-09-17
        # 只对已有标注再做一遍复核（改口径、或补复核时用）
"""
from __future__ import annotations

import argparse, json, os, re, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLAUDE_CLI = os.environ.get("CLAUDE_CLI") or shutil.which("claude") or "claude"
TR_BATCH, EV_BATCH, RV_BATCH, WORKERS, ROUNDS = 5, 8, 3, 4, 3

KEYS = ["id", "title_cn", "abstract_cn", "summary_cn", "topic", "tags", "objective_summary",
        "contribution_type", "evidence_strength", "evidence_note", "novelty", "novelty_note",
        "scope", "limitations", "artifact"]
TR_KEYS = ["title_cn", "abstract_cn"]
EV_KEYS = [k for k in KEYS if k not in ("id", *TR_KEYS)]
CONTRIBUTION = ["新方法/新技术", "新工具/系统", "基准/数据集", "实证研究", "综述/系统性文献回顾",
                "立场/展望", "理论/形式化", "复现/负面结果"]
ARTIFACT = ["公开", "承诺公开", "未提及"]
BANNED = re.compile(r"值得(一)?读|必读|推荐阅读|强烈推荐|对读者|建议阅读|不容错过")

# 各分类的主题体系。cs.SE 走 vault 那条流水线，这里只管仓库自足的合集。
# cs.CR 的边界口径是对照已发布 323 篇（09-21~09-28）归纳出来的，改之前先看已发布的怎么归。
TAXONOMY = {
    "cs.CR": {
        "name": "密码学与安全",
        "topics": {
            "AI for Security": "用 LLM/agent（或以 AI 为主角）完成安全任务；评估 AI 生成物（如 AI 生成代码）的安全性；"
                               "深度伪造/AI 生成内容检测（含人类识别深伪的用户研究）；LLM 安全能力基准；"
                               "被当作安全工具使用的 AI（钓鱼判定、来源验证器、安全测试裁判）能否被操纵",
            "Security of AI": "AI 系统本身的攻防：越狱、提示注入、后门/投毒（含投毒 ML 恶意软件检测器）、对抗样本、"
                              "模型窃取、护栏与注入检测器被绕过；agent 授权、权限隔离、运行时治理；模型/生成内容水印；"
                              "联邦/分布式学习的拜占庭鲁棒；LLM 推理完整性与身份验证（即使手段是 ZK、承诺、同态）；"
                              "以移除有害能力为动机的遗忘与概念擦除",
            "Vulnerability & Exploitation": "漏洞发现与利用：模糊测试、静态/动态分析、漏洞利用、补丁、CVE、供应链漏洞；"
                                            "LLM 只是组件、贡献落在漏洞问题上的也归这里；必须以安全漏洞为对象，"
                                            "通用程序分析工具摘要里不涉及安全应用的归 Other",
            "Malware & Attacks": "恶意软件、勒索、钓鱼、诈骗、洗钱、攻击活动及其检测（含常规 ML 检测及其评估）；"
                                 "研究对象是具体攻击的测量研究（蜜罐采集、链上攻击测量等）",
            "Systems & Network Security": "OS/硬件/TEE/侧信道（传感器、电磁）/网络/无线/车载/IoT/Web 安全；"
                                          "常规 ML/DL 做的入侵检测与流量异常检测（含对 IDS 模型的评估与压缩）；"
                                          "区块链共识、罚没/激励、MEV 机制及其博弈分析、链上防护系统",
            "Crypto & Protocols": "密码学构造与分析、后量子（含 PQC 性能基准与迁移）、同态/MPC/零知识、签名、VRF、"
                                  "支付通道等密码与安全协议；以同态等密码技术为主的联邦学习",
            "Privacy": "隐私威胁与保护、差分隐私、再识别/去匿名化、设备追踪、元数据推断；隐写与隐蔽信道"
                       "（含借 LLM 的隐蔽通信）；以数据删除请求为动机的机器遗忘；隐私监管",
            "Measurement & Empirical": "对象是生态、市场或从业实践的测量与人的研究；网络风险量化",
            "Formal Methods & Theory": "安全性质的形式化验证、形式模型与证明（非密码构造本身）",
            "Other": "主体不是安全问题：通用程序分析（摘要不涉及安全应用）、系统性能、可靠性/容错、"
                     "一般法规落地/频谱/成本治理",
        },
        "terms": "side channel=侧信道、prompt injection=提示注入、jailbreak=越狱、backdoor=后门、poisoning=投毒、"
                 "membership inference=成员推断、fuzzing=模糊测试、TEE=可信执行环境、zero-knowledge=零知识、"
                 "agent=智能体、harness=harness（保留英文，不是「测试框架」）、containment=隔离、"
                 "open-weight=开放权重（不是「开源」）、power-of-two=2 的幂、blinded=盲评、"
                 "N× / N-fold increase=增至原来的 N 倍（不是「增加了 N 倍」）",
    },
}


# ---------------------------------------------------------------- 模型调用

def call_claude(prompt: str, model: str, timeout: int = 900, retries: int = 2) -> str | None:
    for attempt in range(retries + 1):
        try:
            r = subprocess.run([CLAUDE_CLI, "-p", prompt, "--output-format", "text", "--model", model],
                               capture_output=True, text=True, timeout=timeout)
            if r.stdout.strip():
                return r.stdout.strip()
            print(f"    {model} 空输出: {r.stderr[:200]!r}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"    {model} 超时 {timeout}s", file=sys.stderr)
        except Exception as e:
            print(f"    {model} 异常: {e}", file=sys.stderr)
        if attempt < retries:
            time.sleep(5)
    return None


def salvage_objects(text: str) -> list[dict]:
    """整段 JSON 解析失败时逐个扫出还能解析的顶层对象，不让一篇写坏的引号毁掉一整批。"""
    dec, out, i = json.JSONDecoder(), [], 0
    while (ch := text.find("{", i)) >= 0:
        try:
            obj, end = dec.raw_decode(text, ch)
        except json.JSONDecodeError:
            i = ch + 1
            continue
        if isinstance(obj, dict):
            out.append(obj)
        i = end
    return out


def parse_json_array(text: str) -> list[dict]:
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1)
    s, e = text.find("["), text.rfind("]")
    if s < 0 or e <= s:
        return salvage_objects(text)
    try:
        arr = json.loads(text[s:e + 1])
        return [x for x in arr if isinstance(x, dict)]
    except json.JSONDecodeError:
        return salvage_objects(text[s:e + 1])


# ---------------------------------------------------------------- 翻译（sonnet）

def tr_prompt(cat: str, batch: list[dict]) -> str:
    tax = TAXONOMY[cat]
    slim = [{"arxiv_id": p["id"], "title": p["title"], "abstract": p["abstract"]} for p in batch]
    return f"""把下面 {len(batch)} 篇 arXiv {cat}（{tax['name']}）论文的标题和摘要译成中文，给一个公开的论文合集网页用。严禁偷懒、严禁省略。

要求：
- 标题忠实原题；系统/方法/数据集名保留英文原名。
- 摘要**完整**翻译：逐句对应，不漏句、不概括、不添加原文没有的内容；数字、百分比、倍数、样本量、模型名、数据集名、会议名照搬。
- 用领域惯用译法：{tax['terms']}。
- 原文里的 LaTeX（如 $p^{{1/3}}$、\\%、\\textsc{{X}}）改写成纯文本（p^(1/3)、%、X）。
- 中文里要用引号一律用「」。

输出格式（严格如下，每篇一段，顺序与输入一致，不要任何其他文字）：
###paper <arxiv_id>
title_cn: <中文标题>
abstract_cn: <中文摘要，写成一段>

输入：
{json.dumps(slim, ensure_ascii=False)}"""


def parse_tr(text: str) -> dict[str, dict]:
    """###paper 分隔格式。一块里只认一个 title_cn / abstract_cn，摘要截到空行或下一个字段头为止，
    免得格式走样时把下一篇（或末尾的代码围栏、附言）并进上一篇。可疑的块整块丢掉，下一轮重问。"""
    text = re.sub(r"^\s*```\w*\s*$", "", text, flags=re.M)
    got, seen = {}, set()
    for chunk in re.split(r"^#{2,}\s*paper\s+", text, flags=re.M | re.I)[1:]:
        head, _, body = chunk.partition("\n")
        pid = head.strip().strip("<>").strip().split("v")[0]
        if pid in seen:          # 同一个 id 出现两次：不知道哪块是真的，都不要
            got.pop(pid, None)
            continue
        seen.add(pid)
        if len(re.findall(r"^\s*title_cn[:：]", body, re.M)) != 1 or \
                len(re.findall(r"^\s*abstract_cn[:：]", body, re.M)) != 1:
            continue
        t = re.search(r"^\s*title_cn[:：]\s*(.+)$", body, re.M)
        a = re.search(r"^\s*abstract_cn[:：]\s*(.+?)(?=\n\s*\n|^\s*(?:#{2,}|title_cn|abstract_cn)|\Z)", body, re.M | re.S)
        if t and a:
            got[pid] = {"title_cn": t.group(1).strip(),
                        "abstract_cn": " ".join(a.group(1).split())}
    return got


def tr_ok(p: dict, x: dict) -> str | None:
    if not x.get("title_cn") or not x.get("abstract_cn"):
        return "空"
    cn = x["abstract_cn"]
    ratio = len(p["abstract"]) / max(1, len(cn))
    if ratio > 5.5:  # 英文字符 / 中文字数正常在 2~4，太大多半是概括或截断了
        return f"摘要疑似不完整（长度比 {ratio:.1f}）"
    if ratio < 1.3:  # 太小多半是没翻、或把别的内容并进来了
        return f"摘要疑似混入别的内容（长度比 {ratio:.1f}）"
    if len(re.findall(r"[\u4e00-\u9fff]", cn)) < 0.3 * len(cn):
        return "摘要里中文太少，疑似没翻"
    return None


# ---------------------------------------------------------------- 评级（opus）

def calibration(cat: str, per_topic: int = 2) -> str:
    """从同一分类**所有月份**已有的标注里现算分数分布 + 每个主题挑几篇样例，让新标注和已有的用同一把尺子。
    跨月取，是为了月初新合集还一篇没标时也有参照。"""
    from collections import Counter
    short = cat.split(".")[-1].lower()
    anns, titles = [], {}
    for d in sorted((REPO / "annotations").glob(f"arxiv-{short}-*")):
        src = REPO / ".cache/fetch" / f"{d.name}.raw.json"
        src = src if src.exists() else REPO / "collections" / d.name / "data/papers.json"
        if src.exists():
            titles.update({p["id"].split("v")[0]: p["title"]
                           for p in json.loads(src.read_text(encoding="utf-8"))})
        for f in sorted(d.glob("*.json")):
            try:
                anns.append(json.loads(f.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    if not anns:
        return ""
    dist = lambda k: "、".join(f"{s} 分 {n} 篇" for s, n in sorted(Counter(x.get(k) for x in anns).items()))
    lines, seen = [], Counter()
    for x in anns:  # 按 id 顺序，每个主题取前几篇
        if seen[x.get("topic")] < per_topic and x["id"] in titles:
            seen[x.get("topic")] += 1
            lines.append(f"  {x['topic']} | {x['contribution_type']} | 证据 {x['evidence_strength']} 新颖 "
                         f"{x['novelty']} | {titles[x['id']]}")
    return (f"已发布的 {len(anns)} 篇同分类标注，打分和归类请与它们保持**同一尺度**：\n"
            f"- 证据强度分布：{dist('evidence_strength')}\n- 新颖性分布：{dist('novelty')}\n"
            f"- 样例（topic | 贡献类型 | 分数 | 标题）：\n" + "\n".join(lines) + "\n")


FACT_RULES = """硬规则（抽查里最常见的错，逐条遵守）：
- 每条事实——数字、实验设置、对照组、数据集、被测的防御或攻击、统计检验、代码是否公开、「首个/首次」——
  都必须能在摘要或 comment 里找到。找不到就不写，或写「摘要未给」。不要替作者补全实验细节。
- 原文的限定（「在成功运行中」「在所测的 5 个 harness 上」「可能」）要保留，不许丢掉或说重。
- 原文没报告统计显著性就不要用「显著」；原文没说「主流」「首次」就不要写。
- 时态与情态照原文：will / plan to 是「计划」，不是已完成；may / can 是「可能」，不是一定。
- 不同实验的数字不要合并成一句（A 实验的样本量配 B 实验的结果），各说各的。
- scope 只用摘要里出现过的系统 / 数据集 / 模型 / 规模名词，不要添加原文没有的限定（如「模拟环境」「非量子」）。
- limitations 优先写作者自陈的局限；作者没写时，只写摘要里直接可见的范围限制（如「只在 X 上评估」），
  或写「摘要未讨论局限」——**不要推测方法缺陷**。作者的免责声明不是局限。"""


def ev_prompt(cat: str, batch: list[dict], calib: str = "") -> str:
    tax = TAXONOMY[cat]
    slim = [{"arxiv_id": p["id"], "title": p["title"], "abstract": p["abstract"],
             "comment": p.get("comment") or "", "categories": p.get("categories") or []} for p in batch]
    topics = "\n".join(f'- "{k}"：{v}' for k, v in tax["topics"].items())
    return f"""你在给一个**公开的** arXiv {cat}（{tax['name']}）论文合集网页做客观评价标注。严禁偷懒、严禁省略。

下面是 {len(batch)} 篇论文：
{json.dumps(slim, ensure_ascii=False)}

评价必须**只依据论文自身内容**（标题 + 摘要 + comment），不考虑「对某个读者是否重要」「是否热门」，
不引入你对这篇论文的任何外部了解。禁止出现「值得读」「必读」「推荐阅读」「对读者」这类面向特定读者的措辞。

topic 严格取其一（英文原样），按论文的**主要贡献**归类：
{topics}

{calib}
{FACT_RULES}

为每一篇输出一个对象，字段：
- "id": 原样抄 arxiv_id
- "summary_cn": 一句中文要点（约 40-90 字）：做了什么 + 关键结果，有数字带数字；comment 里有录用会议可在末尾括注（如「（IEEE S&P'27）」）
- "topic": 见上
- "tags": 3-4 个中文短标签的数组（专有缩写可保留英文，如「LLM 安全」「TEE」）
- "objective_summary": 1-2 句中文，中立陈述「做了什么 + 用什么验证」，不评价好坏
- "contribution_type": 严格取其一：{" / ".join(CONTRIBUTION)}
- "evidence_strength": 1-5 整数，**只看证据本身**：样本/数据规模、有无 baseline 与对照、有无统计检验、是否多设置复现。
  5=大规模且有对照与统计检验；3=有实验但规模或对照有限；1=仅示例、玩具实验或无实证。纯理论论文看证明是否完整给出。
  大多数论文在 2-4，5 很少。
- "evidence_note": 一句话写打分的**具体依据**（规模、baseline、检验等原文里能找到的事实；原文没给的就说摘要未给，不许编），20-60 字
- "novelty": 1-5 整数：5=此前没有的问题/方法/发现；3=已有方向上的实质改进；1=常规组合或复述已知结论。大多数论文是 2-4。
- "novelty_note": 一句话说新在哪里或为何不新，20-50 字；原文没自称「首个」就不要写「首个」
- "scope": 一句话写结论的适用边界（在什么系统/数据集/模型/规模/威胁模型下成立），20-50 字
- "limitations": 一句话写最主要的局限，20-50 字；规则见上面的硬规则，宁可写「摘要未讨论局限；仅在 X 上评估」也不要猜
- "artifact": 严格取其一：公开（摘要或 comment 给了代码/数据链接——arXiv 会把链接显示成 this https URL——或明确说已公开）/
  承诺公开（will be released、upon acceptance 之类）/ 未提及。指向别的论文或 DOI 的链接不算。

只输出一个 JSON 数组，长度必须是 {len(batch)}，顺序与输入一致，不要任何解释文字。
中文用全角标点（，。；：），要用引号请用「」，不要在字符串里出现英文双引号、反斜杠或 LaTeX。"""


def as_score(v) -> int | None:
    try:
        return min(5, max(1, int(float(v))))
    except (TypeError, ValueError):
        m = re.search(r"[1-5]", str(v or ""))
        return int(m.group()) if m else None


def normalize_ev(cat: str, x: dict) -> dict | None:
    out = {k: x.get(k) for k in EV_KEYS}
    out["evidence_strength"] = as_score(out["evidence_strength"])
    out["novelty"] = as_score(out["novelty"])
    if isinstance(out["tags"], str):
        out["tags"] = [t.strip() for t in re.split(r"[,，、]", out["tags"]) if t.strip()]
    if str(out.get("artifact", "")).strip() not in ARTIFACT:
        out["artifact"] = "未提及"
    for k, v in out.items():
        if isinstance(v, str):
            out[k] = v.strip()
    return out


def ev_ok(cat: str, x: dict) -> str | None:
    missing = [k for k in EV_KEYS if x.get(k) in (None, "", [])]
    if missing:
        return f"缺 {missing}"
    if not isinstance(x["topic"], str) or not isinstance(x["contribution_type"], str):
        return "topic / contribution_type 不是字符串"
    if x["topic"] not in TAXONOMY[cat]["topics"]:
        return f"topic={x['topic']!r}"
    if x["contribution_type"] not in CONTRIBUTION:
        return f"contribution_type={x['contribution_type']!r}"
    if not isinstance(x["tags"], list) or not 2 <= len(x["tags"]) <= 5:
        return f"tags={x['tags']!r}"
    text = " ".join(str(v) for v in x.values())
    if m := BANNED.search(text):
        return f"禁用措辞「{m.group()}」"
    return None


# ---------------------------------------------------------------- 复核（opus）

def rv_prompt(cat: str, batch: list[dict]) -> str:
    tax = TAXONOMY[cat]
    topics = "\n".join(f'- "{k}"：{v}' for k, v in tax["topics"].items())
    items = [{"arxiv_id": p["id"], "title": p["title"], "abstract": p["abstract"],
              "comment": p.get("comment") or "", "annotation": {k: p["_rec"][k] for k in KEYS if k != "id"}}
             for p in batch]
    return f"""你在复核一个**公开的** arXiv {cat}（{tax['name']}）论文合集网页的中文标注。默认怀疑：逐篇对照英文原文找错。

{json.dumps(items, ensure_ascii=False)}

逐篇逐字段检查：
1. title_cn、abstract_cn：漏句、添加原文没有的内容、误译术语、数字/百分比/倍数/样本量/模型名/数据集名/会议名与原文不一致。
   领域译法：{tax['terms']}。
2. summary_cn、objective_summary、evidence_note、novelty_note、scope、limitations：
{FACT_RULES}
3. topic 严格按下面的定义与边界（英文原样）；contribution_type 取其一：{" / ".join(CONTRIBUTION)}。
{topics}
4. evidence_strength / novelty 只在明显违背锚点时改（证据：5=大规模且有对照与统计检验，3=有实验但规模或对照有限，1=无实证；
   新颖：5=此前没有，3=已有方向的实质改进，1=常规组合）。3/4 之间的边界不要动。
5. artifact：给了链接（arXiv 显示成 this https URL）或明确已公开=公开；will be released=承诺公开；否则未提及。
6. 不许有「值得读」「必读」等面向读者的话；中文用全角标点；不要英文双引号、反斜杠、LaTeX。

为每一篇输出 {{"id": "<arxiv_id>", "fix": {{字段: 改正后的完整新值, ...}}}}。只放**需要改**的字段；完全没问题就给空对象 {{}}。
改 abstract_cn 时给整段新摘要，不要只给片段。只输出一个 JSON 数组，长度 {len(batch)}，顺序与输入一致，不要任何解释文字。"""


def normalize_fix(fix) -> dict | None:
    if not isinstance(fix, dict):
        return None
    out = {}
    for k, v in fix.items():
        if k not in KEYS or k == "id":
            continue
        if k in ("evidence_strength", "novelty"):
            v = as_score(v)
        elif k == "tags" and isinstance(v, str):
            v = [x.strip() for x in re.split(r"[,，、]", v) if x.strip()]
        elif isinstance(v, str):
            v = v.strip()
        out[k] = v
    return out


def apply_fix(rec: dict, fix: dict) -> dict:
    return {k: fix.get(k, rec[k]) for k in KEYS}


def rv_ok(cat: str, p: dict, x: dict) -> str | None:
    new = apply_fix(p["_rec"], x["fix"])
    return tr_ok(p, new) or ev_ok(cat, {k: new[k] for k in EV_KEYS})


# ---------------------------------------------------------------- 调度

def atomic_write(f: Path, text: str) -> None:
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(f)


def read_cached(f: Path, check) -> dict | None:
    """缓存读出来再过一遍检查；坏的删掉，下次重问。"""
    if not f.exists():
        return None
    try:
        x = json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        x = None
    if x is None or check(x):
        f.unlink(missing_ok=True)
        return None
    return x


def run_stage(kind: str, cat: str, papers: list[dict], cache: Path, model: str, calib: str = "") -> None:
    """对 papers 里 cache 还没有的，按批调模型；每轮只重问没过检查的那几篇，最多 ROUNDS 轮。"""
    cache.mkdir(parents=True, exist_ok=True)
    size = {"tr": TR_BATCH, "ev": EV_BATCH, "rv": RV_BATCH}[kind]
    for rnd in range(ROUNDS):
        todo = [p for p in papers if not (cache / f"{p['id']}.json").exists()]
        if not todo:
            return
        batches = [todo[i:i + size] for i in range(0, len(todo), size)]
        print(f"  [{kind}·{model}] 第 {rnd + 1} 轮：{len(todo)} 篇 / {len(batches)} 批", file=sys.stderr)

        def work(batch: list[dict]) -> tuple[int, list[str]]:
            by_id = {p["id"]: p for p in batch}
            if kind == "tr":
                out = call_claude(tr_prompt(cat, batch), model)
                got = parse_tr(out or "")
            else:
                prompt = ev_prompt(cat, batch, calib) if kind == "ev" else rv_prompt(cat, batch)
                out = call_claude(prompt, model)
                arr = [x for x in parse_json_array(out or "") if isinstance(x, dict)]
                ids = [str(x.get("id", "")).split("v")[0] for x in arr]
                # 同一个 id 回了两次：分不清哪条是哪篇的，都不要
                if kind == "ev":
                    got = {pid: normalize_ev(cat, x) for pid, x in zip(ids, arr) if ids.count(pid) == 1}
                else:
                    got = {pid: {"fix": normalize_fix(x.get("fix", {}))} for pid, x in zip(ids, arr)
                           if ids.count(pid) == 1 and normalize_fix(x.get("fix", {})) is not None}
            if not got:  # 整批一篇都没解析出来：把输出开头打出来，限流/报错一眼能看到
                print(f"    {kind} 批无可解析输出：{(out or '')[:160]!r}", file=sys.stderr)
            ok, bad = 0, []
            for pid, x in got.items():
                if pid not in by_id or x is None:
                    continue
                why = (tr_ok(by_id[pid], x) if kind == "tr" else ev_ok(cat, x) if kind == "ev"
                       else rv_ok(cat, by_id[pid], x))
                if why:
                    bad.append(f"{pid}: {why}")
                    continue
                atomic_write(cache / f"{pid}.json", json.dumps(x, ensure_ascii=False))
                ok += 1
            bad += [f"{pid}: 模型没给" for pid in by_id if pid not in got]
            return ok, bad

        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = [ex.submit(work, b) for b in batches]
            for i, f in enumerate(as_completed(futs), 1):
                try:
                    ok, bad = f.result()
                except Exception as e:
                    print(f"    批失败: {e}", file=sys.stderr)
                    continue
                print(f"    {kind} 批 {i}/{len(batches)}：过 {ok}" + (f"，退回 {bad}" if bad else ""),
                      file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", default="cs.CR", choices=sorted(TAXONOMY))
    ap.add_argument("--month", default="2026-09")
    ap.add_argument("--all", action="store_true", help="没有公告日的论文也标（默认只标日榜上的）")
    ap.add_argument("--ids", help="只标这些 id（逗号分隔），已有标注和缓存都重做；配合 --out 试跑")
    ap.add_argument("--limit", type=int, help="只做前 N 篇")
    ap.add_argument("--out", help="结果写到这个目录而不是 annotations/<slug>/（试跑用，缓存也放那里）")
    ap.add_argument("--tr-model", default="sonnet", help="翻译用的模型，默认 sonnet")
    ap.add_argument("--ev-model", default="opus", help="分类评级用的模型，默认 opus")
    ap.add_argument("--rv-model", default="opus", help="复核用的模型，默认 opus")
    ap.add_argument("--no-review", action="store_true", help="跳过复核（不建议：不复核时错误率很高）")
    ap.add_argument("--review-existing", action="store_true",
                    help="不新标，只把已有标注再复核一遍；用 --ids 或 --days 选范围")
    ap.add_argument("--days", help="配合 --review-existing：只复核这些公告日（逗号分隔）")
    a = ap.parse_args()

    slug = f"arxiv-{a.category.split('.')[-1].lower()}-{a.month}"
    raw_f = REPO / ".cache/fetch" / f"{slug}.raw.json"
    if not raw_f.exists():
        sys.exit(f"没有 {raw_f.relative_to(REPO)}，先跑 tools/fetch_arxiv.py --category {a.category} --month {a.month}")
    raw = json.loads(raw_f.read_text(encoding="utf-8"))
    ann_dir = Path(a.out) if a.out else REPO / "annotations" / slug
    cache = (Path(a.out) / ".cache") if a.out else REPO / ".cache/annotate" / slug
    ann_dir.mkdir(parents=True, exist_ok=True)

    papers = [dict(p, id=p["id"].split("v")[0]) for p in raw]
    if a.review_existing:
        review_existing(a, papers, ann_dir, cache)
        return
    if a.ids:
        want = {x.strip().split("v")[0] for x in a.ids.split(",") if x.strip()}
        todo = [p for p in papers if p["id"] in want]
    else:
        todo = [p for p in papers if (a.all or p.get("day"))
                and not (ann_dir / f"{p['id']}.json").exists()]
    todo.sort(key=lambda p: (p.get("day") or "", p["id"]))
    if a.limit:
        todo = todo[:a.limit]
    print(f"{slug}：待标注 {len(todo)} 篇（翻译 {a.tr_model} / 评级 {a.ev_model}）", file=sys.stderr)
    if not todo:
        return

    if a.ids:  # 重做就是重做：清掉这些 id 的分段缓存
        for p in todo:
            for part in ("tr", "ev", "rv"):
                (cache / part / f"{p['id']}.json").unlink(missing_ok=True)
    calib = calibration(a.category)
    # 翻译与评级互不依赖，两段并行跑
    with ThreadPoolExecutor(max_workers=2) as ex:
        for f in [ex.submit(run_stage, "tr", a.category, todo, cache / "tr", a.tr_model),
                  ex.submit(run_stage, "ev", a.category, todo, cache / "ev", a.ev_model, calib)]:
            f.result()

    ready, missing = [], []
    for p in todo:
        tr = read_cached(cache / "tr" / f"{p['id']}.json", lambda x, p=p: tr_ok(p, x))
        ev = read_cached(cache / "ev" / f"{p['id']}.json", lambda x: ev_ok(a.category, x))
        if not (tr and ev):
            missing.append(p["id"])
            continue
        p["_rec"] = {"id": p["id"], **{k: tr[k] for k in TR_KEYS}, **{k: ev[k] for k in EV_KEYS}}
        ready.append(p)

    if not a.no_review and ready:
        run_stage("rv", a.category, ready, cache / "rv", a.rv_model)
    written, changed = 0, 0
    for p in ready:
        rec = p["_rec"]
        if not a.no_review:
            rv = read_cached(cache / "rv" / f"{p['id']}.json", lambda x, p=p: rv_ok(a.category, p, x))
            if rv is None:
                missing.append(p["id"])
                continue
            changed += bool(rv["fix"])
            rec = apply_fix(rec, rv["fix"])
        atomic_write(ann_dir / f"{p['id']}.json", json.dumps(rec, ensure_ascii=False, indent=1))
        written += 1
    if not a.no_review:
        print(f"  复核改动了 {changed}/{written} 篇", file=sys.stderr)

    days = sorted({p.get("day") for p in todo if p["id"] in missing and p.get("day")})
    print(f"✅ 写入 {written} 篇 → {ann_dir}" + (f"；还缺 {len(missing)} 篇 {missing[:5]}，"
          f"涉及公告日 {days} 暂不进日榜，重跑本脚本会只补这几篇" if missing else ""))
    if missing:
        sys.exit(1)


def review_existing(a, papers: list[dict], ann_dir: Path, cache: Path) -> None:
    if not (a.ids or a.days):
        sys.exit("--review-existing 要配 --ids 或 --days 选范围")
    want_ids = {x.strip().split("v")[0] for x in (a.ids or "").split(",") if x.strip()}
    want_days = {x.strip() for x in (a.days or "").split(",") if x.strip()}
    todo = []
    for p in papers:
        f = ann_dir / f"{p['id']}.json"
        if (p["id"] in want_ids or p.get("day") in want_days) and f.exists():
            p["_rec"] = json.loads(f.read_text(encoding="utf-8"))
            (cache / "rv" / f"{p['id']}.json").unlink(missing_ok=True)   # 重新复核
            todo.append(p)
    print(f"复核已有标注 {len(todo)} 篇（{a.rv_model}）", file=sys.stderr)
    run_stage("rv", a.category, todo, cache / "rv", a.rv_model)
    done, changed, fields, missing = 0, 0, {}, []
    for p in todo:
        rv = read_cached(cache / "rv" / f"{p['id']}.json", lambda x, p=p: rv_ok(a.category, p, x))
        if rv is None:
            missing.append(p["id"])
            continue
        done += 1
        if rv["fix"]:
            changed += 1
            for k in rv["fix"]:
                fields[k] = fields.get(k, 0) + 1
            atomic_write(ann_dir / f"{p['id']}.json",
                         json.dumps(apply_fix(p["_rec"], rv["fix"]), ensure_ascii=False, indent=1))
    top = sorted(fields.items(), key=lambda kv: -kv[1])
    print(f"✅ 复核 {done} 篇，改动 {changed} 篇（按字段：{top}）"
          + (f"；{len(missing)} 篇没复核成，重跑会补：{missing[:5]}" if missing else ""))
    if missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
