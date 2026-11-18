# -*- coding: utf-8 -*-
"""Qt 双绑定冒烟测试：同一套测试分别跑 PySide2(Qt5) 与 PySide6(Qt6)。

为什么需要它
    Houdini 20.5 及以下是 PySide2 / Qt5，21.0 起换成 PySide6 / Qt6。
    两边的差异不是"改个 import"那么温柔：枚举被收进作用域类、部分枚举改名
    （Qt.ItemIsTristate -> Qt.ItemIsAutoTristate）、QHeaderView.setResizeMode
    被删掉……这些错误要到真实机器上点开窗口才炸。这个脚本在离线状态就把两个
    工具的主窗口 / 对话框真正 new 出来，把问题提前暴露。

两种跑法
    1) 有 license 的 Houdini（真 hou、真 Qt）：
           <HFS>/bin/hython.exe tools/test_qt_bindings.py
    2) 没有 H21 license 也能测 Qt6：拿 H21 自带的 Python + PySide6，
       配一个假 hou 模块（只做 UI 构建，不碰场景）：
           set H21_HFS=E:\\Houdini_21\\Houdini21.0.440
           %H21_HFS%\\python311\\python.exe tools\\test_qt_bindings.py

环境变量
    H21_HFS       Houdini 21 安装目录（纯 Python 模式用；默认 E:/Houdini_21/Houdini21.0.440）
    TOOLKIT_SRC   源码根（默认 N:/Houdini_path）
"""

import os
import sys
import traceback
import types

H21_HFS = os.environ.get("H21_HFS", r"E:\Houdini_21\Houdini21.0.440")
SRC = os.environ.get("TOOLKIT_SRC", r"N:/Houdini_path")

IN_HOUDINI = False
try:
    import hou as _real_hou                                   # noqa: F401
    IN_HOUDINI = True
except Exception:
    pass

if not IN_HOUDINI:
    sys.path.insert(0, os.path.join(H21_HFS, "python311", "lib", "site-packages-forced"))
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(os.path.join(H21_HFS, "bin"))
        os.add_dll_directory(os.path.join(H21_HFS, "qt", "bin"))

sys.path.insert(0, os.path.join(SRC, "toolbar", "node_preset"))
sys.path.insert(0, os.path.join(SRC, "toolbar", "hip_packer"))
sys.path.insert(0, os.path.join(SRC, "python3.11libs"))


# ---------------------------------------------------------------- 假 hou
class Dummy(object):
    """万能空对象：未知属性返回自己，可迭代（空）、可调用、bool False。"""

    def __init__(self, name="dummy"):
        self._name = name

    def __getattr__(self, item):
        if item.startswith("__"):
            raise AttributeError(item)
        return Dummy("%s.%s" % (self._name, item))

    def __call__(self, *a, **kw):
        return Dummy(self._name + "()")

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return False


if not IN_HOUDINI:
    hou = types.ModuleType("hou")
    hou.__getattr__ = lambda item: Dummy(item)
    hou.getenv = lambda name, default=None: (
        default if name != "HOUDINI_PATH" else SRC)
    hou.applicationVersion = lambda: (21, 0, 440)
    hou.applicationVersionString = lambda: "21.0.440"
    hou.severityType = Dummy("severityType")
    hou.ui = Dummy("ui")
    hou.qt = Dummy("qt")
    hou.pwd = lambda: None
    hou.node = lambda path: None
    hou.paneTabType = Dummy("paneTabType")
    hou.nodeType = lambda *a, **kw: None
    hou.context = lambda name: None
    hou.expandString = lambda s: s
    sys.modules["hou"] = hou

# 走工具自己的兼容层，这样 Qt5 / Qt6 跑的是同一份测试
import node_preset.qt as nq                                    # noqa: E402
QtCore, QtGui, QtWidgets = nq.QtCore, nq.QtGui, nq.QtWidgets

print("%s / Qt %s / py %s / in_houdini=%s"
      % (nq.BINDING, QtCore.qVersion(), sys.version.split()[0], IN_HOUDINI))

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

RESULTS = []


