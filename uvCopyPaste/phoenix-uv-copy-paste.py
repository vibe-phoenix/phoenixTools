"""
Phoenix UV Transfer Tool  —  retro pixel UI
===========================================
Transfer UVs from one source object to multiple targets using Maya's
transferAttributes node, with full control over sample space,
search method, UV sets, flip and mirror options.

UI: frameless retro panel (same look as Phoenix Autosave / Set Tools).
    Double-click the UV TRANSFER title for THEMES:
      Presets · Customize colours · Shelf icon (clipboard, 6 colour versions)
    Esc or x closes. Drag the window by any empty area.
    Theme + icon choice persist via optionVars.

The shelf icon picker updates any shelf button that runs this tool
(the Phoenix UV smart loader is detected automatically).

Usage:
    Run this script in Maya's Script Editor (Python tab),
    or via the Phoenix UV smart loader shelf button.
PySide2 / PySide6 compatible.
"""

import os
import sys
import copy
import json
import base64
import zlib

import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as omui

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from shiboken6 import wrapInstance
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    from shiboken2 import wrapInstance


# ── Helpers ──────────────────────────────────────────────────────────────────

def toast(msg, pos="topCenter"):
    """Viewport-style feedback via in-view message."""
    cmds.inViewMessage(amg=f"<hl>{msg}</hl>", pos=pos, fade=True, fst=700, fad=400)


# ── Constants ─────────────────────────────────────────────────────────────────

WINDOW_TITLE  = "Phoenix UV Transfer"
WINDOW_OBJECT = "PhoenixUVTransferWin"

# sampleSpace flag values for transferAttributes
SAMPLE_SPACE_MAP = {
    "World":     0,
    "Local":     1,
    "UV":        3,
    "Component": 4,
    "Topology":  5,
}

# searchMethod flag values
SEARCH_METHOD_MAP = {
    "Closest Along Normal": 0,
    "Closest Point":        3,
}

_SHELF_TAG   = "phxUVTransfer"
_SHELF_MATCH = ("phoenix-uv-copy-paste", "Phoenix UV Copy Paste", "load_phoenix_uv", "PhoenixUVTransfer")
_OPT_THEME   = "phoenixUVTransfer_theme_json"
_OPT_ICON    = "phoenixUVTransfer_shelf_icon"
_WIDTH       = 320


# =========================================================================
# THEMES
# =========================================================================
_THEMES_BASE = [
    {"n": "Olive LCD",         "bg": "#9ba383", "fld": "#9ba383", "ink": "#1f2613", "mid": "#5d6648", "sh": "#7c8466", "tsh": "#7c8466", "acc": "#1f2613", "acci": "#9ba383", "nb": "#2b2f26", "nt": "#e8ecdf", "nd": "#6f7563"},
    {"n": "Cream navy orange", "bg": "#e8d8c9", "fld": "#e8d8c9", "ink": "#1b1b1b", "mid": "#4b607f", "sh": "#4b607f", "tsh": "#f3701e", "acc": "#f3701e", "acci": "#1b1b1b", "nb": "#2a2a2a", "nt": "#e8d8c9", "nd": "#6e6862"},
    {"n": "Teal red sand",     "bg": "#3e6868", "fld": "#c1ab85", "ink": "#161616", "mid": "#203a3a", "sh": "#161616", "tsh": "#c94e44", "acc": "#c94e44", "acci": "#161616", "nb": "#1e2424", "nt": "#c1ab85", "nd": "#5e5644"},
    {"n": "Red and gold",      "bg": "#121312", "fld": "#121312", "ink": "#a67d43", "mid": "#5e4626", "sh": "#ad0013", "tsh": "#ad0013", "acc": "#ad0013", "acci": "#e3c08a", "nb": "#2a2622", "nt": "#e3c08a", "nd": "#6a5a44"},
    {"n": "Red butter black",  "bg": "#c0301e", "fld": "#f6da9d", "ink": "#000000", "mid": "#6a160c", "sh": "#000000", "tsh": "#f6da9d", "acc": "#000000", "acci": "#f6da9d", "nb": "#1c1c1c", "nt": "#f6da9d", "nd": "#6e6450"},
    {"n": "Sky mist slate",    "bg": "#7ba7af", "fld": "#e2e4d2", "ink": "#1b2321", "mid": "#455957", "sh": "#455957", "tsh": "#6e8584", "acc": "#455957", "acci": "#e2e4d2", "nb": "#26302f", "nt": "#e2e4d2", "nd": "#66706c"},
]
_THEME_KEYS = [("bg", "PANEL"), ("fld", "FIELD"), ("ink", "INK"), ("mid", "MUTED"), ("sh", "SHADOW"),
               ("tsh", "TITLE SH"), ("acc", "ACCENT"), ("acci", "ACC TEXT"), ("nb", "INPUT"),
               ("nt", "INPUT TXT"), ("nd", "DIMMED")]
