"""
Drag this file into any Maya 2024-2027 viewport to (re)install
Phoenix Topo Loop Preview cleanly.

Every time it runs it:
  1. shuts down the running tool and unloads its VP2 plugin,
  2. deletes every old copy of the three tool files (and their .old /
     cached bytecode) from every folder on Maya's Python path,
  3. copies the new files into  Documents/maya/scripts,
  4. puts one guarded autostart block in that folder's userSetup.py
     (only enables the tool if "Start with Maya" is ticked) and removes
     old blocks from other userSetup.py files,
  5. adds / replaces the shelf button on the current shelf,
  6. imports the new code fresh and enables it.

You can also run it from the Script Editor:
    exec(open(r"C:/path/to/install_phoenix_topo_preview.py").read()); install()
"""
import glob
import importlib
import os
import re
import shutil
import sys
import time
import traceback

import maya.cmds as cmds
import maya.mel as mel

FILES = ("phoenix_topo_core.py", "phoenix_topo_preview.py", "phoenixTopoPreview.py")
MODULES = ("phoenix_topo_preview", "phoenix_topo_core", "phoenixTopoPreview")
SHELF_TAG = "phoenixTopoPreview"
USERSETUP_BEGIN = "# >>> phoenix_topo_preview autostart >>>"
USERSETUP_END = "# <<< phoenix_topo_preview autostart <<<"
USERSETUP_BLOCK = '''%s
def _phx_topo_preview_autostart():
    try:
        import phoenix_topo_preview
        phoenix_topo_preview.autostart()
    except Exception:
        import traceback
        traceback.print_exc()
import maya.utils as _phx_mu
_phx_mu.executeDeferred(_phx_topo_preview_autostart)
%s
''' % (USERSETUP_BEGIN, USERSETUP_END)



def _log(msg):
    print("[Phoenix Topo install] " + msg)


def _src_dir():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def _target_dir():
    """Documents/maya/scripts (shared by all Maya versions)."""
    d = os.path.join(cmds.internalVar(userAppDir=True), "scripts")
    if not os.path.isdir(d):
        os.makedirs(d)
    return os.path.normpath(d)


def _search_dirs():
    dirs = []
    extra = [cmds.internalVar(userScriptDir=True),
             os.path.join(cmds.internalVar(userAppDir=True), "scripts"),
             os.path.join(cmds.internalVar(userAppDir=True), cmds.about(version=True), "plug-ins")]
    for d in list(sys.path) + extra:
        if not d:
            continue
        d = os.path.normpath(os.path.abspath(d))
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs


# --------------------------------------------------------------- steps
def shutdown():
    mod = sys.modules.get("phoenix_topo_preview")
    if mod is not None:
        try:
            mod.unload_plugin()
        except Exception as exc:
            _log("unload warning: %s" % exc)
    old = getattr(sys, "_phoenix_topo_preview_controller", None)
    if old is not None:
        try:
            old.disable()
        except Exception:
            pass
    try:                                   # API delete: no undo entries
        import maya.api.OpenMaya as om
        for n in cmds.ls(type="phoenixTopoPreviewLocator", long=True) or []:
            sel = om.MSelectionList()
            sel.add(n)
            mod = om.MDagModifier()
            mod.deleteNode(om.MFnDagNode(sel.getDependNode(0)).parent(0))
            mod.doIt()
    except Exception:
        pass
    for name in cmds.pluginInfo(q=True, listPlugins=True) or []:
        if os.path.splitext(name)[0] == "phoenixTopoPreview":
            try:
                cmds.unloadPlugin(name, force=True)
            except Exception as exc:
                _log("plugin unload warning: %s" % exc)
    for name in MODULES:
        sys.modules.pop(name, None)
    try:
        delattr(sys, "_phoenix_topo_preview_controller")
    except Exception:
        pass
    importlib.invalidate_caches()


def remove_old_copies(src):
    removed = []
    for d in _search_dirs():
        if os.path.normpath(d) == os.path.normpath(src):
            continue                      # never delete the files we install from
        for f in FILES:
            for path in [os.path.join(d, f)] + glob.glob(os.path.join(d, f + ".old*")):
                if os.path.isfile(path):
                    try:
                        os.remove(path)
                        removed.append(path)
                    except Exception as exc:
                        _log("could not remove %s: %s" % (path, exc))
            for pyc in glob.glob(os.path.join(d, "__pycache__", f[:-3] + ".*.pyc")):
                try:
                    os.remove(pyc)
                except Exception:
                    pass
    for r in removed:
        _log("removed old copy: " + r)
    return removed


