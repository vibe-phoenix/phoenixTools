"""Headless smoke test (no Maya): runs the real controller + VP2 plugin code
against a stubbed Maya API backed by phoenix_topo_core, with real Qt key events.

    pip install PySide6-Essentials
    python test_smoke_headless.py
"""
import os, sys, types, math
os.environ["QT_QPA_PLATFORM"] = "offscreen"
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SRC)
import phoenix_topo_core as core
from test_phoenix_topo_core import grid, build

# ------------------------------------------------------------ stub mesh
NX = NY = 6
FACES = grid(NX, NY)
REF = build(FACES)                       # CSR reference topology
W = NX + 1
POS = {j * W + i: (float(i), 0.0, float(j)) for j in range(NY + 1) for i in range(NX + 1)}

# ------------------------------------------------------------ stub OpenMaya
om = types.ModuleType("maya.api.OpenMaya")
class MPoint:
    def __init__(s, x=0.0, y=0.0, z=0.0, w=1.0): s.x, s.y, s.z = float(x), float(y), float(z)
    def __mul__(s, m): return MPoint(s.x + m.t[0], s.y + m.t[1], s.z + m.t[2])
    def __sub__(s, o): return MVector(s.x - o.x, s.y - o.y, s.z - o.z)
    def __add__(s, v): return MPoint(s.x + v.x, s.y + v.y, s.z + v.z)
    def distanceTo(s, o): return math.sqrt((s.x-o.x)**2 + (s.y-o.y)**2 + (s.z-o.z)**2)
class MVector(MPoint):
    def __mul__(s, o):
        if isinstance(o, MVector): return s.x*o.x + s.y*o.y + s.z*o.z
        if isinstance(o, MMatrix): return MVector(s.x, s.y, s.z)
        return MVector(s.x*o, s.y*o, s.z*o)
class MMatrix:
    def __init__(s, t=(0, 0, 0)): s.t = t
class MPointArray(list):
    pass
class MColor(tuple):
    def __new__(c, v): return tuple.__new__(c, v)
om.MPoint, om.MVector, om.MMatrix, om.MPointArray, om.MColor = MPoint, MVector, MMatrix, MPointArray, MColor
om.MFloatPoint = MPoint
om.MFloatVector = MVector
om.MSpace = types.SimpleNamespace(kObject=1, kWorld=4)
om.MFn = types.SimpleNamespace(kMesh=296, kMeshVertComponent=31, kMeshEdgeComponent=32, kMeshPolygonComponent=33)
om.MTypeId = lambda x: x
om.MBoundingBox = lambda: None
class MUserData:
    def __init__(s, *a): pass
om.MUserData = MUserData

class MObject:
    def __init__(s, kind): s.kind = kind
    def hasFn(s, fn): return fn == s.kind
    def isNull(s): return False
    def apiType(s): return s.kind
MESH = MObject(296)
class MObjectHandle:
    def __init__(s, o): s.o = o
    def isValid(s): return True
    def isAlive(s): return True
    def object(s): return s.o
om.MObjectHandle = MObjectHandle
class MDagPath:
    def __init__(s, other=None): s.n = MESH
    def node(s): return s.n
    def fullPathName(s): return "|grid|gridShape"
    def isVisible(s): return True
    def childCount(s): return 0
    def inclusiveMatrix(s): return MMatrix((10.0, 0, 0))     # translated mesh
    def inclusiveMatrixInverse(s): return MMatrix((-10.0, 0, 0))
om.MDagPath = MDagPath

CALLS = {"accel_free": 0, "callbacks": 0, "removed": 0}
class MFnMesh:
    def __init__(s, o): pass
    numVertices = REF.num_vertices; numEdges = REF.num_edges; numPolygons = REF.num_faces
    def getPolygonVertices(s, f): return list(REF.face_verts(f))
    def getEdgeVertices(s, e): return REF.edge_verts(e)
    def getPoint(s, v, space): return MPoint(*POS[v])
    def autoUniformGridParams(s): return "accel"
    def freeCachedIntersectionAccelerator(s): CALLS["accel_free"] += 1
    def closestIntersection(s, src, d, space, mx, both, a, b, c, accel):
        # ray straight down in object space onto y=0 plane
        if d.y == 0: return None
        t = -src.y / d.y
        hx, hz = src.x + d.x * t, src.z + d.z * t
        if not (0 <= hx < NX and 0 <= hz < NY): return (MPoint(), 0, -1, -1, 0, 0)
        return (MPoint(hx, 0, hz), t, int(hz) * NX + int(hx), 0, 0, 0)
om.MFnMesh = MFnMesh
class _It:
    def __init__(s, o): s.i = 0
    def setIndex(s, i): s.i = i; return 0
class MItMeshPolygon(_It):
    def getEdges(s): return list(REF.face_edges(s.i))
class MItMeshEdge(_It):
    def getConnectedFaces(s): return list(REF.edge_faces(s.i))
class MItMeshVertex(_It):
    def getConnectedEdges(s): return list(REF.vert_edges(s.i))
om.MItMeshPolygon, om.MItMeshEdge, om.MItMeshVertex = MItMeshPolygon, MItMeshEdge, MItMeshVertex
class MFnDependencyNode:
    def __init__(s, o): pass
    def uuid(s): return types.SimpleNamespace(asString=lambda: "UUID-GRID")
om.MFnDependencyNode = MFnDependencyNode
class MFnDagNode:
    def __init__(s, o): pass
    isIntermediateObject = False
    def fullPathName(s): return "|phoenixTopoPreview_helper"
om.MFnDagNode = MFnDagNode

