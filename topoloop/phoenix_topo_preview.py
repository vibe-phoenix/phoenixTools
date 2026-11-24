"""
phoenix_topo_preview.py  --  Phoenix Topo Loop Preview (Maya 2024-2027)

Hold Shift in a polygon component mode to preview loops (blue) and rings
(lavender) from the hovered component, or from the last selected one when
nothing is hovered. Release Shift and the preview is gone.

    import phoenix_topo_preview as ptp
    ptp.toggle()          # enable / disable
    ptp.show_settings()   # Phoenix retro settings panel (double-click the title: themes)
    ptp.unload_plugin()   # disable + unload the VP2 plugin

How it works
  * A QApplication event filter only *observes* Shift press/release (never
    consumes events), so Maya's Shift-select, hotkeys and navigation are
    untouched. The preview never calls select / never edits the mesh.
  * While Shift is held a 16 ms timer polls the cursor; the hovered
    component comes from a ray cast (MFnMesh.closestIntersection with a
    cached uniform-grid accelerator) against the meshes you could pick
    components on (hilited objects + objects with selected components).
  * Topology is queried lazily per component (phoenix_topo_core.CachedTopology)
    and memoised per mesh; caches drop on topology change (MPolyMessage),
    accelerators on geometry change (dirty-plug callback).
  * Drawing: one hidden helper locator (plugin phoenixTopoPreview.py) whose
    MPxDrawOverride draws MUIDrawManager lines with depth priority bias.
    It is created with MDagModifier outside the undo queue, is not
    selectable, hidden in the Outliner, never written to file, and does not
    leave the scene marked modified.
"""
import os
import re
import sys
import time
import traceback

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import maya.api.OpenMayaRender as omr

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    import shiboken6 as shiboken
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets
    import shiboken2 as shiboken

import phoenix_topo_core as core

VERSION = "1.2.0"
PLUGIN_BASENAME = "phoenixTopoPreview"
PLUGIN_FILE = PLUGIN_BASENAME + ".py"
NODE_TYPE = "phoenixTopoPreviewLocator"
HELPER_NAME = "phoenixTopoPreview_helper"
_INSTANCE_ATTR = "_phoenix_topo_preview_controller"
_OPT = "phxTopoPreview_"
_SETTINGS_WIN = "phxTopoPreviewSettingsWin"

VERTEX, EDGE, FACE = core.VERTEX, core.EDGE, core.FACE


# =========================================================================
# Qt compatibility (PySide2 / PySide6)
# =========================================================================
def _qenum(owner, group, name):
    v = getattr(owner, name, None)
    if v is None:
        v = getattr(getattr(owner, group), name)
    return v


def _as_int(v):
    try:
        return int(v)
    except TypeError:
        return int(v.value)


_EV_KEYPRESS = _qenum(QtCore.QEvent, "Type", "KeyPress")
_EV_KEYRELEASE = _qenum(QtCore.QEvent, "Type", "KeyRelease")
_EV_APPSTATE = _qenum(QtCore.QEvent, "Type", "ApplicationStateChange")
_KEY_SHIFT = _as_int(_qenum(QtCore.Qt, "Key", "Key_Shift"))
_MOD_SHIFT = _as_int(_qenum(QtCore.Qt, "KeyboardModifier", "ShiftModifier"))
_MOD_OTHER = (_as_int(_qenum(QtCore.Qt, "KeyboardModifier", "ControlModifier"))
              | _as_int(_qenum(QtCore.Qt, "KeyboardModifier", "AltModifier"))
              | _as_int(_qenum(QtCore.Qt, "KeyboardModifier", "MetaModifier")))
_APP_ACTIVE = _as_int(_qenum(QtCore.Qt, "ApplicationState", "ApplicationActive"))
_TEXT_WIDGETS = (QtWidgets.QLineEdit, QtWidgets.QTextEdit,
                 QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)


def _ptr(w):
    try:
        return int(shiboken.getCppPointer(w)[0])
    except Exception:
        return id(w)


# =========================================================================
# Settings (optionVars)
# =========================================================================
XRAY_MODES = ("Auto (viewport X-Ray)", "Auto + Camera-based selection off",
              "Always on top", "Never (depth tested)")

# Colours: one global pair (+ selection colour) used by every mode, unless a
# mode's "custom colours" override is ticked.
#   primary   = loop (edge mode) / direction 1 (vertex, face mode)
#   secondary = ring (edge mode) / direction 2 (vertex, face mode)
BLUE = (0.15, 0.50, 1.00)
LAVENDER = (0.74, 0.62, 1.00)
DEFAULTS = {
    "colorA": BLUE,
    "colorB": LAVENDER,
    "selectionColor": (1.00, 0.78, 0.15),
    "vertexCustom": 0, "vertexColorA": BLUE, "vertexColorB": LAVENDER,
    "edgeCustom": 0, "edgeColorA": BLUE, "edgeColorB": LAVENDER,
    "faceCustom": 0, "faceColorA": BLUE, "faceColorB": LAVENDER,
    "lineWidth": 3.0,          # one thickness for loop + ring lines
    "pointSize": 9.0,
    "faceFillOpacity": 0.35,
    "showRings": 1,            # edge mode ring
    "ringSpine": 0,
    "vertexPathLines": 1,
    "faceSpine": 0,
    "seedMarker": 1,           # draw the seed component in the selection colour
    "hover": 1,                # Shift mode: seed = component under the cursor
    "xrayMode": 0,
    "maxSteps": 20000,
    "autoStart": 0,
    "alwaysOn": 0,             # debug / screenshot mode: no Shift needed
    "seeSelection": 1,         # never draw over currently selected components
    "hideMayaHover": 1,        # hide Maya's red pre-selection highlight while Shift is held
}

MODE_PREFIX = {VERTEX: "vertex", EDGE: "edge", FACE: "face"}

class Settings(object):
    def __init__(self):
        self.load()

    def _get(self, key):
        name = _OPT + key
        default = DEFAULTS[key]
        if not cmds.optionVar(exists=name):
            return default
        raw = cmds.optionVar(q=name)
        try:
            if isinstance(default, tuple):
                vals = tuple(float(x) for x in str(raw).split(","))
                return vals if len(vals) == 3 else default
            if isinstance(default, float):
                return float(raw)
            return int(raw)
        except Exception:
            return default

    def load(self):
        for k in DEFAULTS:
            setattr(self, k, self._get(k))

    def set(self, key, value):
        name = _OPT + key
        if isinstance(DEFAULTS[key], tuple):
            value = tuple(float(x) for x in value)
            cmds.optionVar(stringValue=(name, ",".join("%.4f" % x for x in value)))
        elif isinstance(DEFAULTS[key], float):
            value = float(value)
            cmds.optionVar(floatValue=(name, value))
        else:
            value = int(value)
            cmds.optionVar(intValue=(name, value))
        setattr(self, key, value)

    @staticmethod
    def reset():
        for k in DEFAULTS:
            if cmds.optionVar(exists=_OPT + k):
                cmds.optionVar(remove=_OPT + k)


# =========================================================================
# Per-mesh cache
# =========================================================================
class _MeshQueries(object):
    """Local adjacency lookups through Maya API; iterators recreated lazily."""

    def __init__(self, obj):
        self.handle = om.MObjectHandle(obj)
        self._fn = None
        self._pit = self._eit = self._vit = None

    def reset(self):
        self._fn = None
        self._pit = self._eit = self._vit = None

    def _obj(self):
        if not self.handle.isValid():
            raise RuntimeError("mesh no longer exists")
        return self.handle.object()

    def fn(self):
        if self._fn is None:
            self._fn = om.MFnMesh(self._obj())
        return self._fn

    def face_verts(self, f):
        return self.fn().getPolygonVertices(f)

    def face_edges(self, f):
        if self._pit is None:
            self._pit = om.MItMeshPolygon(self._obj())
        self._pit.setIndex(f)
        return self._pit.getEdges()

    def edge_verts(self, e):
        return self.fn().getEdgeVertices(e)

    def edge_faces(self, e):
        if self._eit is None:
            self._eit = om.MItMeshEdge(self._obj())
        self._eit.setIndex(e)
        return self._eit.getConnectedFaces()

    def vert_edges(self, v):
        if self._vit is None:
            self._vit = om.MItMeshVertex(self._obj())
        self._vit.setIndex(v)
        return self._vit.getConnectedEdges()


class _MeshEntry(object):
    def __init__(self, obj, uuid):
        self.uuid = uuid
        self.handle = om.MObjectHandle(obj)
        self.fn = om.MFnMesh(obj)              # owns the cached accelerator
        self.queries = _MeshQueries(obj)
        self.topo = None
        self.topo_counts = None
        self.topo_version = 0
        self.geom_version = 0
        self.accel = None
        self.accel_dirty = False
        self.callbacks = []

    def alive(self):
        return self.handle.isValid() and self.handle.isAlive()

    def counts(self):
        fn = self.fn
        return (fn.numVertices, fn.numEdges, fn.numPolygons)

    def topology(self):
        c = self.counts()
        if self.topo is None or self.topo_counts != c:
            self.queries.reset()
            q = self.queries
            self.topo = core.CachedTopology(c[0], c[1], c[2],
                                            q.face_verts, q.face_edges,
                                            q.edge_verts, q.edge_faces,
                                            q.vert_edges)
            self.topo_counts = c
            self.topo_version += 1
            self.accel_dirty = True
        return self.topo

    def accelerator(self):
        if self.accel is not None and self.accel_dirty:
            self.free_accel()
        if self.accel is None:
            try:
                self.accel = self.fn.autoUniformGridParams()
            except Exception:
                self.accel = None             # intersect still works, just unaccelerated
            self.accel_dirty = False
        return self.accel

    def free_accel(self):
        if self.accel is not None:
            try:
                self.fn.freeCachedIntersectionAccelerator()
            except Exception:
                pass
        self.accel = None

    def invalidate_topology(self):
        self.topo = None
        self.topo_counts = None
        self.queries.reset()
        self.accel_dirty = True

    def invalidate_geometry(self):
        self.geom_version += 1
        self.accel_dirty = True
        self.queries.reset()          # topology caches stay valid

    def dispose(self):
        if self.callbacks:
            try:
                om.MMessage.removeCallbacks(self.callbacks)
            except Exception:
                for cb in self.callbacks:
                    try:
                        om.MMessage.removeCallback(cb)
                    except Exception:
                        pass
        self.callbacks = []
        if self.alive():
            self.free_accel()
        self.accel = None
        self.topo = None


# =========================================================================
# Draw payload (read by the plugin)
# =========================================================================
# layer kinds / line styles understood by the VP2 plugin
TRIS, LINES, POINTS = 0, 1, 2
SOLID, DASHED = 0, 1


class DrawPayload(object):
    """Ordered draw layers for the VP2 plugin.

    layers: [(kind, MPointArray, MColor, size, style, priority)], world space.
    kind TRIS -> triangle list, LINES -> segment pairs, POINTS -> dots.
    size is line width / point size. priority is added to Maya's active-line
    depth priority (higher draws on top where coincident).
    """
    __slots__ = ("layers", "xray", "empty")

    def __init__(self):
        self.layers = []
        self.xray = False
        self.empty = True


def _plugin_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), PLUGIN_FILE)


def _plugin_loaded_name():
    for name in cmds.pluginInfo(q=True, listPlugins=True) or []:
        if os.path.splitext(name)[0] == PLUGIN_BASENAME:
            return name
    return None


