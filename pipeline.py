# -*- coding: utf-8 -*-
"""
多作品生产调度器

调度层：多作品轮转，每轮每个作品推进一步，日志带作品标签 —— 看起来并行
执行层：串行，同一时刻只有一个 API 调用 —— 避免限流、文件冲突、成本失控

用法：
  python pipeline.py                 全量跑所有启用的作品
  python pipeline.py --book <id>     只跑指定作品
  python pipeline.py --dry-run       只做校验，不调 API
  python pipeline.py --status        只看各作品状态
  python pipeline.py --max-steps N   最多调度 N 步（调试用）
"""
import argparse
import json
import os
import re
import sys
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
BOOKS_FILE = os.path.join(HERE, "books.json")
CONFIG_FILE = os.path.join(HERE, "config.json")

import config_manager as cm
from novel_generator.architecture import Novel_architecture_generate
from novel_generator.blueprint import Chapter_blueprint_generate
from novel_generator.chapter import generate_chapter_draft
from novel_generator.finalization import finalize_chapter


# ==================== 多轨道日志 ====================
def log(tag, msg, mark=""):
    ts = time.strftime("%H:%M:%S")
    print("%s %s %s%s" % (ts, ("[%s]" % tag).ljust(14), mark, msg), flush=True)


def OK(tag, m):   log(tag, m, "OK   ")
def WARN(tag, m): log(tag, m, "WARN ")
def ERR(tag, m):  log(tag, m, "FAIL ")
def STEP(tag, m): log(tag, m, "-->  ")


# ==================== 工具 ====================
def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def cn_len(t):
    return len(re.sub(r"\s", "", t))


def nonempty(p, min_size=200):
    return os.path.exists(p) and os.path.getsize(p) >= min_size


def count_blueprint(p):
    if not os.path.exists(p):
        return 0
    txt = open(p, encoding="utf-8").read()
    return len(set(re.findall(r"第\s*(\d+)\s*章", txt)))


def outline_target(b):
    """大纲规划目标章数。
    优先取 outline_chapters（可以比正文长很多，为后续续写留空间），
    没有则退回 total_chapters。
    """
    return int(b.get("outline_chapters") or b["total_chapters"])


# ==================== 前置校验 ====================
def precheck(books, cfg):
    problems = []

    if not cfg.get("apiKey"):
        problems.append("config.json 里没有 API Key")

    emb = cfg.get("embedding", {})
    if not emb.get("api_key"):
        problems.append("embedding（硅基流动）key 为空，向量库会失效（不阻塞，仅提示）")

    for b in books:
        tag = b["id"]
        if not os.path.isdir(b["source_dir"]):
            problems.append("[%s] source_dir 不存在: %s" % (tag, b["source_dir"]))
        out_parent = os.path.dirname(b["output_dir"])
        if not os.path.isdir(out_parent):
            problems.append("[%s] output_dir 的父目录不存在: %s" % (tag, out_parent))
        if not b.get("total_chapters"):
            problems.append("[%s] total_chapters 未设置" % tag)

    try:
        import shutil
        free_gb = shutil.disk_usage(HERE).free / (1024 ** 3)
        if free_gb < 1:
            problems.append("磁盘剩余空间不足 1GB（当前 %.2f GB）" % free_gb)
    except Exception:
        pass

    return problems


