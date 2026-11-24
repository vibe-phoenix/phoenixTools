# ======================= install_phoenix_tools.py =======================
# Phoenix Tools — one installer for every Phoenix script.
#
# Drag this file into the Maya viewport (or run it in the Script Editor).
# A retro panel opens with a card per tool: click cards to select /
# deselect, pick the shelf, press INSTALL.
#
# Keep the tool files next to this installer:
#   phoenix-set-tools.py        -> Set Tools      (scripts/phoenix_set_tools.py)
#   phoenix_units.py            -> Units          (scripts/phoenix_units.py)
#   phoenix-uv-copy-paste.py    -> UV Transfer    (smart loader, auto-updates
#                                                  from GitHub; local copy is
#                                                  used as the first cache)
#   autosave_script.py          -> Autosave       (scripts/ + userSetup.py)
#
# Re-running updates scripts and shelf buttons in place (no duplicates).
# Replaces install_set_tools.py and install_units.py.
# PySide2 / PySide6 compatible.
# =========================================================================

import os
import sys
import json
import zlib
import base64
import shutil
import importlib
import traceback

import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as omui

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import wrapInstance
except Exception:
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import wrapInstance


_HERE = os.path.dirname(os.path.abspath(globals().get("__file__") or os.getcwd()))
_UI_OBJECT_NAME = "phxToolsInstallerWin"
_WIDTH = 440
_COLS = 6          # max tiles per row
_TILE_W = 112
_TILE_GAP = 8

# ── theme (installer uses the Olive LCD look) ────────────────────────────
_THEMES_BASE = [
    {"n": "Olive LCD",         "bg": "#9ba383", "fld": "#9ba383", "ink": "#1f2613", "mid": "#5d6648", "sh": "#7c8466", "tsh": "#7c8466", "acc": "#1f2613", "acci": "#9ba383", "nb": "#2b2f26", "nt": "#e8ecdf", "nd": "#6f7563"},
    {"n": "Cream navy orange", "bg": "#e8d8c9", "fld": "#e8d8c9", "ink": "#1b1b1b", "mid": "#4b607f", "sh": "#4b607f", "tsh": "#f3701e", "acc": "#f3701e", "acci": "#1b1b1b", "nb": "#2a2a2a", "nt": "#e8d8c9", "nd": "#6e6862"},
    {"n": "Teal red sand",     "bg": "#3e6868", "fld": "#c1ab85", "ink": "#161616", "mid": "#203a3a", "sh": "#161616", "tsh": "#c94e44", "acc": "#c94e44", "acci": "#161616", "nb": "#1e2424", "nt": "#c1ab85", "nd": "#5e5644"},
    {"n": "Red and gold",      "bg": "#121312", "fld": "#121312", "ink": "#a67d43", "mid": "#5e4626", "sh": "#ad0013", "tsh": "#ad0013", "acc": "#ad0013", "acci": "#e3c08a", "nb": "#2a2622", "nt": "#e3c08a", "nd": "#6a5a44"},
    {"n": "Red butter black",  "bg": "#c0301e", "fld": "#f6da9d", "ink": "#000000", "mid": "#6a160c", "sh": "#000000", "tsh": "#f6da9d", "acc": "#000000", "acci": "#f6da9d", "nb": "#1c1c1c", "nt": "#f6da9d", "nd": "#6e6450"},
    {"n": "Sky mist slate",    "bg": "#7ba7af", "fld": "#e2e4d2", "ink": "#1b2321", "mid": "#455957", "sh": "#455957", "tsh": "#6e8584", "acc": "#455957", "acci": "#e2e4d2", "nb": "#26302f", "nt": "#e2e4d2", "nd": "#66706c"},
]
_UI_THEME = _THEMES_BASE[0]


def _c(key):
    return QtGui.QColor(_UI_THEME[key])


# =========================================================================
# ICONS — same pixel art + file naming each tool uses for itself
# =========================================================================
_MAP_SET_TOOLS = [
    "            ### ",
    "        ####ll# ",
    "    ####ll##ll##",
    "####ll##ll##ll# ",
    "#l##ll##ll##### ",
    "##ll##ll###     ",
    "####            ",
    "################",
    "#ll##ll##ll##ll#",
    "#l##ll##ll##ll##",
    "################",
    "#bbbbbbbbbbbbbb#",
    "#bssssbbbbaaabb#",
    "#bbbbbbbbbbbbbb#",
    "#bsssbbbssssbbb#",
    "################",
]
_MAP_UNITS = [
    "", "", "", "",
    " ############## ",
    " #l#l#l#l#l#l## ",
    " #lll#lll#llll# ",
    " #lllll#llllll# ",
    " #llllllllllll# ",
    " ############## ",
    "",
    " #            # ",
    " #aaaaaaaaaaaa# ",
    " #            # ",
    "", "",
]
_MAP_UV = [
    "                ",
    "     ######     ",
    "  ####llll####  ",
    "  #mm######mm#  ",
    "  #mmmmmmmmmm#  ",
    "  #m########m#  ",
    "  #m#llaall#m#  ",
    "  #m#llaall#m#  ",
    "  #m#aallaa#m#  ",
    "  #m#aallaa#m#  ",
    "  #m#llaall#m#  ",
    "  #m#llaall#m#  ",
    "  #m#aallaa#m#  ",
    "  #m########m#  ",
    "  #mmmmmmmmmm#  ",
    "  ############  ",
]
_MAP_AUTOSAVE = [
    "##############  ",
    "#b#ssssss#bb##  ",
    "#b#ss##ss#bbb## ",
    "#b#ss##ss#bbbb##",
    "#b#ssssss#bbbbb#",
    "#bb######bbbbbb#",
    "#bbbbbbbbbbbbbb#",
    "#bbbbbbbbbbbbbb#",
    "#b############b#",
    "#b#llllllllll#b#",
    "#b#llaallllll#b#",
    "#b#lallalllll#b#",
    "#b#laaaalllll#b#",
    "#b#lallalllll#b#",
    "#b#lallalllll#b#",
    "################",
]
_AUTOSAVE_CLOCK = ["  ###  ", " #nnn# ", "#nn#nn#", "#nn##n#", "#nnnnn#", " #nnn# ", "  ###  "]


def _pixels(map_rows, theme, fill_bg, overlay=None):
    col = {"#": theme["ink"], "b": theme["bg"], "s": theme["mid"], "m": theme["mid"],
           "l": theme["nt"], "n": theme["nt"], "a": theme["acc"]}
    empty = theme["bg"] if fill_bg else None
    g = [[col.get(ch, empty) for ch in row.ljust(16)[:16]] for row in map_rows]
    if overlay:
        rows, ox, oy = overlay
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch != " " and 0 <= y + oy < 16 and 0 <= x + ox < 16:
                    g[y + oy][x + ox] = col[ch]
    return g


def _image(pixels, scale=2):
    img = QtGui.QImage(16 * scale, 16 * scale, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    for y, row in enumerate(pixels):
        for x, c in enumerate(row):
            if c:
                p.fillRect(x * scale, y * scale, scale, scale, QtGui.QColor(c))
    p.end()
    return img


def _tool_theme(opt_theme, opt_icon):
    """(icon index, theme dict) the tool itself would use for its shelf icon."""
    idx = 0
    themes = [dict(t) for t in _THEMES_BASE]
    try:
        if cmds.optionVar(exists=opt_icon):
            idx = max(0, min(5, int(cmds.optionVar(q=opt_icon))))
    except Exception:
        pass
    try:
        if cmds.optionVar(exists=opt_theme):
            data = json.loads(cmds.optionVar(q=opt_theme) or "{}")
            for i, t in enumerate(data.get("themes", [])[:6]):
                for k, v in t.items():
                    if k in themes[i] and isinstance(v, str) and QtGui.QColor(v).isValid():
                        themes[i][k] = v
    except Exception:
        pass
    return idx, themes[idx]


def _write_icon_file(prefix, idx, theme, sig_keys, pixels, map_rows):
    # the drawing is part of the name, so a changed icon never reuses a cached PNG
    sig = "".join(theme[k] for k in sig_keys).replace("#", "") + "|".join(map_rows)
    tag = format(zlib.crc32(sig.encode("ascii")) & 0xFFFFFF, "06x")
    folder = cmds.internalVar(userBitmapsDir=True)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "{}_{}_{}.png".format(prefix, idx, tag)).replace("\\", "/")
    if not os.path.exists(path):
        _image(pixels, 2).save(path, "PNG")
    for f in os.listdir(folder):
        if f.startswith(prefix + "_") and f.endswith(".png"):
            full = os.path.join(folder, f).replace("\\", "/")
            if full != path:
                try: os.remove(full)
                except Exception: pass
    return path


# =========================================================================
# SHELF HELPERS
# =========================================================================
def _shelf_top():
    try:
        return mel.eval("$_phx_inst_tmp = $gShelfTopLevel")
    except Exception:
        return None


def _shelf_names():
    top = _shelf_top()
    if not top or not cmds.tabLayout(top, exists=True):
        return [], None
    names = cmds.tabLayout(top, q=True, childArray=True) or []
    cur = cmds.tabLayout(top, q=True, selectTab=True)
    return names, cur


def _find_shelf_buttons(doc_tag, match=()):
    top = _shelf_top()
    out = []
    if not top or not cmds.tabLayout(top, exists=True):
        return out
    for sh in cmds.tabLayout(top, q=True, childArray=True) or []:
        try:
            full = cmds.shelfLayout(sh, q=True, fullPathName=True)
        except Exception:
            continue
        for b in cmds.shelfLayout(full, q=True, childArray=True) or []:
            path = full + "|" + b
            try:
                if cmds.objectTypeUI(path) != "shelfButton":
                    continue
                tag = cmds.shelfButton(path, q=True, docTag=True) or ""
                cmd = cmds.shelfButton(path, q=True, command=True) or ""
                if tag == doc_tag or any(k in cmd for k in match):
                    out.append(path)
            except Exception:
                pass
    return out


def _put_shelf_button(shelf, doc_tag, label, annotation, icon, command,
                      dbl_command=None, match=()):
    """Make sure exactly one button for the tool sits on `shelf`.
    A button already on that shelf is updated in place; copies on other shelves
    are removed (so the button always ends up where the user picked)."""
    kw = dict(image=icon, image1=icon, annotation=annotation, sourceType="python",
              command=command, docTag=doc_tag)
    if dbl_command:
        kw["doubleClickCommand"] = dbl_command
    target = cmds.shelfLayout(shelf, q=True, fullPathName=True)
    existing = _find_shelf_buttons(doc_tag, match)
    on_target = [b for b in existing if b.rsplit("|", 1)[0] == target]
    for b in existing:
        if b not in on_target:
            try:
                cmds.deleteUI(b)
                print("[PhoenixInstaller] removed old {} button from {}".format(label, b.rsplit("|", 2)[-2]))
            except Exception:
                pass
    if on_target:
        for b in on_target[1:]:
            try: cmds.deleteUI(b)
            except Exception: pass
        cmds.shelfButton(on_target[0], e=True, **kw)
        result = "updated"
    else:
        cmds.shelfButton(parent=target, label=label, imageOverlayLabel="", **kw)
        result = "added"
    # verify
    if not [b for b in _find_shelf_buttons(doc_tag, match) if b.rsplit("|", 1)[0] == target]:
        raise RuntimeError("{} button was not created on shelf '{}'".format(label, shelf))
    print("[PhoenixInstaller] {} button {} on shelf '{}'".format(label, result, shelf))
    return result


def _save_shelves():
    try:
        mel.eval("saveAllShelves $gShelfTopLevel;")
    except Exception:
        pass


def _copy_to_scripts(src, module):
    scripts_dir = cmds.internalVar(userScriptDir=True)
    os.makedirs(scripts_dir, exist_ok=True)
    dest = os.path.join(scripts_dir, module + ".py")
    if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dest)):
        shutil.copyfile(src, dest)
    cache = os.path.join(scripts_dir, "__pycache__")
    if os.path.isdir(cache):
        for f in os.listdir(cache):
            if f.startswith(module + "."):
                try: os.remove(os.path.join(cache, f))
                except Exception: pass
    if scripts_dir not in sys.path:
        sys.path.append(scripts_dir)
    importlib.invalidate_caches()
    return dest


def _reload_cmd(module):
    return ("import sys, importlib\n"
            "m = sys.modules.get('{0}')\n"
            "if m: importlib.reload(m)\n"
            "else: import {0}").format(module)