def _recover_preselect_pref():
    """If Maya quit while Shift was held, Preselection Highlight may still be
    off from us: turn it back on."""
    name = _OPT + "restorePreselect"
    try:
        if cmds.optionVar(exists=name) and int(cmds.optionVar(q=name)):
            cmds.selectPref(preSelectHilite=True)
            cmds.optionVar(intValue=(name, 0))
    except Exception:
        pass


def _check_versions():
    """All three files must come from the same build."""
    problems = []
    core_v = getattr(core, "VERSION", "1.0.0 or older")
    if core_v != VERSION:
        problems.append("phoenix_topo_core.py is %s (%s)" % (core_v, getattr(core, "__file__", "?")))
    name = _plugin_loaded_name()
    if name:
        try:
            plug_v = cmds.pluginInfo(name, q=True, version=True)
        except Exception:
            plug_v = "?"
        if plug_v != VERSION:
            try:
                where = cmds.pluginInfo(name, q=True, path=True)
            except Exception:
                where = "?"
            problems.append("phoenixTopoPreview.py plugin is %s (%s)" % (plug_v, where))
    if problems:
        msg = ("Phoenix Topo Preview %s (%s): mismatched files - %s. "
               "Drag install_phoenix_topo_preview.py into Maya to reinstall cleanly."
               % (VERSION, __file__, "; ".join(problems)))
        om.MGlobal.displayError(msg)
        raise RuntimeError(msg)


def _file_version(path):
    """VERSION = "x.y.z" read from a .py file without importing it."""
    try:
        with open(path, "r") as fh:
            for line in fh:
                m = re.match(r"""VERSION\s*=\s*["']([^"']+)["']""", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def _delete_helper_nodes():
    """Remove helper locators without touching the undo queue."""
    try:
        nodes = cmds.ls(type=NODE_TYPE, long=True) or []
    except Exception:
        return
    for n in nodes:
        try:
            sel = om.MSelectionList()
            sel.add(n)
            parent = om.MFnDagNode(sel.getDependNode(0)).parent(0)
            mod = om.MDagModifier()
            mod.deleteNode(parent)
            mod.doIt()
        except Exception:
            pass


def _load_plugin():
    """Load the VP2 plugin that sits next to this file. A different version
    already in memory (stale session) is unloaded and replaced."""
    path = _plugin_path()
    disk_v = _file_version(path)
    if disk_v is not None and disk_v != VERSION:
        msg = ("Phoenix Topo Preview %s: %s is version %s. Drag install_phoenix_topo_preview.py "
               "into Maya to reinstall cleanly." % (VERSION, path, disk_v))
        om.MGlobal.displayError(msg)
        raise RuntimeError(msg)
    name = _plugin_loaded_name()
    if name:
        try:
            loaded_v = cmds.pluginInfo(name, q=True, version=True)
        except Exception:
            loaded_v = None
        if loaded_v == VERSION:
            return True
        om.MGlobal.displayInfo("Phoenix Topo Preview: replacing loaded plugin %s with %s"
                               % (loaded_v, VERSION))
        core.DRAW["payload"] = None
        _delete_helper_nodes()
        try:
            cmds.unloadPlugin(name, force=True)
        except Exception as exc:
            om.MGlobal.displayWarning("Phoenix Topo Preview: could not unload old plugin: %s" % exc)
    if not os.path.isfile(path):
        raise RuntimeError("Plugin file not found: %s" % path)
    for attempt in range(2):
        _purge_plugin_module(path)
        cmds.loadPlugin(path, quiet=True)
        name = _plugin_loaded_name()
        if not name:
            return False
        try:
            loaded_v = cmds.pluginInfo(name, q=True, version=True)
        except Exception:
            loaded_v = VERSION
        if loaded_v == VERSION:
            return True
        # Maya re-used a cached module of the old plugin: drop it and retry
        _delete_helper_nodes()
        try:
            cmds.unloadPlugin(name, force=True)
        except Exception:
            pass
    msg = ("Phoenix Topo Preview: Maya keeps the old plugin (%s) in memory. "
           "Restart Maya once to finish the update." % loaded_v)
    om.MGlobal.displayError(msg)
    raise RuntimeError(msg)


def _purge_plugin_module(path):
    """Forget every cached Python module / bytecode of the plugin file so
    loadPlugin executes the file on disk."""
    target = os.path.normcase(os.path.normpath(os.path.abspath(path)))
    stem = os.path.splitext(os.path.basename(path))[0]
    for key, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None) if mod is not None else None
        same = False
        if f:
            ff = os.path.normcase(os.path.normpath(os.path.abspath(f)))
            same = ff == target or ff == target + "c"
        if same or key == stem or key.endswith("." + stem):
            sys.modules.pop(key, None)
    cache = os.path.join(os.path.dirname(path), "__pycache__")
    if os.path.isdir(cache):
        for fn in os.listdir(cache):
            if fn.startswith(stem + "."):
                try:
                    os.remove(os.path.join(cache, fn))
                except Exception:
                    pass
    try:
        import importlib
        importlib.invalidate_caches()
    except Exception:
        pass