# ==================== 待办判定 ====================
def next_todo(b):
    """返回 (阶段, 参数)。阶段 ∈ arch/blueprint/chapter/polish/export/None"""
    sd = b["source_dir"]
    need = int(b["total_chapters"])
    arch = os.path.join(sd, "Novel_architecture.txt")
    bp = os.path.join(sd, "Novel_directory.txt")
    chdir = os.path.join(sd, "chapters")
    fndir = os.path.join(sd, "chapters_final")

    # S1 架构
    if not nonempty(arch, 5000):
        return ("arch", None)

    # S2 大纲（用 outline_chapters，可为正文留出后续空间）
    if count_blueprint(bp) < outline_target(b) * 0.95:
        return ("blueprint", None)

    # S3 正文
    for n in range(1, need + 1):
        if not nonempty(os.path.join(chdir, "chapter_%d.txt" % n)):
            return ("chapter", n)

    # S4 文本润色
    for n in range(1, need + 1):
        if not nonempty(os.path.join(fndir, "chapter_%d.txt" % n)):
            return ("polish", n)

    # S5 导出
    # 注意：只判断"合并稿存在"是不够的——旧版本的合并稿会让系统误以为
    # 已经导出完成，导致后续新增的章节永远不进成品。这里额外比较时间戳。
    merged = os.path.join(b["output_dir"], "%s.txt" % b["name"])
    if not os.path.exists(merged):
        return ("export", None)
    try:
        mtime = os.path.getmtime(merged)
        newest = 0
        for f in os.listdir(fndir):
            if re.match(r"chapter_\d+\.txt$", f):
                newest = max(newest, os.path.getmtime(os.path.join(fndir, f)))
        if newest > mtime:
            return ("export", None)
    except OSError:
        return ("export", None)

    return (None, None)


# ==================== 阶段执行 ====================
def make_llm(cfg, which):
    name = cfg["choose"][which]
    c = cfg["llm"][name]
    return c


def do_arch(b, cfg, dry=False):
    tag = b["id"]
    if dry:
        OK(tag, "S1 架构 —— 待生成（干跑跳过）")
        return True
    c = make_llm(cfg, "architecture_llm")
    STEP(tag, "S1 生成小说架构 ...")
    t0 = time.time()
    Novel_architecture_generate(
        interface_format=c["interface_format"], api_key=c["api_key"],
        base_url=c["base_url"], llm_model=c["model_name"],
        topic=b["topic"], genre=b["genre"],
        number_of_chapters=int(b["total_chapters"]),
        word_number=int(b["chapter_words"]),
        filepath=b["source_dir"],
        temperature=c["temperature"], max_tokens=c["max_tokens"],
        timeout=c["timeout"], user_guidance="",
    )
    arch = os.path.join(b["source_dir"], "Novel_architecture.txt")
    if not nonempty(arch, 5000):
        ERR(tag, "S1 失败：架构文件为空或过小")
        return False
    OK(tag, "S1 完成 %.0fs，架构 %d 字节" % (time.time() - t0, os.path.getsize(arch)))
    return True


def do_blueprint(b, cfg, dry=False):
    tag = b["id"]
    if dry:
        OK(tag, "S2 大纲 —— 待生成（干跑跳过）")
        return True
    c = make_llm(cfg, "chapter_outline_llm")
    tgt = outline_target(b)
    STEP(tag, "S2 生成章节目录（目标 %d 章，正文只写 %d 章）..." % (tgt, int(b["total_chapters"])))
    t0 = time.time()
    Chapter_blueprint_generate(
        interface_format=c["interface_format"], api_key=c["api_key"],
        base_url=c["base_url"], llm_model=c["model_name"],
        filepath=b["source_dir"],
        number_of_chapters=tgt,
        user_guidance="", temperature=c["temperature"],
        max_tokens=c["max_tokens"], timeout=c["timeout"],
    )
    n = count_blueprint(os.path.join(b["source_dir"], "Novel_directory.txt"))
    need = tgt
    if n < need * 0.95:
        WARN(tag, "S2 只产出 %d/%d 章，下次调度继续补" % (n, need))
        return True
    OK(tag, "S2 完成 %.0fs，目录 %d 章" % (time.time() - t0, n))
    return True


