# -*- coding: utf-8 -*-
"""进度条渲染（供 实时进度.cmd 调用）"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(HERE, "books.json"), encoding="utf-8"))

for b in d["books"]:
    if not b.get("enabled", True):
        continue
    sd = b["source_dir"]
    need = int(b["total_chapters"])

    chdir = os.path.join(sd, "chapters")
    ch = len([f for f in os.listdir(chdir) if re.match(r"chapter_\d+\.txt$", f)]) if os.path.isdir(chdir) else 0

    fnd = os.path.join(sd, "chapters_final")
    fn = len([f for f in os.listdir(fnd) if re.match(r"chapter_\d+\.txt$", f)]) if os.path.isdir(fnd) else 0

    bp = 0
    dp = os.path.join(sd, "Novel_directory.txt")
    if os.path.exists(dp):
        bp = len(set(re.findall(r"第\s*(\d+)\s*章", open(dp, encoding="utf-8").read())))

    pct = int(ch / need * 100) if need else 0
    filled = int(pct / 5)
    bar = "#" * filled + "." * (20 - filled)

    # 最近一章的落盘时间
    last = ""
    if os.path.isdir(chdir):
        fs = [os.path.join(chdir, f) for f in os.listdir(chdir) if re.match(r"chapter_\d+\.txt$", f)]
        if fs:
            import datetime
            last = datetime.datetime.fromtimestamp(max(os.path.getmtime(p) for p in fs)).strftime("%H:%M:%S")

    print("  %-20s [%s] %3d%%" % (b["name"][:18], bar, pct))
    print("      正文 %2d/%d   润色 %2d/%d   大纲 %d 章   最近落盘 %s"
          % (ch, need, fn, need, bp, last or "--"))
    print()
