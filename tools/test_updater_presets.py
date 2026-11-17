# -*- coding: utf-8 -*-
"""离线功能测试：updater 的预设包合并更新逻辑。

不碰真实网络、不碰 N 盘真实 library —— 用 HOUDINI_NODE_PRESET_DIR
把库目录指到临时目录，用一个伪造的 preset-pack zip 验证：
  1. 合并语义：本机独有预设不动、云端同名覆盖、云端新预设落位
  2. catalog.json 被覆盖且先备份
  3. zip-slip（.. 路径）被拦截
  4. 预设条目之外的路径（工具代码）不被预设解压碰
  5. 版本记录：save_installed / preset_installed_version /
     _bootstrap_installed_version 回退
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile

TMP = tempfile.mkdtemp(prefix="np_updater_test_")
LIB = os.path.join(TMP, "library")
TK = os.path.join(TMP, "MyToolkit")           # 模拟 bootstrap 装好的 MyToolkit
os.makedirs(LIB)
os.makedirs(os.path.join(TK, "toolbar", "node_preset", "library"))

# ---- 伪造本机已有数据
os.makedirs(os.path.join(LIB, "p_local"))
with open(os.path.join(LIB, "p_local", "meta.json"), "w", encoding="utf-8") as f:
    json.dump({"name": "本机预设", "keep": True}, f)
with open(os.path.join(LIB, "catalog.json"), "w", encoding="utf-8") as f:
    json.dump({"schema": 1, "folders": [{"id": "f_local", "name": "本机文件夹"}]}, f)

# ---- 伪造云端预设包 zip
zpath = os.path.join(TMP, "preset-pack-9.9.9.zip")
with zipfile.ZipFile(zpath, "w") as z:
    def w(name, data):
        z.writestr(name, data)
    w("MyToolkit/toolbar/node_preset/library/catalog.json",
      json.dumps({"schema": 1, "folders": [{"id": "f_cloud", "name": "云端文件夹"}]}))
    w("MyToolkit/toolbar/node_preset/library/p_cloud/meta.json",
      json.dumps({"name": "云端新预设"}))
    w("MyToolkit/toolbar/node_preset/library/p_cloud/preview.gif", b"GIF89a-fake")
    w("MyToolkit/toolbar/node_preset/library/p_local/meta.json",
      json.dumps({"name": "本机预设", "keep": False, "cloud": True}))   # 同名覆盖
    w("MyToolkit/toolbar/node_preset/library/../../evil.txt", b"boom")  # zip-slip
    w("MyToolkit/toolbar/node_preset/ui.py", b"# should-not-land-in-library")

# ---- 环境：库目录指向临时目录
os.environ["HOUDINI_NODE_PRESET_DIR"] = LIB

sys.path.insert(0, r"N:/Houdini_path/toolbar/node_preset")  # 本机 N 盘源；别的机器改成自己的源路径
from node_preset import paths, updater   # noqa: E402

ok = True
def check(label, cond, extra=""):
    global ok
    print(("  [OK] " if cond else "  [FAIL] ") + label + (("  " + str(extra)) if extra else ""))
    if not cond:
        ok = False

# ---- 1. catalog 备份 + 合并解压
backup = updater._backup_catalog()
check("catalog 先备份", backup and os.path.isfile(os.path.join(backup, "catalog.json")))

written, skipped, error = updater._extract_preset_parts([zpath])
check("解压无错误", error == "", error)
check("云端新预设落位", os.path.isfile(os.path.join(LIB, "p_cloud", "meta.json")))
check("预览图落位", os.path.isfile(os.path.join(LIB, "p_cloud", "preview.gif")))
check("同名预设被云端覆盖",
      json.load(open(os.path.join(LIB, "p_local", "meta.json"), encoding="utf-8")).get("cloud") is True)
check("catalog 已是云端版",
      json.load(open(os.path.join(LIB, "catalog.json"), encoding="utf-8"))["folders"][0]["id"] == "f_cloud")
check("catalog 备份里还是本机版",
      json.load(open(os.path.join(backup, "catalog.json"), encoding="utf-8"))["folders"][0]["id"] == "f_local")
check("zip-slip 被拦截", not os.path.isfile(os.path.join(TMP, "evil.txt"))
      and not os.path.isfile(os.path.normpath(os.path.join(LIB, "..", "evil.txt"))))
check("工具代码没被碰到", not os.path.isfile(os.path.join(LIB, "ui.py")))
print("     （写入 %d，跳过 %d：应 ≥ 2 —— evil.txt 和 ui.py）" % (written, skipped))
check("跳过数 ≥ 2", skipped >= 2)

# ---- 2. 代码解压不碰 library（tool_dir 重定向到临时目录，不碰 N 盘）
code_zip = os.path.join(TMP, "node-preset-9.9.9.zip")
with zipfile.ZipFile(code_zip, "w") as z:
    z.writestr("MyToolkit/toolbar/node_preset/keep_out.py", b"x")
    z.writestr("MyToolkit/toolbar/node_preset/library/hacked.json", b"x")
FAKE_TOOL = os.path.join(TMP, "fake_tool")
os.makedirs(os.path.join(FAKE_TOOL, "node_preset"))
real_tool_dir = paths.tool_dir
paths.tool_dir = lambda: FAKE_TOOL
try:
    cw, cs, ce = updater._extract_parts([code_zip])
    check("代码条目写到工具目录", os.path.isfile(os.path.join(FAKE_TOOL, "keep_out.py")))
    check("代码包里的 library/ 条目被挡住",
          not os.path.isfile(os.path.join(FAKE_TOOL, "library", "hacked.json")))
    check("真实 N 盘 library 无污染",
          not os.path.isfile(os.path.join(real_tool_dir(), "library", "hacked.json")))
finally:
    paths.tool_dir = real_tool_dir

# ---- 3. 版本记录
check("初始：本更新器无记录", updater.preset_installed_version() == "")
# 伪造 bootstrap 记录（installed.json 在 MyToolkit 根下）
with open(os.path.join(TK, "installed.json"), "w", encoding="utf-8") as f:
    json.dump({"version": "9.9.9", "modules": {"preset-pack": "1.2.3"}}, f)
real_tool_dir = paths.tool_dir
paths.tool_dir = lambda: os.path.join(TK, "toolbar", "node_preset")
try:
    check("bootstrap 记录回退生效", updater.preset_installed_version() == "1.2.3")
    check("_bootstrap_installed_version", updater._bootstrap_installed_version("preset-pack") == "1.2.3")
finally:
    paths.tool_dir = real_tool_dir
# 本更新器自己的记录优先
updater.save_installed("2.0.0", {}, updater.PRESET_MODULE_ID)
check("本更新器记录优先", updater.preset_installed_version() == "2.0.0")
rec = json.load(open(updater.paths.installed_path(), encoding="utf-8"))
check("两条记录互不覆盖", rec.get(updater.PRESET_MODULE_ID, {}).get("version") == "2.0.0"
      and rec.get("preset-pack", {}).get("version") == "2.0.0")

# ---- 4. _update_presets 的版本判断（不下载）
class FakeMod(dict):
    pass
missing = updater._update_presets(None, {"modules": []}, "", False)
check("线上没有预设包 → missing", missing == "missing")
cur = updater._update_presets(None, {"modules": [{"id": "preset-pack", "version": "2.0.0", "parts": []}]}, "", False)
check("同版本 → current", cur == "current")

print("\n结果:", "全部通过" if ok else "有失败项")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(0 if ok else 1)