def do_chapter(b, n, cfg, dry=False):
    tag = b["id"]
    if dry:
        OK(tag, "S3 第%d章 —— 待生成（干跑跳过）" % n)
        return True

    emb = cfg["embedding"]
    d = make_llm(cfg, "prompt_draft_llm")
    STEP(tag, "S3 第%d章 草稿 ..." % n)
    t0 = time.time()
    generate_chapter_draft(
        api_key=d["api_key"], base_url=d["base_url"], model_name=d["model_name"],
        filepath=b["source_dir"], novel_number=n,
        word_number=int(b["chapter_words"]), temperature=d["temperature"],
        user_guidance="", characters_involved="", key_items="",
        scene_location="", time_constraint="",
        embedding_api_key=emb["api_key"], embedding_url=emb["base_url"],
        embedding_interface_format=emb["interface_format"],
        embedding_model_name=emb["model_name"],
        embedding_retrieval_k=emb.get("retrieval_k", 2),
        interface_format=d["interface_format"], max_tokens=d["max_tokens"],
        timeout=d["timeout"],
    )
    t1 = time.time()

    fpath = os.path.join(b["source_dir"], "chapters", "chapter_%d.txt" % n)
    if not nonempty(fpath):
        ERR(tag, "S3 第%d章 草稿为空，跳过" % n)
        return False

    f = make_llm(cfg, "final_chapter_llm")
    STEP(tag, "S3 第%d章 定稿 ..." % n)
    finalize_chapter(
        novel_number=n, word_number=int(b["chapter_words"]),
        api_key=f["api_key"], base_url=f["base_url"], model_name=f["model_name"],
        temperature=f["temperature"], filepath=b["source_dir"],
        embedding_api_key=emb["api_key"], embedding_url=emb["base_url"],
        embedding_interface_format=emb["interface_format"],
        embedding_model_name=emb["model_name"],
        interface_format=f["interface_format"], max_tokens=f["max_tokens"],
        timeout=f["timeout"],
    )
    t2 = time.time()
    w = cn_len(open(fpath, encoding="utf-8").read())
    OK(tag, "S3 第%d章 完成 草稿%.0fs+定稿%.0fs %d字" % (n, t1 - t0, t2 - t1, w))
    return True


# ==================== 字数兜底（硬约束 2200-2800）====================
W_LO, W_HI, W_TARGET = 2200, 2800, 2500

FIT_COMPRESS_PROMPT = """这是一章中文网文，字数超标了，请压缩。这是删减任务，不是改写任务。

【压缩要求】
1. 把全章字数压到 2600 字以内（目标 2500 字，绝对不许超过 2800 字）。
2. 只删不改剧情：事件、人物、动作、对白传递的信息，一个都不能丢。
3. 优先删这些：环境与天气描写、重复的心理活动、铺垫、可有可无的动作细节、
   四字成语排比、形容词副词。
4. 对白一句都不要删。如果对白偏少，反而要把叙述改成人物开口说。
5. 保持原有的段落分行和短句风格，不要合并成大片。
6. 只输出压缩后的正文，不要任何解释、标题或 markdown 标记。"""

FIT_EXPAND_PROMPT = """这一章中文网文字数不够，请补足。这是补写任务，不是改写任务。

【补足要求】
1. 把全章字数补到 2400 字以上（2200 字是硬下限，绝对不低于 2200 字）。
2. 禁止靠形容词、心理描写、环境描写、抒情来凑字数。
3. 只能补这些东西：人物之间的对白交锋、具体动作、有信息量的冲突推进。
4. 补的内容必须与前后文连贯，不得新增人物、不得改动剧情走向、不得改变结尾。
5. 保持原有段落分行和短句风格。
6. 只输出补足后的正文，不要任何解释、标题或 markdown 标记。"""


# ==================== 对白兜底（硬约束 >= 40%）====================
DLG_MIN = 0.40
DLG_TARGET = 0.46