def _find_source(names, keywords=(), signature=None):
    """Exact names first, then file names containing the keywords, then any .py
    next to the installer whose contents carry the tool's signature."""
    for n in names:
        p = os.path.join(_HERE, n)
        if os.path.isfile(p):
            return p
    try:
        files = sorted(f for f in os.listdir(_HERE) if f.lower().endswith(".py"))
    except Exception:
        files = []
    files = [f for f in files if "install" not in f.lower()]
    if keywords:
        for f in files:
            low = f.lower()
            if all(k in low for k in keywords):
                return os.path.join(_HERE, f)
    if signature:
        for f in files:
            p = os.path.join(_HERE, f)
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                    if signature in fh.read():
                        return p
            except Exception:
                pass
    return None


# =========================================================================
# TOOLS
# =========================================================================
# UV Transfer smart loader (runs cached instantly, updates from GitHub in the background)
_UV_LOADER = r'''# Phoenix UV Copy Paste - Smart Loader (Shelf Button)
import os, sys, threading, time
import maya.cmds as cmds
RAW_URL   = "https://raw.githubusercontent.com/vibe-phoenix/Phoenix-set-tool/main/phoenix-uv-copy-paste.py"
ETAG_URL  = "https://api.github.com/repos/vibe-phoenix/Phoenix-set-tool/contents/phoenix-uv-copy-paste.py"
TOOL_FILE = "phoenix-uv-copy-paste.py"
ETAG_FILE = "phoenix-uv-copy-paste.etag"
FOLDER    = "phoenixtools"
GLOBAL_KEY = "__phoenix_uv_tools_dir__"
def get_tools_dir():
    d = os.path.join(cmds.internalVar(userAppDir=True), FOLDER)
    if not os.path.exists(d): os.makedirs(d)
    return d
def http_get(url, headers=None):
    try:
        import urllib.request, ssl
        req = urllib.request.Request(url, headers=headers or {})
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=6) as r:
            return r.status, r.read().decode("utf-8")
    except Exception:
        return None, None
def _rd(p):
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f: return f.read()
    return None
def _wr(p, t):
    with open(p, "w", encoding="utf-8") as f: f.write(t)
def get_remote_etag():
    import json
    s, b = http_get(ETAG_URL, headers={"User-Agent": "PhoenixTools-Maya"})
    if s == 200 and b:
        try: return json.loads(b).get("sha", "")
        except Exception: pass
    return None
def run_script(d, code):
    if d not in sys.path: sys.path.insert(0, d)
    p = os.path.join(d, TOOL_FILE)
    exec(compile(code, p, "exec"), {"__file__": p})
def check_for_update(d, cached):
    remote = get_remote_etag()
    if not remote or remote == (cached or "").strip(): return
    s, code = http_get(RAW_URL)
    if s != 200 or not code:
        cmds.evalDeferred("import maya.cmds as cmds; cmds.warning('[PhoenixUV] Update download failed.')"); return
    _wr(os.path.join(d, TOOL_FILE), code); _wr(os.path.join(d, ETAG_FILE), remote)
    cmds.evalDeferred("import maya.cmds as cmds; cmds.inViewMessage(amg='Phoenix UV Copy Paste - update found, reopening...', pos='topCenter', fade=True, fst=1200)")
    time.sleep(1.5)
    import __main__
    setattr(__main__, GLOBAL_KEY, d)
    cmds.evalDeferred("import __main__, os, sys\n_d = getattr(__main__, '%s')\np = os.path.join(_d, '%s')\nwith open(p, 'r', encoding='utf-8') as f:\n    code = f.read()\nif _d not in sys.path:\n    sys.path.insert(0, _d)\nexec(compile(code, p, 'exec'), {'__file__': p})" % (GLOBAL_KEY, TOOL_FILE))
def load_phoenix_uv():
    d = get_tools_dir()
    code = _rd(os.path.join(d, TOOL_FILE))
    etag = _rd(os.path.join(d, ETAG_FILE))
    if code:
        try: run_script(d, code)
        except Exception as e:
            cmds.error("[PhoenixUV] Error running script: " + str(e)); return
        t = threading.Thread(target=check_for_update, args=(d, etag)); t.daemon = True; t.start()
    else:
        cmds.inViewMessage(amg="Phoenix UV Copy Paste - first run, downloading...", pos="topCenter", fade=True, fst=800)
        remote = get_remote_etag()
        s, code = http_get(RAW_URL) if remote else (None, None)
        if s == 200 and code:
            _wr(os.path.join(d, TOOL_FILE), code); _wr(os.path.join(d, ETAG_FILE), remote)
            run_script(d, code)
        else:
            cmds.error("[PhoenixUV] Download failed / offline and no cached script found.")
load_phoenix_uv()
'''


def _install_set_tools(shelf, src):
    _copy_to_scripts(src, "phoenix_set_tools")
    idx, th = _tool_theme("phoenixSetTools_theme_json", "phoenixSetTools_shelf_icon")
    icon = _write_icon_file("phxSetTools", idx, th, ("ink", "bg", "mid", "nt", "acc"),
                            _pixels(_MAP_SET_TOOLS, th, False), _MAP_SET_TOOLS)
    return _put_shelf_button(shelf, "phxSetTools", "Set Tools", "Phoenix Set Tools", icon,
                             _reload_cmd("phoenix_set_tools"))


def _install_units(shelf, src):
    _copy_to_scripts(src, "phoenix_units")
    idx, th = _tool_theme("phoenixUnits_theme_json", "phoenixUnits_shelf_icon")
    icon = _write_icon_file("phxUnits", idx, th, ("ink", "nt", "acc", "bg"),
                            _pixels(_MAP_UNITS, th, True), _MAP_UNITS)
    dbl = ("import sys, importlib\n"
           "m = sys.modules.get('phoenix_units')\n"
           "if m is None: m = importlib.import_module('phoenix_units')\n"
           "m.quick_centimeter()")
    return _put_shelf_button(shelf, "phxUnits", "Units",
                             "Phoenix Units — click: open popup · double-click: centimeter",
                             icon, _reload_cmd("phoenix_units"), dbl)


def _install_uv(shelf, src):
    # local copy becomes the loader's cache so it runs right away (GitHub updates still apply)
    if src:
        d = os.path.join(cmds.internalVar(userAppDir=True), "phoenixtools")
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(src, os.path.join(d, "phoenix-uv-copy-paste.py"))
    idx, th = _tool_theme("phoenixUVTransfer_theme_json", "phoenixUVTransfer_shelf_icon")
    icon = _write_icon_file("phxUVTransfer", idx, th, ("ink", "nt", "acc", "mid", "bg"),
                            _pixels(_MAP_UV, th, True), _MAP_UV)
    return _put_shelf_button(shelf, "phxUVTransfer", "UV Transfer", "Phoenix UV Transfer",
                             icon, _UV_LOADER,
                             match=("phoenix-uv-copy-paste", "load_phoenix_uv", "PhoenixUVTransfer"))


def _install_autosave(shelf, src):
    _copy_to_scripts(src, "autosave_script")
    us = os.path.join(cmds.internalVar(userScriptDir=True), "userSetup.py")
    text = ""
    if os.path.isfile(us):
        with open(us, "r", encoding="utf-8") as f:
            text = f.read()
    if "autosave_script.bootstrap" not in text:
        with open(us, "a", encoding="utf-8") as f:
            f.write(("\n" if text and not text.endswith("\n") else "") +
                    "import autosave_script\nautosave_script.bootstrap()\n")
    mod = sys.modules.get("autosave_script")
    mod = importlib.reload(mod) if mod else importlib.import_module("autosave_script")
    # autosave's own installer puts the button on the current shelf
    top = _shelf_top()
    prev = cmds.tabLayout(top, q=True, selectTab=True) if top else None
    try:
        if top and shelf:
            cmds.tabLayout(top, e=True, selectTab=shelf)
        target = cmds.shelfLayout(shelf, q=True, fullPathName=True)
        existed = False
        for b in mod._shelf_buttons():
            if b.rsplit("|", 1)[0] == target:
                existed = True
            else:
                try: cmds.deleteUI(b)
                except Exception: pass
        mod.install_shelf_button()
        if not [b for b in mod._shelf_buttons() if b.rsplit("|", 1)[0] == target]:
            raise RuntimeError("Autosave button was not created on shelf '{}'".format(shelf))
    finally:
        if top and prev:
            try: cmds.tabLayout(top, e=True, selectTab=prev)
            except Exception: pass
    try:
        mod.bootstrap()
    except Exception:
        pass
    return "updated" if existed else "added"


# key, name, blurb, map, fill_bg, (theme optVar, icon optVar), source names, keywords, installer,
# needs_file, content signature
_TOOLS = [
    ("settools", "SET TOOLS", "Set pipeline panel",
     _MAP_SET_TOOLS, False, ("phoenixSetTools_theme_json", "phoenixSetTools_shelf_icon"),
     ("phoenix-set-tools.py", "phoenix_set_tools.py"), ("set", "tools"), _install_set_tools, True,
     "def show_phoenix_ui"),
    ("units", "UNITS", "Linear unit switcher",
     _MAP_UNITS, True, ("phoenixUnits_theme_json", "phoenixUnits_shelf_icon"),
     ("phoenix_units.py", "phoenix-units.py"), ("units",), _install_units, True,
     "def units_popup"),
    ("uv", "UV TRANSFER", "Copy / paste UVs",
     _MAP_UV, True, ("phoenixUVTransfer_theme_json", "phoenixUVTransfer_shelf_icon"),
     ("phoenix-uv-copy-paste.py", "phoenix_uv_copy_paste.py"), ("uv", "copy"), _install_uv, False,
     "class PhoenixUVTransfer"),
    ("autosave", "AUTOSAVE", "Rolling scene backups",
     _MAP_AUTOSAVE, False, ("autosavePhoenix_theme_json", "autosavePhoenix_shelf_icon"),
     ("autosave_script.py",), ("autosave",), _install_autosave, True,
     "def autosave_status_ui"),
]


# =========================================================================
# UI
# =========================================================================
_FONT_FAMILY = None


def _ensure_font():
    global _FONT_FAMILY
    if _FONT_FAMILY:
        return _FONT_FAMILY
    fam = None
    for blob in (_FONT_SILK_REG, _FONT_SILK_BOLD):
        try:
            data = QtCore.QByteArray(base64.b64decode("".join(blob)))
            fid = QtGui.QFontDatabase.addApplicationFontFromData(data)
            fams = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams and not fam:
                fam = fams[0]
        except Exception:
            pass
    _FONT_FAMILY = fam or "Courier New"
    return _FONT_FAMILY


def _px(size, bold=False, spacing=0.0):
    f = QtGui.QFont(_ensure_font())
    f.setPixelSize(int(size))
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, spacing)
    return f


def _text_w(fm, text):
    return fm.horizontalAdvance(text) if hasattr(fm, "horizontalAdvance") else fm.width(text)


def _pbox(p, x, y, w, h, fill, ink, bw=3, sh=0, shc=None):
    if sh and shc is not None:
        p.fillRect(QtCore.QRect(x + sh, y + sh, w, h), shc)
    p.fillRect(QtCore.QRect(x, y, w, h), ink)
    p.fillRect(QtCore.QRect(x + bw, y + bw, w - 2 * bw, h - 2 * bw), fill)


def _maya_main_window():
    try:
        ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None
    except Exception:
        return None