def step(label, fn):
    try:
        fn()
    except Exception as exc:
        RESULTS.append((False, label, "%s: %s" % (type(exc).__name__, exc)))
        print("  [FAIL] %s  ->  %s: %s" % (label, type(exc).__name__, exc))
        for line in traceback.format_exc().strip().splitlines()[-5:]:
            print("         | " + line)
        return None
    RESULTS.append((True, label, ""))
    print("  [OK]   %s" % label)
    return True


print("\n=== 1. 导入 ===")
np_mod = step("import node_preset", lambda: __import__("node_preset"))
hp_mod = step("import hip_packer", lambda: __import__("hip_packer"))
for name, mod in (("node_preset", sys.modules.get("node_preset")),
                  ("hip_packer", sys.modules.get("hip_packer"))):
    if mod:
        print("       %-12s QT_BINDING=%s" % (name, getattr(mod, "QT_BINDING", "?")))

print("\n=== 2. 主窗口 ===")
np_ui = sys.modules.get("node_preset.ui")
if np_ui:
    step("node_preset.NodePresetWindow()", lambda: np_ui.NodePresetWindow(None))
    step("NodePresetWindow() 第二次", lambda: np_ui.NodePresetWindow(None))

np_widgets = step("import node_preset.widgets",
                  lambda: __import__("node_preset.widgets", fromlist=["widgets"]))
if np_widgets:
    npw = sys.modules["node_preset.widgets"]
    for name in ("PresetView", "TagBar", "PreviewStore", "DropHintLayer"):
        if hasattr(npw, name):
            step("%s()" % name, lambda n=name: getattr(npw, n)())

print("\n=== 3. 对话框 ===")
for modname in ("node_preset.create_dialog", "node_preset.name_dialog",
                "node_preset.folder_dialog"):
    step("import %s" % modname, lambda m=modname: __import__(m, fromlist=["x"]))
    mod = sys.modules.get(modname)
    if not mod:
        continue
    for name in [n for n in dir(mod) if n.endswith("Dialog")]:
        obj = getattr(mod, name)
        if isinstance(obj, type):
            step("%s.%s(None)" % (modname.split(".")[-1], name),
                 lambda o=obj: o(None))

print("\n=== 4. controller（真实库扫描）===")
ctrl = step("import controller",
            lambda: __import__("node_preset.controller", fromlist=["controller"]))
if ctrl and np_ui:
    cmod = sys.modules["node_preset.controller"]
    win = np_ui.NodePresetWindow(None)
    step("Controller(win)", lambda: cmod.Controller(win, parent=win))
    controller = cmod.Controller(win, parent=win)
    for meth in ("reload",):
        if hasattr(controller, meth):
            step("controller.%s()" % meth,
                 lambda m=meth: getattr(controller, m)())

print("\n=== 5. hip_packer ===")
hp_ui = sys.modules.get("hip_packer.ui")
if hp_ui:
    import inspect
    for name in [n for n in dir(hp_ui)
                 if isinstance(getattr(hp_ui, n), type)
                 and (n.endswith("Window") or n.endswith("Dialog"))]:
        obj = getattr(hp_ui, name)
        try:
            sig = inspect.signature(obj.__init__)
            nparams = len([p for p in sig.parameters.values()
                           if p.name != "self" and p.default is p.empty])
        except Exception:
            nparams = 1
        step("%s()" % name, lambda o=obj, n=nparams: o(*([None] * n)))

print("\n=== 6. 图标 / SVG ===")
step("import icons", lambda: __import__("node_preset.icons", fromlist=["icons"]))
icons = sys.modules.get("node_preset.icons")
if icons:
    for name in ("icon", "make_icon", "colored_icon"):
        if hasattr(icons, name):
            step("icons.%s('folder')" % name,
                 lambda n=name: getattr(icons, n)("folder"))

print("\n" + "=" * 60)
bad = [r for r in RESULTS if not r[0]]
print("共 %d 项，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - len(bad), len(bad)))
for _ok, label, err in bad:
    print("  FAIL %s  ->  %s" % (label, err))
sys.exit(1 if bad else 0)
