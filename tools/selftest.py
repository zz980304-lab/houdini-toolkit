# -*- coding: utf-8 -*-
"""
离线自测：用假 hou 模块 + 真网络，把 installer 的完整流程跑一遍。

    python tools/selftest.py

重点：mock 的 hou API 用**真实签名**（不带 **kwargs 兜底）。
如果 installer 里传了 Houdini 不认识的参数，这里会直接 TypeError，
不用等到真机上才炸 —— 之前 `selectFromList(..., multiple_select=True)`
就是这么漏过去的。
"""

import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PREF = tempfile.mkdtemp(prefix="mt_pref_")
print("假 pref 目录:", PREF)

# ---------------------------------------------------------------- mock hou

hou = types.ModuleType("hou")


class _Sev(object):
    ImportantMessage = 0
    Message = 1


hou.severityType = _Sev
hou.expandString = lambda s: s.replace("$HOUDINI_USER_PREF_DIR", PREF)
hou.applicationVersion = lambda: (20, 5, 401)


class _UI(object):
    """签名严格照 Houdini 20.5 的 hou.ui，多一个参数就报错。"""

    def __init__(self):
        self.dialogs = []

    def displayMessage(self, text, buttons=("OK",), severity=None,
                       default_choice=0, close_choice=-1, help=None,
                       title=None, details=None, details_label=None,
                       details_expanded=False, suppress=None):
        self.dialogs.append(text)
        print("\n[DIALOG] " + text.replace("\n", "\n         "))
        if details:
            print("         (details 已附加)")
        return 0

    def selectFromList(self, choices, default_choices=(), exclusive=False,
                       message=None, title=None, column_header="Choices",
                       num_visible_rows=10, clear_on_cancel=False,
                       width=0, height=0, sort=False, condense_paths=False):
        # 按默认勾选返回（跟真实用户不改勾选时一致），
        # 别全选 —— 全选会把 91MB 的 ffx 也下下来，跑一次要几分钟。
        picked = tuple(default_choices) if default_choices else tuple(range(len(choices)))
        print("\n[SELECT] %d 个选项，选中 %d 个" % (len(choices), len(picked)))
        for c in choices:
            print("         - " + str(c))
        return picked

    def setStatusMessage(self, message, severity=None):
        print("[STATUS] " + str(message))


hou.ui = _UI()
hou.shelves = types.SimpleNamespace(
    loadFile=lambda p: print("[SHELF] loadFile " + p))
sys.modules["hou"] = hou

# ---------------------------------------------------------------- 跑 installer

sys.path.insert(0, str(REPO / "installer"))
import installer  # noqa: E402

installer.main()

print("\n" + "#" * 60)
print("# 第二次运行：应提示「已是最新」")
print("#" * 60)
installer.main()

print("\n" + "#" * 60)
print("# 第三次运行：两个更新器的版本记录不同步（那个「反复提示更新」")
print("# 的 bug 场景）——预设库更新器把 node-preset 升到了 9.9.9，")
print("# bootstrap 自己的记录还在老版本。安装器必须取两边最大值，")
print("# 而不是再提示一遍更新/降级。")
print("#" * 60)
tk = os.path.join(PREF, installer.TOOLKIT_DIRNAME)
np_rec = os.path.join(tk, "toolbar", "node_preset", "library", ".update", "installed.json")
os.makedirs(os.path.dirname(np_rec), exist_ok=True)
with open(np_rec, "w", encoding="utf-8") as f:
    json.dump({"node-preset": {"version": "9.9.9",
                               "installed": "2026-11-17 00:00:00"}}, f)
installer.main()


# ---------------------------------------------------------------- 检查

print("\n" + "=" * 60)
ok = True