SEL = {"items": []}   # list of (mode, index)
class MSelectionList:
    def __init__(s, items=()): s.items = list(items)
    def add(s, name):                     # "|grid|gridShape.e[12]"
        letter, idx = name.split(".")[-1][0], int(name.split("[")[1].rstrip("]"))
        s.items.append(({"v": "vertex", "e": "edge", "f": "face"}[letter], idx))
    def length(s): return len(s.items)
    def getComponent(s, i):
        mode, idx = s.items[i]
        kind = {"vertex": 31, "edge": 32, "face": 33}[mode]
        c = MObject(kind); c.ids = [idx]
        return MDagPath(), c
    def getDagPath(s, i): return MDagPath()
om.MSelectionList = MSelectionList
class MFnSingleIndexedComponent:
    def __init__(s, c): s.c = c
    def getElements(s): return s.c.ids
om.MFnSingleIndexedComponent = MFnSingleIndexedComponent
om.MGlobal = types.SimpleNamespace(
    getActiveSelectionList=lambda ordered=False: MSelectionList(SEL["items"]),
    getHiliteList=lambda: MSelectionList([("face", 0)]),
    displayInfo=lambda m: None, displayWarning=lambda m: print("WARN:", m),
    displayError=lambda m: print("ERROR-MSG:", m[:90]))
def _addcb(*a, **k):
    CALLS["callbacks"] += 1; return CALLS["callbacks"]
def _rmcb(*a):
    CALLS["removed"] += len(a[0]) if isinstance(a[0], list) else 1
om.MSceneMessage = types.SimpleNamespace(kBeforeNew=1, kBeforeOpen=2, kBeforeSave=3, kBeforeExport=4,
                                         kMayaExiting=5, addCallback=_addcb)
om.MEventMessage = types.SimpleNamespace(addEventCallback=_addcb)
om.MPolyMessage = types.SimpleNamespace(addPolyTopologyChangedCallback=_addcb)
om.MNodeMessage = types.SimpleNamespace(addNodeDirtyPlugCallback=_addcb, addNodePreRemovalCallback=_addcb)
om.MMessage = types.SimpleNamespace(removeCallback=_rmcb, removeCallbacks=_rmcb)

omui = types.ModuleType("maya.api.OpenMayaUI")
REFRESH = {"n": 0}
omui.M3dView = types.SimpleNamespace(scheduleRefreshAllViews=lambda: REFRESH.__setitem__("n", REFRESH["n"] + 1))
class MPxLocatorNode:
    def __init__(s): pass
omui.MPxLocatorNode = MPxLocatorNode
omr = types.ModuleType("maya.api.OpenMayaRender")
omr.MRenderer = types.SimpleNamespace(setGeometryDrawDirty=lambda *a: None, kAllDevices=7)
class MPxDrawOverride:
    def __init__(s, *a): pass
omr.MPxDrawOverride = MPxDrawOverride
omr.MRenderItem = types.SimpleNamespace(sActiveLineDepthPriority=5)
class MUIDrawManager:
    kLines = 1; kTriangles = 4; kNonSelectable = 1; kSolid = 0; kShortDashed = 5
    def __init__(s): s.log = []
    def __getattr__(s, name):
        return lambda *a: s.log.append((name, a))
omr.MUIDrawManager = MUIDrawManager

# ------------------------------------------------------------ stub cmds
cmds = types.ModuleType("maya.cmds")
STATE = {"mode": "edge", "opt": {}, "modified": False}
def optionVar(**k):
    if "exists" in k: return k["exists"] in STATE["opt"]
    if "q" in k: return STATE["opt"][k["q"]]
    for key in ("intValue", "floatValue", "stringValue"):
        if key in k: STATE["opt"][k[key][0]] = k[key][1]
    if "remove" in k: STATE["opt"].pop(k["remove"], None)
cmds.optionVar = optionVar
def selectType(q=True, **k):
    flag = list(k)[0]
    return {"polymeshVertex": "vertex", "polymeshEdge": "edge", "polymeshFace": "face"}[flag] == STATE["mode"]
cmds.selectType = selectType
def getPanel(**k):
    if "visiblePanels" in k: return []
    if "type" in k: return ["modelPanel1", "modelPanel4"]
    return "modelPanel4"
cmds.getPanel = getPanel
ISO = {"state": {"modelPanel1": False, "modelPanel4": True},
       "members": {"modelPanel4ViewSelectedSet": set()}, "undo": [True], "log": []}
def isolateSelect(panel, **k):
    if k.get("q") and "state" in k: return ISO["state"][panel]
    if k.get("q") and "viewObjects" in k: return panel + "ViewSelectedSet"
    if "addDagObject" in k:
        ISO["log"].append((panel, k["addDagObject"], ISO["undo"][-1]))
        ISO["members"].setdefault(panel + "ViewSelectedSet", set()).add(k["addDagObject"])
        STATE["modified"] = True
cmds.isolateSelect = isolateSelect
cmds.objExists = lambda n: n in ISO["members"]
cmds.sets = lambda obj, isMember=None: obj in ISO["members"].get(isMember, set())
def undoInfo(**k):
    if k.get("q"): return ISO["undo"][-1]
    if "stateWithoutFlush" in k: ISO["undo"].append(k["stateWithoutFlush"])
cmds.undoInfo = undoInfo
cmds.modelEditor = lambda *a, **k: False
def file(**k):
    if k.get("q"): return STATE["modified"]
    STATE["modified"] = k.get("modified", STATE["modified"])
cmds.file = file
PRESEL = {"items": []}
cmds.ls = lambda **k: ((list(PRESEL["items"]) if PREFS.get("preSelectHilite", True) else [])
                       if k.get("preSelectHilite") else [])
PLUGIN_V = {"v": None}
def pluginInfo(*a, **k):
    if k.get("version"):
        import phoenixTopoPreview
        return PLUGIN_V["v"] or phoenixTopoPreview.VERSION
    if k.get("path"):
        return "/fake/plug-ins"
    return ["phoenixTopoPreview.py"]
cmds.pluginInfo = pluginInfo
cmds.inViewMessage = lambda **k: None
cmds.checkBox = lambda *a, **k: False
PREFS = {"trackSelectionOrder": False}
def selectPref(**k):
    if k.get("q"):
        key = [x for x in k if x != "q"][0]
        return PREFS.get(key, True)
    PREFS.update(k)
