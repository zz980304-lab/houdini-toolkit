"""netbox_bundles.py —— 网络盒(Network Box) ↔ Bundle(节点包) 自动同步。

用法（工具架按钮「我的工具」里）：
    新建盒子集合   → 在当前网络层级建盒子，默认名 box_1 / box_2 …，并生成同名集合
    往盒子里拖节点 → 集合里加
    拖出去         → 集合里删
    改盒子标题     → 集合跟着改名
    删掉盒子       → 集合一起消失
    渲染器 Objects 填 @box_1 → 渲盒子里那批物体

v2（2026-10-17）：渲染器参数**保持 @bundle 写法**（可读、可改名、删掉就是断开），
实时性由「渲染前脚本」负责：点渲染那一刻才临时展开成路径，渲完自动还原成 @bundle。
展开脚本直接写进 ROP 的 Pre/Post-Render Script 参数里，随 .hip 保存，
别人拿到工程不用装任何东西就能用 → install_rop_hooks()。

三个关键点（踩过坑才加的）：
1) **盒子的"标题"是 comment 不是 name**。
   在网编里点盒子顶栏输入的那行字存进的是 comment()，name 还是 box_1。
   所以显示名 / bundle 名取 `comment() or name()`，否则会出现"看着改名了、集合没动"。
2) **Houdini 的 Python 会话常驻 + 模块会被重新 import**，
   旧模块实例挂的事件回调和定时器还活着，会和新实例打架（把名字改回去）。
   所以状态一律存在 hou.session 里，且 install() 会先卸掉上一次挂的钩子再重新挂。
3) **渲染器参数里写 @bundle 不会实时更新**（Houdini 缓存了展开结果）。
   所以本模块会把 `@render` 自动改写成显式路径 `/obj/box /obj/sphere1`，
   并在盒子内容变化时重写。原始写法记在节点 userData(`netbox_bundles_refs`) 上，
   restore_refs() 可以还原。EXPAND_REFS=False 可关掉这个行为。

安装：本文件放 HOUDINI_PATH/python3.11libs/ 下；
    工具架按钮脚本里 `import netbox_bundles as nb; nb.install(); nb.create_box()`。
"""

import hou

# 会扫描这些根层级里的网络盒（都是 cheap 调用）
SCOPES = ["/obj", "/out", "/stage", "/mat", "/shop", "/img", "/ch", "/tasks", "/vex"]
POLL_MS = 0             # 兜底轮询间隔(毫秒)，0 = 不轮询。
                        # ⚠ 别开成 QTimer：2026-10-17 连崩两次，堆栈都是
                        # QEventDispatcherWin32::sendTimerEvent -> QMetaObject::cast，
                        # 就是 Qt 定时器回调到已被回收的 Python 对象。事件回调已经够用。
OBJ_ONLY = False        # True = 只收 Object 层级的节点
SET_COMMENT = True      # 建盒子时把 comment 设成名字，保证"看到的标题 == 集合名"

# ---- 渲染参数里的 @bundle 引用（关键，见 expand_refs 的注释）----
# ⚠ Houdini 对 ROP 参数里 "@bundle" 的展开结果有缓存：
#   改 bundle 成员 **不会** 让渲染器更新，只有参数值自己变了才重算。
#   2026-10-17 实测（/out/test arnold，forceobject=@render，用 ifd 产物核对）：
#     bundle 3→2，参数不动        → 渲染还是 3 个（旧缓存）
#     参数 set 成相同值 / 加尾随空格 / 先空再设回 → 都还是 3 个（缓存没清）
#     参数写成显式路径 /obj/box /obj/sphere1     → 立刻变 2 个 ✔
#     再改回 3 个、再减回 2 个，显式路径每次都实时 ✔
#   所以本模块把 "@bundle" 自动改写成显式路径，并在成员变化时重写，保证实时。
# False（v2 默认）= 参数里保留 @bundle 原样，实时性交给 ROP 的渲染前脚本。
#   好处：@bundle 可以随时改名 / 删掉=断开 / 换成另一个框，参数永远是你要的那行字。
# True = 老做法，运行时就把 @bundle 换成显式路径（能实时，但看不出引用关系、也没法断开）。
EXPAND_REFS = False
REF_SCOPES = ["/out"]   # 在这些层级里找引用（/out 放渲染器）
REF_KEY = "netbox_bundles_refs"
ROP_FLAG = "netbox_bundles_rop"   # ROP 上标记“已装渲染前脚本”
BAK_KEY = "netbox_bundles_bak"    # 渲染前临时备份的原始 @ 写法

_EVENTS = (
    hou.nodeEventType.NetworkBoxCreated,
    hou.nodeEventType.NetworkBoxChanged,
    hou.nodeEventType.NetworkBoxDeleted,
    hou.nodeEventType.ChildCreated,
    hou.nodeEventType.ChildDeleted,
)


