# -*- coding: utf-8 -*-
"""Houdini 21（PySide6）全新安装端到端测试 —— 离线、不需要 license。

模拟一台只有 Houdini 21 的机器：
  1. 假 hou（applicationVersion = 21.0.440）+ 假 pref 目录（houdini21.0）
  2. 跑 installer.main()：下载换成从仓库 dist/ 读，避免网络抖动
  3. 检查安装出来的目录结构（pref/toolbar、packages、python3.11libs、预设库）
  4. 从**安装出来的那份代码**里 import 工具，用真 PySide6 把窗口建出来

为什么要"从安装目录"加载：源码能跑不等于发布包能跑。上一轮就吃过亏
（包里的旧 ui.py 缺修复，装完仍报错），所以这里刻意把 N 盘源码从 sys.path
里排除，只认 install_from 解压出来的那份。

用法（H21 自带 Python，无需 license）：
    set H21_HFS=E:\\Houdini_21\\Houdini21.0.440
    %H21_HFS%\\python311\\python.exe tools\\test_h21_install.py

先跑一次 tools/build.py 生成 dist/ 里的包。
"""

import json
import os
import shutil
import sys
import tempfile
import types

HFS = os.environ.get("H21_HFS", r"E:\Houdini_21\Houdini21.0.440")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.join(HFS, "python311", "lib", "site-packages-forced"))
if hasattr(os, "add_dll_directory"):
    os.add_dll_directory(os.path.join(HFS, "bin"))
    os.add_dll_directory(os.path.join(HFS, "qt", "bin"))

PREF = tempfile.mkdtemp(prefix="h21_pref_")
print("假 pref 目录:", PREF)

# ---------------------------------------------------------------- 假 hou（H21）
hou = types.ModuleType("hou")


class _Sev(object):
    ImportantMessage = 0
    Message = 1


hou.severityType = _Sev
hou.expandString = lambda s: s.replace("$HOUDINI_USER_PREF_DIR", PREF)
hou.applicationVersion = lambda: (21, 0, 440)
hou.applicationVersionString = lambda: "21.0.440"


class _UI(object):
    def __init__(self):
        self.dialogs = []

    def displayMessage(self, text, buttons=("OK",), severity=None,
                       default_choice=0, close_choice=-1, help=None,
                       title=None, details=None, details_label=None,
                       details_expanded=False, suppress=None):
        self.dialogs.append(text)
        print("\n[DIALOG] " + text.replace("\n", "\n         "))
        return 0

    def selectFromList(self, choices, default_choices=(), exclusive=False,
                       message=None, title=None, column_header="Choices",
                       num_visible_rows=10, clear_on_cancel=False,
                       width=0, height=0, sort=False, condense_paths=False):
        print("\n[SELECT] %d 个选项" % len(choices))
        return tuple(default_choices) if default_choices else tuple(range(len(choices)))

    def setStatusMessage(self, message, severity=None):
        print("[STATUS] " + str(message))


hou.ui = _UI()
hou.shelves = types.SimpleNamespace(loadFile=lambda p: print("[SHELF] loadFile " + p))
sys.modules["hou"] = hou

# ---------------------------------------------------------------- 离线化下载
sys.path.insert(0, os.path.join(REPO, "installer"))
import installer                                              # noqa: E402

installer.BASE_URLS = ["https://local.invalid"]


def _fetch_local(rel, stamp=True):
    name = rel.split("?")[0].split("/")[-1]
    path = (os.path.join(REPO, name) if name.endswith(".json")
            else os.path.join(REPO, "dist", name))
    with open(path, "rb") as handle:
        return handle.read()


def _download_local(url, dst, progress=None):
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    shutil.copy2(os.path.join(REPO, "dist", name), dst)
    return os.path.getsize(dst)


installer.fetch_bytes = _fetch_local
installer.download_to = _download_local

# 只装四个小模块（跳过大体积 otls，测的是流程不是体积）
installer.choose_modules = lambda manifest, local: [
    "core", "node-preset", "preset-pack", "hip-packer"]

installer.main()

# ---------------------------------------------------------------- 检查
ok = []


def check(label, cond):
    ok.append((bool(cond), label))
    print(("  [OK]   " if cond else "  [FAIL] ") + label)


tk = os.path.join(PREF, "MyToolkit")
print("\n=== 安装结果 ===")
check("MyToolkit 落在 houdini21.0 pref 下", os.path.isdir(tk))
check("toolbar/node_preset 落位",
      os.path.isdir(os.path.join(tk, "toolbar", "node_preset", "node_preset")))
check("toolbar/hip_packer 落位",
      os.path.isdir(os.path.join(tk, "toolbar", "hip_packer", "hip_packer")))