cmds.selectPref = selectPref
mel = types.ModuleType("maya.mel"); mel.eval = lambda *a, **k: ""
omui1 = types.ModuleType("maya.OpenMayaUI")
omui1.MQtUtil = types.SimpleNamespace(mainWindow=lambda: None)
cmds.internalVar = lambda **k: "/tmp/"
maya = types.ModuleType("maya"); api = types.ModuleType("maya.api")
maya.cmds = cmds; maya.api = api; api.OpenMaya = om; api.OpenMayaUI = omui; api.OpenMayaRender = omr
sys.modules.update({"maya": maya, "maya.cmds": cmds, "maya.api": api, "maya.api.OpenMaya": om,
                    "maya.api.OpenMayaUI": omui, "maya.api.OpenMayaRender": omr,
                    "maya.mel": mel, "maya.OpenMayaUI": omui1})

# ------------------------------------------------------------ run
from PySide6 import QtCore, QtGui, QtWidgets
import shiboken6
app = QtWidgets.QApplication([])
import phoenix_topo_preview as ptp
import phoenixTopoPreview as plugin

ok = []
def check(name, cond):
    ok.append(cond); print(("PASS " if cond else "FAIL ") + name)

BLUE, LAV, SELC = ptp.BLUE, ptp.LAVENDER, (1.00, 0.78, 0.15)
def _rgb(c): return tuple(round(float(x), 3) for x in c[:3])
def lay(p, kind=None, rgb=None):
    return [l for l in p.layers if (kind is None or l[0] == kind)
            and (rgb is None or _rgb(l[2]) == _rgb(rgb))]
def npts(p, kind, rgb): return sum(len(l[1]) for l in lay(p, kind, rgb))
def seed_xz(p):
    pts = [pt for l in lay(p, None, SELC) for pt in l[1]]
    return (round(sum(pt.x for pt in pts) / len(pts), 3), round(sum(pt.z for pt in pts) / len(pts), 3))

# offscreen Qt reports the app as inactive; Maya with focus is active
QtWidgets.QApplication.applicationState = staticmethod(lambda: QtCore.Qt.ApplicationActive)
ctl = ptp.TopoPreviewController()
ctl._ensure_helper = lambda: True          # helper node creation needs real Maya
ctl.enable()
ptp.Settings().set("seeSelection", 0); ctl.settings_changed()   # earlier checks draw over selection
check("versions match across all three files",
      ptp.VERSION == core.VERSION == plugin.VERSION)
check("enable registers callbacks + event filter", CALLS["callbacks"] >= 6 and ctl.is_enabled())
check("enable turns on selection-order tracking", PREFS["trackSelectionOrder"] is True)

MODS = {"shift": False, "other": False}
ctl._modifier_state = lambda: (MODS["shift"], MODS["other"])
w = QtWidgets.QWidget(); w.show(); w.setFocus()

def key(ev_type):
    e = QtGui.QKeyEvent(ev_type, QtCore.Qt.Key_Shift, QtCore.Qt.NoModifier)
    return ctl.eventFilter(w, e)

# Shift press with edge selected
SEL["items"] = [("edge", REF._ids[frozenset((2 * W + 2, 2 * W + 3))])]
MODS["shift"] = True
check("eventFilter never consumes (press)", key(QtCore.QEvent.KeyPress) is False)
check("shift press activates + starts timer", ctl._active and ctl._timer.isActive())
ctl._update()
p = core.DRAW["payload"]
L, P_, T = ptp.LINES, ptp.POINTS, ptp.TRIS
check("edge mode: loop edges (5 + seed) in primary", npts(p, L, BLUE) == 10)
check("edge mode: ring edges (6 + seed) in secondary", npts(p, L, LAV) == 12)
check("edge mode: seed edge in selection colour, thicker",
      npts(p, L, SELC) == 2 and lay(p, L, SELC)[0][3] > lay(p, L, BLUE)[0][3])
check("edge mode: loop and ring share one thickness", lay(p, L, BLUE)[0][3] == lay(p, L, LAV)[0][3])
check("edge mode: no fill, no dots, spine off by default",
      not lay(p, T) and not lay(p, P_) and all(l[4] == ptp.SOLID for l in p.layers))
check("world transform applied (+10 x)", min(pt.x for l in lay(p, L, BLUE) for pt in l[1]) == 10.0)
check("seed at edge midpoint", seed_xz(p) == (12.5, 2.0))

# identical key -> no rebuild
before = REFRESH["n"]; ctl._update()
check("unchanged seed does not rebuild", REFRESH["n"] == before and core.DRAW["payload"] is p)

# vertex mode
STATE["mode"] = "vertex"; SEL["items"] = [("vertex", 3 * W + 3)]
ctl._update(); p = core.DRAW["payload"]
check("vertex mode: direction 1 = 6 edges + 6 dots in primary", npts(p, L, BLUE) == 12 and npts(p, P_, BLUE) == 6)
check("vertex mode: direction 2 = 6 edges + 6 dots in secondary", npts(p, L, LAV) == 12 and npts(p, P_, LAV) == 6)
blue_v = {(pt.x, pt.z) for l in lay(p, P_, BLUE) for pt in l[1]}
lav_v = {(pt.x, pt.z) for l in lay(p, P_, LAV) for pt in l[1]}
check("vertex mode: exactly two straight lines, one per colour",
      (len({x for x, _ in blue_v}) == 1 or len({z for _, z in blue_v}) == 1)
      and (len({x for x, _ in lav_v}) == 1 or len({z for _, z in lav_v}) == 1)
      and not (blue_v & lav_v))
