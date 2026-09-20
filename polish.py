# -*- coding: utf-8 -*-
"""
第二道工序：文本自然度优化 + 流畅化
读取 chapters/ 下的章节，逐章重写，输出到 chapters_final/
已处理过的章节自动跳过（断点续传）
"""
import os
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import config_manager as cm
from llm_adapters import create_llm_adapter
from novel_generator.common import invoke_with_cleaning

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")

POLISH_PROMPT = """你的任务：把下面这一章网文**逐句重写一遍**，消除模板化表达，让它读起来更自然、更流畅。

【这是改写任务，不是校对任务】
- 每一句话都要重新组织语序和用词，不要沿用原句。
- 只有人名、地名、数字、关键设定名可以原样保留。
- 如果基本照抄原文返回，视为任务失败。

【去模板化、提自然度 —— 逐条做到】
1. 长句拆短。单句超过 25 字就断开。
2. 出现"仿佛、似乎、不禁、顿时、瞬间、微微、淡淡、缓缓、莫名、悄然、一丝、一抹、宛如、如同、犹如、好似"，全部删掉，换成具体动作。
3. 出现"他知道、他明白、他意识到、他感到、他心中、他想着"，改成他做的事，或者让他开口说。
4. 出现"极其、十分、非常、格外、狠狠地、愤怒地、冷冷地、轻轻地"，删掉。
5. 四字成语连排、排比句，拆开改成人话。
6. 抽象描述换具体动作。写"他把杯子摔了"，不写"他内心充满愤怒"。
7. 超过 4 行的段落拆开。

【流畅化 —— 逐条做到】
8. 对话要像真人开口：允许口语、短促、打断、答非所问。
9. 句子长短交错。紧张的地方用短句短段。
10. 对话（中文引号内内容）占全文字数 40% 以上。不够就在合适的地方补对白。

【内容红线 —— 只守这一条】
剧情走向、人物关系、对话传递的信息，必须保持不变。除此之外所有句子都可以改写。
字数只减不增：不超过原文长度，允许压到原文的 75%。宁可短，不要长。
对话一句都不要删；叙述偏长就压叙述，把信息挪进对白里。

只输出改写后的正文。不要任何解释、标题、章节号或 markdown 标记。
"""


def count_cn(t):
    return len(re.sub(r"\s", "", t))


def dialogue_ratio(t):
    dlg = re.findall(r"[\u201c]([^\u201d]*)[\u201d]", t)
    return sum(len(d) for d in dlg) / max(1, count_cn(t))


def split_paragraphs(text, max_chars=800):
    """按段落分组，每组不超过 max_chars 字，保证长段本身独立成组"""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, cur, cur_len = [], [], 0
    for p in paras:
        if cur and cur_len + len(p) > max_chars:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(p)
        cur_len += len(p)
    if cur:
        chunks.append("\n".join(cur))
    return chunks


CHUNK_PROMPT = """你的任务：把下面这段网文**逐句重写一遍**，消除模板化表达，让它读起来更自然。

【这是改写任务，不是校对】
每一句都要重新组织语序和用词。只有人名、地名、数字、专有名词可以原样保留。
大体照抄返回视为失败。

【必须做到】
1. 长句拆短，单句超过 25 字就断开。
2. 删掉所有比喻和拟人。凡是"像、仿佛、似乎、宛如、如同、犹如、好似"打比方的地方，
   一律把比喻那半句删掉，或改成直接描述。
   例："像素化的方块，边缘整齐，像被删掉的图层" → "像素化的方块，边缘整齐"。
   例："光滑得像骨头" → "表面光滑，凉得硌手"。
   例："像刻在纸上的新字" → "在纸上亮起来"。
   例："每一步都像算好的" → "每一步都精确得一样"。
3. 删掉写景、写环境、写天气、写光线、写气味的句子。场景一句话带过就够，立刻接事件。
4. 把"他知道、他明白、他意识到、他感到、他心中"改成他做的事或他说的话。
5. 删掉"极其、十分、非常、格外、狠狠地、愤怒地、冷冷地、轻轻地"。
6. 四字成语连排、排比句拆开改成人话。
7. 抽象描述换具体动作。写"他把杯子摔了"，不写"他内心充满愤怒"。
8. 超过 4 行的段落拆开。
9. 对话像真人开口：允许口语、短促、打断、答非所问。
10. 这一段里对话（中文引号内内容）要占到四成以上，不够就补对白。

【内容红线】
剧情、人物、对话传递的信息不能变。除此之外所有句子都可以改写。
字数只减不增：改写后不得超过原文长度，允许压到原文的 75%。宁可短，不要长。
对话一句都不要删。叙述段偏长就压叙述，并把信息挪进对白里。

只输出改写后的正文，不要解释，不要加标题。
"""


def main():
    c = cm.load_config(CONFIG_FILE)
    op = c["other_params"]
    filepath = op["filepath"]
    src = os.path.join(filepath, "chapters")
    dst = os.path.join(filepath, "chapters_final")
    os.makedirs(dst, exist_ok=True)

    cfg = c["llm_configs"][c["choose_configs"]["final_chapter_llm"]]
    print("=" * 56)
    print("  文本自然度优化 + 流畅化")
    print("  来源 :", src)
    print("  输出 :", dst)
    print("  模型 :", cfg["model_name"])
    print("=" * 56)

    adapter = create_llm_adapter(
        interface_format=cfg["interface_format"],
        base_url=cfg["base_url"],
        model_name=cfg["model_name"],
        api_key=cfg["api_key"],
        temperature=cfg["temperature"],
        max_tokens=cfg["max_tokens"],
        timeout=cfg["timeout"],
    )

    files = [f for f in os.listdir(src) if re.match(r"chapter_\d+\.txt$", f)]
    files.sort(key=lambda x: int(re.findall(r"\d+", x)[0]))
    print("  待处理:", len(files), "章")
    print()

    ok, skip, fail = 0, 0, []
    t_all = time.time()
    for f in files:
        n = int(re.findall(r"\d+", f)[0])
        out = os.path.join(dst, f)
        if os.path.exists(out) and os.path.getsize(out) > 200:
            skip += 1
            continue

        text = open(os.path.join(src, f), encoding="utf-8").read().strip()
        if count_cn(text) < 300:
            skip += 1
            continue

        t0 = time.time()
        try:
            import difflib
            chunks = split_paragraphs(text)
            parts = []
            for ck in chunks:
                r = invoke_with_cleaning(adapter, CHUNK_PROMPT + "\n\n【原文】\n" + ck).strip()
                r = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", r).strip()
                if count_cn(r) < count_cn(ck) * 0.6:
                    r = ck
                parts.append(r)
            res = "\n\n".join(parts)
            sim = difflib.SequenceMatcher(None, text, res).ratio()
            open(out, "w", encoding="utf-8").write(res)
            print("[%d] %ds  %d段  %d->%d 字  对话%.0f%%  相似度%.0f%%" % (
                n, time.time() - t0, len(chunks), count_cn(text), count_cn(res),
                dialogue_ratio(res) * 100, sim * 100), flush=True)
            ok += 1
        except Exception as e:
            print("[%d] 失败: %s: %s" % (n, type(e).__name__, e), flush=True)
            fail.append(n)
            time.sleep(5)

    print()
    print("=" * 56)
    print("  成功 %d 章，跳过 %d 章，失败 %d 章，用时 %.1f 分钟" % (
        ok, skip, len(fail), (time.time() - t_all) / 60))
    if fail:
        print("  失败章节:", fail)
    print("=" * 56)


if __name__ == "__main__":
    main()
