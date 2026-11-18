# AGENTS.md

给 AI 的项目说明。如果你（AI）被要求"更新阿龙工具集 / 发布 Houdini 工具集"，按这里做。

## 这是什么

一套 Houdini 工具集的**自建分发系统**：

- 打包源：`N:/Houdini_path/`（工具架、Python 模块、预设、HDA）
- 仓库：本仓库
- 分发：`https://houdini-toolkit.pages.dev`（Cloudflare Pages，自动同步 GitHub）
- 客户端：Houdini 工具架「阿龙工具」里的「安装 / 更新工具集」按钮，
  它读 `manifest.json` 比版本，下载 zip，校验 sha256，装到 `$HOUDINI_USER_PREF_DIR/MyToolkit`

用户换电脑时只需要一个 URL，点一下就还原全部工具。

## 更新流程（三步）

```bash
cd N:/Houdini_path/houdini_updata/repo/houdini-toolkit

python tools/build.py --dry-run        # 1. 看哪些模块变了（全是"未变化"就不用发布）
python tools/release.py "说明文字"      # 2. 打包 + 提交 + 推送（自动挑网络通道）
curl -sS https://houdini-toolkit.pages.dev/manifest.json   # 3. 1~2 分钟后验证版本号
```

`release.py` 已经封装了 make_shelf + build + commit + push，别拆开手动跑。

## 目录

| 路径 | 用途 |
|---|---|
| `installer/installer.py` | 下载器源码（改完必须跑 `tools/make_shelf.py` 重新内嵌） |
| `tools/make_shelf.py` | 把 installer 内嵌进 `bootstrap.shelf` |
| `tools/build.py` | 按模块打包，内容哈希判断变化，算 sha256，生成 manifest |
| `tools/build_config.json` | 模块划分、打包源、排除规则 |
| `tools/release.py` | 一键发布（含网络通道自动探测） |
| `tools/selftest.py` | 离线自测，mock 的 hou API 用真实签名 |
| `tools/test_qt_bindings.py` | Qt 双绑定冒烟：同一套用例跑 PySide2(Qt5) 与 PySide6(Qt6) |
| `tools/test_h21_install.py` | Houdini 21 全新安装端到端（假 hou + 真 PySide6，不需要 license） |
| `tools/make_icon.py` | 生成清单缩略图 |
| `manifest.json` / `shelves.json` | **自动生成，不要手改** |
| `dist/` | 打包产物，旧版本 zip 不要删（能回滚） |

## 硬规则（违反会出事）

1. `bootstrap.shelf` 的 XML 声明必须是 `encoding="ISO-8859-1"`（内容按 UTF-8 存字节）。
   写成 `UTF-8` 会让中文标签全部丢失，工具架显示成 `(mytoolkit_bootstrap)`。
2. `shelves.json` 里的 `fileURL` / `thumbnailURL` 必须指向 **github.io**，不能指 pages.dev。
   Houdini 的 Download Shelf 用 urllib（UA `Python-urllib/3.11`），Cloudflare 会对它返回 403，
   缩略图下不来就会弹 `PermissionError: [WinError 32]`。
3. `hou.ui.selectFromList` **没有 `multiple_select` 参数**，多选靠默认的 `exclusive=False`。
4. Cloudflare Pages 单文件硬限 25MB。超过的 zip 必须分片；单个文件超限由
   `split_big_file` 切成 `.p000...` 段，安装器用 `.parts.json` 拼回。
5. 不要修改 `N:/Houdini_path/` 下的源文件（那是用户的活数据）。
6. 不要删 `dist/` 里的旧 zip，不要把 token / 密码写进任何文件。
7. **新增任何工具目录，必须在 `build_config.json` 里注册成模块**。
   `core` 只发 `*.shelf` + `python3.11libs` + `presets`，**不发 `toolbar/` 下的包**；
   `My_Tool.shelf` 里的按钮只是"按 HOUDINI_PATH 找包"，包没发 → 别的电脑上点按钮
   直接 `ImportError`。2026-10-20 就漏了 `hip_packer`（工程保存工具）和 `node_preset`。
8. **界面代码必须走 `qt.py` 兼容层，禁止直接 `import PySide2` / `import PySide6`。**
   Houdini 20.5 及以下是 PySide2/Qt5，21.0 起是 PySide6/Qt6。`qt.py` 优先用
   `hutil.Qt`（跟着主程序走），其次 PySide6 / PySide2，并提供 `enum_of()` /
   `exec_box()` / `event_pos()` 等跨版本取值器。禁用 `Qt.xxx` 直写枚举（Qt6 已
   收进作用域类）、`exec_()`（Qt6 主推 `exec()`）、`QHeaderView.setResizeMode`
   （Qt6 只有 `setSectionResizeMode`）、`QFontMetrics.width`（Qt6 是 `horizontalAdvance`）。

## 验证命令

```bash
python tools/selftest.py                       # 安装器全流程（20.5 语义，假 hou）
python tools/build.py                          # 先出包，下面两个测试要读 dist/

# Qt 双绑定冒烟（两边都要过）
E:/Houdini20.5.401/bin/hython.exe tools/test_qt_bindings.py          # PySide2 / Qt5
E:/Houdini_21/Houdini21.0.440/python311/python.exe tools/test_qt_bindings.py   # PySide6 / Qt6

# H21 全新安装端到端（离线、不需要 license，路径由 H21_HFS 覆盖）
E:/Houdini_21/Houdini21.0.440/python311/python.exe tools/test_h21_install.py
```

⚠️ H21 的 hython 需要 21 的 license；只有 20.5 授权时用上面的"纯 Python + 假 hou"跑法。

详细流程与故障处理见（仓库外）：`N:/Houdini_path/houdini_updata/05-日常维护/02-AI协作更新流程.md`