check("vertex mode: seed dot in selection colour", seed_xz(p) == (13.0, 3.0))
check("vertex mode: no ring bands, no fill", not lay(p, T) and len(p.layers) == 5)
# face mode
STATE["mode"] = "face"; SEL["items"] = [("face", 2 * NX + 2)]
ctl._update(); p = core.DRAW["payload"]
check("face mode: loop 1 filled primary (5 quads)", npts(p, T, BLUE) == 30)
check("face mode: loop 2 filled secondary (5 quads)", npts(p, T, LAV) == 30)
check("face mode: fill opacity 0.35", abs(lay(p, T, BLUE)[0][2][3] - 0.35) < 1e-6)
check("face mode: outlines per loop colour (10 rails + 5 rungs each)",
      npts(p, L, BLUE) == 30 and npts(p, L, LAV) == 30)
check("face mode: seed face filled + outlined in selection colour",
      npts(p, T, SELC) == 6 and npts(p, L, SELC) == 8 and seed_xz(p) == (12.5, 2.5))
# custom per-mode colours
ptp.Settings().set("faceColorA", (1.0, 0.0, 0.0)); ctl.settings_changed()
p = core.DRAW["payload"]
check("override off: face uses global colours", npts(p, T, BLUE) == 30)
ptp.Settings().set("faceCustom", 1); ctl.settings_changed(); p = core.DRAW["payload"]
check("override on: face loop 1 uses custom colour", npts(p, T, (1.0, 0.0, 0.0)) == 30)
ptp.Settings().set("colorA", (0.0, 1.0, 0.0)); ctl.settings_changed(); p = core.DRAW["payload"]
check("global colour change does not touch overridden mode", npts(p, T, (1.0, 0.0, 0.0)) == 30)
STATE["mode"] = "edge"; SEL["items"] = [("edge", REF._ids[frozenset((2 * W + 2, 2 * W + 3))])]
ctl._update(); p = core.DRAW["payload"]
check("global colour applies to non-overridden modes", npts(p, L, (0.0, 1.0, 0.0)) == 10)
ptp.Settings().set("selectionColor", (1.0, 1.0, 1.0)); ctl.settings_changed(); p = core.DRAW["payload"]
check("selection colour setting applied", npts(p, L, (1.0, 1.0, 1.0)) == 2)
ptp.Settings().set("seedMarker", 0); ctl.settings_changed(); p = core.DRAW["payload"]
check("seed highlight off: seed drawn as part of the loop", npts(p, L, (0.0, 1.0, 0.0)) == 12)
ptp.Settings.reset(); ptp.Settings().set("seeSelection", 0); ctl.settings_changed()
STATE["mode"] = "face"; SEL["items"] = [("face", 2 * NX + 2)]; ctl._update()

# hover seed overrides selection
STATE["mode"] = "edge"
SEL["items"] = [("edge", REF._ids[frozenset((2 * W + 2, 2 * W + 3))])]
class View:
    def viewToWorld(s, x, y):  # world ray straight down onto (x,z)
        return MPoint(10.0 + x / 10.0, 5.0, y / 10.0), MVector(0.0, -1.0, 0.0)
seed = ctl._hover_seed("edge", View(), 41, 10)       # object hit (4.1, 0, 1.0)
check("hover picks nearest edge to hit", seed is not None and
      set(REF.edge_verts(seed[1])) == {1 * W + 4, 1 * W + 5})
seed = ctl._hover_seed("vertex", View(), 38, 23)
check("hover picks nearest vertex", seed[1] == 2 * W + 4)
check("hover miss -> None (falls back to selection)", ctl._hover_seed("edge", View(), 900, 900) is None)

STATE["mode"] = "vertex"
ctl._update()
check("mode mismatch with selection -> no preview (no guessing)", core.DRAW["payload"] is None)
STATE["mode"] = "edge"
ctl._update()
# other modifier hides but stays armed
MODS["other"] = True; ctl._tick()
check("ctrl/alt + shift hides preview, stays armed", core.DRAW["payload"] is None and ctl._active)
MODS["other"] = False; ctl._dirty = True; ctl._tick()
check("back to shift-only shows again", core.DRAW["payload"] is not None)

# plugin draw against recorded draw manager
STATE["mode"] = "face"; SEL["items"] = [("face", 2 * NX + 2)]; ctl._update()
data = plugin._PreviewUserData(); data.payload = core.DRAW["payload"]
dm = MUIDrawManager()
plugin.PhoenixTopoPreviewDrawOverride.addUIDrawables(
    plugin.PhoenixTopoPreviewDrawOverride.__new__(plugin.PhoenixTopoPreviewDrawOverride), None, dm, None, data)
names = [n for n, _ in dm.log]
check("plugin draws all layers in one drawable",
      names[0] == "beginDrawable" and names[-1] == "endDrawable"
      and names.count("mesh") == len(core.DRAW["payload"].layers))
check("face fill drawn as triangles first", [a[0] for n, a in dm.log if n == "mesh"][0] == MUIDrawManager.kTriangles)
STATE["mode"] = "vertex"; SEL["items"] = [("vertex", 3 * W + 3)]; ctl._update()
dmv = MUIDrawManager(); plugin.PhoenixTopoPreviewDrawOverride._draw(core.DRAW["payload"], dmv)
check("vertex mode draws dot layers", [n for n, _ in dmv.log].count("points") == 3)
STATE["mode"] = "face"; SEL["items"] = [("face", 2 * NX + 2)]; ctl._update()
prios = [a[0] for n, a in dm.log if n == "setDepthPriority"]
check("loop drawn above ring (depth priority)", max(prios) > min(prios))
check("no x-ray calls when depth tested", "beginDrawInXray" not in names)
core.DRAW["payload"].xray = True; dm2 = MUIDrawManager()
plugin.PhoenixTopoPreviewDrawOverride._draw(core.DRAW["payload"], dm2)
check("x-ray wraps drawing", [n for n, _ in dm2.log].count("beginDrawInXray") == 1)

