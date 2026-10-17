#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 installer/installer.py 内嵌进 bootstrap.shelf

改完 installer.py 之后必须重新跑一次：
    python tools/make_shelf.py

生成的 bootstrap.shelf 是自包含的：即使服务器上 installer.py 没了，
只要这个 shelf 文件在，就能完成安装。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "installer" / "installer.py"
OUT = ROOT / "bootstrap.shelf"

SHELF_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<shelfDocument>
  <!-- 自动生成，请勿手改。源文件：installer/installer.py（改完跑 tools/make_shelf.py） -->
  <toolshelf name="mytoolkit_bootstrap" label="工具集">
    <memberTool name="mytoolkit_installer"/>
  </toolshelf>

  <tool name="mytoolkit_installer" label="安装 / 更新工具集" icon="MISC_python">
    <helpText><![CDATA[从云端（Cloudflare Pages）拉取 MyToolkit 最新版本。
首次使用会安装全部默认模块；之后每次点击会提示是否有更新。]]></helpText>
    <script scriptType="python"><![CDATA[
{code}
]]></script>
  </tool>
</shelfDocument>
"""


def main():
    code = SRC.read_text(encoding="utf-8")

    # 去掉 __main__ 守卫，改成无条件调用（shelf 里 __name__ 不一定是 __main__）
    code = re.sub(
        r'if\s+__name__\s*==\s*[\'"]__main__[\'"]\s*:\s*\n\s*main\(\)\s*\n?',
        "",
        code,
    )
    code = code.rstrip() + "\n\nmain()\n"

    if "]]>" in code:
        raise SystemExit("installer.py 里出现了 ]]> ，会破坏 CDATA 包裹，请改写")

    OUT.write_text(SHELF_TEMPLATE.format(code=code), encoding="utf-8")
    print("已生成 %s（%.1f KB）" % (OUT, OUT.stat().st_size / 1024.0))


if __name__ == "__main__":
    main()
