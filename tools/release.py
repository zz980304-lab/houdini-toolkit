#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键发布：打包 → 提交 → 推送。

日常改完 N 盘的东西之后，只需要：

    python tools/release.py "改了某某节点的默认值"

可选参数：
    --bump major|minor|patch   强制整体版本号递增（一般不用，默认按内容自动判）
    --dry-run                  只打包不提交，看看会变什么
    --no-push                  提交但不推送

它会自己挑一条能用的网络通道推送：先直连，不行就逐个试本机常见的代理端口。
"""

import argparse
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_PORTS = [10809, 10808, 7890, 7897, 33210, 2080, 1080, 8889, 8080]
PROBE_URL = "https://github.com"
PROBE_TIMEOUT = 6


def log(msg=""):
    print(msg, flush=True)


def run(cmd, cwd=ROOT, check=True, capture=True):
    p = subprocess.run(cmd, cwd=str(cwd), shell=False,
                       capture_output=capture, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise SystemExit("命令失败：%s\n%s" % (" ".join(cmd), p.stderr or p.stdout))
    return p


def direct_ok():
    try:
        urllib.request.urlopen(PROBE_URL, timeout=PROBE_TIMEOUT)
        return True
    except Exception:
        return False


def proxy_ok(port):
    try:
        h = urllib.request.ProxyHandler({
            "http": "http://127.0.0.1:%d" % port,
            "https": "http://127.0.0.1:%d" % port,
        })
        op = urllib.request.build_opener(h)
        op.open(PROBE_URL, timeout=PROBE_TIMEOUT)
        return True
    except Exception:
        return False


def git_push():
    base = ["git", "-c", "http.postBuffer=524288000", "push"]

    log("  试直连…")
    p = run(base, check=False)
    if p.returncode == 0:
        log("  ✅ 直连推送成功")
        return True
    log("  直连失败：%s" % (p.stderr or "").strip().splitlines()[-1:])

    log("  扫描本机代理端口…")
    for port in CANDIDATE_PORTS:
        if not proxy_ok(port):
            continue
        log("  端口 %d 可用，改走代理…" % port)
        p = run(base + ["-c", "http.proxy=http://127.0.0.1:%d" % port,
                        "-c", "https.proxy=http://127.0.0.1:%d" % port],
                check=False)
        if p.returncode == 0:
            log("  ✅ 代理 %d 推送成功" % port)
            return True
        log("  代理 %d 也失败" % port)

    log("  ❌ 所有通道都推不上去。检查代理软件是否开着，或手动跑：")
    log("     git -c http.proxy=http://127.0.0.1:<端口> push")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?", default="chore: 更新工具集")
    ap.add_argument("--bump", choices=["major", "minor", "patch"], default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    log("=== 1/4 重新生成 bootstrap.shelf ===")
    run([sys.executable, "tools/make_shelf.py"])

    log("\n=== 2/4 打包 ===")
    cmd = [sys.executable, "tools/build.py"]
    if args.bump:
        cmd += ["--bump", args.bump]
    if args.dry_run:
        cmd += ["--dry-run"]
    run(cmd, capture=False)

    if args.dry_run:
        log("\n[dry-run] 到此为止，没有提交。")
        return 0

    log("\n=== 3/4 提交 ===")
    st = run(["git", "status", "--short"]).stdout.strip()
    if not st:
        log("  没有文件变化，跳过提交")
    else:
        log(st)
        run(["git", "add", "-A"])
        run(["git", "commit", "-m", args.message], capture=False)

    if args.no_push:
        log("\n[--no-push] 已提交，未推送。")
        return 0

    log("\n=== 4/4 推送 ===")
    if not git_push():
        return 1

    log("\n完成。Cloudflare Pages 大约 1~2 分钟后更新，")
    log("之后在 Houdini 里点「阿龙工具 → 安装 / 更新工具集」即可拉到新版本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
