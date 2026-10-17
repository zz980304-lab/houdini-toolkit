"""Houdini 启动后（UI 就绪时）自动拉起 MCP server。

位置：N:\\Houdini_path\\python3.11libs\\uiready.py
  N:\\Houdini_path 通过 My_path.json 进入 HOUDINI_PATH，20.5 / 21.0 共用这一份，
  以后只在这里维护，不用往各版本目录里各放一份。

为什么用 uiready.py 而不是 pythonrc.py：
  - 官方文档规定启动钩子要放在 HOUDINI_PATH 下的 pythonX.Ylibs/ 里。
  - pythonrc.py 跑得极早（UI 还没起来），而且 hython / hbatch 也会执行它，
    批处理进程会跟着抢端口。
  - uiready.py 只在“图形界面”会话执行，且在 UI 就绪之后。

弹窗策略（2026-10-16 改）：
  默认**不弹框**，端口/URL 只写控制台。启动期的模态框会把整个 Houdini 卡住，
  不点掉就没法操作，无人值守时更是灾难。

诊断日志（每次启动写一行）：
  %LOCALAPPDATA%\\houdini-mcp\\autostart.log
  记录 pid / 端口 / 当时可见的顶层窗口。若某次启动有模态框拦住界面，这里能看见。

开关：
  HOUDINI_MCP_AUTOSTART=0   关闭自动启动
  HOUDINI_MCP_QUIET=0       本钩子不再压制弹窗（默认 1=压制）
  HOUDINI_MCP_POPUP=1       放开 autostart._notify 的弹窗（默认 0=只打印）
  真想看弹框要两个一起放开：HOUDINI_MCP_QUIET=0 + HOUDINI_MCP_POPUP=1
"""

import os
import time

PORT_READY = True  # 便于其它脚本 import 判断本文件已加载

LOG_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or ".",
    "houdini-mcp",
    "autostart.log",
)


def _flag(name, default):
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def _log(msg):
    try:
        d = os.path.dirname(LOG_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("%s  pid=%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), msg))
    except Exception:
        pass


def _visible_windows():
    """可见的顶层窗口列表。带 modal=True 的就是会卡住界面的模态框。"""
    try:
        from hutil.Qt import QtWidgets

        out = []
        for w in QtWidgets.QApplication.topLevelWidgets():
            try:
                if not w.isVisible():
                    continue
                title = w.windowTitle() or w.objectName() or type(w).__name__
                if w.isModal():
                    out.append("MODAL[%s]" % title)
                else:
                    out.append(title)
            except Exception:
                continue
        return out
    except Exception as exc:
        return ["<window probe failed: %s>" % exc]


def _report(tag):
    wins = _visible_windows()
    block = [w for w in wins if w.startswith("MODAL[")]
    line = "%s windows=%s" % (tag, wins)
    if block:
        line += "  <-- BLOCKING: %s" % block
    _log(line)
    if block:
        print("[houdini-mcp] WARNING: modal dialog up at startup: %s" % block)


def _autostart():
    # 默认静默：启动期弹模态框会挡住整个 Houdini，必须有人点掉才能继续。
    quiet = _flag("HOUDINI_MCP_QUIET", True)
    _report("boot t0")
    try:
        from houdini_side.autostart import start_with_autoport
    except Exception as exc:
        # MCP 项目没装、路径变了、或 PYTHONPATH 没配好 —— 静默跳过，别影响开机
        print("[houdini-mcp] autostart skipped: %s" % exc)
        _log("autostart skipped: %s" % exc)
        return
    try:
        port = start_with_autoport(quiet=quiet)
        _log("autostart ok, port=%s (quiet=%s)" % (port, quiet))
    except Exception as exc:
        print("[houdini-mcp] autostart failed: %s" % exc)
        _log("autostart failed: %s" % exc)
    _report("boot t0+start")

    # 8 秒后再看一眼：HDA / 授权之类的对话框往往晚一点才弹。
    def _later():
        try:
            _report("boot t+8s")
        except Exception:
            pass

    try:
        import threading

        def _timer():
            import hdefereval

            hdefereval.executeDeferred(_later)

        t = threading.Timer(8.0, _timer)
        t.daemon = True
        t.start()
    except Exception:
        pass


def _netbox_bundles():
    """盒子集合(Network Box -> Bundle)自动同步：开机就接管，不用点工具架按钮。

    以前必须手动点一次「同步/查看盒子集合」才生效，重启 Houdini 就丢。
    开关：HOUDINI_NETBOX_BUNDLES=0 关掉。
    """
    try:
        import netbox_bundles

        netbox_bundles.install()
        _log("netbox_bundles installed")
    except Exception as exc:
        # 模块不在 / 出问题：静默跳过，绝不能因此拖垮启动
        print("[netbox_bundles] skipped: %s" % exc)
        _log("netbox_bundles skipped: %s" % exc)


if _flag("HOUDINI_MCP_AUTOSTART", True):
    # 放到事件循环的下一次空闲再执行：等主窗口画完再起服务，别拖慢启动。
    try:
        import hdefereval

        hdefereval.executeDeferred(_autostart)
    except Exception:
        _autostart()

if _flag("HOUDINI_NETBOX_BUNDLES", True):
    try:
        import hdefereval

        hdefereval.executeDeferred(_netbox_bundles)
    except Exception:
        pass