# =========================================================================
# Controller
# =========================================================================
class TopoPreviewController(QtCore.QObject):

    def __init__(self, parent=None):
        super(TopoPreviewController, self).__init__(parent)
        self.settings = Settings()
        self._enabled = False
        self._active = False
        self._dirty = True
        self._last_cursor = None
        self._last_key = None
        self._entries = {}
        self._global_cbs = []
        self._helper = None            # MObjectHandle of helper transform
        self._helper_shape = None      # MObjectHandle of helper shape
        self._panels = (0.0, [])
        self._widgets = {}
        self._settings_version = 0
        self._last_error = None
        self._isolate_synced = False
        self._frozen = False
        self._sel_version = 0
        self._sel_cache = {}
        self._prev_track_order = None
        self._preselect_hidden = False
        self._ray_validated = False
        self._ray_broken = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------ lifecycle
    def is_enabled(self):
        return self._enabled

    def enable(self):
        if self._enabled:
            return
        old = getattr(sys, _INSTANCE_ATTR, None)
        if old is not None and old is not self:
            try:
                old.disable()
            except Exception:
                pass
        if not _load_plugin():
            raise RuntimeError("Could not load %s" % PLUGIN_FILE)
        _check_versions()
        core.DRAW["payload"] = None
        core.DRAW.pop("draw_error", None)
        core.UNLOAD_HOOKS[:] = [self._on_plugin_unload]
        self._register_global_callbacks()
        QtWidgets.QApplication.instance().installEventFilter(self)
        self._enabled = True
        setattr(sys, _INSTANCE_ATTR, self)
        self._enable_track_order()
        _recover_preselect_pref()
        self._ensure_helper()
        om.MGlobal.displayInfo("Phoenix Topo Preview %s enabled - hold Shift in vertex/edge/face mode." % VERSION)
        if self._persistent():
            self._activate()

    def disable(self, from_unload=False):
        self._frozen = False
        self._deactivate(force=True)
        self._restore_maya_hover()
        self._restore_track_order()
        self._timer.stop()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        self._remove_global_callbacks()
        for entry in list(self._entries.values()):
            entry.dispose()
        self._entries.clear()
        self._widgets.clear()
        if not from_unload:
            self._delete_helper()
        self._helper = self._helper_shape = None
        core.DRAW["payload"] = None
        if self._on_plugin_unload in core.UNLOAD_HOOKS:
            core.UNLOAD_HOOKS.remove(self._on_plugin_unload)
        if getattr(sys, _INSTANCE_ATTR, None) is self:
            try:
                delattr(sys, _INSTANCE_ATTR)
            except Exception:
                pass
        was = self._enabled
        self._enabled = False
        self._refresh()
        if was:
            om.MGlobal.displayInfo("Phoenix Topo Preview disabled.")

    def _on_plugin_unload(self):
        self.disable(from_unload=True)

    def settings_changed(self):
        self.settings.load()
        self._settings_version += 1
        self._last_key = None
        self._dirty = True
        if not self._enabled:
            return
        if not (self.settings.hover and self.settings.hideMayaHover) or self.settings.alwaysOn:
            self._restore_maya_hover()
        if self._persistent() and not self._active:
            self._activate()
            return
        if not self._persistent() and self._active and not self._modifier_state()[0]:
            self._deactivate(force=True)
            return
        if self._active:
            self._update(force=True)

    def _enable_track_order(self):
        """'Last selected' needs Maya's selection-order tracking; turn it on
        while the tool is enabled and restore the user's value afterwards."""
        try:
            prev = bool(cmds.selectPref(q=True, trackSelectionOrder=True))
            if not prev:
                cmds.selectPref(trackSelectionOrder=True)
                self._prev_track_order = prev
        except Exception:
            self._prev_track_order = None

    def _restore_track_order(self):
        if self._prev_track_order is not None:
            try:
                cmds.selectPref(trackSelectionOrder=self._prev_track_order)
            except Exception:
                pass
        self._prev_track_order = None

    # ------------------------------------------------------------ debug modes
    def _persistent(self):
        """True when the preview runs without Shift (Always on)."""
        return bool(getattr(self.settings, "alwaysOn", 0))

    def _raw_modifiers(self):
        mods = _as_int(QtWidgets.QApplication.queryKeyboardModifiers())
        return bool(mods & _MOD_SHIFT), bool(mods & _MOD_OTHER)

    def is_frozen(self):
        return self._frozen

    def set_frozen(self, value):
        """Freeze keeps the current preview exactly as drawn (mouse ignored)."""
        self._frozen = bool(value)
        if not self._frozen:
            self._last_key = None
            self._dirty = True
            if self._active:
                self._update()
    # ------------------------------------------------------------ callbacks
    def _register_global_callbacks(self):
        self._remove_global_callbacks()
        add = self._global_cbs.append
        SM = om.MSceneMessage
        for msg in ("kBeforeNew", "kBeforeOpen"):
            if hasattr(SM, msg):
                add(SM.addCallback(getattr(SM, msg), self._on_before_scene_change))
        for msg in ("kBeforeSave", "kBeforeExport"):
            if hasattr(SM, msg):
                add(SM.addCallback(getattr(SM, msg), self._on_before_save))
        if hasattr(SM, "kMayaExiting"):
            add(SM.addCallback(SM.kMayaExiting, self._on_exit))
        for ev in ("SelectionChanged", "SelectModeChanged", "SelectTypeChanged",
                   "modelEditorChanged"):
            try:
                add(om.MEventMessage.addEventCallback(ev, self._on_ui_event, ev))
            except Exception:
                pass

    def _remove_global_callbacks(self):
        for cb in self._global_cbs:
            try:
                om.MMessage.removeCallback(cb)
            except Exception:
                pass
        self._global_cbs = []

    def _on_before_scene_change(self, *args):
        self._deactivate()
        for entry in list(self._entries.values()):
            entry.dispose()
        self._entries.clear()
        self._helper = self._helper_shape = None   # scene teardown removes it

    def _on_before_save(self, *args):
        # belt and braces on top of doNotWrite: never let the helper hit disk
        self._deactivate()
        self._delete_helper(restore_modified=False)

    def _on_exit(self, *args):
        try:
            self.disable()
        except Exception:
            pass

    def _on_ui_event(self, *args):
        self._isolate_synced = False      # isolate may have been toggled / reloaded
        if args and args[-1] == "SelectionChanged":
            self._sel_version += 1
            self._sel_cache = {}
        if self._active:
            self._last_key = None
            self._dirty = True

    def _on_topology_changed(self, *args):
        uuid = args[-1]
        entry = self._entries.get(uuid)
        if entry is not None:
            entry.invalidate_topology()
            self._last_key = None
            self._dirty = True

    _GEOM_ATTRS = frozenset(("inMesh", "outMesh", "cachedInMesh", "worldMesh",
                             "pnts", "pntx", "pnty", "pntz",
                             "vrts", "vrtx", "vrty", "vrtz"))

    def _on_mesh_dirty(self, *args):
        uuid = args[-1]
        entry = self._entries.get(uuid)
        if entry is not None:
            try:
                attr = om.MFnAttribute(args[1].attribute()).name
                if attr not in self._GEOM_ATTRS:
                    return
            except Exception:
                pass
            entry.invalidate_geometry()
            if self._active:
                self._dirty = True

    def _on_mesh_removed(self, *args):
        uuid = args[-1]
        if uuid in self._entries:
            QtCore.QTimer.singleShot(0, lambda u=uuid: self._drop_entry(u))

    def _drop_entry(self, uuid):
        entry = self._entries.pop(uuid, None)
        if entry is not None:
            entry.dispose()
            self._last_key = None
            self._dirty = True

    # ------------------------------------------------------------ helper node
    def _helper_valid(self):
        h = self._helper_shape
        return h is not None and h.isValid() and h.isAlive()

    def _ensure_helper(self):
        if self._helper_valid():
            return True
        was_modified = bool(cmds.file(q=True, modified=True))
        try:
            # adopt / clean leftovers from a previous run
            existing = cmds.ls(type=NODE_TYPE, long=True) or []
            for extra in existing[1:]:
                self._delete_node_path(extra)
            if existing:
                shape = self._mobject(existing[0])
                parent = om.MFnDagNode(shape).parent(0)
            else:
                mod = om.MDagModifier()
                parent = mod.createNode(NODE_TYPE)
                mod.renameNode(parent, HELPER_NAME)
                mod.doIt()
                shape = om.MFnDagNode(parent).child(0)
                mod2 = om.MDGModifier()
                mod2.renameNode(shape, HELPER_NAME + "Shape")
                mod2.doIt()
            for obj in (parent, shape):
                fn = om.MFnDependencyNode(obj)
                try:
                    fn.setDoNotWrite(True)
                except Exception:
                    pass
                self._set_plug(fn, "hiddenInOutliner", True)
            pfn = om.MFnDependencyNode(parent)
            self._set_plug(pfn, "overrideEnabled", True)
            self._set_plug(pfn, "overrideDisplayType", 2)    # Reference: unselectable
            self._set_plug(om.MFnDependencyNode(shape), "castsShadows", False)
            self._helper = om.MObjectHandle(parent)
            self._helper_shape = om.MObjectHandle(shape)
            self._isolate_synced = False
            return True
        except Exception:
            self._report("helper creation failed")
            return False
        finally:
            if not was_modified:
                cmds.file(modified=False)

    def _sync_isolate(self):
        """Isolate Select only draws members of the panel's isolate set, so
        add the helper to every isolated model panel. Undo recording is
        paused (no undo entries) and the file-modified flag is restored.
        Deleting the helper (disable / before save) removes the membership.
        """
        self._isolate_synced = True
        if not self._helper_valid():
            return
        try:
            helper = om.MFnDagNode(self._helper.object()).fullPathName()
            panels = cmds.getPanel(type="modelPanel") or []
        except Exception:
            return
        was_modified = bool(cmds.file(q=True, modified=True))
        undo_on = bool(cmds.undoInfo(q=True, state=True))
        changed = False
        try:
            if undo_on:
                cmds.undoInfo(stateWithoutFlush=False)
            for panel in panels:
                try:
                    if not cmds.isolateSelect(panel, q=True, state=True):
                        continue
                    view_set = cmds.isolateSelect(panel, q=True, viewObjects=True)
                    if view_set and cmds.objExists(view_set) and \
                            cmds.sets(helper, isMember=view_set):
                        continue
                    cmds.isolateSelect(panel, addDagObject=helper)
                    changed = True
                except Exception:
                    continue
        finally:
            if undo_on:
                cmds.undoInfo(stateWithoutFlush=True)
            if changed and not was_modified:
                cmds.file(modified=False)
        if changed:
            self._refresh()

    @staticmethod
    def _set_plug(fn, name, value):
        try:
            plug = fn.findPlug(name, False)
            if isinstance(value, bool):
                plug.setBool(value)
            else:
                plug.setInt(int(value))
        except Exception:
            pass

    @staticmethod
    def _mobject(name):
        sel = om.MSelectionList()
        sel.add(name)
        return sel.getDependNode(0)

    def _delete_node_path(self, shape_path):
        try:
            shape = self._mobject(shape_path)
            parent = om.MFnDagNode(shape).parent(0)
            mod = om.MDagModifier()
            mod.deleteNode(parent)
            mod.doIt()
        except Exception:
            pass

    def _delete_helper(self, restore_modified=True):
        try:
            nodes = cmds.ls(type=NODE_TYPE, long=True) or []
        except Exception:
            nodes = []
        if not nodes:
            self._helper = self._helper_shape = None
            return
        was_modified = bool(cmds.file(q=True, modified=True))
        for n in nodes:
            self._delete_node_path(n)
        self._helper = self._helper_shape = None
        if restore_modified and not was_modified:
            cmds.file(modified=False)

    # ------------------------------------------------------------ Qt events
    def eventFilter(self, obj, event):
        try:
            t = event.type()
            if t == _EV_KEYPRESS:
                if event.key() == _KEY_SHIFT and not event.isAutoRepeat():
                    self._on_shift_pressed()
            elif t == _EV_KEYRELEASE:
                if event.key() == _KEY_SHIFT and not event.isAutoRepeat():
                    if not self._persistent():
                        self._deactivate()
            elif t == _EV_APPSTATE:
                if _as_int(QtWidgets.QApplication.applicationState()) != _APP_ACTIVE:
                    if not self._persistent():
                        self._deactivate()
        except Exception:
            pass
        return False                      # observe only, never consume

    def _on_shift_pressed(self):
        if not self._enabled or self._active:
            return
        fw = QtWidgets.QApplication.focusWidget()
        if fw is not None and isinstance(fw, _TEXT_WIDGETS):
            return                        # typing capitals, not previewing
        self._activate()

    def _activate(self):
        if not self._enabled:
            return
        self._active = True
        self._dirty = True
        self._last_key = None
        self._last_cursor = None
        self._isolate_synced = False
        self._timer.start()
        QtCore.QTimer.singleShot(0, self._update)

    def _modifier_state(self):
        if self._persistent():
            return True, False
        mods = _as_int(QtWidgets.QApplication.queryKeyboardModifiers())
        return bool(mods & _MOD_SHIFT), bool(mods & _MOD_OTHER)

    def _tick(self):
        if not self._active:
            self._timer.stop()
            return
        shift, other = self._modifier_state()
        if not self._persistent() and (
                not shift or _as_int(QtWidgets.QApplication.applicationState()) != _APP_ACTIVE):
            self._deactivate()            # missed release (focus change etc.)
            return
        if self._frozen and core.DRAW.get("payload") is not None:
            return                        # frozen: keep the exact current image
        if self._persistent():
            if self._raw_modifiers()[1]:
                return                    # Alt/Ctrl (navigating): keep current preview
        if other:                         # Ctrl/Alt join in: hide, stay armed
            if core.DRAW.get("payload") is not None:
                self._last_key = None
                self._set_payload(None)
            return
        pos = QtGui.QCursor.pos()
        if pos != self._last_cursor:
            self._last_cursor = pos
            self._settle = 4              # re-check a few ticks after movement stops
            self._dirty = False
            self._update()
        elif self._dirty or getattr(self, "_settle", 0) > 0:
            self._settle = max(0, getattr(self, "_settle", 0) - 1)
            self._dirty = False
            self._update()

    def _deactivate(self, force=False):
        if not force and self._enabled and self._persistent():
            # Always-on: clear what's drawn (scene change / save) but keep running
            self._last_key = None
            self._dirty = True
            if core.DRAW.get("payload") is not None:
                self._set_payload(None)
            return
        was = self._active
        self._active = False
        self._timer.stop()
        self._last_key = None
        self._restore_maya_hover()
        if was or core.DRAW.get("payload") is not None:
            self._set_payload(None)

    # ------------------------------------------------------------ update
    def _update(self, force=False):
        if not self._active or not self._enabled:
            return
        if self._frozen and not force and core.DRAW.get("payload") is not None:
            return
        try:
            self._update_impl()
        except Exception:
            self._report("preview update failed")
            self._set_payload(None)

    def _update_impl(self):
        shift, other = self._modifier_state()
        if not shift or other:
            self._last_key = None
            self._set_payload(None)
            return
        if not self._ensure_helper():         # e.g. after File > New / Open
            return
        if not self._isolate_synced:
            self._sync_isolate()
        mode = self._component_mode()
        if mode is None:
            self._last_key = None
            self._set_payload(None)
            return

        # Seed source:
        #   Shift + Hover preview on : component under the cursor, else last selected
        #   Shift, Hover preview off : last selected
        #   Always on                : last selected
        st = self.settings
        seed = None
        panel = None
        if st.hover and not st.alwaysOn and st.hideMayaHover:
            self._maybe_hide_maya_hover(mode)
        if st.hover and not st.alwaysOn:
            try:
                hv = self._viewport_under_cursor()
            except Exception:
                hv = None
            if hv is not None:
                panel = hv[0]
                # a) the component Maya itself is pre-highlighting (most reliable)
                seed = self._preselect_seed(mode)
                # b) fallback: our own ray cast (preselection highlight disabled, ...)
                if seed is None:
                    try:
                        _, view, x, y = hv
                        seed = self._hover_seed(mode, view, x, y)
                    except Exception:
                        self._report("hover ray pick failed - using selection instead")
                        seed = None               # never lose the selection preview
        if seed is None:
            seed = self._selected_seed(mode)
        if seed is None:
            self._last_key = None
            self._set_payload(None)
            return

        dag, comp = seed
        entry = self._entry(dag)
        if entry is None:
            self._set_payload(None)
            return
        topo = entry.topology()
        if not topo.valid(mode, comp):
            self._set_payload(None)
            return
        xray = self._resolve_xray(panel)
        hidden = self._selected_ids(dag, mode) if st.seeSelection else frozenset()
        key = (entry.uuid, dag.fullPathName(), mode, comp, entry.topo_version,
               entry.geom_version, self._settings_version, xray,
               self._sel_version if st.seeSelection else 0)
        if key == self._last_key:
            return
        self._last_key = key
        paths = core.compute_preview(topo, mode, comp,
                                     max(10, int(self.settings.maxSteps)))
        self._set_payload(self._build_payload(dag, entry, topo, paths, xray, hidden))

    # ------------------------------------------------------------ mode / seeds
    def _component_mode(self):
        try:
            flags = ((VERTEX, cmds.selectType(q=True, polymeshVertex=True)),
                     (EDGE, cmds.selectType(q=True, polymeshEdge=True)),
                     (FACE, cmds.selectType(q=True, polymeshFace=True)))
        except Exception:
            return None
        on = [m for m, f in flags if f]
        if not on:
            return None
        if len(on) == 1:
            return on[0]
        last = self._last_selected_type()     # multi-component mode
        if last in on:
            return last
        return EDGE if EDGE in on else on[0]

    _API_TO_MODE = {
        om.MFn.kMeshVertComponent: VERTEX,
        om.MFn.kMeshEdgeComponent: EDGE,
        om.MFn.kMeshPolygonComponent: FACE,
    }

    def _iter_selected_components(self):
        sel = om.MGlobal.getActiveSelectionList(True)
        for i in reversed(range(sel.length())):
            try:
                dag, comp = sel.getComponent(i)
            except Exception:
                continue
            if comp is None or comp.isNull():
                continue
            mode = self._API_TO_MODE.get(comp.apiType())
            if mode is None:
                continue
            dag = self._to_mesh_shape(dag)
            if dag is None:
                continue
            yield dag, comp, mode

    # ------------------------------------------------------------ Maya hover highlight
    def _maybe_hide_maya_hover(self, mode):
        """Turn Maya's red pre-selection highlight off while Shift is held.
        Hover seeding then relies on our ray pick, so the first time Maya
        reports a hovered component we check the ray pick finds one too;
        if it can't, the highlight is left on and a warning explains why."""
        if self._preselect_hidden or self._ray_broken:
            return
        try:
            if not cmds.selectPref(q=True, preSelectHilite=True):
                return                    # already off: nothing to hide
        except Exception:
            return
        if not self._ray_validated:
            pre = self._preselect_seed(mode)
            if pre is not None:
                try:
                    hv = self._viewport_under_cursor()
                    ray = self._hover_seed(mode, hv[1], hv[2], hv[3]) if hv else None
                except Exception:
                    ray = None
                if ray is None:
                    self._ray_broken = True
                    om.MGlobal.displayWarning(
                        "Phoenix Topo Preview: can't hide Maya's hover highlight - the tool's own "
                        "hover pick found nothing where Maya highlights %s[%s]. Keeping Maya's "
                        "highlight for hover. Run phoenix_topo_preview.debug_hover() and send the output."
                        % (pre[0].fullPathName(), pre[1]))
                    return
                self._ray_validated = True
        try:
            cmds.selectPref(preSelectHilite=False)
            self._preselect_hidden = True
            cmds.optionVar(intValue=(_OPT + "restorePreselect", 1))   # crash safety
        except Exception:
            pass

    def _restore_maya_hover(self):
        if not self._preselect_hidden:
            return
        self._preselect_hidden = False
        try:
            cmds.selectPref(preSelectHilite=True)
        except Exception:
            pass
        try:
            cmds.optionVar(intValue=(_OPT + "restorePreselect", 0))
        except Exception:
            pass

    def _preselect_seed(self, mode, log=None):
        """Component under the cursor as Maya pre-highlights it
        (ls -preSelectHilite). Returns (dagPath, index) or None."""
        try:
            items = cmds.ls(preSelectHilite=True, flatten=True) or []
        except Exception as exc:
            if log:
                log("ls -preSelectHilite failed: %s" % exc)
            return None
        if log:
            log("Maya pre-highlight: %s" % (items[:5] or "nothing (or Preselection Highlight is off)"))
        for item in reversed(items):
            try:
                sel = om.MSelectionList()
                sel.add(item)
                dag, comp = sel.getComponent(0)
            except Exception:
                continue
            if comp is None or comp.isNull() or self._API_TO_MODE.get(comp.apiType()) != mode:
                continue
            dag = self._to_mesh_shape(dag)
            if dag is None:
                continue
            ids = om.MFnSingleIndexedComponent(comp).getElements()
            if len(ids):
                return dag, int(ids[0])
        return None

    def _selected_ids(self, dag, mode):
        """Selected component ids of `mode` on this mesh (cached per selection)."""
        key = (dag.fullPathName(), mode)
        ids = self._sel_cache.get(key)
        if ids is None:
            ids = set()
            name = key[0]
            for d, comp, m in self._iter_selected_components():
                if m == mode and d.fullPathName() == name:
                    ids.update(int(i) for i in om.MFnSingleIndexedComponent(comp).getElements())
            ids = frozenset(ids)
            self._sel_cache[key] = ids
        return ids

    def _last_selected_type(self):
        for _, _, mode in self._iter_selected_components():
            return mode
        return None

    def _selected_seed(self, mode):
        for dag, comp, m in self._iter_selected_components():
            if m != mode:
                continue
            ids = om.MFnSingleIndexedComponent(comp).getElements()
            if len(ids):
                return dag, int(ids[len(ids) - 1])
        return None

    @staticmethod
    def _to_mesh_shape(dag):
        try:
            if dag.node().hasFn(om.MFn.kMesh):
                if not om.MFnDagNode(dag).isIntermediateObject:
                    return om.MDagPath(dag)
                return None
            for k in range(dag.childCount()):
                child = dag.child(k)
                if not child.hasFn(om.MFn.kMesh):
                    continue
                if om.MFnDagNode(child).isIntermediateObject:
                    continue
                p = om.MDagPath(dag)
                p.push(child)
                return p
        except Exception:
            pass
        return None

    def _candidates(self):
        out = []
        seen = set()

        def add(dag):
            if dag is None:
                return
            name = dag.fullPathName()
            if name in seen:
                return
            try:
                if not dag.isVisible():
                    return
            except Exception:
                pass
            seen.add(name)
            out.append(dag)

        hl = om.MGlobal.getHiliteList()
        for i in range(hl.length()):
            try:
                dag = hl.getDagPath(i)
            except Exception:
                continue
            if dag.node().hasFn(om.MFn.kMesh):
                add(self._to_mesh_shape(dag))
            else:
                for k in range(dag.childCount()):
                    child = dag.child(k)
                    if child.hasFn(om.MFn.kMesh) and not om.MFnDagNode(child).isIntermediateObject:
                        p = om.MDagPath(dag)
                        p.push(child)
                        add(p)
        for dag, _, _ in self._iter_selected_components():
            add(dag)
        return out

    # ------------------------------------------------------------ mesh cache
    def _entry(self, dag):
        try:
            obj = dag.node()
            uuid = om.MFnDependencyNode(obj).uuid().asString()
        except Exception:
            return None
        entry = self._entries.get(uuid)
        if entry is not None and entry.alive():
            return entry
        if entry is not None:
            entry.dispose()
        entry = _MeshEntry(obj, uuid)
        cbs = entry.callbacks
        try:
            cbs.append(om.MPolyMessage.addPolyTopologyChangedCallback(obj, self._on_topology_changed, uuid))
        except Exception:
            pass                          # count check in topology() still catches changes
        try:
            cbs.append(om.MNodeMessage.addNodeDirtyPlugCallback(obj, self._on_mesh_dirty, uuid))
        except Exception:
            pass
        try:
            cbs.append(om.MNodeMessage.addNodePreRemovalCallback(obj, self._on_mesh_removed, uuid))
        except Exception:
            pass
        self._entries[uuid] = entry
        return entry

    # ------------------------------------------------------------ viewport / hover
    def _model_panels(self):
        stamp, panels = self._panels
        now = time.time()
        if now - stamp > 1.0:
            panels = [p for p in (cmds.getPanel(visiblePanels=True) or [])
                      if cmds.getPanel(typeOf=p) == "modelPanel"]
            self._panels = (now, panels)
        return panels

    def _wrap(self, ptr):
        ptr = int(ptr)
        w = self._widgets.get(ptr)
        if w is not None and shiboken.isValid(w):
            return w
        w = shiboken.wrapInstance(ptr, QtWidgets.QWidget)
        self._widgets[ptr] = w
        return w

    def _viewport_under_cursor(self, log=None):
        """(panel, M3dView, x, y) for the model panel under the mouse, in
        M3dView port coordinates (origin bottom-left), or None."""
        try:
            panel = cmds.getPanel(underPointer=True)
        except Exception:
            panel = None
        if log:
            log("panel under pointer: %r" % panel)
        if not panel or cmds.getPanel(typeOf=panel) != "modelPanel":
            return None
        try:
            view = omui.M3dView.getM3dViewFromModelPanel(panel)
            w = self._wrap(view.widget())
        except Exception as exc:
            if log:
                log("M3dView/widget failed: %s" % exc)
            return None
        pos = QtGui.QCursor.pos()
        local = w.mapFromGlobal(pos)
        ww, wh = max(1, w.width()), max(1, w.height())
        pw, ph = view.portWidth(), view.portHeight()
        if log:
            log("widget %dx%d  port %dx%d  local (%d, %d)" % (ww, wh, pw, ph, local.x(), local.y()))
        if not w.rect().contains(local):
            return None
        x = int(local.x() * pw / float(ww))
        y = int((wh - 1 - local.y()) * ph / float(wh))     # M3dView origin: bottom-left
        return panel, view, max(0, min(pw - 1, x)), max(0, min(ph - 1, y))

    _ISECT_VARIANTS = ("keywords", "positional", "no_accel")

    def _intersect(self, entry, src, d):
        """closestIntersection across Maya versions; remembers which call
        form works. Returns (MFloatPoint, face) or None."""
        fn = entry.fn
        sp = om.MFloatPoint(src.x, src.y, src.z)
        dv = om.MFloatVector(d.x, d.y, d.z)
        k = om.MSpace.kObject
        order = list(self._ISECT_VARIANTS)
        known = getattr(self, "_isect_variant", None)
        if known in order:
            order.remove(known)
            order.insert(0, known)
        last_exc = None
        for variant in order:
            try:
                if variant == "keywords":
                    res = fn.closestIntersection(sp, dv, k, 1.0e12, False,
                                                 accelParams=entry.accelerator())
                elif variant == "positional":
                    res = fn.closestIntersection(sp, dv, k, 1.0e12, False,
                                                 None, None, False, entry.accelerator())
                else:
                    res = fn.closestIntersection(sp, dv, k, 1.0e12, False)
            except (TypeError, ValueError) as exc:
                last_exc = exc
                continue
            self._isect_variant = variant
            if not res or res[2] is None or res[2] < 0:
                return None
            return res[0], int(res[2])
        raise RuntimeError("closestIntersection failed in every form: %s" % last_exc)

    def _hover_seed(self, mode, view, x, y, log=None):
        near, direction = view.viewToWorld(int(x), int(y))
        if log:
            log("ray origin (%.3f, %.3f, %.3f) dir (%.3f, %.3f, %.3f)"
                % (near.x, near.y, near.z, direction.x, direction.y, direction.z))
        best = None
        cands = self._candidates()
        if log:
            log("candidate meshes: %s" % ([c.fullPathName() for c in cands] or "NONE (nothing hilited / no component selection)"))
        for dag in cands:
            entry = self._entry(dag)
            if entry is None:
                continue
            try:
                entry.topology()          # refreshes accel_dirty on topo change
                inv = dag.inclusiveMatrixInverse()
                hit = self._intersect(entry, near * inv, direction * inv)
            except Exception:
                self._report("hover pick failed on %s" % dag.fullPathName())
                if log:
                    log("  %s: ERROR %s" % (dag.fullPathName(), traceback.format_exc().strip().splitlines()[-1]))
                continue
            if log:
                log("  %s: %s" % (dag.fullPathName(), "face %d" % hit[1] if hit else "no hit"))
            if hit is None:
                continue
            hp = om.MPoint(hit[0].x, hit[0].y, hit[0].z)
            dist = (hp * dag.inclusiveMatrix()).distanceTo(near)
            if best is None or dist < best[0]:
                best = (dist, dag, entry, hit[1], hp)
        if best is None:
            return None
        _, dag, entry, face, hp = best
        comp = self._component_from_hit(mode, entry, face, hp)
        if log:
            log("seed: %s %s[%s]" % (dag.fullPathName(), mode, comp))
        return None if comp is None else (dag, comp)

    @staticmethod
    def _component_from_hit(mode, entry, face, hit):
        if mode == FACE:
            return face
        topo = entry.topology()
        fn = entry.fn
        k = om.MSpace.kObject
        if mode == VERTEX:
            best, bv = None, None
            for v in topo.face_verts(face):
                d = fn.getPoint(v, k).distanceTo(hit)
                if best is None or d < best:
                    best, bv = d, v
            return bv
        best, be = None, None
        for e in topo.face_edges(face):
            a, b = topo.edge_verts(e)
            pa = fn.getPoint(a, k)
            pb = fn.getPoint(b, k)
            ab = pb - pa
            ll = ab * ab
            t = 0.0 if ll <= 1e-20 else max(0.0, min(1.0, ((hit - pa) * ab) / ll))
            d = (pa + ab * t).distanceTo(hit)
            if best is None or d < best:
                best, be = d, e
        return be

    def _resolve_xray(self, panel):
        mode = int(self.settings.xrayMode)
        if mode == 2:
            return True
        if mode == 3:
            return False
        if panel is None:
            try:
                p = cmds.getPanel(withFocus=True)
                panel = p if cmds.getPanel(typeOf=p) == "modelPanel" else None
            except Exception:
                panel = None
            if panel is None:
                panels = self._model_panels()
                panel = panels[0] if panels else None
        xr = False
        if panel:
            try:
                xr = bool(cmds.modelEditor(panel, q=True, xray=True))
                if not xr:
                    xr = bool(cmds.modelEditor(panel, q=True, activeComponentsXray=True))
            except Exception:
                xr = False
        if not xr and mode == 1:
            try:
                xr = not bool(cmds.selectPref(q=True, useDepth=True))
            except Exception:
                pass
        return xr

    # ------------------------------------------------------------ payload
    def _mode_colors(self, mode):
        s = self.settings
        pre = MODE_PREFIX[mode]
        if getattr(s, pre + "Custom", 0):
            return getattr(s, pre + "ColorA"), getattr(s, pre + "ColorB")
        return s.colorA, s.colorB

    def _build_payload(self, dag, entry, topo, paths, xray, hidden=frozenset()):
        """vertex mode -> two direction lines with dots (primary / secondary)
        edge mode   -> loop (primary) + ring (secondary) edges
        face mode   -> two face loops filled (primary / secondary)
        The seed component is drawn in the selection colour."""
        s = self.settings
        fn = entry.fn
        M = dag.inclusiveMatrix()
        kobj = om.MSpace.kObject
        cache = {}
        mode = paths.mode
        seed = paths.seed

        def P(v):
            p = cache.get(v)
            if p is None:
                p = fn.getPoint(v, kobj) * M
                cache[v] = p
            return p

        def mid(e):
            a, b = topo.edge_verts(e)
            pa, pb = P(a), P(b)
            return om.MPoint((pa.x + pb.x) * 0.5, (pa.y + pb.y) * 0.5, (pa.z + pb.z) * 0.5)

        def center(f):
            vs = topo.face_verts(f)
            x = y = z = 0.0
            for v in vs:
                p = P(v)
                x += p.x
                y += p.y
                z += p.z
            n = float(max(1, len(vs)))
            return om.MPoint(x / n, y / n, z / n)

        def edge_lines(edges):
            arr = om.MPointArray()
            for e in edges:
                a, b = topo.edge_verts(e)
                if a < 0 or b < 0:
                    continue
                arr.append(P(a))
                arr.append(P(b))
            return arr

        def chain_lines(chains):
            arr = om.MPointArray()
            for chain, closed in chains:
                pts = [mid(i) if k == core.E else center(i) for k, i in chain]
                for i in range(len(pts) - 1):
                    arr.append(pts[i])
                    arr.append(pts[i + 1])
                if closed and len(pts) > 2:
                    arr.append(pts[-1])
                    arr.append(pts[0])
            return arr

        def point_array(verts):
            arr = om.MPointArray()
            for v in verts:
                arr.append(P(v))
            return arr

        def tris(faces):
            arr = om.MPointArray()
            for f in faces:
                vs = topo.face_verts(f)
                if len(vs) < 3:
                    continue
                p0 = P(vs[0])
                for i in range(1, len(vs) - 1):
                    arr.append(p0)
                    arr.append(P(vs[i]))
                    arr.append(P(vs[i + 1]))
            return arr

        def edge_vert_set(edges):
            out = set()
            for e in edges:
                for x in topo.edge_verts(e):
                    if x >= 0:
                        out.add(x)
            return out

        def color(rgb, alpha=1.0):
            return om.MColor([float(rgb[0]), float(rgb[1]), float(rgb[2]), float(alpha)])

        layers = []

        def add(kind, pts, col, size, style, prio):
            if pts is not None and len(pts):
                layers.append((kind, pts, col, float(size), style, prio))

        rgb_a, rgb_b = self._mode_colors(mode)
        A, B = color(rgb_a), color(rgb_b)
        hl = bool(s.seedMarker)
        SEL = color(s.selectionColor) if hl else A
        w = max(1.0, float(s.lineWidth))
        ps = max(1.0, float(s.pointSize))
        alpha = max(0.0, min(1.0, float(s.faceFillOpacity)))

        # "See selection": selected components are left to Maya's own highlight
        hidden = set(hidden)
        seed_hidden = seed in hidden

        if mode == EDGE:
            loop = paths.loop_edges - {seed} - hidden
            ring = (paths.ring_edges - {seed} - hidden) if s.showRings else set()
            shared = loop & ring
            add(LINES, edge_lines(shared), B, w + 4.0, SOLID, 1)        # halo
            add(LINES, edge_lines(ring - shared), B, w, SOLID, 1)
            if s.showRings and s.ringSpine:
                add(LINES, chain_lines(paths.ring_chains), B, max(1.0, w * 0.6), DASHED, 1)
            add(LINES, edge_lines(loop), A, w, SOLID, 2)
            if not seed_hidden:
                add(LINES, edge_lines([seed]), SEL, w + (2.0 if hl else 0.0), SOLID, 4)

        elif mode == VERTEX:
            for i, g in enumerate(paths.groups):
                primary = (i % 2 == 0)
                col = A if primary else B
                prio = 2 if primary else 1
                if s.vertexPathLines:
                    add(LINES, edge_lines(g["edges"]), col, w, SOLID, prio)
                add(POINTS, point_array(edge_vert_set(g["edges"]) - {seed} - hidden),
                    col, ps, SOLID, prio + 2)
            if not seed_hidden:
                sp = om.MPointArray()
                sp.append(P(seed))
                add(POINTS, sp, SEL, ps + (5.0 if hl else 0.0), SOLID, 5)

        elif mode == FACE:
            hidden_edges = set()
            for f in hidden:
                hidden_edges.update(topo.face_edges(f))
            seed_edges = set(topo.face_edges(seed))
            for i, g in enumerate(paths.groups):
                primary = (i % 2 == 0)
                rgb = rgb_a if primary else rgb_b
                col = A if primary else B
                prio = 2 if primary else 1
                faces = g["faces"] - {seed} - hidden
                add(TRIS, tris(faces), color(rgb, alpha), 0, SOLID, prio - 1)
                outline = set()
                for f in faces:
                    outline.update(topo.face_edges(f))
                rails = outline - g["rungs"]
                add(LINES, edge_lines(rails - seed_edges - hidden_edges), col, w, SOLID, prio)
                add(LINES, edge_lines(g["rungs"] - seed_edges - hidden_edges), col,
                    max(1.0, w * 0.5), SOLID, prio)
                if s.faceSpine:
                    add(LINES, chain_lines([g["chain"]]), col, max(1.0, w * 0.6), DASHED, prio)
            if not seed_hidden:
                add(TRIS, tris([seed]), color(s.selectionColor if hl else rgb_a, max(alpha, 0.55)),
                    0, SOLID, 2)
                add(LINES, edge_lines(seed_edges - hidden_edges), SEL, w, SOLID, 4)

        p = DrawPayload()
        p.layers = layers
        p.xray = bool(xray)
        p.empty = not layers
        return p

    def _set_payload(self, payload):
        if core.DRAW.get("payload") is None and payload is None:
            return
        core.DRAW["payload"] = payload
        self._refresh()

    def _refresh(self):
        if self._helper_valid():
            try:
                omr.MRenderer.setGeometryDrawDirty(self._helper_shape.object(), True)
            except Exception:
                pass
        try:
            omui.M3dView.scheduleRefreshAllViews()
        except Exception:
            try:
                cmds.refresh(currentView=True)
            except Exception:
                pass

    def _report(self, what):
        msg = traceback.format_exc()
        if msg != self._last_error:
            self._last_error = msg
            om.MGlobal.displayWarning("Phoenix Topo Preview: %s (details in Script Editor)" % what)
            sys.stdout.write(msg)