# ---------------------------------------------------------------- 工具函数
def _state():
    """状态存 hou.session：模块被重新 import 也不会丢，新旧实例共享同一份。"""
    s = getattr(hou.session, "netbox_bundles_state", None)
    if s is None or not isinstance(s, dict):
        s = {"sid2name": {}, "sig": None}
        hou.session.netbox_bundles_state = s
    s.setdefault("sid2name", {})
    s.setdefault("sig", None)
    return s


def _safe(s):
    s = (s or "").strip()
    out = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in s)
    if out and out[0].isdigit():
        out = "_" + out
    return out or "box"


def _display_name(box):
    """盒子上看到的那行标题 = comment；没写 comment 才用 name。"""
    try:
        c = (box.comment() or "").strip()
    except Exception:
        c = ""
    if c:
        return _safe(c)
    try:
        return _safe(box.name())
    except Exception:
        return "box"


def _scopes():
    return [hou.node(p) for p in SCOPES if hou.node(p) is not None]


def _all_boxes():
    out = []
    for p in _scopes():
        try:
            out += list(p.networkBoxes())
        except Exception:
            pass
    return out


def _box_nodes(box):
    try:
        ns = [n for n in box.nodes(recurse=False) if n is not None]
    except Exception:
        return []
    if OBJ_ONLY:
        ns = [n for n in ns if n.type().category().name() == "Object"]
    return ns


def _unique_name(base, parent=None):
    """盒子名 + bundle 名都要唯一（bundle 是全局的）。"""
    taken = set()
    for p in _scopes():
        try:
            for b in p.networkBoxes():
                taken.add(b.name())
                c = (b.comment() or "").strip()
                if c:
                    taken.add(_safe(c))
        except Exception:
            pass
    try:
        taken |= {b.name() for b in hou.nodeBundles()}
    except Exception:
        pass
    i = 1
    while True:
        name = "%s_%d" % (base, i)
        if name not in taken:
            return name
        i += 1


# ---------------------------------------------------------------- 同步核心
def sync():
    """把所有盒子同步成同名 bundle。返回本轮有变化的 bundle 名列表。"""
    st = _state()
    sid2name = st["sid2name"]
    changed = []
    live = {}
    claim = {v: k for k, v in sid2name.items()}   # bundle名 -> sid

    for box in _all_boxes():
        try:
            sid = box.sessionId()
            name = _display_name(box)
            # 名字被别的盒子占了（不同层级重名）→ 加层级后缀
            if name in claim and claim[name] != sid:
                try:
                    name = _safe("%s_%s" % (name, box.parent().name()))
                except Exception:
                    name = name + "_x"

            prev = sid2name.get(sid)
            b = hou.nodeBundle(name)

            # 改名：把旧 bundle 的内容搬到新名字下，删掉旧的
            if prev and prev != name:
                old = hou.nodeBundle(prev)
                if old is not None:
                    if b is None:
                        b = hou.addNodeBundle(name)
                    _set_members(b, [n for n in old.nodes() if n is not None])
                    try:
                        _remove_bundle(prev)
                    except Exception:
                        pass
                    _rename_refs(prev, name)
                    changed.append("%s -> %s" % (prev, name))
            if b is None:
                b = hou.addNodeBundle(name)
                changed.append(name)

            sid2name[sid] = name
            live[sid] = name

            want = _box_nodes(box)
            cur = [n for n in b.nodes() if n is not None]
            if [n.path() for n in cur] != [n.path() for n in want]:
                _set_members(b, want)
                if name not in changed:
                    changed.append(name)
        except Exception:
            pass

    # 盒子没了 → 对应 bundle 一起删
    gone = [n for sid, n in list(sid2name.items()) if sid not in live]
    for name in gone:
        try:
            _remove_bundle(name)
            changed.append("-" + name)
        except Exception:
            pass
        for sid, n in list(sid2name.items()):
            if n == name:
                sid2name.pop(sid, None)

    # 成员有变化 → 把渲染器里引用这些 bundle 的参数重写成最新路径
    if changed:
        try:
            sync_refs()
        except Exception:
            pass

    return changed


def _remove_bundle(name):
    """删 bundle。注意 hou 模块**没有** removeNodeBundle()，要用 bundle.destroy()。"""
    b = hou.nodeBundle(name)
    if b is None:
        return False
    try:
        b.destroy()
        return True
    except Exception:
        return False


def _set_members(bundle, nodes):
    try:
        bundle.clear()
    except Exception:
        pass
    for n in nodes:
        try:
            bundle.addNode(n)
        except Exception:
            pass