class _Label(QtWidgets.QWidget):
    def __init__(self, text, px=12, key="ink", align=None, bold=False, spacing=0.5, parent=None):
        super(_Label, self).__init__(parent)
        self._text, self._key = text, key
        self._font = _px(px, bold, spacing)
        self._align = align if align is not None else (QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.setMinimumHeight(QtGui.QFontMetrics(self._font).height())
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

    def sizeHint(self):
        fm = QtGui.QFontMetrics(self._font)
        return QtCore.QSize(_text_w(fm, self._text) + 4, fm.height())

    def setText(self, text, key=None):
        self._text = text
        if key:
            self._key = key
        self.updateGeometry(); self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setFont(self._font); p.setPen(_c(self._key))
        p.drawText(self.rect(), self._align, self._text)
        p.end()


class _PixelButton(QtWidgets.QAbstractButton):
    def __init__(self, text, height=36, parent=None):
        super(_PixelButton, self).__init__(parent)
        self.setText(text)
        self._hot = False
        self._hover = False
        self._font = _px(12, False, 0.5)
        self._h = height
        self.setFixedHeight(height)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

    def sizeHint(self):
        return QtCore.QSize(_text_w(QtGui.QFontMetrics(self._font), self.text()) + 24, self._h)

    def minimumSizeHint(self):
        return QtCore.QSize(30, self._h)

    def setHot(self, hot):
        self._hot = hot; self.update()

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        down = self.isDown()
        off, sh = (2, 1) if down else (0, 3)
        w, h = self.width() - 3, self.height() - 3
        ink = _c("ink")
        if not self.isEnabled():
            fill, fg, ink = _c("fld"), _c("nd"), _c("nd")
        elif self._hot:
            fill, fg = _c("acc"), _c("acci")
        else:
            fill, fg = _c("fld"), _c("ink")
        if self._hover and not down and self.isEnabled():
            fill = fill.darker(109)
        _pbox(p, off, off, w, h, fill, ink, 3, sh, _c("sh"))
        p.setFont(self._font); p.setPen(fg)
        p.drawText(QtCore.QRect(off, off, w, h), QtCore.Qt.AlignCenter, self.text())
        p.end()


class _WinBtn(QtWidgets.QAbstractButton):
    _X = ("10001", "01010", "00100", "01010", "10001")

    def __init__(self, parent=None):
        super(_WinBtn, self).__init__(parent)
        self._hover = False
        self.setFixedSize(22, 20)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        if self._hover:
            p.fillRect(self.rect(), _c("ink")); glyph = _c("bg")
        else:
            _pbox(p, 0, 0, 22, 20, _c("fld"), _c("ink"), 3); glyph = _c("ink")
        for r, row in enumerate(self._X):
            for c, ch in enumerate(row):
                if ch == "1":
                    p.fillRect(QtCore.QRect(6 + c * 2, 5 + r * 2, 2, 2), glyph)
        p.end()


class _BigTitle(QtWidgets.QWidget):
    def __init__(self, text, parent=None):
        super(_BigTitle, self).__init__(parent)
        self._text = text
        self._font = _px(26, True, 2.0)
        self.setFixedHeight(QtGui.QFontMetrics(self._font).height() + 5)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setFont(self._font)
        r = self.rect().adjusted(0, 0, -3, -3)
        p.setPen(_c("tsh")); p.drawText(r.translated(3, 3), QtCore.Qt.AlignCenter, self._text)
        p.setPen(_c("ink")); p.drawText(r, QtCore.Qt.AlignCenter, self._text)
        p.end()


class _Rule(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(_Rule, self).__init__(parent)
        self.setFixedHeight(3)

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.fillRect(self.rect(), _c("ink")); p.end()


class _Combo(QtWidgets.QComboBox):
    def __init__(self, parent=None):
        super(_Combo, self).__init__(parent)
        self._hover = False
        self.setFixedHeight(33)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setFont(_px(11, False, 0.5))
        self.view().setFont(_px(11, False, 0.5))
        t = _UI_THEME
        self.setStyleSheet(
            "QComboBox QAbstractItemView {{ background:{fld}; color:{ink}; border:3px solid {ink};"
            " selection-background-color:{acc}; selection-color:{acci}; outline:0; padding:2px; }}"
            .format(**t))

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        fill = _c("fld")
        if self._hover:
            fill = fill.darker(109)
        w, h = self.width() - 3, self.height() - 3
        _pbox(p, 0, 0, w, h, fill, _c("ink"), 3, 3, _c("sh"))
        p.setFont(_px(11, False, 0.5)); p.setPen(_c("ink"))
        fm = QtGui.QFontMetrics(_px(11, False, 0.5))
        p.drawText(QtCore.QRect(10, 0, w - 34, h), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fm.elidedText(self.currentText().upper(), QtCore.Qt.ElideRight, w - 34))
        ax, ay = w - 20, h // 2 - 2
        for i, wd in enumerate((8, 6, 4, 2)):
            p.fillRect(QtCore.QRect(ax + i, ay + i, wd, 1), _c("ink"))
        p.end()


class _ToolCard(QtWidgets.QWidget):
    """Selectable tool tile: icon, name, blurb, status, check square."""
    toggled = QtCore.Signal()

    def __init__(self, tool, parent=None):
        super(_ToolCard, self).__init__(parent)
        (self.key, self.name, self.blurb, self._map, self._fill_bg, opts,
         names, keys, self.installer, needs_file, signature) = tool
        self._names, self._needs_file = names, needs_file
        self.src = _find_source(names, keys, signature)
        print("[PhoenixInstaller] {}: {}".format(self.name, self.src or "not found in " + _HERE))
        self.available = bool(self.src) or not needs_file
        self.selected = self.available
        self.status = ("READY" if self.src else "GITHUB") if self.available else "MISSING"
        self.status_key = "mid" if self.available else "nd"
        self._hover = False
        _idx, th = _tool_theme(*opts)
        overlay = (_AUTOSAVE_CLOCK, 9, 9) if self.key == "autosave" else None
        self._pixels = _pixels(self._map, th, self._fill_bg, overlay)
        self.setFixedSize(_TILE_W, 110)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._update_tip()

    def _update_tip(self):
        names = self._names
        tip = self.name.title() + " — " + self.blurb + "\n" + (
            self.src or ("Downloads from GitHub on first click" if self.available
                         else "File missing: put {} next to the installer,\n"
                              "or click to locate it".format(names[0])))
        self.setToolTip(tip)

    def _locate(self):
        res = cmds.fileDialog2(fileFilter="Python (*.py)", fm=1, dialogStyle=2, startingDirectory=_HERE,
                               caption="Locate {} ({})".format(self.name.title(), self._names[0]))
        if not res:
            return
        self.src = res[0]
        self.available = self.selected = True
        self.status, self.status_key = "READY", "mid"
        self._update_tip()
        self.update()
        self.toggled.emit()

    def setSelected(self, on):
        if self.available:
            self.selected = bool(on); self.update()

    def setStatus(self, text, key="mid"):
        self.status, self.status_key = text, key
        self.update()

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != QtCore.Qt.LeftButton or not self.rect().contains(e.pos()):
            return
        if not self.available:
            self._locate()
            return
        self.selected = not self.selected
        self.update()
        self.toggled.emit()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        w, h = self.width() - 3, self.height() - 3
        on = self.selected and self.available
        ink = _c("ink") if self.available else _c("nd")
        fill = _c("fld")
        if self._hover and self.available:
            fill = fill.darker(106)
        _pbox(p, 0, 0, w, h, fill, ink, 3, 3, _c("sh"))
        # icon (3x pixels), centred
        s = 3
        ix, iy = (w - 48) // 2, 12
        for y, row in enumerate(self._pixels):
            for x, c in enumerate(row):
                if c:
                    col = QtGui.QColor(c)
                    if not self.available:
                        g = int(col.red() * .3 + col.green() * .59 + col.blue() * .11)
                        col = QtGui.QColor(g, g, g)
                    p.fillRect(QtCore.QRect(ix + x * s, iy + y * s, s, s), col)
        # name + status under the icon
        f = _px(11, False, 0.5)
        fm = QtGui.QFontMetrics(f)
        p.setFont(f); p.setPen(ink)
        p.drawText(QtCore.QRect(4, 66, w - 8, 16), QtCore.Qt.AlignCenter,
                   fm.elidedText(self.name, QtCore.Qt.ElideRight, w - 8))
        f2 = _px(10, False, 0.5)
        fm2 = QtGui.QFontMetrics(f2)
        p.setFont(f2); p.setPen(_c(self.status_key))
        p.drawText(QtCore.QRect(4, 84, w - 8, 14), QtCore.Qt.AlignCenter,
                   fm2.elidedText(self.status, QtCore.Qt.ElideRight, w - 8))
        # check square, top-right corner
        cx, cy = w - 21, 6
        _pbox(p, cx, cy, 15, 15, _c("fld"), ink if on else (_c("mid") if self.available else _c("nd")), 3)
        if on:
            p.fillRect(QtCore.QRect(cx + 4, cy + 4, 7, 7), _c("acc"))
        p.end()


class PhoenixToolsInstaller(QtWidgets.QWidget):
    RADIUS = 14
    BORDER = 3

    def __init__(self, parent=None):
        super(PhoenixToolsInstaller, self).__init__(parent or _maya_main_window())
        _ensure_font()
        self.setObjectName(_UI_OBJECT_NAME)
        self.setWindowTitle("Phoenix Tools Installer")
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        cols = min(_COLS, len(_TOOLS))
        grid_w = cols * _TILE_W + (cols - 1) * _TILE_GAP
        self.setFixedWidth(max(_WIDTH, grid_w + 2 * (self.BORDER + 14)))
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._press = None
        self._origin = None
        self._moving = False

        lay = QtWidgets.QVBoxLayout(self)
        b = self.BORDER
        lay.setContentsMargins(b + 14, b + 8, b + 14, b + 14)
        lay.setSpacing(0)

        top = QtWidgets.QHBoxLayout(); top.addStretch(1)
        x = _WinBtn(); x.setToolTip("Close")
        x.clicked.connect(lambda *_: self.close())
        top.addWidget(x)
        lay.addLayout(top)

        lay.addWidget(_BigTitle("PHOENIX TOOLS"))
        lay.addSpacing(2)
        lay.addWidget(_Label("INSTALLER  ·  MADE BY VIBEPHOENIX", 10, "mid", QtCore.Qt.AlignCenter))
        lay.addSpacing(12)

        lay.addWidget(_Label("Pick the tools to install"))
        lay.addSpacing(6)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(_TILE_GAP); grid.setVerticalSpacing(_TILE_GAP)
        self.cards = []
        for i, tool in enumerate(_TOOLS):
            c = _ToolCard(tool)
            c.toggled.connect(self._update_install_btn)
            grid.addWidget(c, i // _COLS, i % _COLS)
            self.cards.append(c)
        grid_row = QtWidgets.QHBoxLayout()
        grid_row.addStretch(1); grid_row.addLayout(grid); grid_row.addStretch(1)
        lay.addLayout(grid_row)
        lay.addSpacing(10)

        sel = QtWidgets.QGridLayout(); sel.setHorizontalSpacing(10)
        b_all = _PixelButton("SELECT ALL"); b_none = _PixelButton("SELECT NONE")
        b_all.clicked.connect(lambda *_: self._select(True))
        b_none.clicked.connect(lambda *_: self._select(False))
        sel.addWidget(b_all, 0, 0); sel.addWidget(b_none, 0, 1)
        sel.setColumnStretch(0, 1); sel.setColumnStretch(1, 1)
        lay.addLayout(sel)

        lay.addSpacing(12); lay.addWidget(_Rule()); lay.addSpacing(12)

        row = QtWidgets.QHBoxLayout(); row.setSpacing(8)
        lbl = _Label("SHELF", 11); lbl.setFixedWidth(60)
        self.shelf_combo = _Combo()
        names, cur = _shelf_names()
        self.shelf_combo.addItems(names)
        if cur in names:
            self.shelf_combo.setCurrentIndex(names.index(cur))
        self.shelf_combo.setToolTip("Every installed tool gets its button on this shelf\n(old copies on other shelves are removed)")
        row.addWidget(lbl); row.addWidget(self.shelf_combo, 1)
        lay.addLayout(row)
        lay.addSpacing(12)

        self.b_install = _PixelButton("INSTALL", 40)
        self.b_install.setHot(True)
        self.b_install.clicked.connect(lambda *_: self._install())
        lay.addWidget(self.b_install)
        lay.addSpacing(8)
        self.status = _Label("", 10, "mid", QtCore.Qt.AlignCenter)
        lay.addWidget(self.status)

        self._update_install_btn()
        self.adjustSize()

    # frame / drag / keys
    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen)
        r = QtCore.QRectF(self.rect())
        p.setBrush(_c("ink"))
        p.drawRoundedRect(r, self.RADIUS, self.RADIUS)
        b = self.BORDER
        p.setBrush(_c("bg"))
        p.drawRoundedRect(r.adjusted(b, b, -b, -b), self.RADIUS - b, self.RADIUS - b)
        p.end()

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key_Escape:
            self.close(); return
        super(PhoenixToolsInstaller, self).keyPressEvent(e)

    @staticmethod
    def _gpos(e):
        return e.globalPosition().toPoint() if hasattr(e, "globalPosition") else e.globalPos()

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._press = self._gpos(e)
            self._origin = self.frameGeometry().topLeft()
            self._moving = False

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        gp = self._gpos(e)
        if not self._moving:
            if (gp - self._press).manhattanLength() < 4:
                return
            self._moving = True
            handle = self.windowHandle()
            if handle is not None and hasattr(handle, "startSystemMove"):
                try:
                    if handle.startSystemMove():
                        self._press = None
                        return
                except Exception:
                    pass
        self.move(self._origin + gp - self._press)

    def mouseReleaseEvent(self, e):
        self._press = None
        self._moving = False

    # behaviour
    def _select(self, on):
        for c in self.cards:
            c.setSelected(on)
        self._update_install_btn()

    def _update_install_btn(self):
        n = sum(1 for c in self.cards if c.selected and c.available)
        self.b_install.setText("INSTALL {} TOOL{}".format(n, "" if n == 1 else "S") if n else "INSTALL")
        self.b_install.setEnabled(n > 0)
        self.b_install.update()

    def _install(self):
        shelf = self.shelf_combo.currentText()
        if not shelf:
            self.status.setText("NO SHELF FOUND", "nd")
            return
        chosen = [c for c in self.cards if c.selected and c.available]
        ok = fail = 0
        for c in chosen:
            c.setStatus("WORKING...", "ink")
            QtWidgets.QApplication.processEvents()
            try:
                result = c.installer(shelf, c.src)
                c.setStatus("INSTALLED" if result == "added" else "UPDATED", "ink")
                ok += 1
            except Exception as ex:
                c.setStatus("FAILED", "ink")
                c.setToolTip(c.toolTip() + "\nFailed: {}".format(ex))
                print("[PhoenixInstaller] {} failed: {}\n{}".format(c.name, ex, traceback.format_exc()))
                fail += 1
        _save_shelves()
        msg = "{} INSTALLED".format(ok) + ("  ·  {} FAILED".format(fail) if fail else "")
        self.status.setText(msg, "ink")
        try:
            cmds.inViewMessage(amg="Phoenix Tools: " + msg.lower(), pos="topCenter", fade=True, fadeStayTime=2500)
        except Exception:
            pass
        self.raise_()
        self.activateWindow()


_UI_INSTANCE = None


def show_installer():
    global _UI_INSTANCE
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w.objectName() == _UI_OBJECT_NAME:
                w.close(); w.deleteLater()
        except Exception:
            pass
    _UI_INSTANCE = PhoenixToolsInstaller()
    _UI_INSTANCE.show()
    _UI_INSTANCE.raise_()
    _UI_INSTANCE.activateWindow()
    return _UI_INSTANCE


def onMayaDroppedPythonFile(*args):
    """Maya keeps a dropped file cached as a module, so a newer copy of this
    installer would otherwise show the old panel. Always run the file fresh."""
    path = os.path.abspath(__file__)
    if path.lower().endswith((".pyc", ".pyo")):
        path = path[:-1]
    sys.modules.pop(__name__, None)
    ns = {"__name__": "__phx_tools_installer__", "__file__": path}
    with open(path, "r", encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"), ns)
    ns["_HERE"] = os.path.dirname(path)
    ns["show_installer"]()


# =========================================================================
# EMBEDDED FONT — Silkscreen (Regular + Bold)
# Copyright 2001 The Silkscreen Project Authors (github.com/googlefonts/silkscreen)
# Licensed under the SIL Open Font License 1.1 (scripts.sil.org/OFL).
# =========================================================================

_FONT_SILK_REG = (
    "AAEAAAARAQAABAAQR0RFRgVfBBIAAG+YAAAAUkdQT1OejZsYAABv7AAAAgBHU1VCrlKwxgAAcewAAABsT1MvMlySBCAAAAGYAAAA"
    "YGNtYXDYZtqcAAAFYAAAAj5jdnQgB6YSRgAAFmAAAABSZnBnbWIu/XwAAAegAAAODGdhc3AAAAAQAABvkAAAAAhnbHlm6kt80wAA"
    "GGwAAFKOaGVhZAsQfXAAAAEcAAAANmhoZWEHswJSAAABVAAAACRobXR4QC1k6QAAAfgAAANobG9jYaknlGMAABa0AAABtm1heHAC"
    "JQ6xAAABeAAAACBuYW1lMG5NbAAAavwAAAJGcG9zdDur4vEAAG1EAAACTHByZXB0Qd0yAAAVrAAAALIAAQAAAAEAQroF8yVfDzz1"
    "CA8D6AAAAAC5GPoPAAAAAOcqP07/Rf8GBGUD6AAAAAYAAgAAAAAAAAABAAAEBv8GAAAE4v9F/Y8EZQABAAAAAAAAAAAAAAAAAAAA"
    "2gABAAAA2gAkAAUAJAAFAAIALABaAI0AAACFDgwAAgABAAQCwAGQAAUAAAKKAlgAAABLAooCWAAAAV4AMgD6AAAAAAAAAAAAAAAA"
    "gAAAZwAAAAoAAAAAAAAAAFVOS04AwAANISIEBv8GAAAD6AD6AAAAAQAAAAAB9AK8AAAAIAADAnEAfQLuAH0C7gB9Au4AfQLuAH0C"
    "7gB9Au4AfQLuAH0DawB9Au4AfQLuAH0C7gB9Au4AfQNrAH0CcQB9AnEAfQJxAH0CcQB9AnEAfQJxAH0C7gB9Au4AfQF3AH0BdwB9"
    "AXcAAAF3AAABdwAAAu4AfQLuAH0CcQB9A2sAfQNrAH0DawB9Au4AfQLuAH0C7gB9Au4AfQLuAH0DawB9Au4AfQNrAH0C7gB9AnEA"
    "fQLuAH0C7gB9Au4AfQJxAH0C7gB9Au4AfQLuAH0C7gB9Au4AfQNrAH0DawB9A2sAfQNrAH0DawB9AnEAfQLuAH0C7gB9Au4AfQLu"
    "AH0C7gB9Au4AfQLuAH0DawB9Au4AfQLuAH0C7gB9Au4AfQNrAH0CcQB9AnEAfQJxAH0CcQB9AnEAfQJxAH0C7gB9Au4AfQF3AH0B"
    "dwB9AXcAAAF3AAABdwAAAu4AfQLuAH0CcQB9A2sAfQNrAH0DawB9Au4AfQLuAH0C7gB9Au4AfQLuAH0DawB9Au4AfQNrAH0C7gB9"
    "AnEAfQLuAH0C7gB9Au4AfQLuAH0CcQB9Au4AfQLuAH0C7gB9Au4AfQLuAH0DawB9A2sAfQNrAH0DawB9A2sAfQNrAH0CcQB9AnEA"
    "fQJxAH0C7gB9AnEAfQLuAH0C7gB9Au4AfQLuAH0C7gB9Au4AfQLuAH0C7gB9AfQAfQJxAH0CcQB9BGUAfQRlAH0E4gB9AfQAAAH0"
    "AAAB9AAAAXcAfQH0AH0BdwB9AfQAfQNrAH0BdwB9AXcAfQLuAH0C7gB9AXcAfQF3AH0DawB9A2sAfQJxAH0CcQB9AnEAfQAA/0UC"
    "7gB9A+gAfQLuAH0B9AB9AfQAfQJxAH0CcQB9AfQAfQH0AH0B9AB9AnEAfQJxAH0CcQB9AXcAfQF3AH0DawB9A2sAfQH0AH0B9AB9"
    "AnEAfQF3AH0DawB9Au4AfQNrAH0C7gB9BGUAfQRlAH0E4gB9AnEAfQF3AH0BdwB9AnEAfQNrAH0C7gB9A2sAfQLuAH0DawB9A2sA"
    "fQJxAH0CcQB9AnEAfQJxAH0CcQB9A2sAfQLuAH0C7gB9AnEAfQLuAH0DawB9AAAAfQAAAH0AAAB9AAAAfQAAAH0AAAB9AAAAhwJx"
    "AH0B9AB9AfQAfQJxAH0C7gB9Au4AfQH0AIcAAAACAAAAAwAAABQAAwABAAAAFAAEAioAAAAoACAABAAIAA0ALwA5AH4A/wFTAsYC"
    "3AMBAwQDCCAUIBogHiAiICYgOiCsISL//wAAAA0AIAAwADoAoAFSAsYC3AMAAwMDCCATIBggHCAiICYgOSCsISL//wB6AAAARwAA"
    "AAAAAP4Q/fv9zf3N/cTgiAAAAADgcuBo4HPgEd+UAAEAAAAmAAAAQgDKAYgAAAAAAAAAAAAAAAABfgGCAAAAAAAAAAAAAAAAAIgA"
    "jwCuAJYAvADLALEArwCeAJ8AlQDAAIsAmQCKAJcAjACNAMUAwwDEAJEAsAABAAkACgAMAA4AEwAUABUAFgAbABwAHQAeAB8AIQAp"
    "ACsALAAtAC4ALwA0ADUANgA3ADkAogCYAKMAyQCdANQAOgBCAEMARQBHAEwATQBOAE8AVABVAFYAVwBYAFoAYgBkAGUAZgBoAGkA"
    "bgBvAHAAcQB0AKAAuAChAMcAiQCQALoAvgC7AL8AuQCzANMAtAB1AKoAyACaALUA2AC3AMYAggCDANUAygCyAJMA2QCBAHYAqwCF"
    "AIQAhgCSAAUAAgADAAcABAAGAAgACwASAA8AEAARABoAFwAYABkADQAgACUAIgAjACcAJADBACYAMwAwADEAMgA4ACoAZwA+ADsA"
    "PABAAD0APwBBAEQASwBIAEkASgBTAFAAUQBSAEYAWQBeAFsAXABgAF0AwgBfAG0AagBrAGwAcgBjAHMAKABhAKgAqQCkAKYApwCl"
    "AACwACwgsABVWEVZICBLuAAOUUuwBlNaWLA0G7AoWWBmIIpVWLACJWG5CAAIAGNjI2IbISGwAFmwAEMjRLIAAQBDYEItsAEssCBg"
    "Zi2wAiwjISMhLbADLCBkswMUFQBCQ7ATQyBgYEKxAhRDQrElA0OwAkNUeCCwDCOwAkNDYWSwBFB4sgICAkNgQrAhZRwhsAJDQ7IO"
    "FQFCHCCwAkMjQrITARNDYEIjsABQWGVZshYBAkNgQi2wBCywAyuwFUNYIyEjIbAWQ0MjsABQWGVZGyBkILDAULAEJlqyKAENQ0Vj"
    "RbAGRVghsAMlWVJbWCEjIRuKWCCwUFBYIbBAWRsgsDhQWCGwOFlZILEBDUNFY0VhZLAoUFghsQENQ0VjRSCwMFBYIbAwWRsgsMBQ"
    "WCBmIIqKYSCwClBYYBsgsCBQWCGwCmAbILA2UFghsDZgG2BZWVkbsAIlsAxDY7AAUliwAEuwClBYIbAMQxtLsB5QWCGwHkthuBAA"
    "Y7AMQ2O4BQBiWVlkYVmwAStZWSOwAFBYZVlZIGSwFkMjQlktsAUsIEUgsAQlYWQgsAdDUFiwByNCsAgjQhshIVmwAWAtsAYsIyEj"
    "IbADKyBksQdiQiCwCCNCsAZFWBuxAQ1DRWOxAQ1DsAJgRWOwBSohILAIQyCKIIqwASuxMAUlsAQmUVhgUBthUllYI1khWSCwQFNY"
    "sAErGyGwQFkjsABQWGVZLbAHLLAJQyuyAAIAQ2BCLbAILLAJI0IjILAAI0JhsAJiZrABY7ABYLAHKi2wCSwgIEUgsA5DY7gEAGIg"
    "sABQWLBAYFlmsAFjYESwAWAtsAossgkOAENFQiohsgABAENgQi2wCyywAEMjRLIAAQBDYEItsAwsICBFILABKyOwAEOwBCVgIEWK"
    "I2EgZCCwIFBYIbAAG7AwUFiwIBuwQFlZI7AAUFhlWbADJSNhRESwAWAtsA0sICBFILABKyOwAEOwBCVgIEWKI2EgZLAkUFiwABuw"
    "QFkjsABQWGVZsAMlI2FERLABYC2wDiwgsAAjQrMNDAADRVBYIRsjIVkqIS2wDyyxAgJFsGRhRC2wECywAWAgILAPQ0qwAFBYILAP"
    "I0JZsBBDSrAAUlggsBAjQlktsBEsILAQYmawAWMguAQAY4ojYbARQ2AgimAgsBEjQiMtsBIsS1RYsQRkRFkksA1lI3gtsBMsS1FY"
    "S1NYsQRkRFkbIVkksBNlI3gtsBQssQASQ1VYsRISQ7ABYUKwEStZsABDsAIlQrEPAiVCsRACJUKwARYjILADJVBYsQEAQ2CwBCVC"
    "ioogiiNhsBAqISOwAWEgiiNhsBAqIRuxAQBDYLACJUKwAiVhsBAqIVmwD0NHsBBDR2CwAmIgsABQWLBAYFlmsAFjILAOQ2O4BABi"
    "ILAAUFiwQGBZZrABY2CxAAATI0SwAUOwAD6yAQEBQ2BCLbAVLACxAAJFVFiwEiNCIEWwDiNCsA0jsAJgQiBgtxgYAQARABMAQkJC"
    "imAgsBQjQrABYbEUCCuwiysbIlktsBYssQAVKy2wFyyxARUrLbAYLLECFSstsBkssQMVKy2wGiyxBBUrLbAbLLEFFSstsBwssQYV"
    "Ky2wHSyxBxUrLbAeLLEIFSstsB8ssQkVKy2wKywjILAQYmawAWOwBmBLVFgjIC6wAV0bISFZLbAsLCMgsBBiZrABY7AWYEtUWCMg"
    "LrABcRshIVktsC0sIyCwEGJmsAFjsCZgS1RYIyAusAFyGyEhWS2wICwAsA8rsQACRVRYsBIjQiBFsA4jQrANI7ACYEIgYLABYbUY"
    "GAEAEQBCQopgsRQIK7CLKxsiWS2wISyxACArLbAiLLEBICstsCMssQIgKy2wJCyxAyArLbAlLLEEICstsCYssQUgKy2wJyyxBiAr"
    "LbAoLLEHICstsCkssQggKy2wKiyxCSArLbAuLCA8sAFgLbAvLCBgsBhgIEMjsAFgQ7ACJWGwAWCwLiohLbAwLLAvK7AvKi2wMSwg"
    "IEcgILAOQ2O4BABiILAAUFiwQGBZZrABY2AjYTgjIIpVWCBHICCwDkNjuAQAYiCwAFBYsEBgWWawAWNgI2E4GyFZLbAyLACxAAJF"
    "VFixDgZFQrABFrAxKrEFARVFWDBZGyJZLbAzLACwDyuxAAJFVFixDgZFQrABFrAxKrEFARVFWDBZGyJZLbA0LCA1sAFgLbA1LACx"
    "DgZFQrABRWO4BABiILAAUFiwQGBZZrABY7ABK7AOQ2O4BABiILAAUFiwQGBZZrABY7ABK7AAFrQAAAAAAEQ+IzixNAEVKiEtsDYs"
    "IDwgRyCwDkNjuAQAYiCwAFBYsEBgWWawAWNgsABDYTgtsDcsLhc8LbA4LCA8IEcgsA5DY7gEAGIgsABQWLBAYFlmsAFjYLAAQ2Gw"
    "AUNjOC2wOSyxAgAWJSAuIEewACNCsAIlSYqKRyNHI2EgWGIbIVmwASNCsjgBARUUKi2wOiywABawFyNCsAQlsAQlRyNHI2GxDABC"
    "sAtDK2WKLiMgIDyKOC2wOyywABawFyNCsAQlsAQlIC5HI0cjYSCwBiNCsQwAQrALQysgsGBQWCCwQFFYswQgBSAbswQmBRpZQkIj"
    "ILAKQyCKI0cjRyNhI0ZgsAZDsAJiILAAUFiwQGBZZrABY2AgsAErIIqKYSCwBENgZCOwBUNhZFBYsARDYRuwBUNgWbADJbACYiCw"
    "AFBYsEBgWWawAWNhIyAgsAQmI0ZhOBsjsApDRrACJbAKQ0cjRyNhYCCwBkOwAmIgsABQWLBAYFlmsAFjYCMgsAErI7AGQ2CwASuw"
    "BSVhsAUlsAJiILAAUFiwQGBZZrABY7AEJmEgsAQlYGQjsAMlYGRQWCEbIyFZIyAgsAQmI0ZhOFktsDwssAAWsBcjQiAgILAFJiAu"
    "RyNHI2EjPDgtsD0ssAAWsBcjQiCwCiNCICAgRiNHsAErI2E4LbA+LLAAFrAXI0KwAyWwAiVHI0cjYbAAVFguIDwjIRuwAiWwAiVH"
    "I0cjYSCwBSWwBCVHI0cjYbAGJbAFJUmwAiVhuQgACABjYyMgWGIbIVljuAQAYiCwAFBYsEBgWWawAWNgIy4jICA8ijgjIVktsD8s"
    "sAAWsBcjQiCwCkMgLkcjRyNhIGCwIGBmsAJiILAAUFiwQGBZZrABYyMgIDyKOC2wQCwjIC5GsAIlRrAXQ1hQG1JZWCA8WS6xMAEU"
    "Ky2wQSwjIC5GsAIlRrAXQ1hSG1BZWCA8WS6xMAEUKy2wQiwjIC5GsAIlRrAXQ1hQG1JZWCA8WSMgLkawAiVGsBdDWFIbUFlYIDxZ"
    "LrEwARQrLbBDLLA6KyMgLkawAiVGsBdDWFAbUllYIDxZLrEwARQrLbBELLA7K4ogIDywBiNCijgjIC5GsAIlRrAXQ1hQG1JZWCA8"
    "WS6xMAEUK7AGQy6wMCstsEUssAAWsAQlsAQmICAgRiNHYbAMI0IuRyNHI2GwC0MrIyA8IC4jOLEwARQrLbBGLLEKBCVCsAAWsAQl"
    "sAQlIC5HI0cjYSCwBiNCsQwAQrALQysgsGBQWCCwQFFYswQgBSAbswQmBRpZQkIjIEewBkOwAmIgsABQWLBAYFlmsAFjYCCwASsg"
    "iophILAEQ2BkI7AFQ2FkUFiwBENhG7AFQ2BZsAMlsAJiILAAUFiwQGBZZrABY2GwAiVGYTgjIDwjOBshICBGI0ewASsjYTghWbEw"
    "ARQrLbBHLLEAOisusTABFCstsEgssQA7KyEjICA8sAYjQiM4sTABFCuwBkMusDArLbBJLLAAFSBHsAAjQrIAAQEVFBMusDYqLbBK"
    "LLAAFSBHsAAjQrIAAQEVFBMusDYqLbBLLLEAARQTsDcqLbBMLLA5Ki2wTSywABZFIyAuIEaKI2E4sTABFCstsE4ssAojQrBNKy2w"
    "TyyyAABGKy2wUCyyAAFGKy2wUSyyAQBGKy2wUiyyAQFGKy2wUyyyAABHKy2wVCyyAAFHKy2wVSyyAQBHKy2wViyyAQFHKy2wVyyz"
    "AAAAQystsFgsswABAEMrLbBZLLMBAABDKy2wWiyzAQEAQystsFssswAAAUMrLbBcLLMAAQFDKy2wXSyzAQABQystsF4sswEBAUMr"
    "LbBfLLIAAEUrLbBgLLIAAUUrLbBhLLIBAEUrLbBiLLIBAUUrLbBjLLIAAEgrLbBkLLIAAUgrLbBlLLIBAEgrLbBmLLIBAUgrLbBn"
    "LLMAAABEKy2waCyzAAEARCstsGksswEAAEQrLbBqLLMBAQBEKy2wayyzAAABRCstsGwsswABAUQrLbBtLLMBAAFEKy2wbiyzAQEB"
    "RCstsG8ssQA8Ky6xMAEUKy2wcCyxADwrsEArLbBxLLEAPCuwQSstsHIssAAWsQA8K7BCKy2wcyyxATwrsEArLbB0LLEBPCuwQSst"
    "sHUssAAWsQE8K7BCKy2wdiyxAD0rLrEwARQrLbB3LLEAPSuwQCstsHgssQA9K7BBKy2weSyxAD0rsEIrLbB6LLEBPSuwQCstsHss"
    "sQE9K7BBKy2wfCyxAT0rsEIrLbB9LLEAPisusTABFCstsH4ssQA+K7BAKy2wfyyxAD4rsEErLbCALLEAPiuwQistsIEssQE+K7BA"
    "Ky2wgiyxAT4rsEErLbCDLLEBPiuwQistsIQssQA/Ky6xMAEUKy2whSyxAD8rsEArLbCGLLEAPyuwQSstsIcssQA/K7BCKy2wiCyx"
    "AT8rsEArLbCJLLEBPyuwQSstsIossQE/K7BCKy2wiyyyCwADRVBYsAYbsgQCA0VYIyEbIVlZQiuwCGWwAyRQeLEFARVFWDBZLQBL"
    "uADIUlixAQGOWbABuQgACABjcLEAB0KzAB4CACqxAAdCtSMEEQoCCiqxAAdCtScCGwYCCiqxAAlCuwkABIAAAgALKrEAC0K7AEAA"
    "wAACAAsquQADAABEsSQBiFFYsECIWLkAAwBkRLEoAYhRWLgIAIhYuQADAABEWRuxJwGIUVi6CIAAAQRAiGNUWLkAAwAARFlZWVlZ"
    "tSUCFQYCDiq4Af+FsASNsQIARLMFZAYAREQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAH0AfQD6AD4AggCCAXcAXQJxAAAC"
    "cQJxAAAAAAJxAAACcQJxAAAAAAAYABgAGAAYAnEAfQJxAH0AAAAAACgAXABnALwBDQEYAXABewHeAhkCYQJsApsC2gMGAxEDHAMt"
    "AzgDXwOpA9ED6gP1BAAEEQQcBE8EnQS7BPkFNAU/BXQFfwWKBZsFpgYUBh8GXQaPBsQHAwdgB6wHzQf7CAYIEQgiCC0IbwizCRkJ"
    "XAloCckJ/QoICl0Krgq5CxELHAt/C7oMAgwNDDwMewynDLIMvQzODNkNAA1KDXINiw28DfIOJA5VDogO1g70DzIPbQ94D60PuBAN"
    "EF4QaRDXEOIRIBFSEYcRxhIjEm8StBLVEwMTDhNbE6UTsBPyFDYUnBTfFOsU/BVdFYwVvhXzFhkWfxbKFvUXMhdxF7sYCRhJGGcY"
    "tBj7GV0ZvBojGiMaIxojGjsaYBqJGr4a7BsUGz0bfhu/G9ob9RxBHI0cwxzuHQkdEh0tHUgdZx2aHcseHh5xHpQetx7cHv8fIx9H"
    "H2AfeR/AIAcgNCBhIIUgniDqIV4hoCH6IlAipSLgIxkjNSNdI40kBSSBJNklCiVNJXQlsSXnJhImWiakJtInCSc7J2onnifhKAwo"
    "NChcKIsowijiKQ8pFykfKScpLyk3KT8pRwAAAAIAfQAAAfQCcQADAAcAKkAnAAAAAwIAA2cAAgEBAlcAAgIBXwQBAQIBTwAABwYF"
    "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"
    "fQLufX19fX19AAACAH0C7gJxA+gAAwAPADmxBmREQC4DAQEGAQACAQBnBAECBQUCVwQBAgIFXwgHAgUCBU8EBAQPBA8REREREhEQ"
    "CQcdK7EGAEQBIzUzBTUzNTMVMxUjNSMVAnF9ff4MfX19fX0Da336fX19fX19AAEAfQH0AnECcQADACaxBmREQBsAAAEBAFcAAAAB"
    "XwIBAQABTwAAAAMAAxEDBxcrsQYARBM1IRV9AfQB9H19AAACAIf/BgGBAAAAAwAHADexBmREQCwAAAQBAQIAAWcAAgMDAlcAAgID"
    "XwUBAwIDTwQEAAAEBwQHBgUAAwADEQYHFyuxBgBEBTUzFQc1MxUBBH36fX19fX19ff//AH0DawH0A+gABgDMAAD//wB9Au4BdwPo"
    "AAYAzQAA//8AfQLuAXcD6AAGAM4AAP//AH0C7gH0A+gABgDPAAD//wB9Au4CcQPoAAYA0AAA//8AfQH0AnECcQAGANEAAP//AIf/"
    "BgGBAAAABgDSAAAAAAAAAAgAZgADAAEECQAAALIBLgADAAEECQABABQBGgADAAEECQACAA4BDAADAAEECQADADoA0gADAAEECQAE"
    "ACQArgADAAEECQAFAFQAWgADAAEECQAGACQANgADAAEECQAOADYAAABoAHQAdABwAHMAOgAvAC8AcwBjAHIAaQBwAHQAcwAuAHMA"
    "aQBsAC4AbwByAGcALwBPAEYATABTAGkAbABrAHMAYwByAGUAZQBuAC0AUgBlAGcAdQBsAGEAcgBWAGUAcgBzAGkAbwBuACAAMQAu"
    "ADAAMAAxADsAIAB0AHQAZgBhAHUAdABvAGgAaQBuAHQAIAAoAHYAMQAuADgALgA0AC4ANwAtADUAZAA1AGIAKQBTAGkAbABrAHMA"
    "YwByAGUAZQBuACAAUgBlAGcAdQBsAGEAcgAxAC4AMAAwADEAOwBVAE4ASwBOADsAUwBpAGwAawBzAGMAcgBlAGUAbgAtAFIAZQBn"
    "AHUAbABhAHIAUgBlAGcAdQBsAGEAcgBTAGkAbABrAHMAYwByAGUAZQBuAEMAbwBwAHkAcgBpAGcAaAB0ACAAMgAwADAAMQAgAFQA"
    "aABlACAAUwBpAGwAawBzAGMAcgBlAGUAbgAgAFAAcgBvAGoAZQBjAHQAIABBAHUAdABoAG8AcgBzACAAKABoAHQAdABwAHMAOgAv"
    "AC8AZwBpAHQAaAB1AGIALgBjAG8AbQAvAGcAbwBvAGcAbABlAGYAbwBuAHQAcwAvAHMAaQBsAGsAcwBjAHIAZQBlAG4AKQAAAAIA"
    "AAAAAAD/nAAyAAAAAAAAAAAAAAAAAAAAAAAAAAAA2gAAACQAyQDHAGIArQBjAK4AkAAlACYAZAAnAOkAKABlAMgAygDLACkAKgAr"
    "ACwAzADNAM4AzwAtAC4ALwAwADEAZgAyANAA0QBnANMAkQCvALAAMwDtADQANQA2ADcAOADUANUAaADWADkAOgA7ADwA6wA9AEQA"
    "aQBrAGwAagBuAG0AoABFAEYAbwBHAOoASABwAHIAcwBxAEkASgBLAEwAdAB2AHcAdQBNAE4ATwBQAFEAeABSAHkAewB8AHoAoQB9"
    "ALEAUwDuAFQAVQBWAIkAVwBYAH4AgACBAH8AWQBaAFsAXADsALoAXQCdAJ4AEwAUABUAFgAXABgAGQAaABsAHAECAQMBBAD0APUA"
    "9gEFAAMBBgARAA8AHQAeAKsABACjACIAogDDAIcADQAGABIAPwAQAQcAsgCzAEIACwAMAF4AYAA+AEAAxADFALQAtQC2ALcAqQCq"
    "AL4AvwAFAAoAIwAJAIgAhgCLAIoAjACDAF8A6ACEAL0ABwEIAIUAlgAOAPAAuAAgACEAHwCTAGEApABBAQkACAEKAQsBDAENAQ4B"
    "DwEQAI4AQwCNANgA2QDaAN4HdW5pMDBCOQd1bmkwMEIyB3VuaTAwQjMCQ1IHdW5pMDBBMAd1bmkwMEFEBEV1cm8HdW5pMDBCNQd1"
    "bmkwMzA4CWdyYXZlY29tYglhY3V0ZWNvbWIHdW5pMDMwMgl0aWxkZWNvbWIHdW5pMDMwNAd1bmkwMzI3AAEAAf//AA8AAQACAA4A"
    "AAAAAAAAPAACAAcAAQAFAAEABwAMAAEADgA+AAEAQABPAAEAVQB0AAEAzADOAAMA0ADRAAMAAQABAAAACAABAAUAzADNAM4A0ADR"
    "AAAAAQAAAAoAIAA6AAFERkxUAAgABAAAAAD//wACAAAAAQACbWFyawAUbWttawAOAAAAAQABAAAAAQAAAAIAKAAGAAYAEAABAAoA"
    "AAABAagBqAABAXoADAAFAToBLgEuAYQBigAEAAAAAQAIAAEBiAE4AAEBWgAMAGsBagFkAWoBagFkAWQBJgFkAWQBZAFkASABZAEa"
    "ARQBDgEaAWQBZAEIAQ4BAgD8APYBZAFkARoBJgDwAOoBagFkAWQA5AFkASYBZAEmAWQBGgFkAWQBagEaAWoBZAFkAOQBZAEmASYB"
    "JgDeASYBIAFqAWQBagDkAWQBZAEmAWQBagFqAWQBJgEgAWQBGgEUAQ4BGgFkAWQBZAEaASYA8ADqAWoBZAFqAWoBZAEmAWQBJgFk"
    "ARoBZAFkAWQBZAEaAWoBZAFqAWoBZAEmASYBJgDeAOoA2AEaAAEBtgNrAAEBtgJxAAEBdwNrAAEB9APoAAEB9AJxAAEAfQPoAAEA"
    "vANrAAEAvAPoAAEAvAJxAAEA+gPoAAEBOQNrAAEBOQPoAAEBOQJxAAEBtgPoAAIABQABAAUAAAAHAAwABQAOAD4ACwBAAE4APABV"
    "AHQASwAFAAAAKAAAACIAAAAiAAAAHAAAABYAAQF3A+gAAQF3AnEAAQD6AnEAAQE5Au4AAQAFAMwAzQDOANAA0QABAAAACgAeACwA"
    "AURGTFQACAAEAAAAAP//AAEAAAABZnJhYwAIAAAAAQAAAAEABAAEAAAAAQAIAAEALAACABYACgABAAQAhgADAJcAewACAA4ABgCE"
    "AAMAlwB5AIUAAwCXAHsAAQACAHgAeg=="
)

_FONT_SILK_BOLD = (
    "AAEAAAARAQAABAAQR0RFRgVfBBIAAGmIAAAAUkdQT1Oa3Zu0AABp3AAAAfpHU1VCrlKwxgAAa9gAAABsT1MvMl2+BHoAAAGYAAAA"
    "YGNtYXDYZtqcAAAFYAAAAj5jdnQgCEITQAAAFmAAAABSZnBnbWIu/XwAAAegAAAODGdhc3AAAAAQAABpgAAAAAhnbHlmXQiJhAAA"
    "GGwAAEyWaGVhZAtPfXAAAAEcAAAANmhoZWEH8QJSAAABVAAAACRobXR4pEZkEgAAAfgAAANobG9jYQGi7hgAABa0AAABtm1heHAC"
    "EQ6ZAAABeAAAACBuYW1lLTFK8wAAZQQAAAIucG9zdDur4vEAAGc0AAACTHByZXB0Qd0yAAAVrAAAALIAAQAAAAEAQmTbU6tfDzz1"
    "CA8D6AAAAAC5GPoPAAAAAOcqP07/Bv8GBOID6AABAAYAAgAAAAAAAAABAAAEBv8GAAAFX/8G/RIE4gABAAAAAAAAAAAAAAAAAAAA"
    "2gABAAAA2gAgAAQAHAAEAAIAJABOAI0AAAB7DgwAAgABAAQDOgK8AAUAAAKKAlgAAABLAooCWAAAAV4AMgD6AAAAAAAAAAAAAAAA"
    "gAAAZwAAAAoAAAAAAAAAAFVOS04AoAANISIEBv8GAAAD6AD6AAAAAQAAAAAB9AK8AAAAIAADAnEAfQNrAH0DawB9A2sAfQNrAH0D"
    "awB9A2sAfQNrAH0D6AB9A2sAfQNrAH0DawB9A2sAfQPoAH0C7gB9Au4AfQLuAH0C7gB9Au4AfQLuAH0DawB9A2sAfQH0AH0B9AB9"
    "AfQAAAH0AAAB9AAAA2sAfQNrAH0C7gB9A+gAfQPoAH0D6AB9A2sAfQNrAH0DawB9A2sAfQNrAH0D6AB9A2sAfQPoAH0DawB9Au4A"
    "fQNrAH0DawB9A2sAfQLuAH0DawB9A2sAfQNrAH0DawB9A2sAfQPoAH0D6AB9A+gAfQPoAH0D6AB9Au4AfQNrAH0DawB9A2sAfQNr"
    "AH0DawB9A2sAfQNrAH0D6AB9A2sAfQNrAH0DawB9A2sAfQPoAH0C7gB9Au4AfQLuAH0C7gB9Au4AfQLuAH0DawB9A2sAfQH0AH0B"
    "9AB9AfQAAAH0AAAB9AAAA2sAfQNrAH0C7gB9A+gAfQPoAH0D6AB9A2sAfQNrAH0DawB9A2sAfQNrAH0D6AB9A2sAfQPoAH0DawB9"
    "Au4AfQNrAH0DawB9A2sAfQNrAH0C7gB9A2sAfQNrAH0DawB9A2sAfQNrAH0D6AB9A+gAfQPoAH0D6AB9A+gAfQPoAH0C7gB9Au4A"
    "fQLuAH0DawB9Au4AfQNrAH0DawB9A2sAfQNrAH0DawB9A2sAfQNrAH0DawB9AnEAfQLuAH0C7gB9BOIAfQTiAH0FXwB9AfQAAAH0"
    "AAAB9AAAAfQAfQJxAH0B9AB9AnEAfQPoAH0B9AB9AfQAfQNrAH0DawB9AfQAfQH0AH0D6AB9A+gAfQLuAH0C7gB9Au4AfQAA/wYD"
    "awB9BGUAfQNrAH0CcQB9AnEAfQLuAH0C7gB9AnEAfQJxAH0CcQB9Au4AfQLuAH0C7gB9AfQAfQH0AH0D6AB9A+gAfQJxAH0CcQB9"
    "Au4AfQH0AH0D6AB9A2sAfQPoAH0DawB9BOIAfQTiAH0E4gB9Au4AfQH0AH0B9AB9Au4AfQPoAH0DawB9A+gAfQNrAH0D6AB9A+gA"
    "fQLuAH0C7gB9Au4AfQLuAH0C7gB9A+gAfQNrAH0DawB9Au4AfQNrAH0D6AB9AAAAOwAAAH0AAAB9AAAAfQAAAH0AAAB9AAAAfQLu"
    "ADsCcQB9AnEAfQLuAH0DawB9A2sAfQJxAH0AAAACAAAAAwAAABQAAwABAAAAFAAEAioAAAAoACAABAAIAA0ALwA5AH4A/wFTAsYC"
    "3AMBAwQDCCAUIBogHiAiICYgOiCsISL//wAAAA0AIAAwADoAoAFSAsYC3AMAAwMDCCATIBggHCAiICYgOSCsISL//wB6AAAARwAA"
    "AAAAAP4Q/fv9zf3N/cTgiAAAAADgcuBo4HPgEd+UAAEAAAAmAAAAQgDKAYgAAAAAAAAAAAAAAAABfgGCAAAAAAAAAAAAAAAAAIgA"
    "jwCuAJYAvADLALEArwCeAJ8AlQDAAIsAmQCKAJcAjACNAMUAwwDEAJEAsAABAAkACgAMAA4AEwAUABUAFgAbABwAHQAeAB8AIQAp"
    "ACsALAAtAC4ALwA0ADUANgA3ADkAogCYAKMAyQCdANQAOgBCAEMARQBHAEwATQBOAE8AVABVAFYAVwBYAFoAYgBkAGUAZgBoAGkA"
    "bgBvAHAAcQB0AKAAuAChAMcAiQCQALoAvgC7AL8AuQCzANMAtAB1AKoAyACaALUA2AC3AMYAggCDANUAygCyAJMA2QCBAHYAqwCF"
    "AIQAhgCSAAUAAgADAAcABAAGAAgACwASAA8AEAARABoAFwAYABkADQAgACUAIgAjACcAJADBACYAMwAwADEAMgA4ACoAZwA+ADsA"
    "PABAAD0APwBBAEQASwBIAEkASgBTAFAAUQBSAEYAWQBeAFsAXABgAF0AwgBfAG0AagBrAGwAcgBjAHMAKABhAKgAqQCkAKYApwCl"
    "AACwACwgsABVWEVZICBLuAAOUUuwBlNaWLA0G7AoWWBmIIpVWLACJWG5CAAIAGNjI2IbISGwAFmwAEMjRLIAAQBDYEItsAEssCBg"
    "Zi2wAiwjISMhLbADLCBkswMUFQBCQ7ATQyBgYEKxAhRDQrElA0OwAkNUeCCwDCOwAkNDYWSwBFB4sgICAkNgQrAhZRwhsAJDQ7IO"
    "FQFCHCCwAkMjQrITARNDYEIjsABQWGVZshYBAkNgQi2wBCywAyuwFUNYIyEjIbAWQ0MjsABQWGVZGyBkILDAULAEJlqyKAENQ0Vj"
    "RbAGRVghsAMlWVJbWCEjIRuKWCCwUFBYIbBAWRsgsDhQWCGwOFlZILEBDUNFY0VhZLAoUFghsQENQ0VjRSCwMFBYIbAwWRsgsMBQ"
    "WCBmIIqKYSCwClBYYBsgsCBQWCGwCmAbILA2UFghsDZgG2BZWVkbsAIlsAxDY7AAUliwAEuwClBYIbAMQxtLsB5QWCGwHkthuBAA"
    "Y7AMQ2O4BQBiWVlkYVmwAStZWSOwAFBYZVlZIGSwFkMjQlktsAUsIEUgsAQlYWQgsAdDUFiwByNCsAgjQhshIVmwAWAtsAYsIyEj"
    "IbADKyBksQdiQiCwCCNCsAZFWBuxAQ1DRWOxAQ1DsAJgRWOwBSohILAIQyCKIIqwASuxMAUlsAQmUVhgUBthUllYI1khWSCwQFNY"
    "sAErGyGwQFkjsABQWGVZLbAHLLAJQyuyAAIAQ2BCLbAILLAJI0IjILAAI0JhsAJiZrABY7ABYLAHKi2wCSwgIEUgsA5DY7gEAGIg"
    "sABQWLBAYFlmsAFjYESwAWAtsAossgkOAENFQiohsgABAENgQi2wCyywAEMjRLIAAQBDYEItsAwsICBFILABKyOwAEOwBCVgIEWK"
    "I2EgZCCwIFBYIbAAG7AwUFiwIBuwQFlZI7AAUFhlWbADJSNhRESwAWAtsA0sICBFILABKyOwAEOwBCVgIEWKI2EgZLAkUFiwABuw"
    "QFkjsABQWGVZsAMlI2FERLABYC2wDiwgsAAjQrMNDAADRVBYIRsjIVkqIS2wDyyxAgJFsGRhRC2wECywAWAgILAPQ0qwAFBYILAP"
    "I0JZsBBDSrAAUlggsBAjQlktsBEsILAQYmawAWMguAQAY4ojYbARQ2AgimAgsBEjQiMtsBIsS1RYsQRkRFkksA1lI3gtsBMsS1FY"
    "S1NYsQRkRFkbIVkksBNlI3gtsBQssQASQ1VYsRISQ7ABYUKwEStZsABDsAIlQrEPAiVCsRACJUKwARYjILADJVBYsQEAQ2CwBCVC"
    "ioogiiNhsBAqISOwAWEgiiNhsBAqIRuxAQBDYLACJUKwAiVhsBAqIVmwD0NHsBBDR2CwAmIgsABQWLBAYFlmsAFjILAOQ2O4BABi"
    "ILAAUFiwQGBZZrABY2CxAAATI0SwAUOwAD6yAQEBQ2BCLbAVLACxAAJFVFiwEiNCIEWwDiNCsA0jsAJgQiBgtxgYAQARABMAQkJC"
    "imAgsBQjQrABYbEUCCuwiysbIlktsBYssQAVKy2wFyyxARUrLbAYLLECFSstsBkssQMVKy2wGiyxBBUrLbAbLLEFFSstsBwssQYV"
    "Ky2wHSyxBxUrLbAeLLEIFSstsB8ssQkVKy2wKywjILAQYmawAWOwBmBLVFgjIC6wAV0bISFZLbAsLCMgsBBiZrABY7AWYEtUWCMg"
    "LrABcRshIVktsC0sIyCwEGJmsAFjsCZgS1RYIyAusAFyGyEhWS2wICwAsA8rsQACRVRYsBIjQiBFsA4jQrANI7ACYEIgYLABYbUY"
    "GAEAEQBCQopgsRQIK7CLKxsiWS2wISyxACArLbAiLLEBICstsCMssQIgKy2wJCyxAyArLbAlLLEEICstsCYssQUgKy2wJyyxBiAr"
    "LbAoLLEHICstsCkssQggKy2wKiyxCSArLbAuLCA8sAFgLbAvLCBgsBhgIEMjsAFgQ7ACJWGwAWCwLiohLbAwLLAvK7AvKi2wMSwg"
    "IEcgILAOQ2O4BABiILAAUFiwQGBZZrABY2AjYTgjIIpVWCBHICCwDkNjuAQAYiCwAFBYsEBgWWawAWNgI2E4GyFZLbAyLACxAAJF"
    "VFixDgZFQrABFrAxKrEFARVFWDBZGyJZLbAzLACwDyuxAAJFVFixDgZFQrABFrAxKrEFARVFWDBZGyJZLbA0LCA1sAFgLbA1LACx"
    "DgZFQrABRWO4BABiILAAUFiwQGBZZrABY7ABK7AOQ2O4BABiILAAUFiwQGBZZrABY7ABK7AAFrQAAAAAAEQ+IzixNAEVKiEtsDYs"
    "IDwgRyCwDkNjuAQAYiCwAFBYsEBgWWawAWNgsABDYTgtsDcsLhc8LbA4LCA8IEcgsA5DY7gEAGIgsABQWLBAYFlmsAFjYLAAQ2Gw"
    "AUNjOC2wOSyxAgAWJSAuIEewACNCsAIlSYqKRyNHI2EgWGIbIVmwASNCsjgBARUUKi2wOiywABawFyNCsAQlsAQlRyNHI2GxDABC"
    "sAtDK2WKLiMgIDyKOC2wOyywABawFyNCsAQlsAQlIC5HI0cjYSCwBiNCsQwAQrALQysgsGBQWCCwQFFYswQgBSAbswQmBRpZQkIj"
    "ILAKQyCKI0cjRyNhI0ZgsAZDsAJiILAAUFiwQGBZZrABY2AgsAErIIqKYSCwBENgZCOwBUNhZFBYsARDYRuwBUNgWbADJbACYiCw"
    "AFBYsEBgWWawAWNhIyAgsAQmI0ZhOBsjsApDRrACJbAKQ0cjRyNhYCCwBkOwAmIgsABQWLBAYFlmsAFjYCMgsAErI7AGQ2CwASuw"
    "BSVhsAUlsAJiILAAUFiwQGBZZrABY7AEJmEgsAQlYGQjsAMlYGRQWCEbIyFZIyAgsAQmI0ZhOFktsDwssAAWsBcjQiAgILAFJiAu"
    "RyNHI2EjPDgtsD0ssAAWsBcjQiCwCiNCICAgRiNHsAErI2E4LbA+LLAAFrAXI0KwAyWwAiVHI0cjYbAAVFguIDwjIRuwAiWwAiVH"
    "I0cjYSCwBSWwBCVHI0cjYbAGJbAFJUmwAiVhuQgACABjYyMgWGIbIVljuAQAYiCwAFBYsEBgWWawAWNgIy4jICA8ijgjIVktsD8s"
    "sAAWsBcjQiCwCkMgLkcjRyNhIGCwIGBmsAJiILAAUFiwQGBZZrABYyMgIDyKOC2wQCwjIC5GsAIlRrAXQ1hQG1JZWCA8WS6xMAEU"
    "Ky2wQSwjIC5GsAIlRrAXQ1hSG1BZWCA8WS6xMAEUKy2wQiwjIC5GsAIlRrAXQ1hQG1JZWCA8WSMgLkawAiVGsBdDWFIbUFlYIDxZ"
    "LrEwARQrLbBDLLA6KyMgLkawAiVGsBdDWFAbUllYIDxZLrEwARQrLbBELLA7K4ogIDywBiNCijgjIC5GsAIlRrAXQ1hQG1JZWCA8"
    "WS6xMAEUK7AGQy6wMCstsEUssAAWsAQlsAQmICAgRiNHYbAMI0IuRyNHI2GwC0MrIyA8IC4jOLEwARQrLbBGLLEKBCVCsAAWsAQl"
    "sAQlIC5HI0cjYSCwBiNCsQwAQrALQysgsGBQWCCwQFFYswQgBSAbswQmBRpZQkIjIEewBkOwAmIgsABQWLBAYFlmsAFjYCCwASsg"
    "iophILAEQ2BkI7AFQ2FkUFiwBENhG7AFQ2BZsAMlsAJiILAAUFiwQGBZZrABY2GwAiVGYTgjIDwjOBshICBGI0ewASsjYTghWbEw"
    "ARQrLbBHLLEAOisusTABFCstsEgssQA7KyEjICA8sAYjQiM4sTABFCuwBkMusDArLbBJLLAAFSBHsAAjQrIAAQEVFBMusDYqLbBK"
    "LLAAFSBHsAAjQrIAAQEVFBMusDYqLbBLLLEAARQTsDcqLbBMLLA5Ki2wTSywABZFIyAuIEaKI2E4sTABFCstsE4ssAojQrBNKy2w"
    "TyyyAABGKy2wUCyyAAFGKy2wUSyyAQBGKy2wUiyyAQFGKy2wUyyyAABHKy2wVCyyAAFHKy2wVSyyAQBHKy2wViyyAQFHKy2wVyyz"
    "AAAAQystsFgsswABAEMrLbBZLLMBAABDKy2wWiyzAQEAQystsFssswAAAUMrLbBcLLMAAQFDKy2wXSyzAQABQystsF4sswEBAUMr"
    "LbBfLLIAAEUrLbBgLLIAAUUrLbBhLLIBAEUrLbBiLLIBAUUrLbBjLLIAAEgrLbBkLLIAAUgrLbBlLLIBAEgrLbBmLLIBAUgrLbBn"
    "LLMAAABEKy2waCyzAAEARCstsGksswEAAEQrLbBqLLMBAQBEKy2wayyzAAABRCstsGwsswABAUQrLbBtLLMBAAFEKy2wbiyzAQEB"
    "RCstsG8ssQA8Ky6xMAEUKy2wcCyxADwrsEArLbBxLLEAPCuwQSstsHIssAAWsQA8K7BCKy2wcyyxATwrsEArLbB0LLEBPCuwQSst"
    "sHUssAAWsQE8K7BCKy2wdiyxAD0rLrEwARQrLbB3LLEAPSuwQCstsHgssQA9K7BBKy2weSyxAD0rsEIrLbB6LLEBPSuwQCstsHss"
    "sQE9K7BBKy2wfCyxAT0rsEIrLbB9LLEAPisusTABFCstsH4ssQA+K7BAKy2wfyyxAD4rsEErLbCALLEAPiuwQistsIEssQE+K7BA"
    "Ky2wgiyxAT4rsEErLbCDLLEBPiuwQistsIQssQA/Ky6xMAEUKy2whSyxAD8rsEArLbCGLLEAPyuwQSstsIcssQA/K7BCKy2wiCyx"
    "AT8rsEArLbCJLLEBPyuwQSstsIossQE/K7BCKy2wiyyyCwADRVBYsAYbsgQCA0VYIyEbIVlZQiuwCGWwAyRQeLEFARVFWDBZLQBL"
    "uADIUlixAQGOWbABuQgACABjcLEAB0KzAB4CACqxAAdCtSMEEQoCCiqxAAdCtScCGwYCCiqxAAlCuwkABIAAAgALKrEAC0K7AEAA"
    "wAACAAsquQADAABEsSQBiFFYsECIWLkAAwBkRLEoAYhRWLgIAIhYuQADAABEWRuxJwGIUVi6CIAAAQRAiGNUWLkAAwAARFlZWVlZ"
    "tSUCFQYCDiq4Af+FsASNsQIARLMFZAYAREQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPoA+gF3AF0AggCCAXcAXQJxAAAC"
    "cQJxAAAAAAJxAAACcQJxAAAAAAAYABgAGAAYAnEAfQJxAH0AAAAAACgAXQBoAL4AzwDaATABOwFtAagB4wHuAh0CXQKJApQCnwLd"
    "AugDDwNEA2wDhQOQA5sDxAPPA/oEVARyBJoEywTWBQsFFgVsBX0FiAXNBdgGDQY/BmkGqAbiByAHQQdrB3YHwQfSB90IDwg4CKEI"
    "6Qj1CU4JgwmOCeQJ9QoAClYKYQqTCs4LCQsUC0MLgwuvC7oLxQwDDA4MNQxqDJIMqwzcDSANSQ16DaUN/w4dDkUOdg6BDrYOwQ8X"
    "DygPMw94D4MPuA/qEBQQUxCNEMsREREyEVwRZxGyEcMRzhIAEikSkhLaEuYTRhOfE8MT6hQfFEUUnRTbFQIVOBV4FcgWFxZXFnYW"
    "xBcLF30XthglGCUYJRglGD0YXhiHGLkY0Rj5GSIZXRmbGbYZ0RoHGj4agxrJGuQa7RsIGyMbQhtuG5ob7hxCHGUciBypHMEc2hzz"
    "HQwdJR1nHakd1h4DHhweNR5qHt4fBR9fH7UgCCBDIHEgjSC1IOUhDiGCIdEiAiJeIoUisCLnIxIjciPTJAEkKiRcJJYkxSTtJRgl"
    "QCVoJaIlyyXrJhMmGyYjJismMyY7JkMmSwAAAAIAfQAAAfQCcQADAAcAKkAnAAAAAwIAA2cAAgEBAlcAAgIBXwQBAQIBTwAABwYF"
    "BAADAAMRBQYXKzMRIREnMxEjfQF3+n19AnH9j30BdwAAAgB9AAAC7gJxAAsADwA1QDIABgAEAwYEZwAHBwFfAAEBFU0CAQAAA18I"
    "BQIDAxYDTgAADw4NDAALAAsREREREQkHGyszETM1IRUzESM1IxURMzUjfX0Bd336fX19AfR9ff4M+voBd3gA//8AfQAAAu4D6AIm"
    "AAEAAAAGAM59AAADAH0AAALuA+gACwAXABsAWEBVAAEABAABBGcCAQAOBQIDBwADZwAMAAoJDApnAA0NB18ABwcVTQgBBgYJXw8L"
    "AgkJFglODAwAABsaGRgMFwwXFhUUExIREA8ODQALAAsRERERERAHGysTNTM1IRUzFSM1IxUDETM1IRUzESM1IxURMzUjfX0Bd336"
    "ffp9AXd9+n19fQLufX19fX19/RIB9H19/gz6+gF3eAD//wB9AAAC7gNrAiYAAQAAAQYAzEKDAAmxAgK4/4OwNSsA//8AfQAAAu4D"
    "6AImAAEAAAAGAM19AAADAH0AAALuA+gACwAXABsAWEBVAwEBBAEAAgEAZwACDgEFBwIFZwAMAAoJDApnAA0NB18ABwcVTQgBBgYJ"
    "Xw8LAgkJFglODAwAABsaGRgMFwwXFhUUExIREA8ODQALAAsRERERERAHGysTNSM1MxUzNTMVIxUBETM1IRUzESM1IxURMzUj+n36"
    "ffp9/gx9AXd9+n19fQLufX19fX19/RIB9H19/gz6+gF3eAD//wB9AAAC7gPoAiYAAQAAAAYA0AAAAAEAfQAAA2sCcQANADhANQAA"
    "AgMCAAOAAAMABAUDBGcAAgIBXwABARVNAAUFBmAHAQYGFgZOAAAADQANERERERERCAccKzMRMzUhFSMVMxUjFTMVfX0Ccfr6+voB"
    "9H19fX19fQADAH0AAALuAnEABwALAA8APUA6AAQABwIEB2cAAQACBgECZwAFBQBfAAAAFU0ABgYDXwgBAwMWA04AAA8ODQwLCgkI"
    "AAcABxEREQkHGSszESEVMxEjFQMzNSMRMzUjfQH0fX36eHh4eAJxff6JfQF3ff6JfQAAAQB9AAAC7gJxABMAPUA6AwEBAAQHAQRn"
    "AAcIAQAGBwBnAAUFAl8AAgIVTQAGBglfCgEJCRYJTgAAABMAExEREREREREREQsHHyszNSMRMzUhFTMVIzUjETM1MxUjFfp9fQF3"
    "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"
    "AXd9ffp9fQLufX19fX19/RJ9AXd9ff6JfYIBbf//AH0AAALuA2sCJgAhAAABBgDMQoMACbECArj/g7A1KwD//wB9AAAC7gPoAiYA"
    "IQAAAAYAzX0AAAMAfQAAA2sCcQALAA8AEwBIQEUABgEJAQYJgAAJAAEJAH4DAQEEAQAIAQBnAAcHAl8AAgIVTQAICAVgCgEFBRYF"
    "TgAAExIREA8ODQwACwALERERERELBxsrMzUjETM1IRUzESMVATM1IxMzNSP6fX0B9H19/ol9fX19fX0Bd319/ol9AXd9/ol9//8A"
    "fQAAAu4D6AImACEAAAAGANAAAAABAH0AAANrAnEADwA5QDYABAAFAAQFZwABAAAGAQBnAAMDAl8AAgIVTQAGBgdfCAEHBxYHTgAA"
    "AA8ADxEREREREREJBx0rMzUjETM1IRUjFTMVIxUzFfp9fQJx+vr6+n0Bd319fX19fQAAAgB9AAAC7gJxAAkADQA1QDIAAQACBQEC"
    "ZwAFAAMEBQNnAAYGAF8AAAAVTQcBBAQWBE4AAA0MCwoACQAJEREREQgHGiszESEVMxUjFSMVETM1I30B9H19+nh4AnF9fX36AXd9"
    "AAEAfQAAAnECcQALAC1AKgACAAMEAgNnAAEABAUBBGcAAAAVTQYBBQUWBU4AAAALAAsREREREQcHGyszETMVMxUzFSMVIxV9+n19"
    "fX0CcX19fX19AAIAff+DAu4CcQAPABMAPkA7BAECBQEBCAIBZwAGCgEHBgdjAAkJA18AAwMVTQAICABfAAAAFgBOAAATEhEQAA8A"
    "DxERERERERELBx0rBTUjNSMRMzUhFTMRIxUzFSUzESMB9Pp9fQF3fX19/ol9fX19fQF3fX3+iX19+gF3AAIAfQAAAu4CcQANABEA"
    "PUA6AAEAAgcBAmcABwAFAwcFZwAICABfAAAAFU0AAwMEXwkGAgQEFgROAAAREA8OAA0ADREREREREQoHHCszESEVMxUjFTMVIzUj"
    "FREzNSN9AfR9fX36fX19AnF9ffp9fX0Bd30AAQB9AAAC7gJxABMAQ0BAAAMAAgYDAmcABgABBwYBZwAHAAgABwhnAAUFBF8ABAQV"
    "TQAAAAlfCgEJCRYJTgAAABMAExEREREREREREQsHHyszNSE1IzUjNTM1IRUhFTMVMxUjFX0Bd/p9fQH0/on6fX19fX19fX19fX19"
    "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"
    "TwQEAAAEBwQHBgUAAwADEQYHFyuxBgBEATUzFSE1MxUBsvr9j/oDa319fX0AAQB9Au4B9APoAAcAMLEGZERAJQABAAACAQBnAAID"
    "AwJXAAICA18EAQMCA08AAAAHAAcREREFBxkrsQYARBM1IzUzFTMV+n36fQLufX19fQABAH0C7gH0A+gABwAwsQZkREAlAAEAAgAB"
    "AmcAAAMDAFcAAAADXwQBAwADTwAAAAcABxEREQUHGSuxBgBEEzUzNTMVIxV9ffp9Au59fX19AAEAfQLuAnED6AAHAFOxBmRES7AK"
    "UFhAGQABAAABcAIBAAMDAFcCAQAAA2AEAQMAA1AbQBgAAQABhQIBAAMDAFcCAQAAA2AEAQMAA1BZQAwAAAAHAAcREREFBxkrsQYA"
    "RBM1MzUzFTMVfX36fQLufX19fQAAAQB9Au4C7gPoAAcAMLEGZERAJQABAAIAAQJnAAADAwBXAAAAA18EAQMAA08AAAAHAAcREREF"
    "BxkrsQYARBM1MzUhFSMVfX0B9H0C7n19fX0AAAEAfQH0Au4CcQADACaxBmREQBsAAAEBAFcAAAABXwIBAQABTwAAAAMAAxEDBxcr"
    "sQYARBM1IRV9AnEB9H19AAABAH3/BgH0AAAABwAwsQZkREAlAAEAAgABAmcAAAMDAFcAAAADXwQBAwADTwAAAAcABxEREQUHGSux"
    "BgBEFzUzNTMVIxV9ffp9+n19fX0A//8AOwNrAqwD6AAGAMwAAP//AH0C7gH0A+gABgDNAAD//wB9Au4B9APoAAYAzgAA//8AfQLu"
    "AnED6AAGAM8AAP//AH0C7gLuA+gABgDQAAD//wB9AfQC7gJxAAYA0QAA//8Aff8GAfQAAAAGANIAAAAAAAAACABmAAMAAQQJAAAA"
    "sgEWAAMAAQQJAAEAFAECAAMAAQQJAAIACAD6AAMAAQQJAAMANADGAAMAAQQJAAQAHgCoAAMAAQQJAAUAVABUAAMAAQQJAAYAHgA2"
    "AAMAAQQJAA4ANgAAAGgAdAB0AHAAcwA6AC8ALwBzAGMAcgBpAHAAdABzAC4AcwBpAGwALgBvAHIAZwAvAE8ARgBMAFMAaQBsAGsA"
    "cwBjAHIAZQBlAG4ALQBCAG8AbABkAFYAZQByAHMAaQBvAG4AIAAxAC4AMAAwADEAOwAgAHQAdABmAGEAdQB0AG8AaABpAG4AdAAg"
    "ACgAdgAxAC4AOAAuADQALgA3AC0ANQBkADUAYgApAFMAaQBsAGsAcwBjAHIAZQBlAG4AIABCAG8AbABkADEALgAwADAAMQA7AFUA"
    "TgBLAE4AOwBTAGkAbABrAHMAYwByAGUAZQBuAC0AQgBvAGwAZABCAG8AbABkAFMAaQBsAGsAcwBjAHIAZQBlAG4AQwBvAHAAeQBy"
    "AGkAZwBoAHQAIAAyADAAMAAxACAAVABoAGUAIABTAGkAbABrAHMAYwByAGUAZQBuACAAUAByAG8AagBlAGMAdAAgAEEAdQB0AGgA"
    "bwByAHMAIAAoAGgAdAB0AHAAcwA6AC8ALwBnAGkAdABoAHUAYgAuAGMAbwBtAC8AZwBvAG8AZwBsAGUAZgBvAG4AdABzAC8AcwBp"
    "AGwAawBzAGMAcgBlAGUAbgApAAAAAgAAAAAAAP+cADIAAAAAAAAAAAAAAAAAAAAAAAAAAADaAAAAJADJAMcAYgCtAGMArgCQACUA"
    "JgBkACcA6QAoAGUAyADKAMsAKQAqACsALADMAM0AzgDPAC0ALgAvADAAMQBmADIA0ADRAGcA0wCRAK8AsAAzAO0ANAA1ADYANwA4"
    "ANQA1QBoANYAOQA6ADsAPADrAD0ARABpAGsAbABqAG4AbQCgAEUARgBvAEcA6gBIAHAAcgBzAHEASQBKAEsATAB0AHYAdwB1AE0A"
    "TgBPAFAAUQB4AFIAeQB7AHwAegChAH0AsQBTAO4AVABVAFYAiQBXAFgAfgCAAIEAfwBZAFoAWwBcAOwAugBdAJ0AngATABQAFQAW"
    "ABcAGAAZABoAGwAcAQIBAwEEAPQA9QD2AQUAAwEGABEADwAdAB4AqwAEAKMAIgCiAMMAhwANAAYAEgA/ABABBwCyALMAQgALAAwA"
    "XgBgAD4AQADEAMUAtAC1ALYAtwCpAKoAvgC/AAUACgAjAAkAiACGAIsAigCMAIMAXwDoAIQAvQAHAQgAhQCWAA4A8AC4ACAAIQAf"
    "AJMAYQCkAEEBCQAIAQoBCwEMAQ0BDgEPARAAjgBDAI0A2ADZANoA3gd1bmkwMEI5B3VuaTAwQjIHdW5pMDBCMwJDUgd1bmkwMEEw"
    "B3VuaTAwQUQERXVybwd1bmkwMEI1B3VuaTAzMDgJZ3JhdmVjb21iCWFjdXRlY29tYgd1bmkwMzAyCXRpbGRlY29tYgd1bmkwMzA0"
    "B3VuaTAzMjcAAQAB//8ADwABAAIADgAAAAAAAAA8AAIABwABAAUAAQAHAAwAAQAOAD4AAQBAAE8AAQBVAHQAAQDMAM4AAwDQANEA"
    "AwABAAEAAAAIAAEABQDMAM0AzgDQANEAAAABAAAACgAgADoAAURGTFQACAAEAAAAAP//AAIAAAABAAJtYXJrABRta21rAA4AAAAB"
    "AAEAAAABAAAAAgAoAAYABgAQAAEACgAAAAEBogGiAAEBdAAMAAUBLgEiASIBfgGEAAQAAAABAAgAAQGCATIAAQFUAAwAawFkAV4B"
    "ZAEgAV4BXgEaAV4BXgFeAV4BFAFeAQ4BCAECAQ4BXgFeAPwBAgD2APwA8AFeAV4BDgEaAOoA5AFkAV4BZAEgAV4BGgFeARoBXgEO"
    "AV4BXgFkAQ4BZAFeAWQBIAFeARoBGgEaAN4A5AEUAWQBXgFkASABXgFeARoBXgFeAV4BXgEaARQBXgEOARQBAgEOAV4BXgFeAQ4B"
    "GgDqAOQBZAFeAWQBIAFeARoBXgEaAV4BDgFeAV4BXgFeAQ4BZAFeAWQBIAFeARoBGgEaAN4A2ADeAQ4AAQI0A+gAAQH0AnEAAQIz"
    "A+gAAQIzAnEAAQC8A+gAAQD6A+gAAQD6AnEAAQE5A+gAAQF6A2sAAQF3A+gAAQF3AnEAAQH0A+gAAQG5A2sAAgAFAAEABQAAAAcA"
    "DAAFAA4APgALAEAATgA8AFUAdABLAAUAAAAoAAAAIgAAACIAAAAcAAAAFgABAbYD6AABAbYCcQABATkCcQABAXQC7gABAAUAzADN"
    "AM4A0ADRAAAAAQAAAAoAHgAsAAFERkxUAAgABAAAAAD//wABAAAAAWZyYWMACAAAAAEAAAABAAQABAAAAAEACAABACwAAgAWAAoA"
    "AQAEAIYAAwCXAHsAAgAOAAYAhAADAJcAeQCFAAMAlwB7AAEAAgB4AHo="
)



if __name__ == "__main__":
    show_installer()
