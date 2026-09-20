# -*- coding: utf-8 -*-
"""强制重生成指定章节（S3 草稿 + 定稿）——换提示词后重刷、单章验证用。

用法:
    python _redo_chapter.py qq_gaowu 1        # 重生成第 1 章
    python _redo_chapter.py qq_gaowu 1 5      # 重生成第 1-5 章

会把原草稿备份成 chapter_N.bak.txt，再调 pipeline.do_chapter 重做。
"""
import json
import os
import shutil
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

    lo = int(sys.argv[2])
    hi = int(sys.argv[3]) if len(sys.argv) > 3 else lo
    targets = list(range(lo, hi + 1))

    cfg = load_cfg()
    chdir = os.path.join(b["source_dir"], "chapters")
    os.makedirs(chdir, exist_ok=True)

    print("=" * 62)
    print("  强制重生成 S3  %s  [%s]" % (b["name"], b["platform"]))
    print("  章节: %s" % targets)
    print("=" * 62)

    ok, fail = 0, []
    for n in targets:
        src = os.path.join(chdir, "chapter_%d.txt" % n)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(chdir, "chapter_%d.bak.txt" % n))
        # 同时清掉对应的成品，避免新旧混在一起
        f = os.path.join(b["source_dir"], "chapters_final", "chapter_%d.txt" % n)
        if os.path.exists(f):
            os.remove(f)
        try:
            if pl.do_chapter(b, n, cfg):
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