# ------------------------------------------------- 渲染参数里的 @bundle 引用
def _iter_ref_nodes(max_nodes=600):
    out = []
    for root in REF_SCOPES:
        r = hou.node(root)
        if r is None:
            continue
        stack = [r]
        while stack and len(out) < max_nodes:
            n = stack.pop(0)
            out.append(n)
            try:
                stack = list(n.children()) + stack
            except Exception:
                pass
    return out


def _refs_of(node):
    """读节点上记的引用表：{parm名: {"o": 原始写法(含@), "l": 上次展开结果}}"""
    import json

    try:
        raw = node.userData(REF_KEY)
        if not raw:
            return {}
        d = json.loads(raw)
    except Exception:
        return {}
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out[k] = {"o": v.get("o", ""), "l": v.get("l", "")}
        else:                       # 兼容旧格式（裸字符串）
            out[k] = {"o": str(v), "l": ""}
    return out


def _save_refs(node, refs):
    import json

    try:
        node.setUserData(REF_KEY, json.dumps(refs) if refs else "")
    except Exception:
        pass


def _expand_tokens(val):
    """'@render /obj/extra' -> (['/obj/box','/obj/sphere1','/obj/extra'], hit)"""
    toks = (val or "").split()
    out = []
    hit = False
    for t in toks:
        if t.startswith("@") and len(t) > 1:
            b = hou.nodeBundle(t[1:])
            if b is not None:
                out += [n.path() for n in b.nodes() if n is not None]
                hit = True
                continue
        out.append(t)
    return out, hit


def _is_string_parm(p):
    try:
        return p.parmTemplate().type() == hou.parmTemplateType.String
    except Exception:
        return False


def expand_refs():
    """扫描 REF_SCOPES，把参数里的 '@bundle' 全改成显式路径，并记下原始写法。

    之后 sync() 每次发现盒子成员变了，就会按原始写法重新展开写回参数，
    于是渲染器看到的永远是最新成员（绕开 Houdini 的 bundle 展开缓存）。
    """
    if not EXPAND_REFS:
        return []
    log = []
    for node in _iter_ref_nodes():
        refs = _refs_of(node)
        dirty = False
        for p in node.parms():
            try:
                if not _is_string_parm(p):
                    continue
                v = p.rawValue()
                if not isinstance(v, str) or "@" not in v:
                    continue
                toks, hit = _expand_tokens(v)
                if not hit:
                    continue
                refs[p.name()] = {"o": v, "l": " ".join(toks)}
                p.set(" ".join(toks))
                dirty = True
                log.append("%s.%s  @ -> %d 个路径" % (node.path(), p.name(), len(toks)))
            except Exception:
                pass
        if dirty:
            _save_refs(node, refs)
    return log


def sync_refs():
    """按记录的原始写法，把参数重写成 bundle 当前成员（显式路径）。"""
    if not EXPAND_REFS:
        return []
    log = []
    for node in _iter_ref_nodes():
        refs = _refs_of(node)
        if not refs:
            continue
        dirty = False
        for pname, rec in list(refs.items()):
            p = node.parm(pname)
            if p is None:
                refs.pop(pname, None)
                dirty = True
                continue
            try:
                toks, _hit = _expand_tokens(rec.get("o", ""))
                # 用户在参数里手写的额外路径要留着：当前值里不属于上次展开结果的 token
                cur = (p.rawValue() or "").split()
                last = set((rec.get("l") or "").split())
                extra = [t for t in cur if t not in last]
                merged = list(dict.fromkeys(toks + extra))
                newstr = " ".join(merged)
                if newstr != (p.rawValue() or ""):
                    p.set(newstr)
                    log.append("%s.%s -> %s" % (node.path(), pname, newstr))
                rec["l"] = " ".join(toks)
                dirty = True
            except Exception:
                pass
        if dirty:
            _save_refs(node, refs)
    return log


def _rename_refs(old, new):
    """盒子改标题 → bundle 改名 → 引用里的 @old 也要跟着改成 @new，
    否则 sync_refs() 会照着旧的 @old 去展开（bundle 已删）→ 参数被清空。"""
    for node in _iter_ref_nodes():
        refs = _refs_of(node)
        dirty = False
        for _pname, rec in refs.items():
            toks = (rec.get("o") or "").split()
            newtoks = []
            hit = False
            for t in toks:
                if t == "@" + old:
                    newtoks.append("@" + new)
                    hit = True
                else:
                    newtoks.append(t)
            if hit:
                rec["o"] = " ".join(newtoks)
                dirty = True
        if dirty:
            _save_refs(node, refs)