_THEME_STATE = None


def _theme_state():
    global _THEME_STATE
    if _THEME_STATE is None:
        themes = copy.deepcopy(_THEMES_BASE)
        cur = 0
        try:
            if cmds.optionVar(exists=_OPT_THEME):
                data = json.loads(cmds.optionVar(q=_OPT_THEME) or "{}")
                for i, t in enumerate(data.get("themes", [])[:len(themes)]):
                    for k, _ in _THEME_KEYS:
                        v = t.get(k)
                        if isinstance(v, str) and QtGui.QColor(v).isValid():
                            themes[i][k] = v
                cur = max(0, min(len(themes) - 1, int(data.get("cur", 0))))
        except Exception:
            pass
        _THEME_STATE = {"themes": themes, "cur": cur}
    return _THEME_STATE


def _save_theme_state():
    st = _theme_state()
    try:
        slim = [{k: t[k] for k, _ in _THEME_KEYS} for t in st["themes"]]
        cmds.optionVar(sv=(_OPT_THEME, json.dumps({"cur": st["cur"], "themes": slim})))
    except Exception:
        pass


def _T():
    st = _theme_state()
    return st["themes"][st["cur"]]


def _c(key, theme=None):
    return QtGui.QColor((theme or _T())[key])


# =========================================================================
# FONT + PAINT HELPERS
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


def _sel_frame(p, rect, col):
    p.fillRect(QtCore.QRect(rect.x(), rect.y(), rect.width(), 3), col)
    p.fillRect(QtCore.QRect(rect.x(), rect.bottom() - 2, rect.width(), 3), col)
    p.fillRect(QtCore.QRect(rect.x(), rect.y(), 3, rect.height()), col)
    p.fillRect(QtCore.QRect(rect.right() - 2, rect.y(), 3, rect.height()), col)


def _maya_main_window():
    try:
        ptr = omui.MQtUtil.mainWindow()
        return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None
    except Exception:
        return None


# =========================================================================
# WIDGETS
# =========================================================================
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

    def setText(self, text):
        if text != self._text:
            self._text = text
            self.updateGeometry(); self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setFont(self._font)
        p.setPen(_c(self._key))
        p.drawText(self.rect(), self._align, self._text)
        p.end()


class _PixelButton(QtWidgets.QAbstractButton):
    """Same look as the DEFAULTS button in Phoenix Autosave."""
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
        if hot != self._hot:
            self._hot = hot; self.update()

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def _frame(self, p):
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
        return QtCore.QRect(off, off, w, h), fg

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        r, fg = self._frame(p)
        p.setFont(self._font); p.setPen(fg)
        p.drawText(r, QtCore.Qt.AlignCenter, self.text())
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
        ink, fld, bg = _c("ink"), _c("fld"), _c("bg")
        if self._hover:
            p.fillRect(self.rect(), ink); glyph = bg
        else:
            _pbox(p, 0, 0, 22, 20, fld, ink, 3); glyph = ink
        for r, row in enumerate(self._X):
            for c, ch in enumerate(row):
                if ch == "1":
                    p.fillRect(QtCore.QRect(6 + c * 2, 5 + r * 2, 2, 2), glyph)
        p.end()


