# -*- coding: utf-8 -*-
"""通用作品体检：按 books.json 里的 id 查任意作品的正文质量。

用法:
    python _check_book.py qimao_zhuanzhi              # 默认查 chapters（草稿）
    python _check_book.py qimao_zhuanzhi final        # 查 chapters_final（润色后）
    python _check_book.py qimao_zhuanzhi draft 20 24  # 只看第 20-24 章

指标全部来自 TOMATO_STYLE_RULES（番茄连载铁律）。
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.abspath(__file__))

# 比喻/拟人/通感词：铁律要求 0
# 注意：最常用的是裸"像"字（"光滑得像骨头"），必须抓；
# 但要排除"好像/图像/想象/印象/偶像/录像/影像/景象/形象/真相/迹象/雕塑"里的"像"。
BI_RE = re.compile(r"仿佛|宛如|如同|犹如|好似|恍若|似的|"
                   r"(?<![好图想印偶录影景形真迹雕塑])像")
BI_LIST = ["仿佛", "宛如", "如同", "犹如", "好似", "恍若", "似的"]
# AI 套话高频词：越少越好
AI = ["不禁", "顿时", "瞬间", "微微", "淡淡", "缓缓", "莫名", "悄然", "一丝", "一抹",
      "他知道", "他明白", "他意识到", "他感到", "极其", "十分", "非常", "狠狠地",
      "愤怒地", "冷冽", "深邃", "嘴角勾起", "深吸一口气", "眼神一凝"]
# 抒情/散文腔
LY = ["心中五味杂陈", "百感交集", "说不出的", "无法形容", "仿佛整个世界", "时间仿佛",
      "空气仿佛", "弥漫着", "笼罩着", "久久不能平静"]

W_MIN, W_MAX = 2200, 2800
DLG_MIN = 40.0
LONG_MAX = 30.0   # 超25字句占比上限，超过说明句子太拖


def load_books():
    with open(os.path.join(ROOT, "books.json"), encoding="utf-8") as f:
        return {b["id"]: b for b in json.load(f)["books"]}


def stats(path):
    t = open(path, encoding="utf-8").read().strip()
    nows = re.sub(r"\s", "", t)
    n = len(nows)
    dlg = re.findall(r"[\u201c]([^\u201d]*)[\u201d]", t)
    dlg_pct = sum(len(d) for d in dlg) / max(1, n) * 100
    sents = [s for s in re.split(r"[。！？\n]", t) if s.strip()]
    longs = [s for s in sents if len(s) > 25]
    long_pct = len(longs) / max(1, len(sents)) * 100
    paras = [p for p in t.split("\n") if p.strip()]
    para_max = max((len(p) for p in paras), default=0)
    ai = sum(t.count(w) for w in AI)
    bi = len(BI_RE.findall(t))
    ly = sum(t.count(w) for w in LY)
    return dict(n=n, dlg=dlg_pct, long=long_pct, ai=ai, bi=bi, ly=ly,
                pmax=para_max, np=len(paras), ns=len(sents), sents=longs)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    bid = sys.argv[1]
    books = load_books()
    if bid not in books:
        print("未找到作品:", bid, "  可用:", ", ".join(books))
        sys.exit(1)
    b = books[bid]

    sub = "chapters"
    args = sys.argv[2:]
    if args and args[0] in ("draft", "chapters", "final", "chapters_final"):
        sub = "chapters" if args[0] in ("draft", "chapters") else "chapters_final"
        args = args[1:]
    lo = int(args[0]) if len(args) > 0 else 1
    hi = int(args[1]) if len(args) > 1 else 10 ** 6

    base = os.path.join(b["source_dir"], sub)
    if not os.path.isdir(base):
        print("目录不存在:", base)
        sys.exit(1)
    files = [f for f in os.listdir(base) if re.match(r"chapter_\d+\.txt$", f)]
    files.sort(key=lambda x: int(re.findall(r"\d+", x)[0]))
    files = [f for f in files if lo <= int(re.findall(r"\d+", f)[0]) <= hi]
    if not files:
        print("该区间没有章节:", base)
        sys.exit(1)

    print("=" * 78)
    print("  %s  [%s]  %s  %d 章" % (b["name"], b["platform"], sub, len(files)))
    print("=" * 78)
    print("%-5s %7s %8s %9s %5s %5s %5s %7s %5s" %
          ("章", "字数", "对话%", "超25字%", "AI", "比喻", "抒情", "最长段", "段数"))
    print("-" * 78)

    agg = dict(n=[], dlg=[], long=[], ai=0, bi=0, ly=0, pmax=0)
    bad = []
    for f in files:
        s = stats(os.path.join(base, f))
        ch = f.replace("chapter_", "").replace(".txt", "")
        flags = []
        if not (W_MIN <= s["n"] <= W_MAX):
            flags.append("字数")
        if s["dlg"] < DLG_MIN:
            flags.append("对话")
        if s["long"] > LONG_MAX:
            flags.append("长句")
        if s["bi"]:
            flags.append("比喻")
        if s["ai"] >= 8:
            flags.append("AI味")
        mark = ("  << " + ",".join(flags)) if flags else ""
        print("%-5s %7d %7.1f%% %8.1f%% %5d %5d %5d %7d %5d%s" %
              (ch, s["n"], s["dlg"], s["long"], s["ai"], s["bi"], s["ly"],
               s["pmax"], s["np"], mark))
        agg["n"].append(s["n"])
        agg["dlg"].append(s["dlg"])
        agg["long"].append(s["long"])
        agg["ai"] += s["ai"]
        agg["bi"] += s["bi"]
        agg["ly"] += s["ly"]
        agg["pmax"] = max(agg["pmax"], s["pmax"])
        if flags:
            bad.append(ch)

    k = len(files)
    print("-" * 78)
    print("平均字数 : %d   (要求 %d-%d)" % (sum(agg["n"]) // k, W_MIN, W_MAX))
    print("平均对话 : %.1f%% (要求 >=%.0f%%)" % (sum(agg["dlg"]) / k, DLG_MIN))
    print("长句占比 : %.1f%% (要求 <=%.0f%%)" % (sum(agg["long"]) / k, LONG_MAX))
    print("全篇合计 : AI套话 %d 次 / 比喻拟人 %d 次 / 抒情腔 %d 次" %
          (agg["ai"], agg["bi"], agg["ly"]))
    print("最长段落 : %d 字 (要求 <=160)" % agg["pmax"])
    print()
    if bad:
        print("有问题的章（%d 章）: %s" % (len(bad), ", ".join(bad)))
    else:
        print("全部达标。")


if __name__ == "__main__":
    main()