def restore_refs():
    """把参数还原成原始的 '@bundle' 写法（想退回 Houdini 原生行为时用）。"""
    log = []
    for node in _iter_ref_nodes():
        refs = _refs_of(node)
        if not refs:
            continue
        for pname, rec in list(refs.items()):
            p = node.parm(pname)
            if p is None:
                continue
            try:
                p.set(rec.get("o", ""))
                log.append("%s.%s -> %s" % (node.path(), pname, rec.get("o", "")))
            except Exception:
                pass
        try:
            node.setUserData(REF_KEY, "")
        except Exception:
            pass
    return log


def report_refs():
    lines = []
    for node in _iter_ref_nodes():
        refs = _refs_of(node)
        for pname, rec in refs.items():
            lines.append("%s.%s  原写法=%s  当前=%s" % (
                node.path(), pname, rec.get("o", ""),
                (node.parm(pname).rawValue() if node.parm(pname) else "<已删除>")))
    return "\n".join(lines) or "(没有被接管的 @bundle 引用)"


# ---------------------------------------------------------------- 建盒子
def current_network():
    """当前网络编辑器所在的层级；拿不到就退回选中节点的父级 / /obj。"""
    try:
        tab = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if tab is not None:
            pwd = tab.pwd()
            if pwd is not None:
                return pwd
    except Exception:
        pass
    sel = hou.selectedNodes()
    if sel:
        return sel[0].parent()
    return hou.node("/obj")


def create_box(parent=None, name=None, wrap_selection=True):
    """在当前层级建一个盒子（默认 box_N），建完立刻同步出同名 bundle。"""
    parent = parent or current_network()
    if parent is None:
        return None

    name = name or _unique_name("box", parent)
    box = parent.createNetworkBox()
    try:
        box.setName(name)
    except Exception:
        pass
    if SET_COMMENT:
        try:
            box.setComment(name)      # 让顶栏显示的字 == 集合名
        except Exception:
            pass

    # 有选中节点就把它包起来
    placed = False
    if wrap_selection:
        sel = []
        try:
            tab = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            sel = [n for n in (tab.selection() if tab else []) if n.parent() == parent]
        except Exception:
            sel = [n for n in hou.selectedNodes() if n.parent() == parent]
        if sel:
            for n in sel:
                try:
                    box.addItem(n)
                except Exception:
                    pass
            try:
                box.fitAroundContents()
            except Exception:
                pass
            placed = True

    if not placed:
        try:
            tab = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            vb = tab.visibleBounds()
            cx = (vb.xmin() + vb.xmax()) * 0.5
            cy = (vb.ymin() + vb.ymax()) * 0.5
            box.setPosition(hou.Vector2(cx - 2, cy - 1))
            box.setSize(hou.Vector2(4, 2))
        except Exception:
            pass

    install()
    sync()
    return box


def create_box_msg():
    """给工具架按钮用：建盒子 + 在状态栏提示（不弹窗）。"""
    box = create_box()
    if box is None:
        hou.ui.setStatusMessage("netbox_bundles: 拿不到当前层级",
                                severity=hou.severityType.Warning)
        return None
    nm = _display_name(box)
    try:
        where = box.parent().path() + "/" + box.name()
    except Exception:
        where = "?"
    hou.ui.setStatusMessage(
        "盒子集合: %s -> @%s   (渲染器 Objects 里填 @%s)" % (where, nm, nm))
    return box


# ---------------------------------------------------------------- 事件 + 轮询
def iter_rop_nodes(max_nodes=4000):
    """整场景递归找渲染节点（ROP）——**不限 /out**。

    渲染器可以建在任何层级：/out、/obj、/obj/geo1 下都可能有一个，
    所以从根 "/" 一路递归下去按类型找，而不是只看 /out 的子节点。
    判据：节点类型属于 Driver 分类，或者有 prerender/postrender 参数。
    """
    out = []
    root = hou.node("/")
    if root is None:
        return out
    stack = list(root.children())
    seen = set()
    while stack:
        n = stack.pop(0)
        if n is None:
            continue
        try:
            path = n.path()
        except Exception:
            continue
        if path in seen:
            continue
        seen.add(path)

        # ⚠ 判据只看类型分类是不是 Driver（渲染器），
        #   **不能**用"有没有 prerender/postrender 参数"来判断：
        #   2026-10-17 踩过——SOP 的 filecache 也有这两个参数（给缓存用的脚本），
        #   按参数判会把缓存节点当成渲染器，把人家的脚本覆盖掉。
        is_rop = False
        try:
            is_rop = n.type().category().name() == "Driver"
        except Exception:
            pass
        if is_rop:
            if len(out) >= max_nodes:
                continue
            out.append(n)

        # 不钻进锁定的数字资产内部：里面通常没有要接管的渲染器，
        # 但可能成千上万个节点，会把扫描拖垮。
        try:
            if n.isLocked():
                continue
        except Exception:
            pass
        try:
            stack = list(n.children()) + stack
        except Exception:
            pass
    return out