class _BigTitle(QtWidgets.QWidget):
    doubleClicked = QtCore.Signal()

    def __init__(self, text, parent=None):
        super(_BigTitle, self).__init__(parent)
        self._text = text
        self._font = _px(26, True, 2.0)
        self.setFixedHeight(QtGui.QFontMetrics(self._font).height() + 5)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Double-click for themes")

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setFont(self._font)
        r = self.rect().adjusted(0, 0, -3, -3)
        p.setPen(_c("tsh")); p.drawText(r.translated(3, 3), QtCore.Qt.AlignCenter, self._text)
        p.setPen(_c("ink")); p.drawText(r, QtCore.Qt.AlignCenter, self._text)
        p.end()


class _ValueBox(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(_ValueBox, self).__init__(parent)
        self._text = ""
        self._font = _px(12, False, 1.0)
        self.setFixedHeight(34)

    def setText(self, t):
        t = (t or "").upper()
        if t != self._text:
            self._text = t; self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        _pbox(p, 0, 0, self.width() - 3, 31, _c("fld"), _c("ink"), 3, 3, _c("sh"))
        p.setFont(self._font); p.setPen(_c("ink"))
        p.drawText(QtCore.QRect(0, 0, self.width() - 3, 31), QtCore.Qt.AlignCenter, self._text)
        p.end()


class _HeaderBar(QtWidgets.QWidget):
    def __init__(self, text, close_cb, parent=None):
        super(_HeaderBar, self).__init__(parent)
        self.setFixedHeight(36)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(14, 6, 8, 9)
        lab = _Label(text, 12)
        lab.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        h.addWidget(lab); h.addStretch(1)
        x = _WinBtn(); x.setToolTip("Back")
        x.clicked.connect(lambda *_: close_cb())
        h.addWidget(x)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.fillRect(QtCore.QRect(0, self.height() - 3, self.width(), 3), _c("ink"))
        p.end()


class _ThemeCard(QtWidgets.QWidget):
    clicked = QtCore.Signal(int)

    def __init__(self, idx, parent=None):
        super(_ThemeCard, self).__init__(parent)
        self._idx = idx
        self.setFixedHeight(48)
        self.setMinimumWidth(40)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(_THEMES_BASE[idx]["n"])

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self._idx)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        st = _theme_state()
        t = st["themes"][self._idx]
        m = 5
        w, h = self.width() - 2 * m - 3, self.height() - 2 * m - 3
        if st["cur"] == self._idx:
            _sel_frame(p, QtCore.QRect(0, 0, self.width() - 3, self.height() - 3), _c("acc"))
        _pbox(p, m, m, w, h, QtGui.QColor(t["bg"]), QtGui.QColor(t["ink"]), 3, 3, _c("sh"))
        f = _px(10)
        fm = QtGui.QFontMetrics(f)
        p.setFont(f); p.setPen(QtGui.QColor(t["ink"]))
        p.drawText(QtCore.QRect(m + 6, m + 5, w - 12, 12), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fm.elidedText(t["n"].upper(), QtCore.Qt.ElideRight, max(10, w - 12)))
        sx, sy, sw = m + 6, m + h - 6 - 10, w - 12
        p.fillRect(QtCore.QRect(sx, sy, sw, 10), QtGui.QColor(t["ink"]))
        iw = (sw - 4) / 4.0
        for i, k in enumerate(("fld", "acc", "nb", "tsh")):
            x0 = int(sx + 2 + i * iw); x1 = int(sx + 2 + (i + 1) * iw)
            p.fillRect(QtCore.QRect(x0, sy + 2, x1 - x0, 6), QtGui.QColor(t[k]))
        p.end()


class _Swatch(QtWidgets.QWidget):
    clicked = QtCore.Signal(str)

    def __init__(self, key, label, parent=None):
        super(_Swatch, self).__init__(parent)
        self._key, self._label = key, label
        self.setFixedHeight(46)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Change {} colour".format(label.lower()))

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self._key)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        _pbox(p, 0, 0, self.width() - 2, 26, _c(self._key), _c("ink"), 3, 2, _c("sh"))
        p.setFont(_px(10)); p.setPen(_c("ink"))
        p.drawText(QtCore.QRect(-8, 32, self.width() + 16, 14), QtCore.Qt.AlignCenter, self._label)
        p.end()