check("python3.11libs 落位（H21 也是 3.11）",
      os.path.isdir(os.path.join(tk, "python3.11libs")))
check("预设库落位", os.path.isdir(os.path.join(tk, "toolbar", "node_preset", "library")))
check("My_Tool.shelf 拷到 pref/toolbar",
      os.path.isfile(os.path.join(PREF, "toolbar", "My_Tool.shelf")))
check("packages/mytoolkit.json 注册",
      os.path.isfile(os.path.join(PREF, "packages", "mytoolkit.json")))
inst = os.path.join(tk, "installed.json")
check("installed.json 记录", os.path.isfile(inst))
if os.path.isfile(inst):
    data = json.load(open(inst, encoding="utf-8"))
    print("        记录:", json.dumps(data.get("modules"), ensure_ascii=False))
    check("记录了 node-preset 版本", data.get("modules", {}).get("node-preset"))

# ---------------------------------------------------------------- ensure_python_libs 回归
# 之前的坑：python_libs_name() 生成 python311libs（少个点），PY_LIBS_RE 又
# 匹配不到带点的真实目录 python3.11libs，结果函数永远 no-op、从没补建过。
# 这里同时验证「认得带点目录」和「版本不符时真能补建」。用独立临时目录，
# 不污染上面的安装目录。
print("\n=== ensure_python_libs 回归 ===")
_libs_tmp = tempfile.mkdtemp(prefix="h21_libs_")
os.makedirs(os.path.join(_libs_tmp, "python3.11libs"))
with open(os.path.join(_libs_tmp, "python3.11libs", "probe.py"), "w", encoding="utf-8") as _f:
    _f.write("# probe\n")
check("python_libs_name 返回带点目录名",
      installer.python_libs_name() == "python%d.%dlibs" % sys.version_info[:2])
check("PY_LIBS_RE 认得 python3.11libs",
      bool(installer.PY_LIBS_RE.match("python3.11libs")))
check("已有匹配目录时不重复补建", installer.ensure_python_libs(_libs_tmp) == "")
_real_name = installer.python_libs_name
installer.python_libs_name = lambda: "python3.12libs"   # 模拟未来 Houdini 换 Python 版本
try:
    _made = installer.ensure_python_libs(_libs_tmp)
finally:
    installer.python_libs_name = _real_name
check("版本不符时真能补建 python3.12libs", _made == "python3.12libs")
check("补建的目录带上了模块文件",
      os.path.isfile(os.path.join(_libs_tmp, "python3.12libs", "probe.py")))
shutil.rmtree(_libs_tmp, ignore_errors=True)

# ---------------------------------------------------------------- 从安装目录加载 UI
print("\n=== 从安装出来的代码加载 UI（真 PySide6）===")
for p in (os.path.join(tk, "toolbar", "node_preset"),
          os.path.join(tk, "toolbar", "hip_packer"),
          os.path.join(tk, "python3.11libs")):
    if p not in sys.path:
        sys.path.insert(0, p)

for key in [k for k in sys.modules if k.split(".")[0] in ("node_preset", "hip_packer")]:
    sys.modules.pop(key, None)

from PySide6 import QtCore, QtGui, QtWidgets                  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def step(label, fn):
    try:
        fn()
    except Exception as exc:
        ok.append((False, label))
        print("  [FAIL] %s -> %s: %s" % (label, type(exc).__name__, exc))
        import traceback
        traceback.print_exc()
        return None
    ok.append((True, label))
    print("  [OK]   " + label)
    return True


step("import node_preset（安装目录）", lambda: __import__("node_preset"))
mod = sys.modules.get("node_preset")
if mod:
    print("        Qt 绑定 =", getattr(mod, "QT_BINDING", "?"),
          "| 位置 =", os.path.dirname(mod.__file__))
    check("走的是 PySide6", getattr(mod, "QT_BINDING", "") == "PySide6")
    check("模块确实来自安装目录",
          os.path.abspath(mod.__file__).lower().startswith(os.path.abspath(tk).lower()))
    step("NodePresetWindow()",
         lambda: sys.modules["node_preset.ui"].NodePresetWindow(None))

step("import hip_packer（安装目录）", lambda: __import__("hip_packer"))
if sys.modules.get("hip_packer"):
    step("HipPackerWindow()",
         lambda: sys.modules["hip_packer.ui"].HipPackerWindow())

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 60)
bad = [x for x in ok if not x[0]]
print("共 %d 项，通过 %d，失败 %d" % (len(ok), len(ok) - len(bad), len(bad)))
for _c, label in bad:
    print("  FAIL", label)
print("安装目录:", tk)
shutil.rmtree(PREF, ignore_errors=True)
sys.exit(1 if bad else 0)
