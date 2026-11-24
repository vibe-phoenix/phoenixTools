"""
phoenixTopoPreview.py  --  Phoenix Topo Loop Preview, Viewport 2.0 draw plugin.

Maya 2024-2027, Python API 2.0.

Registers one locator node type whose MPxDrawOverride draws the current
preview payload (written by phoenix_topo_preview.py into
phoenix_topo_core.DRAW) with MUIDrawManager. The controller creates a
single hidden helper instance of this node: non-selectable, hidden in the
Outliner, never saved, created outside the undo queue.

Load through phoenix_topo_preview.enable(); don't create the node by hand.
"""
import os
import sys

import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr
import maya.api.OpenMayaUI as omui


def maya_useNewAPI():
    pass


NODE_NAME = "phoenixTopoPreviewLocator"
NODE_ID = om.MTypeId(0x0007F7A1)          # local/dev id range 0x0007F000-0x0007FFFF
DRAW_CLASSIFICATION = "drawdb/geometry/phoenixTopoPreview"
DRAW_REGISTRANT = "phoenixTopoPreviewPlugin"
VERSION = "1.2.0"
PAYLOAD_FORMAT = 2

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.append(_HERE)
except NameError:
    _HERE = None


def _core():
    try:
        import phoenix_topo_core
        return phoenix_topo_core
    except Exception:
        return None


def _payload():
    core = _core()
    if core is None:
        return None
    return core.DRAW.get("payload")


def _base_priority():
    for name in ("sActiveLineDepthPriority", "sActiveWireDepthPriority"):
        v = getattr(omr.MRenderItem, name, None)
        if isinstance(v, int):
            return v
    return 5


_NON_SELECTABLE = getattr(omr.MUIDrawManager, "kNonSelectable", None)
_LINE_SOLID = getattr(omr.MUIDrawManager, "kSolid", 0)
_LINE_DASHED = getattr(omr.MUIDrawManager, "kShortDashed",
                       getattr(omr.MUIDrawManager, "kDashed", 1))


# ---------------------------------------------------------------- node
class PhoenixTopoPreviewLocator(omui.MPxLocatorNode):
    def __init__(self):
        omui.MPxLocatorNode.__init__(self)

    @staticmethod
    def creator():
        return PhoenixTopoPreviewLocator()

    @staticmethod
    def initialize():
        pass

    def isBounded(self):
        return False

    def excludeAsLocator(self):
        # keep drawing even when Show > Locators is off
        return False


# ---------------------------------------------------------------- draw override
class _PreviewUserData(om.MUserData):
    def __init__(self):
        try:
            om.MUserData.__init__(self, False)
        except TypeError:
            om.MUserData.__init__(self)
        self.payload = None


def _noop_draw(context, data):
    return


class PhoenixTopoPreviewDrawOverride(omr.MPxDrawOverride):
    @staticmethod
    def creator(obj):
        return PhoenixTopoPreviewDrawOverride(obj)

    def __init__(self, obj):
        # isAlwaysDirty=True: prepareForDraw runs every refresh, which only
        # swaps a reference to the payload (cheap).
        omr.MPxDrawOverride.__init__(self, obj, _noop_draw, True)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kAllDevices

    def isBounded(self, objPath, cameraPath):
        return False

    def boundingBox(self, objPath, cameraPath):
        return om.MBoundingBox()

    def hasUIDrawables(self):
        return True

    def prepareForDraw(self, objPath, cameraPath, frameContext, oldData):
        data = oldData if isinstance(oldData, _PreviewUserData) else _PreviewUserData()
        data.payload = _payload()
        return data

    def addUIDrawables(self, objPath, dm, frameContext, data):
        p = getattr(data, "payload", None)
        if p is None or getattr(p, "empty", True):
            return
        if not hasattr(p, "layers"):
            core = _core()
            if core is not None and not core.DRAW.get("draw_error"):
                core.DRAW["draw_error"] = "format"
                om.MGlobal.displayError(
                    "Phoenix Topo Preview: phoenix_topo_preview.py and phoenixTopoPreview.py are "
                    "from different versions. Drag install_phoenix_topo_preview.py into Maya to reinstall.")
            return
        try:
            self._draw(p, dm)
        except Exception as exc:          # never let a draw error spam/abort VP2
            core = _core()
            if core is not None and not core.DRAW.get("draw_error"):
                core.DRAW["draw_error"] = str(exc)
                om.MGlobal.displayWarning("Phoenix Topo Preview draw error: %s" % exc)

    @staticmethod
    def _draw(p, dm):
        """Draw p.layers: [(kind, MPointArray, MColor, size, style, priority)]
        kind 0 = triangles, 1 = line pairs, 2 = points; style 0 solid, 1 dashed."""
        base = _base_priority()
        if _NON_SELECTABLE is not None:
            try:
                dm.beginDrawable(_NON_SELECTABLE, 0)
            except TypeError:
                dm.beginDrawable()
        else:
            dm.beginDrawable()
        xray = bool(p.xray)
        if xray:
            dm.beginDrawInXray()

        for kind, pts, color, size, style, prio in p.layers:
            n = len(pts) if pts is not None else 0
            if not n:
                continue
            dm.setDepthPriority(base + int(prio))
            dm.setColor(color)
            if kind == 0:
                if n >= 3:
                    dm.mesh(omr.MUIDrawManager.kTriangles, pts)
            elif kind == 1:
                if n >= 2:
                    dm.setLineWidth(max(1.0, float(size)))
                    dm.setLineStyle(_LINE_DASHED if style == 1 else _LINE_SOLID)
                    dm.mesh(omr.MUIDrawManager.kLines, pts)
            else:
                dm.setPointSize(max(1.0, float(size)))
                dm.points(pts, False)

        dm.setLineStyle(_LINE_SOLID)
        if xray:
            dm.endDrawInXray()
        dm.endDrawable()


# ---------------------------------------------------------------- registration
def initializePlugin(mobject):
    plugin = om.MFnPlugin(mobject, "Phoenix (Anurag Tantarpale)", VERSION, "Any")
    plugin.registerNode(NODE_NAME, NODE_ID,
                        PhoenixTopoPreviewLocator.creator,
                        PhoenixTopoPreviewLocator.initialize,
                        om.MPxNode.kLocatorNode, DRAW_CLASSIFICATION)
    try:
        omr.MDrawRegistry.registerDrawOverrideCreator(
            DRAW_CLASSIFICATION, DRAW_REGISTRANT,
            PhoenixTopoPreviewDrawOverride.creator)
    except Exception:
        plugin.deregisterNode(NODE_ID)
        raise


def uninitializePlugin(mobject):
    plugin = om.MFnPlugin(mobject)
    core = _core()
    if core is not None:
        for hook in list(core.UNLOAD_HOOKS):
            try:
                hook()
            except Exception:
                pass
        core.DRAW["payload"] = None
    try:
        omr.MDrawRegistry.deregisterDrawOverrideCreator(DRAW_CLASSIFICATION, DRAW_REGISTRANT)
    finally:
        plugin.deregisterNode(NODE_ID)