BOOST_DIALOGUE_PROMPT = """这是一章中文网文，对白太少，读起来像旁白流水账。请改写，把对白占比提上来。

【硬指标】
- 现在全章对白占比只有 {cur}%，必须提到 {target}% 以上才算合格。
- 全章中文引号内的字数要达到 {need} 字以上（现在只有约 {have} 字）。

【只能这样提】
1. 把叙述改成人物开口说。例：
   原："陈凡意识到对方在试探他" 改："赵铁柱盯着他：'你觉得我在试探你？'"
2. 凡是交代情报、规则、计划、命令、威胁、嘲笑、质疑的叙述句，一律改成对白。
3. 场景里的每个人物都要分到台词，不许只有主角开口。
4. 心理活动不许直接写，让它变成台词或者具体动作。
5. 每段叙述后面紧跟一句对白回应。
6. 把长叙述拆成"人物说一句 + 动作一句 + 人物再说一句"。

【绝对不许做】
- 不许改动剧情走向、人物关系、事件顺序和结果。
- 不许删掉任何已有的对白。
- 不许新增人物、新增设定。
- 不许用形容词、心理描写、环境描写来凑字数。
- 全章字数不得超过 2800 字。

只输出改写后的正文，不要任何解释、标题或 markdown 标记。"""


def dlg_ratio(t):
    """引号内字符数 ÷ 全文字符数"""
    dlg = re.findall(r"[\u201c]([^\u201d]*)[\u201d]", t)
    return sum(len(d) for d in dlg) / max(1, cn_len(t))


def _boost_dialogue(adapter, text, tag, n):
    """对白占比不足 40% 就把叙述改写成对白。最多 2 轮，无改善就放弃。"""
    from novel_generator.common import invoke_with_cleaning
    for _ in range(2):
        r0 = dlg_ratio(text)
        if r0 >= DLG_MIN:
            return text
        w = cn_len(text)
        p = (BOOST_DIALOGUE_PROMPT
             .replace("{cur}", "%.0f" % (r0 * 100))
             .replace("{target}", "%.0f" % (DLG_TARGET * 100))
             .replace("{need}", str(int(w * DLG_TARGET)))
             .replace("{have}", str(int(w * r0))))
        try:
            r = invoke_with_cleaning(adapter, p + "\n\n【原文】\n" + text).strip()
        except Exception as e:
            WARN(tag, "S4 第%d章 对白提升调用失败(%s)，保留原稿" % (n, e))
            return text
        r = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", r).strip()
        if cn_len(r) < 1200 or cn_len(r) > 4000:
            WARN(tag, "S4 第%d章 对白提升结果异常 %d字，保留原稿" % (n, cn_len(r)))
            return text
        r1 = dlg_ratio(r)
        if r1 <= r0 + 0.01:
            WARN(tag, "S4 第%d章 对白提升无改善(%.0f%%)，保留原稿" % (n, r1 * 100))
            return text
        STEP(tag, "S4 第%d章 对白提升 %.0f%% -> %.0f%%" % (n, r0 * 100, r1 * 100))
        text = r
    return text


def _fit_length(adapter, text, tag, n):
    """把章节字数压进 2200-2800。最多修 2 轮，改坏了就退回上一版。"""
    from novel_generator.common import invoke_with_cleaning
    for _ in range(2):
        w = cn_len(text)
        if W_LO <= w <= W_HI:
            return text
        prompt = FIT_COMPRESS_PROMPT if w > W_HI else FIT_EXPAND_PROMPT
        try:
            r = invoke_with_cleaning(adapter, prompt + "\n\n【原文】\n" + text).strip()
        except Exception as e:
            WARN(tag, "S4 第%d章 字数修正调用失败(%s)，保留原稿" % (n, e))
            return text
        r = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", r).strip()
        rw = cn_len(r)
        # 结果离谱（被截断或爆写）就保留原稿，避免越修越坏
        if rw < 1200 or rw > 4000:
            WARN(tag, "S4 第%d章 字数修正结果异常 %d字，保留原稿" % (n, rw))
            return text
        STEP(tag, "S4 第%d章 字数对齐 %d -> %d" % (n, w, rw))
        text = r
    return text