def _on_event(node, event_type, **kwargs):
    # 只同步盒子 -> 集合。**不会**自动去动渲染器：装脚本是显式点按钮才做的事。
    try:
        sync()
    except Exception:
        pass
    # NetworkBoxDeleted 是"删除前"触发，推迟一拍再同步才能真正清掉 bundle
    try:
        import hdefereval

        hdefereval.executeDeferred(sync)
    except Exception:
        pass


def _on_hip_event(event_type, **kwargs):
    try:
        if event_type == hou.hipFileEventType.AfterLoad:
            hou.session.netbox_bundles_installed = False
            _state()["sid2name"].clear()
            install()
        else:
            # 保存等时机顺手同步一次：改盒子标题没有专用事件，靠这个兜底
            sync()
    except Exception:
        pass


def _signature():
    parts = []
    for box in _all_boxes():
        try:
            parts.append((box.sessionId(), _display_name(box),
                          tuple(n.path() for n in box.nodes(recurse=False))))
        except Exception:
            pass
    return tuple(sorted(parts))


def _tick():
    st = _state()
    try:
        s = _signature()
        if s != st["sig"]:
            st["sig"] = s
            sync()
    except Exception:
        pass


def _uninstall():
    """卸掉上一次实例挂的钩子/定时器，避免新旧两份代码同时生效互相打架。"""
    for node, events, cb in getattr(hou.session, "netbox_bundles_hooks", []):
        try:
            node.removeEventCallback(events, cb)
        except Exception:
            pass
    hou.session.netbox_bundles_hooks = []

    hcb = getattr(hou.session, "netbox_bundles_hiphook", None)
    if hcb is not None:
        try:
            hou.hipFile.removeEventCallback(hcb)
        except Exception:
            pass
    hou.session.netbox_bundles_hiphook = None

    t = getattr(hou.session, "netbox_bundles_timer", None)
    if t is not None:
        try:
            t.stop()
        except Exception:
            pass
    hou.session.netbox_bundles_timer = None
    hou.session.netbox_bundles_installed = False


def _install_poll():
    """兜底轮询。**只用 Houdini 自己的事件循环回调，绝不再用 QTimer**
    —— PyQt/PySide 的 QTimer + Python 回调在模块被重载后会回调到已回收对象，
    直接把 Houdini 干崩（2026-10-17 连崩两次，堆栈都是 sendTimerEvent）。"""
    if not POLL_MS:
        return False
    try:
        import time

        def _throttled():
            st = _state()
            now = time.time()
            last = st.get("last_poll", 0.0)
            if now - last < POLL_MS / 1000.0:
                return
            st["last_poll"] = now
            _tick()

        hou.ui.addEventLoopCallback(_throttled)
        hou.session.netbox_bundles_poll = _throttled
        return True
    except Exception:
        return False


def install(poll=True):
    """挂事件回调 + 兜底轮询 + 立即同步。幂等；模块重载后会重新挂。"""
    _uninstall()

    hooks = []
    for p in _scopes():
        try:
            p.addEventCallback(_EVENTS, _on_event)
            hooks.append((p, _EVENTS, _on_event))
        except Exception:
            pass
    hou.session.netbox_bundles_hooks = hooks
    try:
        hou.hipFile.addEventCallback(_on_hip_event)
        hou.session.netbox_bundles_hiphook = _on_hip_event
    except Exception:
        pass

    if poll:
        _install_poll()

    hou.session.netbox_bundles_installed = True
    sync()
    try:
        expand_refs()          # 开工时把已有的 @bundle 引用接管成显式路径
    except Exception:
        pass
    return "installed, boxes=%d, bundles=%s" % (
        len(_all_boxes()), sorted(set(_state()["sid2name"].values())))


def report():
    lines = []
    for box in _all_boxes():
        try:
            lines.append("%s%s  ->  @%s   %s" % (
                box.parent().path() + "/", box.name(), _display_name(box),
                [n.name() for n in box.nodes(recurse=False)]))
        except Exception:
            pass
    return "\n".join(lines) or "(no network box)"


def report_msg():
    hou.ui.setStatusMessage("盒子集合: " + str(sorted(set(_state()["sid2name"].values()))))
    print(report())
    print("--- 渲染器引用 ---")
    print(report_refs())


