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


# ---------------------------------------------------------------- 检查

print("\n" + "=" * 60)
ok = True


def check(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [FAIL] ") + label
          + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False


tk = os.path.join(PREF, installer.TOOLKIT_DIRNAME)
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

check("第二次运行提示「已是最新」", "已是最新" in (hou.ui.dialogs[-1] if hou.ui.dialogs else ""))

# ---------------------------------------------------------------- 卸载

print("\n" + "#" * 60)
print("# 卸载")
print("#" * 60)
installer.uninstall_main()

# ---------- 卸载结果 ----------
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