class _SwatchGrid(QtWidgets.QWidget):
    """4 per row, last row centred."""
    COLS, GX, GY, H = 4, 8, 8, 46

    def __init__(self, swatches, parent=None):
        super(_SwatchGrid, self).__init__(parent)
        self._items = swatches
        for s in swatches:
            s.setParent(self)
        rows = (len(swatches) + self.COLS - 1) // self.COLS
        self.setFixedHeight(rows * self.H + (rows - 1) * self.GY)

    def resizeEvent(self, e):
        W = self.width()
        iw = int((W - (self.COLS - 1) * self.GX) / self.COLS)
        n = len(self._items)
        for i, s in enumerate(self._items):
            r, c = divmod(i, self.COLS)
            in_row = min(self.COLS, n - r * self.COLS)
            row_w = in_row * iw + (in_row - 1) * self.GX
            x0 = (W - row_w) // 2
            s.setGeometry(x0 + c * (iw + self.GX), r * (self.H + self.GY), iw, self.H)
        super(_SwatchGrid, self).resizeEvent(e)



class _Badge(QtWidgets.QWidget):
    """Dark pill showing NONE / SET."""
    def __init__(self, parent=None):
        super(_Badge, self).__init__(parent)
        self._on = False
        self._font = _px(11, False, 1.0)
        self.setFixedSize(64, 34)

    def setOn(self, on):
        if on != self._on:
            self._on = on; self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen); p.setBrush(_c("nb"))
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 7, 7)
        p.setFont(self._font); p.setPen(_c("nt") if self._on else _c("nd"))
        p.drawText(self.rect(), QtCore.Qt.AlignCenter, "SET" if self._on else "NONE")
        p.end()


class _SourceBox(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(_SourceBox, self).__init__(parent)
        self._text = "NONE SELECTED"
        self._set = False
        self._font = _px(11, False, 0.5)
        self.setFixedHeight(34)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)

    def setSource(self, name):
        self._set = bool(name)
        self._text = name.upper() if name else "NONE SELECTED"
        self.setToolTip(name or "")
        self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        _pbox(p, 0, 0, self.width() - 3, 31, _c("fld"), _c("ink"), 3, 3, _c("sh"))
        p.setFont(self._font); p.setPen(_c("ink") if self._set else _c("mid"))
        fm = QtGui.QFontMetrics(self._font)
        r = QtCore.QRect(8, 0, self.width() - 19, 31)
        p.drawText(r, QtCore.Qt.AlignCenter, fm.elidedText(self._text, QtCore.Qt.ElideMiddle, r.width()))
        p.end()


class _Combo(QtWidgets.QComboBox):
    """Pixel-box dropdown. Items are shown uppercase; the original text is the item data."""
    def __init__(self, items, parent=None):
        super(_Combo, self).__init__(parent)
        for it in items:
            label, data = it if isinstance(it, tuple) else (it.upper(), it)
            self.addItem(label, data)
        self._hover = False
        self.setFixedHeight(33)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setFont(_px(11, False, 0.5))
        self.restyle()

    def value(self):
        return self.currentData() or self.currentText().title()

    def restyle(self):
        t = _T()
        self.view().setFont(_px(11, False, 0.5))
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
        p.drawText(QtCore.QRect(10, 0, w - 34, h), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   self.currentText())
        # pixel down-arrow
        ax, ay = w - 20, h // 2 - 2
        for i, wd in enumerate((8, 6, 4, 2)):
            p.fillRect(QtCore.QRect(ax + i, ay + i, wd, 1), _c("ink"))
        p.end()


class _Check(QtWidgets.QWidget):
    toggled = QtCore.Signal(bool)

    def __init__(self, text, parent=None):
        super(_Check, self).__init__(parent)
        self._text = text
        self._on = False
        self._font = _px(12, False, 0.5)
        fm = QtGui.QFontMetrics(self._font)
        self.setFixedSize(20 + 8 + _text_w(fm, text) + 4, 22)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def isChecked(self):
        return self._on

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.rect().contains(e.pos()):
            self._on = not self._on
            self.update()
            self.toggled.emit(self._on)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        col = _c("ink") if self._on else _c("mid")
        _pbox(p, 0, 1, 20, 20, _c("fld"), col, 3)
        if self._on:
            p.fillRect(QtCore.QRect(6, 7, 8, 8), _c("acc"))
        p.setFont(self._font); p.setPen(col)
        p.drawText(QtCore.QRect(28, 0, self.width() - 28, self.height()),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self._text)
        p.end()