# ================================================================ v2：随工程
# 思路：把「同步盒子→bundle」+「展开 @bundle」两步做成一段自包含脚本，
# 塞进 ROP 的 Pre-Render Script；渲完由 Post-Render Script 还原成 @bundle。
# 全部存在 .hip 里（参数 + userData），不依赖 N 盘任何文件，
# 所以工程发给谁、谁打开就生效 —— 这才是真正"跟着工程走"。
_ROP_PRE = r'''
# ---- netbox_bundles: 渲染前把 @bundle 临时展开成路径（渲完自动还原）----
# 这段脚本写在 ROP 的 Pre-Render Script 参数里，跟着 .hip 走，不需要任何外部文件。
# 参数里你看到的仍然是 @render：能改名、能删掉断开、能换成 @light。
import hou, json

NB_SCOPES = ["/obj", "/out", "/stage", "/mat", "/shop", "/img", "/ch", "/tasks", "/vex"]
NB_BAK = "netbox_bundles_bak"
NB_SKIP_PARMS = ("prerender", "postrender", "preframe", "postframe")

def _nb_safe(s):
    s = (s or "").strip()
    out = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in s)
    if out and out[0].isdigit():
        out = "_" + out
    return out or "box"

def nb_sync():
    """网络盒 -> 同名 bundle（自包含版，不依赖任何外部模块）"""
    seen = []
    for root in NB_SCOPES:
        p = hou.node(root)
        if p is None:
            continue
        try:
            boxes = list(p.networkBoxes())
        except Exception:
            continue
        for box in boxes:
            try:
                name = _nb_safe((box.comment() or "").strip() or box.name())
                b = hou.nodeBundle(name)
                if b is None:
                    b = hou.addNodeBundle(name)
                want = [n.path() for n in box.nodes(recurse=False) if n is not None]
                cur = [n.path() for n in b.nodes() if n is not None]
                if want != cur:
                    try:
                        b.clear()
                    except Exception:
                        pass
                    for path in want:
                        np_ = hou.node(path)
                        if np_ is not None:
                            try:
                                b.addNode(np_)
                            except Exception:
                                pass
                seen.append(name)
            except Exception:
                pass
    return seen

def _nb_box_paths(name):
    """按盒子标题找成员 —— 盒子才是"真理来源"，bundle 只是它的影子。"""
    for root in NB_SCOPES:
        p = hou.node(root)
        if p is None:
            continue
        try:
            boxes = list(p.networkBoxes())
        except Exception:
            continue
        for box in boxes:
            try:
                if _nb_safe((box.comment() or "").strip() or box.name()) == name:
                    return [n.path() for n in box.nodes(recurse=False) if n is not None]
            except Exception:
                pass
    return None

def _nb_names(tok):
    """'@render' 和 '@//render/' 都要认。

    Houdini 从 Bundle 选择器里手填 / 回写时经常变成 '@//名字/' 这种带斜杠的形式，
    原样拿去查是查不到的（表现就是"脚本没起作用、渲的还是旧的"）。
    这里给出几个候选名依次试：原样、去掉首尾斜杠、再清洗成合法标识。
    """
    raw = tok[1:]
    cands = []
    for s in (raw, raw.strip("/"), _nb_safe(raw.strip("/"))):
        if s and s not in cands:
            cands.append(s)
    return cands

def _nb_lookup(tok):
    """盒子优先（真身）→ bundle 兜底（影子 / 手工清单）。"""
    names = _nb_names(tok)
    for name in names:
        paths = _nb_box_paths(name)
        if paths is not None:
            return paths
    for name in names:
        b = hou.nodeBundle(name)
        if b is not None:
            return [n.path() for n in b.nodes() if n is not None]
    return None

def _nb_expand(val):
    out = []
    for t in (val or "").split():
        if t.startswith("@") and len(t) > 1:
            paths = _nb_lookup(t)
            if paths is not None:
                out += paths
                continue
        out.append(t)
    return " ".join(out)

def _nb_restore(n):
    try:
        bak = json.loads(n.userData(NB_BAK) or "{}")
    except Exception:
        bak = {}
    for k, v in (bak or {}).items():
        p = n.parm(k)
        if p is not None:
            try:
                p.set(v)
            except Exception:
                pass
    try:
        n.setUserData(NB_BAK, "")
    except Exception:
        pass

def nb_prerender():
    n = hou.pwd()
    _nb_restore(n)          # 上次渲染被中断的话，先把 @ 还原回来
    nb_sync()               # 盒子内容 -> bundle（没装工具架也照样同步）
    bak = {}
    done = []
    for p in n.parms():
        try:
            if p.parmTemplate().type() != hou.parmTemplateType.String:
                continue
            # ⚠ 绝不能动脚本参数自己：prerender 的代码注释里就写着 @bundle / @render，
            #   不跳过的话脚本会把自己的代码改写掉（2026-10-17 抓到过）。
            if p.name() in NB_SKIP_PARMS:
                continue
            v = p.rawValue()
            if not isinstance(v, str) or "\n" in v:
                continue      # 多行 = 脚本源码，不是节点列表
            if not isinstance(v, str) or "@" not in v:
                continue
            nv = _nb_expand(v)
            if nv != v:
                bak[p.name()] = v
                p.set(nv)
                done.append("%s: %s -> [%s]" % (p.name(), v, nv))
        except Exception:
            pass
    if bak:
        n.setUserData(NB_BAK, json.dumps(bak))
    # 状态栏回显：脚本有没有跑、展开了什么，一眼能看到（排查用，不影响渲染）
    try:
        hou.ui.setStatusMessage(
            "盒子集合: " + (" | ".join(done) if done else "没有可展开的 @引用"),
            severity=hou.severityType.ImportantMessage)
    except Exception:
        pass
    # htoa 下 Post-Render Script 实测不执行（渲完参数会停在展开后的路径），
    # 所以自己排一个"渲完再还原"。判定"还在不在渲"不能用 n.isRendering()
    # （20.5 的 RopNode 没这个 API，会 AttributeError），改成看 Pre-Frame 脚本
    # 每帧刷新的时间戳：10 秒内还有新帧就是还没渲完，继续排队。
    try:
        import hdefereval

        def _nb_deferred():
            try:
                import time as _t
                tick = float(n.userData("netbox_bundles_tick") or 0)
                if tick and (_t.time() - tick) < 10.0:
                    hdefereval.executeDeferred(_nb_deferred)
                    return
            except Exception:
                pass
            _nb_restore(n)

        hdefereval.executeDeferred(_nb_deferred)
    except Exception:
        pass

nb_prerender()
'''