# focus loss while holding shift
QtWidgets.QApplication.applicationState = staticmethod(lambda: QtCore.Qt.ApplicationInactive)
ctl._tick()
check("app losing focus clears preview", core.DRAW["payload"] is None and not ctl._active)
QtWidgets.QApplication.applicationState = staticmethod(lambda: QtCore.Qt.ApplicationActive)
key(QtCore.QEvent.KeyPress); ctl._update()

# isolate select
ctl._helper = ctl._helper_shape = MObjectHandle(MObject(1))
STATE["modified"] = False
ctl._sync_isolate()
check("isolate: helper added to isolated panel only",
      [(pnl, obj) for pnl, obj, _ in ISO["log"]] == [("modelPanel4", "|phoenixTopoPreview_helper")])
check("isolate: undo recording paused during add, restored after",
      ISO["log"][0][2] is False and ISO["undo"][-1] is True)
check("isolate: file-modified flag restored", STATE["modified"] is False)
ctl._sync_isolate()
check("isolate: no duplicate add when already a member", len(ISO["log"]) == 1)
ISO["members"]["modelPanel4ViewSelectedSet"].clear()      # user reloads isolate set
ctl._on_ui_event(); ctl._update()
check("isolate: re-added after isolate set reload", len(ISO["log"]) == 2)
ctl._helper = ctl._helper_shape = None

# release
MODS["shift"] = False
check("eventFilter never consumes (release)", key(QtCore.QEvent.KeyRelease) is False)
check("shift release clears instantly", core.DRAW["payload"] is None and not ctl._active and not ctl._timer.isActive())

# missed release safety (poll)
MODS["shift"] = True; key(QtCore.QEvent.KeyPress); ctl._update()
MODS["shift"] = False; ctl._tick()
check("poll catches missed release", core.DRAW["payload"] is None and not ctl._active)

# typing in a text field does not activate
le = QtWidgets.QLineEdit(); le.show(); le.setFocus(); app.processEvents()
MODS["shift"] = True; key(QtCore.QEvent.KeyPress)
check("shift while typing in text field ignored",
      not ctl._active or not isinstance(QtWidgets.QApplication.focusWidget(), QtWidgets.QLineEdit))
ctl._deactivate()

# topology change invalidates cache
entry = list(ctl._entries.values())[0]
v0 = entry.topo_version; ctl._on_topology_changed(MESH, "UUID-GRID"); entry.topology()
check("topology callback rebuilds topo cache", entry.topo_version == v0 + 1)
g0 = entry.geom_version
plug = types.SimpleNamespace(attribute=lambda: None)
om.MFnAttribute = lambda a: types.SimpleNamespace(name="outMesh")
ctl._on_mesh_dirty(MESH, plug, "UUID-GRID")
check("geometry dirty bumps version + accel", entry.geom_version == g0 + 1 and entry.accel_dirty)
om.MFnAttribute = lambda a: types.SimpleNamespace(name="instObjGroups")
ctl._on_mesh_dirty(MESH, plug, "UUID-GRID")
check("non-geometry dirty ignored", entry.geom_version == g0 + 1)

# always-on debug mode (real modifier query: nothing held)
del ctl._modifier_state
STATE["mode"] = "face"; SEL["items"] = [("face", 2 * NX + 2)]
ptp.set_always_on(True)
app.processEvents()                     # first draw is deferred one event-loop tick
p_on = core.DRAW["payload"]
check("always-on: preview shown with no Shift held", ctl._active and p_on is not None and not p_on.empty)
key(QtCore.QEvent.KeyRelease)
check("always-on: Shift release does not clear", ctl._active and core.DRAW["payload"] is not None)
QtWidgets.QApplication.applicationState = staticmethod(lambda: QtCore.Qt.ApplicationInactive)
ctl._dirty = True; ctl._tick()
QtWidgets.QApplication.applicationState = staticmethod(lambda: QtCore.Qt.ApplicationActive)
check("always-on: focus loss (screenshot tool) does not clear", core.DRAW["payload"] is not None)
ctl._on_before_scene_change()
check("always-on: survives scene change (cleared, still running)", ctl._active and core.DRAW["payload"] is None)
ctl._dirty = True; ctl._tick()
check("always-on: redraws after scene change", core.DRAW["payload"] is not None)
# freeze
ptp.set_frozen(True); frozen_p = core.DRAW["payload"]
SEL["items"] = [("face", 0)]; ctl._on_ui_event("SelectionChanged"); ctl._tick(); ctl._update()
check("freeze: preview unchanged when selection/mouse change", core.DRAW["payload"] is frozen_p)
ptp.set_frozen(False)
check("unfreeze: follows new seed", core.DRAW["payload"] is not frozen_p
      and seed_xz(core.DRAW["payload"]) == (10.5, 0.5))
# always-on seeds from the LAST selected component, never the mouse
ctl._viewport_under_cursor = lambda: ("modelPanel4", View(), 0, 0)
ctl._hover_seed = lambda *a: (MDagPath(), 3 * NX + 4)          # mouse over another face
SEL["items"] = [("face", 1), ("face", 2 * NX + 5)]           # last selected = face 17
ctl._last_key = None; ctl._update()
check("always-on: seed = last selected component (hover ignored)", seed_xz(core.DRAW["payload"]) == (15.5, 2.5))
SEL["items"] = [("face", 2 * NX + 5), ("face", 1)]           # reselect in other order
ctl._update()
check("always-on: follows selection order, not index", seed_xz(core.DRAW["payload"]) == (11.5, 0.5))
del ctl._viewport_under_cursor; del ctl._hover_seed
ptp.set_always_on(False)
check("always-on off: preview stops (no Shift held)", not ctl._active and core.DRAW["payload"] is None)
ctl._modifier_state = lambda: (MODS["shift"], MODS["other"])
MODS["shift"] = False

# ---------------- hover preview (needs Shift; only changes the seed)
le.close(); w.setFocus(); app.processEvents()       # leave the text field from the typing test
check("focus is no longer in a text field",
      not isinstance(QtWidgets.QApplication.focusWidget(), QtWidgets.QLineEdit))