def do_polish(b, n, cfg, dry=False):
    tag = b["id"]
    if dry:
        OK(tag, "S4 第%d章 文本润色 —— 待处理（干跑跳过）" % n)
        return True

    src = os.path.join(b["source_dir"], "chapters", "chapter_%d.txt" % n)
    dst_dir = os.path.join(b["source_dir"], "chapters_final")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "chapter_%d.txt" % n)

    text = open(src, encoding="utf-8").read().strip()
    if cn_len(text) < 300:
        WARN(tag, "S4 第%d章 原文过短，跳过" % n)
        return True

    try:
        import polish as polish_mod
        c = make_llm(cfg, "final_chapter_llm")
        from llm_adapters import create_llm_adapter
        adapter = create_llm_adapter(
            interface_format=c["interface_format"], base_url=c["base_url"],
            model_name=c["model_name"], api_key=c["api_key"],
            temperature=c["temperature"], max_tokens=c["max_tokens"],
            timeout=c["timeout"],
        )
        from novel_generator.common import invoke_with_cleaning

        STEP(tag, "S4 第%d章 文本润色 ..." % n)
        t0 = time.time()
        chunks = polish_mod.split_paragraphs(text)
        parts = []
        for ck in chunks:
            r = invoke_with_cleaning(adapter, polish_mod.CHUNK_PROMPT + "\n\n【原文】\n" + ck).strip()
            r = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", r).strip()
            if cn_len(r) < cn_len(ck) * 0.6:
                r = ck
            parts.append(r)
        res = "\n\n".join(parts)
        res = _boost_dialogue(adapter, res, tag, n)   # 兜底：对白 >= 40%
        res = _fit_length(adapter, res, tag, n)       # 兜底：字数 2200-2800
        open(dst, "w", encoding="utf-8").write(res)

        import difflib
        sim = difflib.SequenceMatcher(None, text, res).ratio()
        dlg = re.findall(r"[\u201c]([^\u201d]*)[\u201d]", res)
        dr = sum(len(d) for d in dlg) / max(1, cn_len(res)) * 100
        OK(tag, "S4 第%d章 完成 %.0fs %d->%d字 对话%.0f%% 相似度%.0f%%" % (
            n, time.time() - t0, cn_len(text), cn_len(res), dr, sim * 100))
        return True
    except Exception as e:
        ERR(tag, "S4 第%d章 失败: %s" % (n, e))
        traceback.print_exc()
        return False


def parse_titles(dirfile):
    """从 Novel_directory.txt 解析 {章号: 标题}"""
    titles = {}
    if not os.path.exists(dirfile):
        return titles
    txt = open(dirfile, encoding="utf-8").read()
    for m in re.finditer(r"第\s*(\d+)\s*章\s*[-—–:：]?\s*([^\n]*)", txt):
        n = int(m.group(1))
        t = m.group(2).strip().strip("-—–:：").strip()
        if n not in titles and t:
            titles[n] = t
    return titles