# 每帧刷新一个时间戳，给上面的"渲完再还原"判断用
_ROP_FRAME = r'''
import time
hou.pwd().setUserData("netbox_bundles_tick", "%.3f" % time.time())
'''

_ROP_POST = r'''
# ---- netbox_bundles: 渲完把参数还原回 @bundle ----
import hou, json
NB_BAK = "netbox_bundles_bak"
n = hou.pwd()
try:
    bak = json.loads(n.userData(NB_BAK) or "{}")
except Exception:
    bak = {}
for k, v in (bak or {}).items():
    p = n.parm(k)
    if p is not None:
        try:
            p.set(v)
        except Exception:
            pass
try:
    n.setUserData(NB_BAK, "")
except Exception:
    pass
'''


def install_rop_hooks(node=None, force=False):
    """给渲染节点装上「渲染前展开 / 渲完还原」脚本。

    node=None 时扫描**整场景所有层级**的渲染节点（/out、/obj、/obj/geo1… 都算），
    因为渲染器不一定只建在 /out 下。
    脚本写在 ROP 自己的 prerender / postrender 参数里（Python），随 .hip 保存，
    所以工程拷给别人、对方没装任何工具也能正常按盒子渲。

    force=False 时不动“别人自己写的” Pre-Render 脚本：只有空着的、或者本来就
    是本模块装的（userData 有标记）才会写/更新。
    """
    nodes = [node] if node is not None else iter_rop_nodes()
    log = []
    for n in nodes:
        if n is None:
            continue
        try:
            # 只认渲染节点（Driver）。SOP 的 filecache 也有 prerender 参数，
            # 那是给缓存用的，绝不能往里写渲染脚本。
            if n.type().category().name() != "Driver":
                continue
        except Exception:
            continue
        p_pre, p_post = n.parm("prerender"), n.parm("postrender")
        if p_pre is None or p_post is None:
            log.append("跳过(没有 Pre/Post-Render 参数): " + n.path())
            continue
        try:
            mine = bool(n.userData(ROP_FLAG))
            cur = p_pre.rawValue() or ""
            if mine and not force and cur == _ROP_PRE:
                log.append("已是最新: " + n.path())
                continue
            # 没标记 + 已经写了别的脚本 → 那是用户自己的，别覆盖
            if (not mine) and cur.strip() and "netbox_bundles" not in cur:
                log.append("跳过(已有自己的 Pre-Render 脚本): " + n.path())
                continue
            # t* = 打开脚本开关（都是整型）
            for pn in ("tprerender", "tpostrender", "tpreframe"):
                p = n.parm(pn)
                if p is not None:
                    try:
                        p.set(1)
                    except Exception:
                        pass
            # l* = 脚本语言：**Arnold 是整型(1=Python)，Mantra/ifd 是字符串("python")**
            #   2026-10-17 踩过：统一 set(1) 会让 mantra 报 "Cannot set a string parm to a int"，
            #   结果整个脚本都装不上。所以两种类型都试。
            for pn in ("lprerender", "lpostrender", "lpreframe"):
                p = n.parm(pn)
                if p is None:
                    continue
                try:
                    p.set(1)
                except Exception:
                    try:
                        p.set("python")
                    except Exception:
                        pass
            # 只有 ROP 原本没写过 Pre-Frame 脚本才写，别覆盖用户自己的
            if n.parm("preframe") is not None and not (n.parm("preframe").rawValue() or "").strip():
                n.parm("preframe").set(_ROP_FRAME)
            p_pre.set(_ROP_PRE)
            p_post.set(_ROP_POST)
            n.setUserData(ROP_FLAG, "1")
            log.append("已装: " + n.path())
        except Exception as exc:
            log.append("失败: %s (%s)" % (n.path(), exc))
    return log