# =========================================================================
# Public API
# =========================================================================
def get_controller(create=False):
    ctl = getattr(sys, _INSTANCE_ATTR, None)
    if ctl is None and create:
        ctl = TopoPreviewController()
    return ctl


def is_enabled():
    ctl = get_controller()
    return bool(ctl and ctl.is_enabled())


def enable():
    ctl = get_controller(create=True)
    ctl.enable()
    _sync_settings_ui()
    return ctl


def disable():
    ctl = get_controller()
    if ctl is not None:
        ctl.disable()
    _sync_settings_ui()


def toggle():
    if is_enabled():
        disable()
        state = "OFF"
    else:
        enable()
        state = "ON - hold Shift"
    try:
        cmds.inViewMessage(amg="Topo Loop Preview: <hl>%s</hl>" % state,
                           pos="topCenter", fade=True, fadeStayTime=900)
    except Exception:
        pass
    return is_enabled()


def unload_plugin():
    """Disable, then unload the VP2 plugin (safe to call any time)."""
    disable()
    name = _plugin_loaded_name()
    if name:
        cmds.unloadPlugin(name)


def autostart():
    """Called from userSetup.py; enables if 'Start with Maya' is on."""
    if Settings().autoStart:
        try:
            enable()
        except Exception:
            traceback.print_exc()