class _Rule(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(_Rule, self).__init__(parent)
        self.setFixedHeight(3)

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.fillRect(self.rect(), _c("ink")); p.end()


# =========================================================================
# SHELF ICON — pixel clipboard with UV checker, one colour version per theme
# =========================================================================
_ICON_MAP = [
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


def _icon_pixels(theme):
    """16x16 colours; empty cells are filled with the theme's panel colour."""
    col = {"#": theme["ink"], "l": theme["nt"], "a": theme["acc"], "m": theme["mid"]}
    bg = theme["bg"]
    return [[col.get(ch, bg) for ch in row.ljust(16)[:16]] for row in _ICON_MAP]


def _icon_image(theme, scale=2):
    img = QtGui.QImage(16 * scale, 16 * scale, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    for y, row in enumerate(_icon_pixels(theme)):
        for x, c in enumerate(row):
            if c:
                p.fillRect(x * scale, y * scale, scale, scale, QtGui.QColor(c))
    p.end()
    return img


def _icon_choice():
    try:
        if cmds.optionVar(exists=_OPT_ICON):
            return max(0, min(len(_THEMES_BASE) - 1, int(cmds.optionVar(q=_OPT_ICON))))
    except Exception:
        pass
    return 0


def _write_icon(idx):
    theme = _theme_state()["themes"][idx]
    sig = "".join(theme[k] for k in ("ink", "nt", "acc", "mid", "bg")).replace("#", "") + "|".join(_ICON_MAP)
    tag = format(zlib.crc32(sig.encode("ascii")) & 0xFFFFFF, "06x")
    folder = cmds.internalVar(userBitmapsDir=True)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "phxUVTransfer_{}_{}.png".format(idx, tag)).replace("\\", "/")
    if not os.path.exists(path):
        _icon_image(theme, 2).save(path, "PNG")
    for f in os.listdir(folder):
        if f.startswith("phxUVTransfer_") and f.endswith(".png"):
            full = os.path.join(folder, f).replace("\\", "/")
            if full != path:
                try: os.remove(full)
                except Exception: pass
    return path



def _shelf_top():
    try:
        return mel.eval("$_phx_uv_tmp = $gShelfTopLevel")
    except Exception:
        return None


def _shelf_buttons():
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
                if tag == _SHELF_TAG or any(k in cmd for k in _SHELF_MATCH):
                    out.append(path)
            except Exception:
                pass
    return out




class _IconTile(QtWidgets.QWidget):
    clicked = QtCore.Signal(int)

    def __init__(self, idx, parent=None):
        super(_IconTile, self).__init__(parent)
        self._idx = idx
        self.setFixedHeight(44)
        self.setMinimumWidth(36)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(_THEMES_BASE[idx]["n"])

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self._idx)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        px = 2
        size = 16 * px
        x0 = (self.width() - size) // 2
        y0 = (self.height() - size) // 2
        if _icon_choice() == self._idx:
            _sel_frame(p, QtCore.QRect(x0 - 5, y0 - 5, size + 10, size + 10), _c("acc"))
        for y, row in enumerate(_icon_pixels(_theme_state()["themes"][self._idx])):
            for x, c in enumerate(row):
                if c:
                    p.fillRect(QtCore.QRect(x0 + x * px, y0 + y * px, px, px), QtGui.QColor(c))
        p.end()



def set_shelf_icon(idx=None):
    """Pick the shelf icon (0-5, theme order) and update every UV Transfer shelf button
    (buttons running the Phoenix UV smart loader are detected by their command)."""
    if idx is None:
        idx = _icon_choice()
    idx = max(0, min(len(_THEMES_BASE) - 1, int(idx)))
    try: cmds.optionVar(iv=(_OPT_ICON, idx))
    except Exception: pass
    path = _write_icon(idx)
    for b in _shelf_buttons():
        try: cmds.shelfButton(b, e=True, image=path, image1=path, docTag=_SHELF_TAG)
        except Exception: pass
    return path


# =========================================================================
# WINDOW
# =========================================================================
class PhoenixUVTransfer(QtWidgets.QWidget):
    RADIUS = 14
    BORDER = 3

    def __init__(self, parent=None):
        super(PhoenixUVTransfer, self).__init__(parent or maya_main_window())
        _ensure_font()
        _theme_state()
        self.setObjectName(WINDOW_OBJECT)
        self.setWindowTitle(WINDOW_TITLE)
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setFixedWidth(_WIDTH)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        self._source_object = None
        self._press = None
        self._origin = None
        self._moving = False

        lay = QtWidgets.QVBoxLayout(self)
        b = self.BORDER
        lay.setContentsMargins(b, b, b, b)
        lay.setSpacing(0)
        self.page_main = self._build_main()
        self.page_theme = self._build_themes()
        lay.addWidget(self.page_main)
        lay.addWidget(self.page_theme)
        self.page_theme.hide()
        self.adjustSize()

    # ── frame / drag / keys ──────────────────────────────────────────────
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
            if self.page_theme.isVisible():
                self._close_themes()
            else:
                self.close()
            return
        super(PhoenixUVTransfer, self).keyPressEvent(e)

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

    # ── main page ────────────────────────────────────────────────────────
    def _build_main(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(14, 8, 14, 14)
        v.setSpacing(0)

        top = QtWidgets.QHBoxLayout()
        top.addStretch(1)
        x = _WinBtn(); x.setToolTip("Close")
        x.clicked.connect(lambda *_: self.close())
        top.addWidget(x)
        v.addLayout(top)

        title = _BigTitle("UV TRANSFER")
        title.doubleClicked.connect(self._open_themes)
        v.addWidget(title)
        v.addSpacing(2)
        v.addWidget(_Label("MADE BY VIBEPHOENIX", 10, "mid", QtCore.Qt.AlignCenter))
        v.addSpacing(12)

        # Source row
        v.addWidget(_Label("Source")); v.addSpacing(5)
        src = QtWidgets.QHBoxLayout(); src.setSpacing(8)
        self.lbl_source = _SourceBox()
        self.badge = _Badge()
        src.addWidget(self.lbl_source, 1)
        src.addWidget(self.badge, 0, QtCore.Qt.AlignTop)
        v.addLayout(src); v.addSpacing(10)

        # Copy / Paste
        btns = QtWidgets.QGridLayout(); btns.setHorizontalSpacing(10)
        self.btn_copy = _PixelButton("COPY SOURCE")
        self.btn_copy.setToolTip("Select one object, then click to set as UV source")
        self.btn_copy.clicked.connect(lambda *_: self._on_copy())
        self.btn_paste = _PixelButton("PASTE UVS")
        self.btn_paste.setToolTip("Select one or more targets, then click to transfer UVs")
        self.btn_paste.setEnabled(False)
        self.btn_paste.clicked.connect(lambda *_: self._on_paste())
        btns.addWidget(self.btn_copy, 0, 0); btns.addWidget(self.btn_paste, 0, 1)
        btns.setColumnStretch(0, 1); btns.setColumnStretch(1, 1)
        v.addLayout(btns)

        v.addSpacing(12); v.addWidget(_Rule()); v.addSpacing(12)

        # Options
        v.addWidget(_Label("Transfer options")); v.addSpacing(5)
        self._cmb_sample_space = self._combo_row(v, "SAMPLE", "Sample space",
                                                 ["Topology", "World", "Local", "UV", "Component"])
        v.addSpacing(5)
        self._cmb_search = self._combo_row(v, "SEARCH", "Search method",
                                           [("CLOSEST POINT", "Closest Point"),
                                            ("ALONG NORMAL", "Closest Along Normal")])
        v.addSpacing(5)
        self._cmb_uvsets = self._combo_row(v, "UV SETS", "UV sets", ["Current", "All"])

        v.addSpacing(12); v.addWidget(_Rule()); v.addSpacing(12)

        # Flip / Mirror
        tog = QtWidgets.QHBoxLayout(); tog.setSpacing(28)
        self.chk_flip = _Check("FLIP UVS")
        self.chk_mirror = _Check("MIRROR UVS")
        tog.addStretch(1)
        tog.addWidget(self.chk_flip); tog.addWidget(self.chk_mirror)
        tog.addStretch(1)
        v.addLayout(tog)
        return page

    def _combo_row(self, v, label, tip, items):
        row = QtWidgets.QHBoxLayout(); row.setSpacing(8)
        lbl = _Label(label, 11)
        lbl.setFixedWidth(78)
        lbl.setToolTip(tip)
        cmb = _Combo(items)
        cmb.setToolTip(tip)
        row.addWidget(lbl); row.addWidget(cmb, 1)
        v.addLayout(row)
        return cmb

    # ── themes page ──────────────────────────────────────────────────────
    def _build_themes(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(_HeaderBar("THEMES", self._close_themes))
        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(14, 10, 14, 14)
        body.setSpacing(0)
        v.addLayout(body)

        body.addWidget(_Label("Presets")); body.addSpacing(2)
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2); grid.setVerticalSpacing(2)
        for i in range(len(_THEMES_BASE)):
            c = _ThemeCard(i)
            c.clicked.connect(self._pick_theme)
            grid.addWidget(c, i // 3, i % 3)
            grid.setColumnStretch(i % 3, 1)
        body.addLayout(grid); body.addSpacing(8)

        self.cust_lbl = _Label("Customize")
        body.addWidget(self.cust_lbl); body.addSpacing(5)
        sw = []
        for k, lab in _THEME_KEYS:
            s = _Swatch(k, lab)
            s.clicked.connect(self._edit_color)
            sw.append(s)
        body.addWidget(_SwatchGrid(sw)); body.addSpacing(10)

        body.addWidget(_Label("Shelf icon")); body.addSpacing(2)
        icons = QtWidgets.QGridLayout(); icons.setHorizontalSpacing(0)
        self.icon_tiles = []
        for i in range(len(_THEMES_BASE)):
            t = _IconTile(i)
            t.clicked.connect(self._pick_icon)
            icons.addWidget(t, 0, i); icons.setColumnStretch(i, 1)
            self.icon_tiles.append(t)
        body.addLayout(icons); body.addSpacing(10)

        row = QtWidgets.QGridLayout(); row.setHorizontalSpacing(10)
        b_reset = _PixelButton("RESET THEME")
        b_done = _PixelButton("DONE"); b_done.setHot(True)
        b_reset.setToolTip("Put the current theme back to its original colours")
        b_reset.clicked.connect(lambda *_: self._reset_theme())
        b_done.clicked.connect(lambda *_: self._close_themes())
        row.addWidget(b_reset, 0, 0); row.addWidget(b_done, 0, 1)
        row.setColumnStretch(0, 1); row.setColumnStretch(1, 1)
        body.addLayout(row)
        self._update_cust_label()
        return page

    def _fit(self):
        QtCore.QTimer.singleShot(0, self.adjustSize)

    def _open_themes(self):
        self._update_cust_label()
        self.page_main.hide()
        self.page_theme.show()
        self._fit()

    def _close_themes(self):
        _save_theme_state()
        try:
            if _shelf_buttons():
                set_shelf_icon(_icon_choice())
        except Exception:
            pass
        self.page_theme.hide()
        self.page_main.show()
        self._fit()

    def _update_cust_label(self):
        self.cust_lbl.setText("Customize / " + _T()["n"].upper())

    def _repaint_all(self):
        for c in (self._cmb_sample_space, self._cmb_search, self._cmb_uvsets):
            c.restyle()
        for w in self.findChildren(QtWidgets.QWidget):
            w.update()
        self.update()

    def _pick_theme(self, idx):
        _theme_state()["cur"] = idx
        _save_theme_state()
        self._update_cust_label()
        self._repaint_all()

    def _reset_theme(self):
        st = _theme_state()
        st["themes"][st["cur"]] = copy.deepcopy(_THEMES_BASE[st["cur"]])
        _save_theme_state()
        self._repaint_all()

    def _edit_color(self, key):
        t = _T()
        col = QtWidgets.QColorDialog.getColor(QtGui.QColor(t[key]), self,
                                              dict(_THEME_KEYS)[key].title() + " colour")
        if col.isValid():
            t[key] = col.name()
            _save_theme_state()
            self._repaint_all()

    def _pick_icon(self, idx):
        try:
            set_shelf_icon(idx)
        except Exception as e:
            cmds.warning("[PhoenixUVTransfer] shelf icon error: {}".format(e))
        for t in self.icon_tiles:
            t.update()

    # ── Logic (unchanged behaviour) ──────────────────────────────────────
    def _on_copy(self):
        sel = cmds.ls(selection=True, long=True)
        if not sel:
            toast("⚠  Select a mesh first", "topCenter")
            return
        if len(sel) > 1:
            toast("⚠  Select only ONE source object", "topCenter")
            return

        # Validate it's a mesh
        shapes = cmds.listRelatives(sel[0], shapes=True, fullPath=True) or []
        if not any(cmds.nodeType(s) == "mesh" for s in shapes):
            toast("⚠  Selected object has no mesh shape", "topCenter")
            return

        self._source_object = sel[0]
        short = sel[0].split("|")[-1]
        self.lbl_source.setSource(short)
        self.badge.setOn(True)
        self.btn_paste.setEnabled(True)
        self.btn_paste.update()
        toast(f"✔  Source locked → {short}", "topCenter")

    def _on_paste(self):
        if not self._source_object:
            toast("⚠  No source set — use Copy Source first", "topCenter")
            return

        sel = cmds.ls(selection=True, long=True)
        if not sel:
            toast("⚠  Select one or more target meshes", "topCenter")
            return

        # Filter out the source itself
        targets = [o for o in sel if o != self._source_object]
        if not targets:
            toast("⚠  No valid targets (source == selection?)", "topCenter")
            return

        # ── Read options ──
        ss_text  = self._cmb_sample_space.value()
        sm_text  = self._cmb_search.value()
        uvs_text = self._cmb_uvsets.value()
        flip     = self.chk_flip.isChecked()
        mirror   = self.chk_mirror.isChecked()

        sample_space  = SAMPLE_SPACE_MAP.get(ss_text, 5)
        search_method = SEARCH_METHOD_MAP.get(sm_text, 3)
        uv_flag       = 2 if uvs_text == "All" else 1  # 1=current, 2=all

        # ── Transfer ──
        success_count = 0
        fail_count    = 0

        for target in targets:
            try:
                cmds.transferAttributes(
                    self._source_object,
                    target,
                    transferPositions=0,
                    transferNormals=0,
                    transferUVs=uv_flag,
                    transferColors=0,
                    sampleSpace=sample_space,
                    searchMethod=search_method,
                    flipUVs=1 if flip else 0,
                )
                # Auto-delete history after bake so the node doesn't persist
                cmds.bakePartialHistory(target, prePostDeformers=True)

                # Mirror UVs post-transfer via polyEditUV (scale U by -1 around 0.5)
                if mirror:
                    all_uvs = cmds.polyListComponentConversion(target, toUV=True)
                    all_uvs = cmds.filterExpand(all_uvs, selectionMask=35, expand=True)
                    if all_uvs:
                        cmds.polyEditUV(
                            all_uvs,
                            pivotU=0.5, pivotV=0.5,
                            scaleU=-1.0, scaleV=1.0,
                            relative=False
                        )

                success_count += 1
            except Exception as e:
                fail_count += 1
                print(f"[PhoenixUVTransfer] Failed on {target}: {e}")

        # ── Feedback ──
        if fail_count == 0:
            toast(f"✔  UVs transferred to {success_count} object(s)", "topCenter")
        else:
            toast(
                f"⚠  {success_count} ok / {fail_count} failed — check Script Editor",
                "topCenter"
            )


# ── Launch ────────────────────────────────────────────────────────────────────

def maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None


def launch():
    # Close existing window if open
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w.objectName() == WINDOW_OBJECT:
                w.close(); w.deleteLater()
        except Exception:
            pass

    win = PhoenixUVTransfer()
    win.show()
    win.raise_()
    win.activateWindow()
    win.setFocus()
    return win


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



launch()