def do_export(b, cfg, dry=False):
    """导出到 母存档\\平台\\作品名\\ —— 完全使用本作品的路径，不依赖全局配置"""
    tag = b["id"]
    if dry:
        OK(tag, "S5 导出 —— 待执行（干跑跳过）")
        return True

    sd = b["source_dir"]
    outdir = b["output_dir"]
    try:
        fnd = os.path.join(sd, "chapters_final")
        raw = os.path.join(sd, "chapters")
        has_fn = os.path.isdir(fnd) and any(re.match(r"chapter_\d+\.txt$", f) for f in os.listdir(fnd))
        use = fnd if has_fn else raw

        titles = parse_titles(os.path.join(sd, "Novel_directory.txt"))
        files = [f for f in os.listdir(use) if re.match(r"chapter_\d+\.txt$", f)]
        files.sort(key=lambda x: int(re.findall(r"\d+", x)[0]))

        os.makedirs(outdir, exist_ok=True)

        # 清理旧合并稿（保留 章节标题.txt / 番茄发布信息.txt）
        for old in os.listdir(outdir):
            p = os.path.join(outdir, old)
            if os.path.isfile(p) and old.endswith(".txt") \
               and not old.startswith("章节标题") and not old.startswith("番茄发布信息"):
                try:
                    os.remove(p)
                except OSError:
                    pass

        STEP(tag, "S5 导出 %d 个章节文件 -> %s" % (len(files), outdir))

        lines, title_lines, used, total = [], [], 0, 0
        for f in files:
            n = int(re.findall(r"\d+", f)[0])
            body = open(os.path.join(use, f), encoding="utf-8").read().strip()
            if cn_len(body) < 200:
                continue
            t = titles.get(n, "")
            head = "第%d章 %s" % (n, t) if t else "第%d章" % n
            lines += [head, "", body, "", ""]
            title_lines.append(head)
            used += 1
            total += cn_len(body)

        if used == 0:
            ERR(tag, "S5 失败：没有可用章节")
            return False

        # 合并稿
        merged = os.path.join(outdir, "%s.txt" % b["name"])
        open(merged, "w", encoding="utf-8").write("\n".join(lines))

        # 标题清单
        open(os.path.join(outdir, "章节标题.txt"), "w", encoding="utf-8").write(
            "\n".join(title_lines) + "\n")

        # 分章
        parts_dir = os.path.join(outdir, "分章")
        os.makedirs(parts_dir, exist_ok=True)
        for old in os.listdir(parts_dir):
            try:
                os.remove(os.path.join(parts_dir, old))
            except OSError:
                pass
        for f in files:
            n = int(re.findall(r"\d+", f)[0])
            body = open(os.path.join(use, f), encoding="utf-8").read().strip()
            if cn_len(body) < 200:
                continue
            t = titles.get(n, "")
            head = "第%d章 %s" % (n, t) if t else "第%d章" % n
            open(os.path.join(parts_dir, "%s.txt" % head), "w", encoding="utf-8").write(
                head + "\n\n" + body + "\n")

        # 平台说明
        notes = {
            "番茄": "日更无强制（账号上限约1万字）| 免费阅读靠广告分成 | 完读率+追读率决定推荐",
            "七猫": "日更无强制 | 免费阅读+渠道 | 最容易签约",
            "纵横": "日更4000字起 | 4000档600元/月，6000档800元/月",
            "飞卢": "日更万字起步 | 广告分成+打赏 | 脑洞爽文，节奏要快",
            "QQ阅读": "日更5000字 | 5000档500元/月，10000档1000元/月 | 稳定优先",
        }
        pf = b["platform"]
        with open(os.path.join(outdir, "_平台说明.txt"), "w", encoding="utf-8") as nf:
            nf.write("=" * 52 + "\n")
            nf.write("  发行平台：%s\n" % pf)
            nf.write("  作品：%s\n" % b["name"])
            nf.write("  共 %d 章 / %d 字\n" % (used, total))
            nf.write("=" * 52 + "\n\n")
            nf.write("  · " + notes.get(pf, "（未收录该平台说明）") + "\n")
            nf.write("\n【发布前】\n")
            nf.write("  1. 上传时如实勾选「使用了 AI 工具」\n")
            nf.write("  2. 建议先囤 10 章再开始发\n")
            nf.write("  3. 固定时段更新：中午12-14点 / 晚上19-21点\n")

        # 后置校验
        merged_n = count_blueprint(merged) if False else len(
            re.findall(r"^第\d+章", open(merged, encoding="utf-8").read(), re.M))
        if merged_n != used:
            WARN(tag, "S5 后置校验：合并稿章数 %d != 实际 %d" % (merged_n, used))

        OK(tag, "S5 完成 %d 章 / %d 字 -> %s" % (used, total, merged))
        return True
    except Exception as e:
        ERR(tag, "S5 异常: %s" % e)
        traceback.print_exc()
        return False


