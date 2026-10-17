#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Houdini 工具集打包脚本

用法（在仓库根目录或任意位置执行都行）：
    python tools/build.py                 # 只重建有变化的模块
    python tools/build.py --all           # 强制重建全部
    python tools/build.py --bump minor    # 强制整体版本递增
    python tools/build.py --dry-run       # 只打印计划，不写文件

设计要点：
  · 每个模块独立计算 content_hash，只有内容变了才重新打包 + 递增模块版本，
    避免每次改个小脚本都要重传 100MB 的节点库。
  · zip 内顶层固定为 MyToolkit/，安装器直接解压到 $HOUDINI_USER_PREF_DIR 即可。
  · 单个 zip 不超过 max_part_mb（Cloudflare Pages 硬限 25MB，默认 20MB 留余量）。
"""

import argparse
import fnmatch
import hashlib
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "tools" / "build_config.json"
MANIFEST_PATH = ROOT / "manifest.json"
DIST = ROOT / "dist"

CHANGELOG_KEEP = 10


def log(msg=""):
    print(msg, flush=True)


def sha256_file(path, bufsize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(bufsize), b""):
            h.update(chunk)
    return h.hexdigest()


def match_any(name, patterns):
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def collect(module):
    """把模块配置解析成 [(源文件 Path, zip 内相对路径), ...]"""
    items = []
    for s in module.get("sources", []):
        raw = s["src"]
        src = Path(raw)
        if not src.is_absolute():
            src = ROOT / src
        dst = (s.get("dst") or "").strip("/")
        includes = s.get("include") or ["*"]
        excludes = s.get("exclude") or []

        if not src.exists():
            log("  ! 源路径不存在，已跳过：%s" % src)
            continue

        if src.is_file():
            items.append((src, dst or src.name))
            continue

        for p in sorted(src.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(src).as_posix()
            parts = rel.split("/")

            # 目录名命中排除规则（例如 __pycache__ / backup）
            if any(match_any(part, excludes) for part in parts[:-1]):
                continue
            # 文件名排除
            if match_any(p.name, excludes):
                continue
            # 文件名包含
            if not match_any(p.name, includes):
                continue

            items.append((p, "%s/%s" % (dst, rel) if dst else rel))

    # 去重（同一 zip 路径只保留第一份）
    seen, uniq = set(), []
    for src, rel in items:
        if rel in seen:
            log("  ! zip 内路径重复，已忽略：%s" % rel)
            continue
        seen.add(rel)
        uniq.append((src, rel))
    return uniq


def content_hash(items):
    h = hashlib.sha256()
    for src, rel in sorted(items, key=lambda x: x[1]):
        h.update(rel.encode("utf-8"))
        h.update(sha256_file(src).encode("ascii"))
    return h.hexdigest()


def split_parts(items, max_bytes):
    parts, cur, size = [], [], 0
    for it in items:
        sz = it[0].stat().st_size
        if cur and size + sz > max_bytes:
            parts.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += sz
    if cur:
        parts.append(cur)
    return parts


def bump(ver, level="patch"):
    try:
        a = [int(x) for x in str(ver).split(".")]
    except Exception:
        return "1.0.0"
    while len(a) < 3:
        a.append(0)
    if level == "major":
        a = [a[0] + 1, 0, 0]
    elif level == "minor":
        a = [a[0], a[1] + 1, 0]
    else:
        a = [a[0], a[1], a[2] + 1]
    return ".".join(str(x) for x in a)


def write_zip(out_path, files, zip_root):
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for src, rel in files:
            z.write(src, "%s/%s" % (zip_root, rel))
    tmp.replace(out_path)


def part_name(mid, ver, idx, total):
    if total == 1:
        return "%s-%s.zip" % (mid, ver)
    return "%s-%s.p%d.zip" % (mid, ver, idx + 1)


def write_shelf_index(cfg, manifest):
    """生成 Houdini「Download Shelf」认的清单（JSON 数组）。

    格式照 www.sidefx.com/exchange/json/list.php?type=Shelf 抄的：
    每项要有 name / author / description / fileURL / thumbnailURL /
    lastModified / version，Houdini 列表里就是按这些字段显示的。
    """
    si = cfg.get("shelf_index")
    if not si:
        return None

    base = str(cfg.get("base_url", "")).rstrip("/")
    target = str(si.get("target", "bootstrap.shelf")).lstrip("/")
    # thumbnail 留空 = 不写 thumbnailURL 字段。
    # Houdini 20.5 的 assetdownload.py 在 Windows 上下载缩略图必然失败
    # （mkstemp 的临时文件还在占用，又把下载结果 move 到同一路径），
    # 失败后 os.remove 抛 PermissionError 弹错框。不写这个字段就不会触发。
    thumb = str(si.get("thumbnail") or "").lstrip("/")
    ver = str(manifest.get("version", "1.0.0"))

    try:
        major = int(ver.split(".")[0])
    except Exception:
        major = 1

    desc = si.get("description", "")
    if desc:
        desc = desc.rstrip() + "\n\n当前版本 v%s（更新于 %s）" % (
            ver, manifest.get("updated", "").replace("T", " ")[:16])

    entry = {
        "author": si.get("author", ""),
        "description": desc,
        "fileURL": "%s/%s" % (base, target),
        "lastModified": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": si.get("name", manifest.get("toolkit", "Toolkit")),
        "version": major,
    }
    if thumb:
        entry["thumbnailURL"] = "%s/%s" % (base, thumb)

    out = ROOT / str(si.get("file", "shelves.json"))
    out.write_text(json.dumps([entry], indent=4, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    log("已写入 %s（Download Shelf 用这个地址）" % out.name)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="强制重建所有模块")
    ap.add_argument("--bump", choices=["major", "minor", "patch"], default=None,
                    help="强制整体版本递增级别")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = ap.parse_args()

    if not CONFIG_PATH.exists():
        log("找不到配置：%s" % CONFIG_PATH)
        return 1
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    old = {}
    if MANIFEST_PATH.exists():
        try:
            old = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            log("旧 manifest 解析失败，按全新处理：%s" % e)
    old_modules = {m["id"]: m for m in old.get("modules", [])}

    zip_root = cfg.get("zip_root", "MyToolkit")
    max_bytes = int(cfg.get("max_part_mb", 20)) * 1024 * 1024
    DIST.mkdir(exist_ok=True)

    new_modules = []
    any_changed = False

    for mod in cfg.get("modules", []):
        mid = mod["id"]
        if not mod.get("enabled", True):
            log("[%s] 已禁用，跳过" % mid)
            prev = old_modules.get(mid)
            if prev:
                new_modules.append(prev)      # 保留旧记录，避免用户被提示"模块消失"
            continue

        log("[%s] 收集文件…" % mid)
        items = collect(mod)
        if not items:
            log("  ! 没有收集到任何文件，跳过")
            continue

        total_bytes = sum(p.stat().st_size for p, _ in items)
        chash = content_hash(items)
        prev = old_modules.get(mid)
        unchanged = (
            prev is not None
            and prev.get("content_hash") == chash
            and not args.all
            and all((DIST / Path(p["url"]).name).exists() for p in prev.get("parts", []))
        )

        if unchanged:
            log("  未变化：%d 个文件 / %.1f MB，沿用 v%s"
                % (len(items), total_bytes / 1048576.0, prev["version"]))
            new_modules.append(prev)
            continue

        any_changed = True
        ver = bump(prev["version"]) if prev else "1.0.0"
        log("  有变化：%d 个文件 / %.1f MB → v%s"
            % (len(items), total_bytes / 1048576.0, ver))

        parts = split_parts(items, max_bytes)
        part_infos = []
        for i, chunk in enumerate(parts):
            name = part_name(mid, ver, i, len(parts))
            out = DIST / name
            chunk_bytes = sum(p.stat().st_size for p, _ in chunk)
            if args.dry_run:
                log("    [dry] dist/%s  （%d 个文件 / %.1f MB）"
                    % (name, len(chunk), chunk_bytes / 1048576.0))
                part_infos.append({"url": "dist/" + name, "size": 0, "sha256": "dry-run"})
                continue
            write_zip(out, chunk, zip_root)
            size = out.stat().st_size
            digest = sha256_file(out)
            if size > 25 * 1024 * 1024:
                log("    ⚠ 警告：%s 有 %.1f MB，超过 Cloudflare 25MB 硬限！"
                    % (name, size / 1048576.0))
            log("    dist/%s  %.2f MB  sha256=%s…" % (name, size / 1048576.0, digest[:12]))
            part_infos.append({
                "url": "dist/" + name,
                "size": size,
                "sha256": digest,
            })

        new_modules.append({
            "id": mid,
            "name": mod.get("name", mid),
            "version": ver,
            "default": bool(mod.get("default", True)),
            "content_hash": chash,
            "parts": part_infos,
        })

    # 整体版本
    old_ver = old.get("version") if old else None
    if not old_ver:
        # 首次构建（旧 manifest 不存在或只是占位）
        overall = bump("1.0.0", args.bump) if args.bump else "1.0.0"
    elif args.bump:
        overall = bump(old_ver, args.bump)
    elif any_changed:
        overall = bump(old_ver, "patch")
    else:
        overall = old_ver

    changelog = list(old.get("changelog", []))
    if any_changed:
        changed_names = [m["name"] + " v" + m["version"]
                         for m in new_modules
                         if old_modules.get(m["id"], {}).get("version") != m["version"]]
        changelog.insert(0, "%s 更新：%s" % (overall, "、".join(changed_names) or "重新构建"))
        changelog = changelog[:CHANGELOG_KEEP]

    manifest = {
        "toolkit": "MyToolkit",
        "version": overall,
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "min_houdini": cfg.get("min_houdini", "19.5"),
        "changelog": changelog,
        "modules": new_modules,
    }

    log()
    log("整体版本：%s %s" % (overall, "（有变化）" if any_changed else "（无变化）"))
    if args.dry_run:
        log("[dry] 不写 manifest.json")
        return 0

    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    log("已写入 manifest.json")
    write_shelf_index(cfg, manifest)
    log("模块：")
    for m in new_modules:
        n = len(m["parts"])
        log("  %-10s v%-8s %d 片  %s" % (m["id"], m["version"], n, m["name"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
