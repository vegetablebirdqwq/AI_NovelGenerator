# -*- coding: utf-8 -*-
"""强制重跑文本润色（S4）——用于修稿、换提示词后重刷、单章验证。

用法:
    python _repolish.py qq_gaowu 1          # 只重刷第 1 章（验证用）
    python _repolish.py qq_gaowu 1 24       # 重刷第 1-24 章
    python _repolish.py qq_gaowu all        # 重刷全部已有草稿的章节

它会删掉 chapters_final 里对应的旧文件，再调 pipeline.do_polish 重做，
因此跑完的字数/对话占比会按最新提示词 + 字数兜底重新对齐。
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import config_manager as cm
import pipeline as pl

CONFIG_FILE = os.path.join(HERE, "config.json")
BOOKS_FILE = os.path.join(HERE, "books.json")


def load_cfg():
    c = cm.load_config(CONFIG_FILE)
    return {
        "apiKey": c["llm_configs"][c["last_llm_config_name"]]["api_key"],
        "llm": c["llm_configs"],
        "choose": c["choose_configs"],
        "embedding": c["embedding_configs"][c["last_embedding_interface_format"]],
    }


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    bid = sys.argv[1]
    books = {b["id"]: b for b in json.load(open(BOOKS_FILE, encoding="utf-8"))["books"]}
    if bid not in books:
        print("未找到作品:", bid, " 可用:", ", ".join(books))
        return 1
    b = books[bid]

    chdir = os.path.join(b["source_dir"], "chapters")
    fndir = os.path.join(b["source_dir"], "chapters_final")
    have = sorted(int(re.findall(r"\d+", f)[0]) for f in os.listdir(chdir)
                  if re.match(r"chapter_\d+\.txt$", f)) if os.path.isdir(chdir) else []
    if not have:
        print("没有草稿可重刷:", chdir)
        return 1

    a = sys.argv[2].lower()
    if a == "all":
        lo, hi = min(have), max(have)
    else:
        lo = int(a)
        hi = int(sys.argv[3]) if len(sys.argv) > 3 else lo

    targets = [n for n in have if lo <= n <= hi]
    if not targets:
        print("该区间没有草稿。已有章节: %d-%d" % (min(have), max(have)))
        return 1

    print("=" * 62)
    print("  强制重刷 S4  %s  [%s]" % (b["name"], b["platform"]))
    print("  目标章节: %d 章  (%d-%d)" % (len(targets), min(targets), max(targets)))
    print("=" * 62)

    cfg = load_cfg()
    os.makedirs(fndir, exist_ok=True)

    ok, fail = 0, []
    for n in targets:
        dst = os.path.join(fndir, "chapter_%d.txt" % n)
        if os.path.exists(dst):
            os.remove(dst)          # 删掉旧结果，强制重做
        try:
            if pl.do_polish(b, n, cfg):
                ok += 1
            else:
                fail.append(n)
        except Exception as e:
            print("  第%d章 异常: %s" % (n, e))
            fail.append(n)

    print()
    print("完成 %d 章，失败 %d 章" % (ok, len(fail)))
    if fail:
        print("失败章节:", fail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