def set_always_on(value=True):
    """Debug / screenshot mode: preview shown without holding Shift."""
    _apply("alwaysOn", 1 if value else 0)
    _sync_settings_ui()
    _view_message("Always-on preview: <hl>%s</hl>" % ("ON" if value else "OFF"))


def debug_hover(delay=3.0):
    """Prints every step of the hover pick. Run it, then within `delay`
    seconds put the cursor over a mesh in component mode (no click)."""
    ctl = get_controller()
    if ctl is None or not ctl.is_enabled():
        print("[topo debug] tool is not enabled - run phoenix_topo_preview.enable() first")
        return

    def log(msg):
        print("[topo debug] " + msg)

    def run():
        log("---- hover pick ----")
        try:
            mode = ctl._component_mode()
            log("component mode: %s" % mode)
            log("hover setting: %s   always-on: %s"
                % (bool(ctl.settings.hover), bool(ctl.settings.alwaysOn)))
            try:
                log("Preselection Highlight pref: %s" % cmds.selectPref(q=True, preSelectHilite=True))
            except Exception:
                pass
            pre = ctl._preselect_seed(mode or EDGE, log)
            log("pre-highlight seed: %s" % (None if pre is None else "%s[%s]" % (pre[0].fullPathName(), pre[1])))
            hv = ctl._viewport_under_cursor(log)
            if hv is None:
                log("RESULT: cursor is not over a model panel viewport")
                return
            panel, view, x, y = hv
            log("port coords (%d, %d)  intersect form: %s"
                % (x, y, getattr(ctl, "_isect_variant", "untested")))
            seed = ctl._hover_seed(mode or EDGE, view, x, y, log)
            log("RESULT: %s" % ("no mesh hit" if seed is None else "OK"))
        except Exception:
            log("EXCEPTION:\n" + traceback.format_exc())

    print("[topo debug] move the cursor over a mesh - sampling in %.1fs" % delay)
    QtCore.QTimer.singleShot(int(delay * 1000), run)


