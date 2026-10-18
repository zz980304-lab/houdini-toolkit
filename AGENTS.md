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

详细流程与故障处理见（仓库外）：`N:/Houdini_path/houdini_updata/05-日常维护/02-AI协作更新流程.md`
