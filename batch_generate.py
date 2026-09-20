# -*- coding: utf-8 -*-
"""
批量生成驱动脚本 —— 无人值守连续生成多章
它本身不写作，只是依次调用 AI_NovelGenerator 现成的引擎：
  Chapter_blueprint_generate -> generate_chapter_draft -> finalize_chapter

用法：
  python batch_generate.py --blueprint                # 目录续写到 num_chapters 章
  python batch_generate.py --start 11 --end 13        # 生成第 11-13 章
  python batch_generate.py --start 11 --end 200       # 全量跑完
"""
import argparse
import os
import sys
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8")

import config_manager as cm
from novel_generator.blueprint import Chapter_blueprint_generate
from novel_generator.chapter import generate_chapter_draft
from novel_generator.finalization import finalize_chapter

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blueprint", action="store_true", help="先把章节目录补齐到 num_chapters 章")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=0)
    ap.add_argument("--force", action="store_true", help="已存在的章节也重写")
    args = ap.parse_args()

    c = cm.load_config(CONFIG_FILE)
    op = c["other_params"]
    filepath = op["filepath"]
    total = op["num_chapters"]
    words = op["word_number"]
    end = args.end or total

    def llm(key):
        return c["llm_configs"][c["choose_configs"][key]]

    emb = c["embedding_configs"][c["last_embedding_interface_format"]]

    print("=" * 56)
    print("  保存路径 :", filepath)
    print("  总章数   :", total, "  单章字数:", words)
    print("  本次范围 :", args.start, "-", end)
    print("  写作模型 :", llm("prompt_draft_llm")["model_name"])
    print("  定稿模型 :", llm("final_chapter_llm")["model_name"])
    print("  embedding:", emb["model_name"], "| key:", (emb["api_key"][:7] + "...") if emb["api_key"] else "空")
    print("=" * 56)
    print()

    if args.blueprint:
        bp = llm("chapter_outline_llm")
        print("[目录] 生成/续写章节目录 ...", flush=True)
        t0 = time.time()
        Chapter_blueprint_generate(
            interface_format=bp["interface_format"],
            api_key=bp["api_key"],
            base_url=bp["base_url"],
            llm_model=bp["model_name"],
            filepath=filepath,
            number_of_chapters=total,
            user_guidance=op.get("user_guidance", ""),
            temperature=bp["temperature"],
            max_tokens=bp["max_tokens"],
            timeout=bp["timeout"],
        )
        # 统计目录现有章数
        dfile = os.path.join(filepath, "Novel_directory.txt")
        n_dir = 0
        if os.path.exists(dfile):
            import re
            txt = open(dfile, encoding="utf-8").read()
            n_dir = len(set(int(x) for x in re.findall(r"第\s*(\d+)\s*章", txt) if x.isdigit()))
        print("[目录] 完成，用时 %.0fs，当前目录共 %d 章" % (time.time() - t0, n_dir))
        print()

    ok, fail = 0, []
    t_all = time.time()

    for n in range(args.start, end + 1):
        ch_file = os.path.join(filepath, "chapters", "chapter_%d.txt" % n)
        if not args.force and os.path.exists(ch_file) and os.path.getsize(ch_file) > 200:
            print("[%d/%d] 已存在，跳过" % (n, end), flush=True)
            continue

        print("[%d/%d] 生成草稿 ..." % (n, end), flush=True)
        t0 = time.time()
        try:
            d = llm("prompt_draft_llm")
            generate_chapter_draft(
                api_key=d["api_key"],
                base_url=d["base_url"],
                model_name=d["model_name"],
                filepath=filepath,
                novel_number=n,
                word_number=words,
                temperature=d["temperature"],
                user_guidance=op.get("user_guidance", ""),
                characters_involved="",
                key_items="",
                scene_location="",
                time_constraint="",
                embedding_api_key=emb["api_key"],
                embedding_url=emb["base_url"],
                embedding_interface_format=emb["interface_format"],
                embedding_model_name=emb["model_name"],
                embedding_retrieval_k=emb.get("retrieval_k", 2),
                interface_format=d["interface_format"],
                max_tokens=d["max_tokens"],
                timeout=d["timeout"],
            )
            t1 = time.time()
            print("[%d/%d] 草稿 %.0fs -> 定稿 ..." % (n, end, t1 - t0), flush=True)

            f = llm("final_chapter_llm")
            finalize_chapter(
                novel_number=n,
                word_number=words,
                api_key=f["api_key"],
                base_url=f["base_url"],
                model_name=f["model_name"],
                temperature=f["temperature"],
                filepath=filepath,
                embedding_api_key=emb["api_key"],
                embedding_url=emb["base_url"],
                embedding_interface_format=emb["interface_format"],
                embedding_model_name=emb["model_name"],
                interface_format=f["interface_format"],
                max_tokens=f["max_tokens"],
                timeout=f["timeout"],
            )
            t2 = time.time()

            body = open(ch_file, encoding="utf-8").read() if os.path.exists(ch_file) else ""
            import re as _re
            wc = len(_re.sub(r"\s", "", body))
            print("[%d/%d] 完成  草稿%.0fs + 定稿%.0fs  %d 字" % (n, end, t1 - t0, t2 - t1, wc), flush=True)
            ok += 1
        except Exception as e:
            print("[%d/%d] 失败: %s: %s" % (n, end, type(e).__name__, e), flush=True)
            traceback.print_exc()
            fail.append(n)
            time.sleep(5)

    print()
    print("=" * 56)
    print("  完成 %d 章，失败 %d 章，总用时 %.0f 分钟" % (ok, len(fail), (time.time() - t_all) / 60))
    if fail:
        print("  失败章节:", fail)
    print("=" * 56)


if __name__ == "__main__":
    main()