def _file_version(path):
    try:
        with open(path, "r") as fh:
            for line in fh:
                m = re.match(r"""VERSION\s*=\s*["']([^"']+)["']""", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def check_source(src):
    """The folder we install from must hold one consistent build."""
    missing = [f for f in FILES if not os.path.isfile(os.path.join(src, f))]
    if missing:
        raise RuntimeError("Missing files next to the installer: %s" % ", ".join(missing))
    versions = {f: _file_version(os.path.join(src, f)) for f in FILES}
    if len(set(versions.values())) != 1 or None in versions.values():
        raise RuntimeError(
            "The folder you installed from has mixed versions:\n%s\n\n%s\n\n"
            "Delete that folder, extract the latest zip again (overwrite all files) and retry."
            % (src, "\n".join("%s = %s" % kv for kv in versions.items())))
    return versions[FILES[0]]


def copy_files(src, dst):
    if os.path.normpath(src) != os.path.normpath(dst):
        now = time.time()
        for f in FILES:
            target = os.path.join(dst, f)
            shutil.copyfile(os.path.join(src, f), target)
            os.utime(target, (now, now))
            _log("installed: " + target)
    while dst in sys.path:
        sys.path.remove(dst)
    sys.path.insert(0, dst)
    importlib.invalidate_caches()


def install_usersetup(dst):
    for d in _search_dirs():
        path = os.path.join(d, "userSetup.py")
        if not os.path.isfile(path):
            continue
        with open(path, "r") as fh:
            text = fh.read()
        if USERSETUP_BEGIN not in text:
            continue
        start = text.index(USERSETUP_BEGIN)
        end = text.find(USERSETUP_END, start)
        end = len(text) if end < 0 else end + len(USERSETUP_END)
        text = (text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")).strip("\n")
        with open(path, "w") as fh:
            fh.write(text + ("\n" if text else ""))
    path = os.path.join(dst, "userSetup.py")
    text = open(path).read() if os.path.isfile(path) else ""
    with open(path, "a") as fh:
        if text and not text.endswith("\n"):
            fh.write("\n")
        fh.write("\n" + USERSETUP_BLOCK)


def install_shelf_button():
    """Replace the TopoLoop shelf button (themed Phoenix icon; click = toggle,
    double-click = settings, right-click = toggles) on the current shelf."""
    import phoenix_topo_preview
    return phoenix_topo_preview.install_shelf_button()


def install():
    src = _src_dir()
    dst = _target_dir()
    version = check_source(src)
    _log("installing %s from %s" % (version, src))
    shutdown()
    remove_old_copies(src)
    copy_files(src, dst)
    install_usersetup(dst)
    import phoenix_topo_preview
    import phoenix_topo_core
    try:
        shelf = install_shelf_button()
    except Exception:
        traceback.print_exc()
        shelf = None
    phoenix_topo_preview.enable()
    _log("loaded %s from %s" % (phoenix_topo_preview.VERSION, phoenix_topo_preview.__file__))
    _log("core %s from %s" % (getattr(phoenix_topo_core, "VERSION", "?"), phoenix_topo_core.__file__))
    msg = ("Phoenix Topo Loop Preview %s installed cleanly to:\n%s\n\n"
           "Old copies removed from other folders (see Script Editor).\n"
           "Shelf button: %s\n\n"
           "ON now: enter vertex / edge / face mode and hold Shift."
           % (phoenix_topo_preview.VERSION, dst, shelf or "not added (no shelf found)"))
    cmds.confirmDialog(title="Phoenix Topo Loop Preview", message=msg, button=["OK"])
    return dst


def onMayaDroppedPythonFile(*args, **kwargs):
    # Maya may hand us a cached copy of an older installer module; make the
    # next drag re-read this file from disk.
    sys.modules.pop("install_phoenix_topo_preview", None)
    try:
        install()
    except Exception as exc:
        traceback.print_exc()
        cmds.confirmDialog(title="Phoenix Topo Loop Preview",
                           message="Install failed:\n%s\n\nSee Script Editor." % exc,
                           button=["OK"])