def uninstall_rop_hooks(node=None):
    """拆掉渲染前脚本，并还原参数的 @ 写法。"""
    nodes = [node] if node is not None else iter_rop_nodes()
    log = []
    for n in nodes:
        if n is None or not n.userData(ROP_FLAG):
            continue
        try:
            for pn in ("prerender", "postrender"):
                p = n.parm(pn)
                if p is not None:
                    p.set("")
            n.setUserData(ROP_FLAG, "")
            n.setUserData(BAK_KEY, "")
            log.append("已拆: " + n.path())
        except Exception as exc:
            log.append("失败: %s (%s)" % (n.path(), exc))
    return log


def scan_refs():
    """当前场景里所有还写着 @xxx 的字符串参数（不依赖 userData）。"""
    out = []
    for node in iter_rop_nodes():
        for p in node.parms():
            try:
                if not _is_string_parm(p):
                    continue
                v = p.rawValue()
                if isinstance(v, str) and "@" in v:
                    b = hou.nodeBundle(v.strip().lstrip("@")) if v.strip().startswith("@") else None
                    mem = len(b.nodes()) if b is not None else -1
                    out.append("%s.%s = %s   (bundle 成员 %s)" % (
                        node.path(), p.name(), v,
                        ("%d 个" % mem) if mem >= 0 else "无此集合"))
            except Exception:
                pass
    return out


def install_rop_hooks_msg(node=None):
    log = install_rop_hooks(node)
    hou.ui.setStatusMessage("渲染前脚本: " + "; ".join(log) or "没找到渲染节点")
    print("\n".join(log))
    print("--- 当前参数里的 @引用 ---")
    print("\n".join(scan_refs()) or "(没有)")
    return log


def sync_all_msg():
    """「同步/查看盒子集合」按钮干的事，一步到位：

    1) 重新加载并挂上常驻钩子（盒子一变就同步；改了代码也是靠这一步生效）
    2) 同步所有盒子 -> 集合
    3) 给**全场景所有层级**的渲染节点装上 / 更新成最新版渲染脚本
    4) 打印对照表：盒子 → 集合 → 成员；渲染器 → 参数当前值
    """
    try:
        install()
    except Exception as exc:
        print("install 失败: %s" % exc)
    try:
        changed = sync()
    except Exception as exc:
        changed = []
        print("sync 失败: %s" % exc)

    rops = iter_rop_nodes()
    log = install_rop_hooks()

    lines = []
    lines.append("=========== 盒子 -> 集合 ===========")
    lines.append(report() or "(没有网络盒)")
    lines.append("")
    lines.append("本轮变化: " + (", ".join(changed) if changed else "无"))
    lines.append("")
    lines.append("=========== 渲染节点 (%d 个) ===========" % len(rops))
    for n in rops:
        try:
            tag = "已装脚本" if n.userData(ROP_FLAG) else "未装"
            lines.append("  %-24s [%s]  %s" % (n.path(), tag, n.type().name()))
        except Exception:
            pass
    lines.append("")
    lines.append("----------- 装脚本结果 -----------")
    lines.append("\n".join(log) or "(没有可装的渲染节点)")
    lines.append("")
    lines.append("----------- 参数里的 @引用 -----------")
    lines.append("\n".join(scan_refs()) or "(没有)")
    txt = "\n".join(lines)

    print(txt)
    try:
        hou.ui.setStatusMessage(
            "盒子集合: %s ｜ 渲染节点 %d 个, 已装 %d" % (
                ", ".join(sorted(set(_state()["sid2name"].values()))) or "无",
                len(rops),
                sum(1 for n in rops if n.userData(ROP_FLAG))),
            severity=hou.severityType.ImportantMessage)
    except Exception:
        pass
    return txt


def restore_msg():
    log = restore_refs()
    hou.ui.setStatusMessage("已还原 %d 处 @引用" % len(log))
    print("\n".join(log) or "(没有需要还原的)")
    print("--- 当前参数 ---")
    print("\n".join(scan_refs()) or "(没有 @ 引用)")
    return log