HOVER = {"seed": None}
real_vuc, real_hs = ctl._viewport_under_cursor, ctl._hover_seed
ctl._viewport_under_cursor = lambda *a: ("modelPanel4", View(), 0, 0) if HOVER["seed"] is not None else None
ctl._hover_seed = lambda *a: (MDagPath(), HOVER["seed"]) if HOVER["seed"] is not None else None
STATE["mode"] = "edge"
E_SEED = REF._ids[frozenset((2 * W + 2, 2 * W + 3))]
E_OTHER = REF._ids[frozenset((4 * W + 1, 4 * W + 2))]
SEL["items"] = [("edge", E_OTHER)]
ptp.set_hover_preview(True)
MODS["shift"] = False; HOVER["seed"] = E_SEED
ctl._dirty = True; ctl._tick()
check("hover preview: nothing without Shift", core.DRAW["payload"] is None and not ctl._active)
MODS["shift"] = True; key(QtCore.QEvent.KeyPress); app.processEvents()
check("hover preview + Shift: seed = hovered edge", seed_xz(core.DRAW["payload"]) == (12.5, 2.0))
HOVER["seed"] = None; ctl._dirty = True; ctl._tick()
check("hover preview + Shift, nothing hovered: last selected", seed_xz(core.DRAW["payload"]) == (11.5, 4.0))
HOVER["seed"] = E_SEED; ptp.set_hover_preview(False)
check("hover preview off: Shift uses last selected even when hovering",
      seed_xz(core.DRAW["payload"]) == (11.5, 4.0))
ptp.set_hover_preview(True)
MODS["shift"] = False; key(QtCore.QEvent.KeyRelease)
ctl._viewport_under_cursor, ctl._hover_seed = real_vuc, real_hs
del ctl._viewport_under_cursor; del ctl._hover_seed

# real viewport -> port coordinate mapping (HiDPI 2x) with a real widget
vw = QtWidgets.QWidget(); vw.resize(200, 100); vw.show(); app.processEvents()
ptr = int(shiboken6.getCppPointer(vw)[0])
FAKE_VIEW = types.SimpleNamespace(widget=lambda: ptr, portWidth=lambda: 400, portHeight=lambda: 200,
                                  viewToWorld=View().viewToWorld)
omui.M3dView.getM3dViewFromModelPanel = lambda panel: FAKE_VIEW
PANEL_UNDER = {"p": "modelPanel4"}
_old_getPanel = cmds.getPanel
def getPanel2(**k):
    if "underPointer" in k: return PANEL_UNDER["p"]
    if "typeOf" in k: return "modelPanel" if k["typeOf"].startswith("modelPanel") else "scriptedPanel"
    return _old_getPanel(**k)
cmds.getPanel = getPanel2
real_pos = QtGui.QCursor.pos
QtGui.QCursor.pos = staticmethod(lambda: vw.mapToGlobal(QtCore.QPoint(50, 25)))
hv = ctl._viewport_under_cursor()
check("viewport under cursor via getPanel(underPointer)", hv is not None and hv[0] == "modelPanel4")
check("HiDPI port coords: x=100, y=(99-25)*2=148", hv is not None and (hv[2], hv[3]) == (100, 148))
PANEL_UNDER["p"] = "outlinerPanel1"
check("non-viewport panel under cursor -> None", ctl._viewport_under_cursor() is None)
PANEL_UNDER["p"] = "modelPanel4"
QtGui.QCursor.pos = real_pos

# closestIntersection call-form fallback (stub rejects the keyword form)
ctl._isect_variant = None
seed = ctl._hover_seed("edge", View(), 41, 10)
check("intersect: falls back to a working call form", seed is not None and ctl._isect_variant == "positional")
logs = []
ptp_ctl = ctl
ctl._hover_seed("face", View(), 41, 10, logs.append)
check("debug log lists candidates + hit", any("candidate meshes" in l for l in logs) and any("face" in l for l in logs))

# ---------------- see selection
ptp.set_see_selection(True)
ctl._viewport_under_cursor = lambda *a: ("modelPanel4", View(), 0, 0) if HOVER["seed"] is not None else None
ctl._hover_seed = lambda *a: (MDagPath(), HOVER["seed"]) if HOVER["seed"] is not None else None
MODS["shift"] = True; key(QtCore.QEvent.KeyPress); app.processEvents()
HOVER["seed"] = E_SEED
loop_e = sorted(core.compute_preview(REF, "edge", E_SEED).loop_edges - {E_SEED})
SEL["items"] = [("edge", loop_e[0]), ("edge", loop_e[1])]; ctl._on_ui_event("SelectionChanged")
ctl._dirty = True; ctl._tick(); p = core.DRAW["payload"]
check("see selection: selected loop edges not overdrawn (6 - seed - 2 = 3)", npts(p, L, BLUE) == 6)
SEL["items"].append(("edge", E_SEED)); ctl._on_ui_event("SelectionChanged"); ctl._tick()
p = core.DRAW["payload"]
check("see selection: selected seed not overdrawn", npts(p, L, SELC) == 0 and npts(p, L, BLUE) == 6)
ptp.set_see_selection(False); p = core.DRAW["payload"]
check("see selection off: overlay draws over selection", npts(p, L, BLUE) == 10 and npts(p, L, SELC) == 2)
ptp.set_see_selection(True)
STATE["mode"] = "face"; HOVER["seed"] = 2 * NX + 2
SEL["items"] = [("face", 2 * NX + 4)]; ctl._on_ui_event("SelectionChanged"); ctl._dirty = True; ctl._tick()
p = core.DRAW["payload"]
check("see selection: selected face in loop left unfilled", npts(p, T, BLUE) + npts(p, T, LAV) == 54)
STATE["mode"] = "vertex"; HOVER["seed"] = 3 * W + 3
SEL["items"] = [("vertex", 3 * W + 5)]; ctl._on_ui_event("SelectionChanged"); ctl._dirty = True; ctl._tick()
p = core.DRAW["payload"]
check("see selection: selected vertex has no overlay dot", npts(p, P_, BLUE) + npts(p, P_, LAV) == 11)
MODS["shift"] = False; key(QtCore.QEvent.KeyRelease)
check("Shift release clears (hover mode)", not ctl._active and core.DRAW["payload"] is None)
del ctl._viewport_under_cursor; del ctl._hover_seed
STATE["mode"] = "edge"