def set_hover_preview(value=True):
    """While Shift is held, seed from the component under the cursor."""
    _apply("hover", 1 if value else 0)
    _sync_settings_ui()
    _view_message("Hover preview: <hl>%s</hl>" % ("ON" if value else "OFF"))


def toggle_hover_preview():
    set_hover_preview(not Settings().hover)
    return bool(Settings().hover)


def set_hide_maya_hover(value=True):
    """Hide Maya's red pre-selection highlight while Shift is held."""
    _apply("hideMayaHover", 1 if value else 0)
    ctl = get_controller()
    if ctl is not None:
        ctl._ray_broken = False           # allow a fresh check after toggling
        ctl._ray_validated = False
    _sync_settings_ui()
    _view_message("Hide Maya hover highlight: <hl>%s</hl>" % ("ON" if value else "OFF"))


def set_see_selection(value=True):
    """Leave selected components to Maya's own highlight (overlay skips them)."""
    _apply("seeSelection", 1 if value else 0)
    _sync_settings_ui()
    _view_message("See selection: <hl>%s</hl>" % ("ON" if value else "OFF"))


def toggle_see_selection():
    set_see_selection(not Settings().seeSelection)
    return bool(Settings().seeSelection)


def toggle_always_on():
    set_always_on(not Settings().alwaysOn)
    return bool(Settings().alwaysOn)


def set_frozen(value=True):
    ctl = get_controller()
    if ctl:
        ctl.set_frozen(value)
    _sync_settings_ui()
    _view_message("Preview <hl>%s</hl>" % ("FROZEN" if value else "live"))


def toggle_freeze():
    ctl = get_controller()
    set_frozen(not (ctl and ctl.is_frozen()))
    ctl = get_controller()
    return bool(ctl and ctl.is_frozen())


def _view_message(text):
    try:
        cmds.inViewMessage(amg=text, pos="topCenter", fade=True, fadeStayTime=900)
    except Exception:
        pass


def reset_settings():
    Settings.reset()
    ctl = get_controller()
    if ctl:
        ctl.settings_changed()
    _sync_settings_ui()


# =========================================================================
# Settings window
# =========================================================================
def _apply(key, value):
    s = Settings()
    s.set(key, value)
    ctl = get_controller()
    if ctl:
        ctl.settings_changed()


# =========================================================================
# Phoenix retro UI (settings panel, themes, shelf icon)
# =========================================================================
import copy
import json
import base64
import zlib
import maya.mel as mel
import maya.OpenMayaUI as _omui1
wrapInstance = shiboken.wrapInstance

_UI_OBJECT_NAME = "phxTopoLoopWin"
_SHELF_TAG = "phoenixTopoPreview"
_OPT_THEME = "phoenixTopoPreview_theme_json"
_OPT_ICON = "phoenixTopoPreview_shelf_icon"
_WIDTH = 340

TOGGLE_CMD = "import phoenix_topo_preview as _ptp\n_ptp.toggle()"
SETTINGS_CMD = "import phoenix_topo_preview as _ptp\n_ptp.show_settings()"

# shelf button contract (read by install_phoenix_tools.py as well)
_SHELF_SPEC = {
    "label": "TopoLoop",
    "annotation": "Phoenix Topo Loop Preview — click: toggle (then hold Shift) · double-click: settings",
    "command": TOGGLE_CMD,
    "doubleClickCommand": SETTINGS_CMD,
    "menuItems": [
        ("Toggle", TOGGLE_CMD),
        ("Settings...", SETTINGS_CMD),
        ("Hover preview (Shift + hover seed)", "import phoenix_topo_preview as _ptp\n_ptp.toggle_hover_preview()"),
        ("See selection", "import phoenix_topo_preview as _ptp\n_ptp.toggle_see_selection()"),
        ("Always on (screenshots)", "import phoenix_topo_preview as _ptp\n_ptp.toggle_always_on()"),
        ("Freeze / unfreeze", "import phoenix_topo_preview as _ptp\n_ptp.toggle_freeze()"),
    ],
}

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
        ptr = _omui1.MQtUtil.mainWindow()
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
        r = QtCore.QRect(8, 0, self.width() - 19, 31)
        p.drawText(r, QtCore.Qt.AlignCenter,
                   QtGui.QFontMetrics(self._font).elidedText(self._text, QtCore.Qt.ElideRight, r.width()))
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



# =========================================================================
# SHELF ICON — eye over a vertex loop, one colour version per theme
# =========================================================================
_ICON_MAP = [
    "      ####      ",
    "   ###llll###   ",
    " ##lllrrrrlll## ",
    " #llllr##rllll# ",
    " #lllrr##rrlll# ",
    " #llllr##rllll# ",
    " ##lllrrrrlll## ",
    "   ###llll###   ",
    "      ####      ",
    "                ",
    " #### #### #### ",
    " #oo# #ll# #oo# ",
    "##oo###ll###oo##",
    "##oo###ll###oo##",
    " #oo# #ll# #oo# ",
    " #### #### #### ",
]


def _icon_cols(theme):
    """(lines, fill, highlight) picked so all three read on every theme."""
    def lum(h):
        c = QtGui.QColor(h)
        return 0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF()
    def dist(a, b):
        a, b = QtGui.QColor(a), QtGui.QColor(b)
        return ((a.redF() - b.redF()) ** 2 + (a.greenF() - b.greenF()) ** 2 + (a.blueF() - b.blueF()) ** 2) ** 0.5 / 1.732
    fill = theme["nt"]
    lines = theme["ink"] if abs(lum(theme["ink"]) - lum(fill)) > 0.3 else theme["nb"]
    best, score = theme["acc"], -1
    for k in ("acc", "tsh", "sh", "mid", "bg", "acci"):
        c = theme[k]
        s = 0.5 * dist(c, fill) + 0.5 * dist(c, lines) + 0.2 * QtGui.QColor(c).hsvSaturationF()
        if dist(c, lines) < 0.15 or dist(c, fill) < 0.15 or dist(c, theme["bg"]) < 0.15:
            s -= 1
        if s > score:
            best, score = c, s
    return lines, fill, best


def _icon_pixels(theme):
    ln, fl, hl = _icon_cols(theme)
    col = {"#": ln, "l": fl, "o": hl, "r": theme["bg"], "m": theme["mid"]}
    bg = theme["bg"]
    n = len(_ICON_MAP)
    return [[col.get(ch, bg) for ch in row.ljust(n)[:n]] for row in _ICON_MAP]