# ==================== 状态 ====================
def show_status(books):
    print()
    print("=" * 66)
    print("  作品状态")
    print("=" * 66)
    for b in books:
        stage, arg = next_todo(b)
        sd = b["source_dir"]
        need = int(b["total_chapters"])
        n_ch = len([f for f in os.listdir(os.path.join(sd, "chapters"))
                    if re.match(r"chapter_\d+\.txt$", f)]) if os.path.isdir(os.path.join(sd, "chapters")) else 0
        n_fn = 0
        fnd = os.path.join(sd, "chapters_final")
        if os.path.isdir(fnd):
            n_fn = len([f for f in os.listdir(fnd) if re.match(r"chapter_\d+\.txt$", f)])
        bp = count_blueprint(os.path.join(sd, "Novel_directory.txt"))
        state = {None: "全部完成"}.get(stage, "%s %s" % (stage, arg if arg else ""))
        print("  [%s] %s" % (b["id"], b["name"]))
        print("        平台 %s | 目标 %d 章 | 大纲 %d 章 | 正文 %d | 润色 %d" % (
            b["platform"], need, bp, n_ch, n_fn))
        print("        下一步：%s" % state)
        print()
    print("=" * 66)


# ==================== 主调度 ====================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", help="只跑指定作品 id")
    ap.add_argument("--dry-run", action="store_true", help="只校验，不调 API")
    ap.add_argument("--status", action="store_true", help="只看状态")
    ap.add_argument("--max-steps", type=int, default=0, help="最多调度多少步")
    args = ap.parse_args()

    data = load_json(BOOKS_FILE)
    books = [b for b in data["books"] if b.get("enabled", True)]
    if args.book:
        books = [b for b in books if b["id"] == args.book]
        if not books:
            print("找不到作品:", args.book)
            return 1

    if args.status:
        show_status(books)
        return 0

    # 载入全局配置
    c = cm.load_config(CONFIG_FILE)
    cfg = {
        "apiKey": c["llm_configs"][c["last_llm_config_name"]]["api_key"],
        "llm": c["llm_configs"],
        "choose": c["choose_configs"],
        "embedding": c["embedding_configs"][c["last_embedding_interface_format"]],
    }

    print()
    print("=" * 66)
    print("  多作品生产调度器")
    print("=" * 66)
    print("  并发模式：调度多轨道 / 执行串行")
    print("  作品数量：", len(books))
    for b in books:
        print("    - [%s] %s (%s)" % (b["id"], b["name"], b["platform"]))
    print("=" * 66)
    print()

    # 前置校验
    problems = precheck(books, cfg)
    fatal = [p for p in problems if "不阻塞" not in p]
    for p in problems:
        print("  ! " + p)
    if fatal:
        print()
        print("  前置校验未通过，已终止。")
        return 1
    print("  前置校验通过")
    print()

    if args.dry_run:
        print("  [干跑模式] 只检查待办，不调 API")
        print()
        for b in books:
            stage, arg = next_todo(b)
            if stage is None:
                OK(b["id"], "已完成，无待办")
            else:
                OK(b["id"], "下一步 -> %s %s" % (stage, arg if arg is not None else ""))
        print()
        return 0

    # 轮转调度
    step = 0
    while True:
        did_work = False
        for b in books:
            stage, arg = next_todo(b)
            if stage is None:
                continue
            did_work = True
            step += 1
            try:
                if stage == "arch":
                    do_arch(b, cfg, args.dry_run)
                elif stage == "blueprint":
                    do_blueprint(b, cfg, args.dry_run)
                elif stage == "chapter":
                    do_chapter(b, arg, cfg, args.dry_run)
                elif stage == "polish":
                    do_polish(b, arg, cfg, args.dry_run)
                elif stage == "export":
                    do_export(b, cfg, args.dry_run)
            except Exception as e:
                ERR(b["id"], "调度异常: %s" % e)
                traceback.print_exc()

            if args.max_steps and step >= args.max_steps:
                print()
                print("  达到 max-steps=%d，停止。" % args.max_steps)
                return 0
            if args.dry_run:
                continue
            time.sleep(1)

        if not did_work:
            break

    print()
    print("=" * 66)
    print("  全部作品的待办已处理完毕")
    print("=" * 66)
    show_status(books)
    return 0


if __name__ == "__main__":
    sys.exit(main())