def check(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [FAIL] ") + label
          + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


check("MyToolkit 目录存在", os.path.isdir(tk))
check("toolbar/My_Tool.shelf",
      os.path.isfile(os.path.join(tk, "toolbar", "My_Tool.shelf")))
check("python3.11libs/netbox_bundles.py",
      os.path.isfile(os.path.join(tk, "python3.11libs", "netbox_bundles.py")))
check("presets/Sop/xform.idx",
      os.path.isfile(os.path.join(tk, "presets", "Sop", "xform.idx")))
check("installed.json", os.path.isfile(os.path.join(tk, "installed.json")))

pkg = os.path.join(PREF, "packages", "mytoolkit.json")
check("packages/mytoolkit.json", os.path.isfile(pkg))
if os.path.isfile(pkg):
    with open(pkg, encoding="utf-8") as f:
        d = json.load(f)
    print("     内容:", json.dumps(d, ensure_ascii=False))
    check("package 用 path 而不是 hpath", "path" in d and "hpath" not in d)

check("shelf 已拷到 pref/toolbar",
      os.path.isfile(os.path.join(PREF, "toolbar", "My_Tool.shelf")))

inst = os.path.join(tk, "installed.json")
if os.path.isfile(inst):
    with open(inst, encoding="utf-8") as f:
        d = json.load(f)
    print("     installed.json:", json.dumps(d, ensure_ascii=False))
    check("记录 core 版本", d.get("modules", {}).get("core"))
    # 预设包（preset-pack）模块：装了就该把 library 原样落位
    if "preset-pack" in (d.get("modules") or {}):
        lib = os.path.join(tk, "toolbar", "node_preset", "library")
        check("预设包：library 目录存在", os.path.isdir(lib))
        check("预设包：catalog.json 落位",
              os.path.isfile(os.path.join(lib, "catalog.json")))
        p_dirs = [n for n in (os.listdir(lib) if os.path.isdir(lib) else [])
                  if n.startswith("p_") and os.path.isdir(os.path.join(lib, n))]
        check("预设包：p_* 预设已落位（%d 个）" % len(p_dirs), len(p_dirs) > 0)
        check("预设包：运行数据目录没进包（无 _cache/_backup/_trash）",
              not any(os.path.isdir(os.path.join(lib, n))
                      for n in ("_cache", "_backup", "_trash")))

check("第二次运行提示「已是最新」", "已是最新" in (hou.ui.dialogs[-1] if hou.ui.dialogs else ""))

# 第三次运行：记录不同步时，不能提示更新/降级（取两边最大值）
third_last = hou.ui.dialogs[-1] if hou.ui.dialogs else ""
check("第三次运行（记录不同步）不提示更新", "发现更新" not in third_last, third_last[:60])
check("第三次运行仍认为已是最新", "已是最新" in third_last)

# ---------------------------------------------------------------- 卸载

print("\n" + "#" * 60)
print("# 卸载")
print("#" * 60)

# 先造一个带预设的 library（模拟用户存了预设再卸载的场景），
# 验证卸载时它会被"抢救"到 pref 下，而不是跟着 MyToolkit 一起被删。
lib_preset = os.path.join(tk, "toolbar", "node_preset", "library", "p_test123")
os.makedirs(lib_preset)
with open(os.path.join(lib_preset, "meta.json"), "w", encoding="utf-8") as f:
    json.dump({"name": "测试预设"}, f)

installer.uninstall_main()

# ---------- 卸载结果 ----------
rescued = [d for d in os.listdir(PREF) if d.startswith("MyToolkit_library_")]
check("卸载：预设库被搬到 pref 下保留", len(rescued) == 1)
if rescued:
    _inner = os.listdir(os.path.join(PREF, rescued[0]))
    check("卸载：抢救出来的库里有预设", "p_test123" in _inner, _inner)
check("卸载：MyToolkit 目录已删除", not os.path.isdir(tk))
check("卸载：packages/mytoolkit.json 已删除",
      not os.path.isfile(os.path.join(PREF, "packages", "mytoolkit.json")))
tb_left = [f for f in os.listdir(os.path.join(PREF, "toolbar"))
           if f.lower().endswith(".shelf")] if os.path.isdir(os.path.join(PREF, "toolbar")) else []
check("卸载：工具集拷入的 shelf 已删除（不应剩 My_Tool.shelf）",
      "My_Tool.shelf" not in tb_left, "剩下 %s" % tb_left)
check("卸载：提示里说了已卸载", "已卸载" in (hou.ui.dialogs[-1] if hou.ui.dialogs else ""))

print("=" * 60)
print("结果:", "全部通过" if ok else "有失败项")
shutil.rmtree(PREF, ignore_errors=True)
sys.exit(0 if ok else 1)