def _icon_image(theme, scale=None):
    px = _icon_pixels(theme)
    scale = scale or max(1, 32 // len(px))
    img = QtGui.QImage(len(px) * scale, len(px) * scale, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    for y, row in enumerate(px):
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
    sig = "".join(theme[k] for k, _ in _THEME_KEYS).replace("#", "") + "|".join(_ICON_MAP)
    tag = format(zlib.crc32(sig.encode("ascii")) & 0xFFFFFF, "06x")
    folder = cmds.internalVar(userBitmapsDir=True)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "phxTopoLoop_{}_{}.png".format(idx, tag)).replace("\\", "/")
    if not os.path.exists(path):
        _icon_image(theme).save(path, "PNG")
    for f in os.listdir(folder):
        if f.startswith("phxTopoLoop_") and f.endswith(".png"):
            full = os.path.join(folder, f).replace("\\", "/")
            if full != path:
                try: os.remove(full)
                except Exception: pass
    return path


def _shelf_top():
    try:
        return mel.eval("$_phx_tl_tmp = $gShelfTopLevel")
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
                if cmds.objectTypeUI(path) == "shelfButton" and \
                        cmds.shelfButton(path, q=True, docTag=True) == _SHELF_TAG:
                    out.append(path)
            except Exception:
                pass
    return out


def set_shelf_icon(idx=None):
    """Pick the shelf icon (0-5, theme order) and update every TopoLoop shelf button."""
    if idx is None:
        idx = _icon_choice()
    idx = max(0, min(len(_THEMES_BASE) - 1, int(idx)))
    try: cmds.optionVar(iv=(_OPT_ICON, idx))
    except Exception: pass
    path = _write_icon(idx)
    for b in _shelf_buttons():
        try: cmds.shelfButton(b, e=True, image=path, image1=path)
        except Exception: pass
    return path


# =========================================================================
# EXTRA WIDGETS
# =========================================================================
class _PixelSlider(QtWidgets.QWidget):
    """Label · pixel track · value. Drag or click; wheel nudges."""
    valueChanged = QtCore.Signal(float)
    released = QtCore.Signal()

    def __init__(self, label, lo, hi, value, decimals=0, parent=None):
        super(_PixelSlider, self).__init__(parent)
        self._label, self._lo, self._hi, self._dec = label, lo, hi, decimals
        self._v = value
        self._drag = False
        self._font = _px(11, False, 0.5)
        self.setFixedHeight(28)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def value(self):
        return self._v

    def setRange(self, lo, hi):
        self._lo, self._hi = lo, max(lo, hi)
        self._v = max(self._lo, min(self._hi, self._v))
        self.update()

    def setValue(self, v, emit=False):
        v = max(self._lo, min(self._hi, v))
        v = round(v, self._dec) if self._dec else int(round(v))
        if v != self._v:
            self._v = v
            self.update()
            if emit:
                self.valueChanged.emit(float(v))

    def _track(self):
        return QtCore.QRect(104, 6, self.width() - 104 - 52, 16)

    def _from_x(self, x):
        r = self._track()
        t = (x - r.left() - 3) / float(max(1, r.width() - 6))
        self.setValue(self._lo + max(0.0, min(1.0, t)) * (self._hi - self._lo), emit=True)

    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.isEnabled():
            self._drag = True
            self._from_x(e.pos().x())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._from_x(e.pos().x())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.released.emit()

    def wheelEvent(self, e):
        if not self.isEnabled():
            return
        step = (10 ** -self._dec) if self._dec else 1
        d = e.angleDelta().y()
        self.setValue(self._v + (step if d > 0 else -step), emit=True)
        self.released.emit()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        on = self.isEnabled()
        ink = _c("ink") if on else _c("nd")
        p.setFont(self._font); p.setPen(ink)
        p.drawText(QtCore.QRect(0, 0, 100, self.height()), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self._label)
        r = self._track()
        _pbox(p, r.x(), r.y(), r.width(), r.height(), _c("fld"), ink, 3, 2, _c("sh"))
        span = (self._hi - self._lo) or 1
        t = (self._v - self._lo) / float(span)
        fw = int((r.width() - 10) * t)
        if fw > 0:
            p.fillRect(QtCore.QRect(r.x() + 5, r.y() + 5, fw, r.height() - 10), _c("acc") if on else _c("nd"))
        txt = ("{:.%df}" % self._dec).format(self._v) if self._dec else str(int(self._v))
        p.setPen(ink)
        p.drawText(QtCore.QRect(self.width() - 46, 0, 46, self.height()),
                   QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, txt)
        p.end()


class _Check(QtWidgets.QWidget):
    toggled = QtCore.Signal(bool)

    def __init__(self, text, parent=None):
        super(_Check, self).__init__(parent)
        self._text, self._on = text, False
        self._font = _px(11, False, 0.5)
        fm = QtGui.QFontMetrics(self._font)
        self.setFixedSize(20 + 8 + _text_w(fm, text) + 4, 22)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def isChecked(self):
        return self._on

    def setChecked(self, on):
        self._on = bool(on); self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.rect().contains(e.pos()) and self.isEnabled():
            self._on = not self._on
            self.update()
            self.toggled.emit(self._on)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        col = _c("nd") if not self.isEnabled() else (_c("ink") if self._on else _c("mid"))
        _pbox(p, 0, 1, 20, 20, _c("fld"), col, 3)
        if self._on:
            p.fillRect(QtCore.QRect(6, 7, 8, 8), _c("acc") if self.isEnabled() else _c("nd"))
        p.setFont(self._font); p.setPen(col)
        p.drawText(QtCore.QRect(28, 0, self.width() - 28, self.height()),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self._text)
        p.end()


class _StatusBox(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(_StatusBox, self).__init__(parent)
        self._text = ""
        self._font = _px(11, False, 0.5)
        self.setFixedHeight(34)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)

    def setText(self, t):
        self._text = (t or "").upper(); self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        _pbox(p, 0, 0, self.width() - 3, 31, _c("fld"), _c("ink"), 3, 3, _c("sh"))
        fm = QtGui.QFontMetrics(self._font)
        p.setFont(self._font); p.setPen(_c("ink"))
        r = QtCore.QRect(8, 0, self.width() - 19, 31)
        p.drawText(r, QtCore.Qt.AlignCenter, fm.elidedText(self._text, QtCore.Qt.ElideRight, r.width()))
        p.end()


class _Rule(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(_Rule, self).__init__(parent)
        self.setFixedHeight(3)

    def paintEvent(self, e):
        p = QtGui.QPainter(self); p.fillRect(self.rect(), _c("ink")); p.end()




class _NumField(QtWidgets.QWidget):
    """Pixel-framed number box (int or float). Type, scroll or use the arrow keys."""
    valueChanged = QtCore.Signal(float)

    def __init__(self, value=2, lo=1, hi=999, decimals=0, step=1, parent=None):
        super(_NumField, self).__init__(parent)
        self._lo, self._hi, self._dec, self._step = lo, hi, decimals, step
        self.setFixedHeight(34)
        self.setMinimumWidth(56)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.edit = QtWidgets.QLineEdit(self)
        if decimals:
            v = QtGui.QDoubleValidator(lo, hi, decimals, self)
            v.setNotation(QtGui.QDoubleValidator.StandardNotation)
        else:
            v = QtGui.QIntValidator(int(lo), int(hi), self)
        self.edit.setValidator(v)
        self.edit.setAlignment(QtCore.Qt.AlignCenter)
        self.edit.setFrame(False)
        self.edit.setFont(_px(14, False, 0.5))
        self.edit.editingFinished.connect(lambda: self.setValue(self.value()))
        self.edit.textChanged.connect(lambda *_: self.valueChanged.emit(self.value()))
        self.edit.installEventFilter(self)
        self.setValue(value)
        self._restyle()

    def _restyle(self):
        t = _T()
        self.edit.setStyleSheet(
            "QLineEdit{{background:transparent;color:{0};border:none;"
            "selection-background-color:{1};selection-color:{2};}}"
            "QLineEdit:disabled{{color:{3};}}".format(t["ink"], t["acc"], t["acci"], t["nd"]))

    def resizeEvent(self, e):
        self.edit.setGeometry(6, 4, self.width() - 15, self.height() - 11)

    def _fmt(self, v):
        return "{:.{}f}".format(v, self._dec) if self._dec else str(int(round(v)))

    def value(self):
        try:
            v = float(self.edit.text().replace(",", "."))
        except ValueError:
            v = self._lo
        v = max(self._lo, min(self._hi, v))
        return v if self._dec else int(round(v))

    def setValue(self, v):
        v = max(self._lo, min(self._hi, float(v)))
        t = self._fmt(v)
        if self.edit.text() != t:
            self.edit.setText(t)

    def _nudge(self, d):
        self.setValue(self.value() + d * self._step)

    def eventFilter(self, obj, e):
        if e.type() == QtCore.QEvent.KeyPress and e.key() in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            self._nudge(1 if e.key() == QtCore.Qt.Key_Up else -1)
            return True
        return False

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if d:
            self._nudge(1 if d > 0 else -1)

    def mousePressEvent(self, e):
        self.edit.setFocus()
        self.edit.selectAll()

    def paintEvent(self, e):
        self._restyle()
        p = QtGui.QPainter(self)
        focus = self.edit.hasFocus()
        ink = _c("nd") if not self.isEnabled() else (_c("acc") if focus else _c("ink"))
        _pbox(p, 0, 0, self.width() - 3, self.height() - 3, _c("fld"), ink, 3, 3, _c("sh"))
        p.end()


class _PixelCombo(QtWidgets.QComboBox):
    """Combo box drawn like the pixel fields."""
    def __init__(self, parent=None):
        super(_PixelCombo, self).__init__(parent)
        self.setFixedHeight(34)
        self.setFont(_px(11, False, 0.5))
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setMinimumWidth(60)
        self._restyle()

    def _restyle(self):
        t = _T()
        self.view().setStyleSheet(
            "QAbstractItemView{{background:{0};color:{1};border:3px solid {1};outline:none;"
            "selection-background-color:{2};selection-color:{3};}}".format(t["fld"], t["ink"], t["acc"], t["acci"]))

    def showPopup(self):
        self._restyle()
        super(_PixelCombo, self).showPopup()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        ink = _c("ink") if self.isEnabled() else _c("nd")
        _pbox(p, 0, 0, self.width() - 3, self.height() - 3, _c("fld"), ink, 3, 3, _c("sh"))
        fm = QtGui.QFontMetrics(self.font())
        r = QtCore.QRect(9, 0, self.width() - (34 if self.isEnabled() else 20), self.height() - 3)
        p.setFont(self.font()); p.setPen(ink)
        p.drawText(r, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   fm.elidedText(self.currentText().upper(), QtCore.Qt.ElideRight, r.width()))
        if self.isEnabled():
            x, y = self.width() - 21, (self.height() - 3) // 2 - 2
            for i, w in enumerate((7, 5, 3, 1)):
                p.fillRect(x + i, y + i, w, 1, ink)
        p.end()


class _RefreshBtn(_PixelButton):
    _MAP = ("  ####  ", " #    # ", "#      #", "#    ###", "#     ##", " #    ##", "  ####  ")

    def __init__(self, parent=None):
        super(_RefreshBtn, self).__init__("", 34, parent)
        self.setFixedWidth(34)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        r, fg = self._frame(p)
        x0 = r.x() + (r.width() - 16) // 2
        y0 = r.y() + (r.height() - 14) // 2
        for y, row in enumerate(self._MAP):
            for x, ch in enumerate(row):
                if ch == "#":
                    p.fillRect(x0 + x * 2, y0 + y * 2, 2, 2, fg)
        p.end()
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
        pix = _icon_pixels(_theme_state()["themes"][self._idx])
        px = max(1, 32 // len(pix))
        size = len(pix) * px
        x0 = (self.width() - size) // 2
        y0 = (self.height() - size) // 2
        if _icon_choice() == self._idx:
            _sel_frame(p, QtCore.QRect(x0 - 5, y0 - 5, size + 10, size + 10), _c("acc"))
        for y, row in enumerate(pix):
            for x, c in enumerate(row):
                if c:
                    p.fillRect(QtCore.QRect(x0 + x * px, y0 + y * px, px, px), QtGui.QColor(c))
        p.end()




def install_shelf_button(shelf=None):
    """Add / replace the TopoLoop shelf button (click = toggle, double-click =
    settings, right-click = toggles) on `shelf` (default: the current shelf)."""
    icon = _write_icon(_icon_choice())
    spec = _SHELF_SPEC
    top = _shelf_top()
    if not top or not cmds.tabLayout(top, exists=True):
        return None
    if shelf is None:
        shelf = cmds.tabLayout(top, q=True, selectTab=True)
    for b in _shelf_buttons():
        try:
            cmds.deleteUI(b)
        except Exception:
            pass
    parent = cmds.shelfLayout(shelf, q=True, fullPathName=True)
    cmds.shelfButton(parent=parent, label=spec["label"], annotation=spec["annotation"],
                     docTag=_SHELF_TAG, image=icon, image1=icon, imageOverlayLabel="",
                     sourceType="python", command=spec["command"],
                     doubleClickCommand=spec["doubleClickCommand"],
                     menuItem=[tuple(m) for m in spec["menuItems"]],
                     menuItemPython=list(range(len(spec["menuItems"]))))
    try:
        mel.eval("saveAllShelves $gShelfTopLevel;")
    except Exception:
        pass
    return shelf


# ── settings widgets ─────────────────────────────────────────────────────
class _Section(QtWidgets.QWidget):
    """Collapsible block: pixel header bar + body."""
    def __init__(self, title, opened=True, parent=None):
        super(_Section, self).__init__(parent)
        self._title = title
        self._open = opened
        self._font = _px(11, False, 0.5)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(3, 30, 3, 3)
        lay.setSpacing(0)
        self.body = QtWidgets.QWidget()
        self.form = QtWidgets.QVBoxLayout(self.body)
        self.form.setContentsMargins(10, 8, 10, 8)
        self.form.setSpacing(6)
        lay.addWidget(self.body)
        self.body.setVisible(opened)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

    def mousePressEvent(self, e):
        y = e.position().y() if hasattr(e, "position") else e.pos().y()
        if e.button() == QtCore.Qt.LeftButton and y <= 30:
            self._open = not self._open
            self.body.setVisible(self._open)
            self.update()
            w = self.window()
            QtCore.QTimer.singleShot(0, w.adjustSize)
            e.accept()
            return
        super(_Section, self).mousePressEvent(e)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        w, h = self.width(), self.height()
        ink = _c("ink")
        p.fillRect(0, 0, w, h, ink)
        p.fillRect(3, 3, w - 6, h - 6, _c("bg"))
        head = QtCore.QRect(3, 3, w - 6, 24)
        p.fillRect(head, ink if self._open else _c("fld"))
        p.setFont(self._font)
        p.setPen(_c("bg") if self._open else ink)
        p.drawText(head.adjusted(8, 0, -8, 0), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self._title.upper())
        p.drawText(head.adjusted(8, 0, -10, 0), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                   "-" if self._open else "+")
        if self._open:
            p.fillRect(3, 27, w - 6, 3, ink)
        p.end()


class _ColorRow(QtWidgets.QWidget):
    """Label + colour chip; click the chip to pick a colour (setting is an RGB tuple 0-1)."""
    def __init__(self, label, key, on_change, parent=None):
        super(_ColorRow, self).__init__(parent)
        self.key = key
        self._on_change = on_change
        self.setFixedHeight(26)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(_Label(label, 10), 1)
        self.chip = _Chip(self)
        self.chip.clicked.connect(self._pick)
        lay.addWidget(self.chip)

    def set_rgb(self, rgb):
        self.chip.rgb = tuple(rgb)
        self.chip.update()

    def setEnabled(self, on):
        super(_ColorRow, self).setEnabled(on)
        self.chip.update()

    def _pick(self):
        if not self.isEnabled():
            return
        c = QtGui.QColor.fromRgbF(*self.chip.rgb)
        col = QtWidgets.QColorDialog.getColor(c, self.window(), self.key)
        if col.isValid():
            rgb = (col.redF(), col.greenF(), col.blueF())
            self.set_rgb(rgb)
            self._on_change(self.key, rgb)


class _Chip(QtWidgets.QAbstractButton):
    def __init__(self, parent=None):
        super(_Chip, self).__init__(parent)
        self.rgb = (1.0, 1.0, 1.0)
        self.setFixedSize(64, 26)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        on = self.parent().isEnabled()
        fill = QtGui.QColor.fromRgbF(*self.rgb) if on else _c("fld")
        _pbox(p, 0, 0, self.width() - 3, self.height() - 3, fill, _c("ink") if on else _c("nd"), 3, 3, _c("sh"))
        if not on:
            p.fillRect(8, (self.height() - 3) // 2 - 1, self.width() - 19, 3, _c("nd"))
        p.end()


def _num_row(label, field):
    row = QtWidgets.QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(_Label(label, 10), 1)
    field.setFixedWidth(96)
    row.addWidget(field)
    return row


# ── window ───────────────────────────────────────────────────────────────
_UI_INSTANCE = None


class _TopoLoopWindow(QtWidgets.QWidget):
    RADIUS = 14
    BORDER = 3

    def __init__(self, parent=None):
        super(_TopoLoopWindow, self).__init__(parent or _maya_main_window())
        _ensure_font()
        _theme_state()
        self.setObjectName(_UI_OBJECT_NAME)
        self.setWindowTitle("Phoenix Topo Loop Preview")
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setFixedWidth(_WIDTH)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._press = None
        self._origin = None
        self._moving = False
        self._syncing = False
        self.checks = {}
        self.colors = {}
        self.nums = {}
        self.custom = {}

        lay = QtWidgets.QVBoxLayout(self)
        b = self.BORDER
        lay.setContentsMargins(b, b, b, b)
        lay.setSpacing(0)
        self.page_main = self._build_main()
        self.page_theme = self._build_themes()
        lay.addWidget(self.page_main)
        lay.addWidget(self.page_theme)
        self.page_theme.hide()
        self.sync()
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
        super(_TopoLoopWindow, self).keyPressEvent(e)

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

    def closeEvent(self, e):
        global _UI_INSTANCE
        _UI_INSTANCE = None
        super(_TopoLoopWindow, self).closeEvent(e)

    # ── main page ────────────────────────────────────────────────────────
    def _check(self, form, label, key, setter=None, tip=None):
        c = _Check(label)
        if tip:
            c.setToolTip(tip)
        c.toggled.connect(lambda on, k=key, s=setter: self._on_check(k, on, s))
        form.addWidget(c)
        self.checks[key] = c
        return c

    def _color(self, form, label, key):
        r = _ColorRow(label, key, lambda k, rgb: self._set(k, rgb))
        form.addWidget(r)
        self.colors[key] = r
        return r

    def _num(self, form, label, key, lo, hi, dec, step):
        f = _NumField(DEFAULTS[key], lo, hi, dec, step)
        f.valueChanged.connect(lambda v, k=key, d=dec: self._set(k, v if d else int(v)))
        form.addLayout(_num_row(label, f))
        self.nums[key] = f
        return f

    def _custom_block(self, form, pre, a, b):
        self._check(form, "Custom colours", pre + "Custom")
        self.custom[pre] = (self._color(form, a, pre + "ColorA"), self._color(form, b, pre + "ColorB"))

    def _build_main(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(14, 8, 14, 14)
        v.setSpacing(0)

        top = QtWidgets.QHBoxLayout(); top.addStretch(1)
        x = _WinBtn(); x.setToolTip("Close")
        x.clicked.connect(lambda *_: self.close())
        top.addWidget(x)
        v.addLayout(top)

        title = _BigTitle("TOPO LOOP")
        title.doubleClicked.connect(self._open_themes)
        v.addWidget(title)
        v.addSpacing(2)
        v.addWidget(_Label("MADE BY VIBEPHOENIX · " + VERSION, 10, "mid", QtCore.Qt.AlignCenter))
        v.addSpacing(12)

        self.b_on = _PixelButton("PREVIEW: ON", 38)
        self.b_on.setToolTip("Turn the Shift preview on / off (same as the shelf button)")
        self.b_on.clicked.connect(lambda *_: self._toggle())
        v.addWidget(self.b_on)
        v.addSpacing(6)
        self.on_hint = _Label("", 10, "mid", QtCore.Qt.AlignCenter)
        v.addWidget(self.on_hint)
        v.addSpacing(6)
        self._check(v, "Start with Maya", "autoStart")
        v.addSpacing(4)

        s = _Section("Behaviour", True)
        self._check(s.form, "Hover preview", "hover", set_hover_preview,
                    "Hold Shift: seed = component under the cursor")
        self._check(s.form, "Hide Maya's red hover", "hideMayaHover", set_hide_maya_hover,
                    "Turn Maya's pre-selection highlight off while Shift is held")
        self._check(s.form, "See selection", "seeSelection", set_see_selection,
                    "Never draw over components you have selected")
        v.addSpacing(8); v.addWidget(s)

        s = _Section("Colours", True)
        self._color(s.form, "PRIMARY · LOOP", "colorA")
        self._color(s.form, "SECONDARY · RING", "colorB")
        self._color(s.form, "SELECTION · SEED", "selectionColor")
        self._check(s.form, "Seed in selection colour", "seedMarker")
        self._num(s.form, "LINE THICKNESS", "lineWidth", 1.0, 20.0, 1, 0.5)
        v.addSpacing(8); v.addWidget(s)

        s = _Section("Vertex mode", False)
        self._custom_block(s.form, "vertex", "DIRECTION 1", "DIRECTION 2")
        self._num(s.form, "DOT SIZE", "pointSize", 1.0, 40.0, 1, 0.5)
        self._check(s.form, "Lines between dots", "vertexPathLines")
        v.addSpacing(8); v.addWidget(s)

        s = _Section("Edge mode", False)
        self._custom_block(s.form, "edge", "LOOP", "RING")
        self._check(s.form, "Show ring", "showRings")
        self._check(s.form, "Dashed ring spine", "ringSpine")
        v.addSpacing(8); v.addWidget(s)

        s = _Section("Face mode", False)
        self._custom_block(s.form, "face", "LOOP 1", "LOOP 2")
        self._num(s.form, "FILL OPACITY", "faceFillOpacity", 0.0, 1.0, 2, 0.05)
        self._check(s.form, "Centre spine", "faceSpine")
        v.addSpacing(8); v.addWidget(s)

        s = _Section("Debug / screenshots", False)
        self._check(s.form, "Always on (no Shift)", "alwaysOn", set_always_on,
                    "Preview without Shift; seed = last selected component")
        self.c_freeze = _Check("Freeze current preview")
        self.c_freeze.setToolTip("Keep the current preview exactly as drawn (not saved)")
        self.c_freeze.toggled.connect(lambda on: None if self._syncing else set_frozen(on))
        s.form.addWidget(self.c_freeze)
        v.addSpacing(8); v.addWidget(s)

        s = _Section("General", False)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(_Label("X-RAY", 10))
        self.xray = _PixelCombo()
        self.xray.addItems([m.upper() for m in XRAY_MODES])
        self.xray.setFixedWidth(200)
        self.xray.currentIndexChanged.connect(lambda i: None if self._syncing else self._set("xrayMode", i))
        row.addWidget(self.xray)
        s.form.addLayout(row)
        self._num(s.form, "MAX PATH STEPS", "maxSteps", 100, 1000000, 0, 1000)
        v.addSpacing(8); v.addWidget(s)

        v.addSpacing(14)
        act = QtWidgets.QGridLayout(); act.setHorizontalSpacing(10)
        b_reset = _PixelButton("RESET DEFAULTS")
        b_close = _PixelButton("CLOSE")
        b_reset.clicked.connect(lambda *_: self._reset())
        b_close.clicked.connect(lambda *_: self.close())
        act.addWidget(b_reset, 0, 0); act.addWidget(b_close, 0, 1)
        act.setColumnStretch(0, 1); act.setColumnStretch(1, 1)
        v.addLayout(act)
        return page

    # ── behaviour ────────────────────────────────────────────────────────
    def _set(self, key, value):
        if self._syncing:
            return
        _apply(key, value)

    def _on_check(self, key, on, setter):
        if self._syncing:
            return
        if setter is not None:
            setter(on)
        else:
            _apply(key, 1 if on else 0)
        if key.endswith("Custom"):
            self._update_custom()

    def _toggle(self):
        try:
            toggle()
        except Exception as exc:
            cmds.warning("[TopoLoop] {}".format(exc))
        self.sync()

    def _reset(self):
        reset_settings()
        self.sync()

    def _update_custom(self):
        for pre, rows in self.custom.items():
            on = self.checks[pre + "Custom"].isChecked()
            for r in rows:
                r.setEnabled(on)

    def sync(self):
        """Pull every value from the saved settings / controller."""
        self._syncing = True
        try:
            s = Settings()
            on = is_enabled()
            self.b_on.setText("PREVIEW: ON" if on else "PREVIEW: OFF")
            self.b_on.setHot(on)
            self.on_hint.setText("HOLD SHIFT IN VERTEX / EDGE / FACE MODE" if on
                                 else "CLICK TO TURN THE PREVIEW ON")
            for key, c in self.checks.items():
                c.setChecked(bool(getattr(s, key)))
            for key, r in self.colors.items():
                r.set_rgb(getattr(s, key))
            for key, f in self.nums.items():
                f.setValue(getattr(s, key))
            self.xray.setCurrentIndex(max(0, min(len(XRAY_MODES) - 1, int(s.xrayMode))))
            ctl = get_controller()
            self.c_freeze.setChecked(bool(ctl and ctl.is_frozen()))
            self._update_custom()
        finally:
            self._syncing = False
        self.update()

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
        b_reset.clicked.connect(lambda *_: self._reset_theme())
        b_done.clicked.connect(lambda *_: self._close_themes())
        row.addWidget(b_reset, 0, 0); row.addWidget(b_done, 0, 1)
        row.setColumnStretch(0, 1); row.setColumnStretch(1, 1)
        body.addLayout(row)
        self._update_cust_label()
        return page

    # ── behaviour ────────────────────────────────────────────────────────
    # ── theme handling ───────────────────────────────────────────────────
    def _fit(self):
        QtCore.QTimer.singleShot(0, self.adjustSize)

    def _open_themes(self):
        self._update_cust_label()
        self.page_main.hide(); self.page_theme.show()
        self._fit()

    def _close_themes(self):
        _save_theme_state()
        try:
            if _shelf_buttons():
                set_shelf_icon(_icon_choice())
        except Exception:
            pass
        self.page_theme.hide(); self.page_main.show()
        self._fit()

    def _update_cust_label(self):
        self.cust_lbl.setText("Customize / " + _T()["n"].upper())

    def _repaint_all(self):
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
            cmds.warning("[TopoLoop] shelf icon error: {}".format(e))
        for t in self.icon_tiles:
            t.update()




# =========================================================================
# Public UI entry points
# =========================================================================
def show_settings():
    """Open (or re-open) the TopoLoop settings panel."""
    global _UI_INSTANCE
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w.objectName() == _UI_OBJECT_NAME:
                w.close(); w.deleteLater()
        except Exception:
            pass
    try:
        if cmds.window(_SETTINGS_WIN, exists=True):      # old Maya-UI settings window
            cmds.deleteUI(_SETTINGS_WIN)
    except Exception:
        pass
    _UI_INSTANCE = _TopoLoopWindow()
    _UI_INSTANCE.show()
    _UI_INSTANCE.raise_()
    _UI_INSTANCE.activateWindow()
    return _UI_INSTANCE


def _sync_settings_ui():
    w = _UI_INSTANCE
    if w is None:
        return
    try:
        if shiboken.isValid(w):
            w.sync()
    except Exception:
        pass
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