# ---------------- hover via Maya's pre-selection highlight
le.close(); w.setFocus(); app.processEvents()
ptp.set_see_selection(False); ptp.set_hover_preview(True); ptp.set_hide_maya_hover(False)
STATE["mode"] = "edge"; SEL["items"] = [("edge", E_OTHER)]
ctl._viewport_under_cursor = lambda *a: ("modelPanel4", View(), 0, 0)
RAY = {"seed": None, "calls": 0}
def fake_ray(*a):
    RAY["calls"] += 1
    return (MDagPath(), RAY["seed"]) if RAY["seed"] is not None else None
ctl._hover_seed = fake_ray
E_PRE = REF._ids[frozenset((1 * W + 0, 1 * W + 1))]
PRESEL["items"] = ["|grid|gridShape.e[%d]" % E_PRE]
MODS["shift"] = True; key(QtCore.QEvent.KeyPress); app.processEvents()
check("Shift + hover: seed = Maya's pre-highlighted edge", seed_xz(core.DRAW["payload"]) == (10.5, 1.0))
check("pre-highlight used without ray casting", RAY["calls"] == 0)
PRESEL["items"] = []; RAY["seed"] = E_SEED; ctl._dirty = True; ctl._tick()
check("no pre-highlight -> ray cast fallback", seed_xz(core.DRAW["payload"]) == (12.5, 2.0) and RAY["calls"] == 1)
PRESEL["items"] = ["|grid|gridShape.vtx[3]"]; ctl._dirty = True; ctl._tick()
check("pre-highlight of another type ignored (edge mode)", seed_xz(core.DRAW["payload"]) == (12.5, 2.0))
RAY["seed"] = None; PRESEL["items"] = []; ctl._dirty = True; ctl._tick()
check("nothing hovered -> last selected", seed_xz(core.DRAW["payload"]) == (11.5, 4.0))
# Maya updates its pre-highlight after our sample: settle ticks catch it without mouse movement
real_pos2 = QtGui.QCursor.pos
CUR = {"p": QtCore.QPoint(5, 5)}
QtGui.QCursor.pos = staticmethod(lambda: CUR["p"])
CUR["p"] = QtCore.QPoint(6, 6); ctl._tick()                 # mouse moved, pre-highlight not ready yet
PRESEL["items"] = ["|grid|gridShape.e[%d]" % E_PRE]         # Maya highlights it a moment later
ctl._tick()                                                   # same cursor position
check("settle tick picks up late pre-highlight", seed_xz(core.DRAW["payload"]) == (10.5, 1.0))
QtGui.QCursor.pos = real_pos2
# face + vertex pre-highlight
STATE["mode"] = "face"; PRESEL["items"] = ["|grid|gridShape.f[%d]" % (3 * NX + 4)]; ctl._dirty = True; ctl._tick()
check("face mode: pre-highlighted face is the seed", seed_xz(core.DRAW["payload"]) == (14.5, 3.5))
STATE["mode"] = "vertex"; PRESEL["items"] = ["|grid|gridShape.vtx[%d]" % (1 * W + 1)]; ctl._dirty = True; ctl._tick()
check("vertex mode: pre-highlighted vertex is the seed", seed_xz(core.DRAW["payload"]) == (11.0, 1.0))
MODS["shift"] = False; key(QtCore.QEvent.KeyRelease)
PRESEL["items"] = []; del ctl._viewport_under_cursor; del ctl._hover_seed; STATE["mode"] = "edge"
# ---------------- hide Maya's red hover highlight while Shift is held
ptp.set_hide_maya_hover(True)
ctl._viewport_under_cursor = lambda *a: ("modelPanel4", View(), 0, 0)
RAY["seed"] = E_PRE; ctl._hover_seed = fake_ray
PRESEL["items"] = ["|grid|gridShape.e[%d]" % E_PRE]; PREFS["preSelectHilite"] = True
STATE["mode"] = "edge"; MODS["shift"] = True; key(QtCore.QEvent.KeyPress); app.processEvents()
check("Shift: Maya's pre-highlight turned off (ray pick validated)",
      PREFS["preSelectHilite"] is False and ctl._ray_validated and STATE["opt"].get("phxTopoPreview_restorePreselect") == 1)
check("hidden: preview still follows hover via ray pick", seed_xz(core.DRAW["payload"]) == (10.5, 1.0))
RAY["seed"] = E_SEED; ctl._dirty = True; ctl._tick()
check("hidden: moving to another edge updates the seed", seed_xz(core.DRAW["payload"]) == (12.5, 2.0))
MODS["shift"] = False; key(QtCore.QEvent.KeyRelease)
check("Shift release restores Maya's pre-highlight",
      PREFS["preSelectHilite"] is True and STATE["opt"].get("phxTopoPreview_restorePreselect") == 0)
# ray pick broken on this machine -> keep Maya's highlight, warn, hover still works via pre-highlight
ctl._ray_validated = False; RAY["seed"] = None
MODS["shift"] = True; key(QtCore.QEvent.KeyPress); app.processEvents()
check("ray pick can't find Maya's hovered edge -> highlight kept on", PREFS["preSelectHilite"] is True and ctl._ray_broken)
check("...and hover still works from Maya's pre-highlight", seed_xz(core.DRAW["payload"]) == (10.5, 1.0))
MODS["shift"] = False; key(QtCore.QEvent.KeyRelease)
ptp.set_hide_maya_hover(True)
check("re-toggling the setting allows a fresh check", not ctl._ray_broken)
# setting off -> never touched
ptp.set_hide_maya_hover(False); RAY["seed"] = E_PRE
MODS["shift"] = True; key(QtCore.QEvent.KeyPress); app.processEvents()
check("setting off: Maya's highlight untouched", PREFS["preSelectHilite"] is True)
MODS["shift"] = False; key(QtCore.QEvent.KeyRelease)
# crash recovery: flag left set by a crash -> enable turns preselect back on
PREFS["preSelectHilite"] = False; STATE["opt"]["phxTopoPreview_restorePreselect"] = 1
ptp._recover_preselect_pref()
check("crash recovery restores Maya's pre-highlight on next start",
      PREFS["preSelectHilite"] is True and STATE["opt"]["phxTopoPreview_restorePreselect"] == 0)
ptp.set_hide_maya_hover(True)
del ctl._viewport_under_cursor; del ctl._hover_seed; PRESEL["items"] = []

# accelerator failure must not break ray picking
MFnMesh.autoUniformGridParams = lambda s: (_ for _ in ()).throw(RuntimeError("no accel"))
for e in ctl._entries.values(): e.accel = None; e.accel_dirty = True
ctl._isect_variant = None
check("ray pick works without accelerator", ctl._hover_seed("edge", View(), 41, 10) is not None)

# stale 1.0.0 plugin still loaded in memory -> unloaded and replaced automatically
LOADS = []
cmds.unloadPlugin = lambda name, force=False: LOADS.append(("unload", name, force))
def loadPlugin(path, quiet=False):
    LOADS.append(("load", os.path.basename(path))); PLUGIN_V["v"] = None
cmds.loadPlugin = loadPlugin
PLUGIN_V["v"] = "1.0.0"
ok_load = ptp._load_plugin()
check("stale plugin in memory: force-unloaded and reloaded from matching file",
      ok_load and LOADS == [("unload", "phoenixTopoPreview.py", True), ("load", "phoenixTopoPreview.py")])
ptp._check_versions()
check("after self-heal versions match", True)
# Maya re-uses a cached module of the old plugin unless it is purged
sys.modules["phoenixTopoPreview"] = types.SimpleNamespace(__file__=ptp._plugin_path(), VERSION="1.1.0")
def loadPlugin_cached(path, quiet=False):
    LOADS.append(("load", os.path.basename(path)))
    PLUGIN_V["v"] = "1.1.0" if "phoenixTopoPreview" in sys.modules else None
cmds.loadPlugin = loadPlugin_cached
PLUGIN_V["v"] = "1.1.0"; del LOADS[:]
check("cached old plugin module purged before load -> new version loads",
      ptp._load_plugin() and PLUGIN_V["v"] is None)
cmds.loadPlugin = lambda path, quiet=False: PLUGIN_V.__setitem__("v", "1.1.0")   # never refreshes
PLUGIN_V["v"] = "1.1.0"
try:
    ptp._load_plugin(); restart_msg = False
except RuntimeError as exc:
    restart_msg = "Restart Maya" in str(exc)
check("plugin that never refreshes -> 'restart Maya once' message", restart_msg)
cmds.loadPlugin = loadPlugin; PLUGIN_V["v"] = None
import tempfile
tmpd = tempfile.mkdtemp(); stale = os.path.join(tmpd, "phoenixTopoPreview.py")
open(stale, "w").write('VERSION = "1.0.0"\n')
real_pp = ptp._plugin_path; ptp._plugin_path = lambda: stale
try:
    ptp._load_plugin(); stale_err = False
except RuntimeError as exc:
    stale_err = stale in str(exc) and "1.0.0" in str(exc)
ptp._plugin_path = real_pp
check("stale plugin FILE on disk: clear error naming it", stale_err)

# stale plugin file -> clear error instead of silently drawing nothing
PLUGIN_V["v"] = "1.0.0"
try:
    ptp._check_versions(); mismatch = False
except RuntimeError as exc:
    mismatch = "phoenixTopoPreview.py plugin is 1.0.0" in str(exc)
check("version mismatch reported with file name", mismatch)
PLUGIN_V["v"] = None
class OldPayload: empty = False
dmo = MUIDrawManager(); data = plugin._PreviewUserData(); data.payload = OldPayload()
core.DRAW.pop("draw_error", None)
plugin.PhoenixTopoPreviewDrawOverride.addUIDrawables(None, None, dmo, None, data)
check("old-format payload: plugin draws nothing and flags it", not dmo.log and core.DRAW.get("draw_error") == "format")
# hover pick crash must not kill the selection preview
ctl._viewport_under_cursor = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
MODS["shift"] = True; key(QtCore.QEvent.KeyPress); app.processEvents()
STATE["mode"] = "edge"; SEL["items"] = [("edge", E_OTHER)]; ptp.set_see_selection(False)
ctl._last_key = None; ctl._update()
check("hover error falls back to the selection seed", core.DRAW["payload"] is not None
      and seed_xz(core.DRAW["payload"]) == (11.5, 4.0))
del ctl._viewport_under_cursor
MODS["shift"] = False; key(QtCore.QEvent.KeyRelease)

# disable cleans up
ctl._delete_helper = lambda **k: None
removed0 = CALLS["removed"]
ctl.disable()
check("disable removes all callbacks", CALLS["removed"] - removed0 >= 9)
check("disable restores user's selection-order pref", PREFS["trackSelectionOrder"] is False)
check("disable clears payload/state/instance",
      core.DRAW["payload"] is None and not ctl._entries and getattr(sys, ptp._INSTANCE_ATTR, None) is None)
MODS["shift"] = True; key(QtCore.QEvent.KeyPress)
check("disabled controller ignores shift", not ctl._active)

print("\n%d/%d smoke checks passed" % (sum(ok), len(ok)))
sys.exit(0 if all(ok) else 1)
