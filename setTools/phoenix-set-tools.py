# Maya Python — PySide2 / PySide6 compatible UI
# ------------------------------------------------------------
# Phoenix Set Tools — retro pixel panel (same look as Phoenix Autosave)
#   Set element : Save Element · Group Ungrouped · Import Blockout
#                 Export Groups · Duplicate · Clean Up · Notes · Group + Rename
#   Navigate    : < Prev · Main · Next >
#   Create Folder Structure
#
# Double-click the SET TOOLS title for SETTINGS:
#   RETRO STYLE  — off = plain default Maya/Qt look (Settings button appears)
#   DOCKABLE     — on  = Maya workspace control; off = frameless rounded window
#   Presets · Customize colours · Shelf icon (film clapper)
#
# INSTALL: drag install_set_tools.py (kept next to this file) into the Maya
#   viewport — copies this script, adds the shelf button and opens the panel.
# Everything is remembered between sessions (optionVars).
# ------------------------------------------------------------

import os
import re
import shutil
import maya.cmds as cmds
import maya.mel as mel
import maya.OpenMayaUI as omui
import maya.app.general.mayaMixin

try:
    from PySide6 import QtWidgets, QtCore
    from shiboken6 import wrapInstance
except Exception:
    from PySide2 import QtWidgets, QtCore
    from shiboken2 import wrapInstance


EMPTY_MA_SOURCE = r"C:\icons-phoenix\mayaFile\setEmptyFile.ma"

# -----------------------------
# Qt / Maya helpers
# -----------------------------
def maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None


def undo_chunk(fn):
    def _wrapped(*args, **kwargs):
        cmds.undoInfo(openChunk=True)
        try:
            return fn(*args, **kwargs)
        finally:
            cmds.undoInfo(closeChunk=True)
    return _wrapped


def safe_fs_name(name):
    name = name.replace(":", "_")
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name.strip(" .")


def camel_case_name(name):
    """Convert 'flower pot 01' -> 'flowerPot01'"""
    parts = re.split(r'[\s_\-]+', name.strip())
    if not parts:
        return name
    result = parts[0].lower()
    for part in parts[1:]:
        result += part[0].upper() + part[1:].lower() if part else ""
    return result


def strip_special_for_name(name):
    """Remove apostrophes and other non-filename chars, keep alphanumeric + common chars"""
    return re.sub(r"['\"]", "", name)


def get_default_project_name():
    scene_path = cmds.file(q=True, sn=True)
    if not scene_path:
        return "project"
    base = os.path.basename(scene_path)
    name, _ = os.path.splitext(base)
    return name or "project"


def ensure_obj_export_plugin():
    try:
        if not cmds.pluginInfo("objExport", q=True, loaded=True):
            cmds.loadPlugin("objExport")
        return True
    except Exception:
        return False


def ensure_fbx_export_plugin():
    try:
        if not cmds.pluginInfo("fbxmaya", q=True, loaded=True):
            cmds.loadPlugin("fbxmaya")
        return True
    except Exception:
        return False


def is_new_empty_scene():
    """Check if current scene is a new, unsaved, empty scene."""
    scene_path = cmds.file(q=True, sn=True)
    if scene_path:
        return False
    all_nodes = cmds.ls(dag=True, long=True) or []
    default_nodes = {"persp", "top", "front", "side", "perspShape", "topShape", "frontShape", "sideShape"}
    non_default = [n for n in all_nodes if n.lstrip("|").split("|")[0] not in default_nodes]
    return len(non_default) == 0


# -----------------------------
# Scene helpers
# -----------------------------
def is_mesh_transform(xform_long):
    shapes = cmds.listRelatives(xform_long, shapes=True, fullPath=True) or []
    return any(cmds.nodeType(s) == "mesh" for s in shapes)


def is_group_transform(xform_long):
    if not cmds.objExists(xform_long) or cmds.nodeType(xform_long) != "transform":
        return False
    if cmds.listRelatives(xform_long, shapes=True, fullPath=True) or []:
        return False
    kids = cmds.listRelatives(xform_long, children=True, type="transform", fullPath=True) or []
    return bool(kids)


def top_level_mesh_transforms():
    all_t = cmds.ls(type="transform", long=True) or []
    top = [t for t in all_t if not (cmds.listRelatives(t, parent=True, fullPath=True) or [])]
    return [t for t in top if is_mesh_transform(t)]


def top_level_groups_only():
    transforms = cmds.ls(type="transform", long=True) or []
    top = [t for t in transforms if not (cmds.listRelatives(t, parent=True, fullPath=True) or [])]
    return [t for t in top if is_group_transform(t)]


def _top_level_of(nodes_long):
    s = set(nodes_long)
    out = []
    for n in nodes_long:
        p = cmds.listRelatives(n, parent=True, fullPath=True) or []
        if not p or p[0] not in s:
            out.append(n)
    return out


def _world_bbox(obj_long):
    return cmds.exactWorldBoundingBox(obj_long)


def _bottom_center_from_bb(bb):
    minx, miny, minz, maxx, maxy, maxz = bb
    return [(minx + maxx) * 0.5, miny, (minz + maxz) * 0.5]


def _move_by_delta_world(obj_long, delta_xyz):
    cmds.move(delta_xyz[0], delta_xyz[1], delta_xyz[2], obj_long, r=True, ws=True)


def _ensure_position_refs_group():
    if cmds.objExists("positionRefs"):
        matches = cmds.ls("positionRefs", long=True) or []
        for m in matches:
            if not (cmds.listRelatives(m, parent=True, fullPath=True) or []):
                return m
        return matches[0]
    grp = cmds.group(em=True, name="positionRefs")
    return cmds.ls(grp, long=True)[0]


def _add_or_set_double_attr(node, attr, value):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, ln=attr, at="double", dv=value)
        cmds.setAttr(f"{node}.{attr}", e=True, keyable=False, channelBox=False)
    cmds.setAttr(f"{node}.{attr}", value)


# ------------------------------------------------------------
# ACTIONS
# ------------------------------------------------------------
@undo_chunk
def action_group_all_ungrouped(*_):
    meshes = top_level_mesh_transforms()
    if not meshes:
        cmds.inViewMessage(amg="No ungrouped (top-level) meshes found.", pos="topCenter", fade=True)
        return

    for geo_long in meshes:
        name = geo_long.split("|")[-1]
        try:
            tmp = cmds.group(em=True, name=name + "_TMP_GRP#")
            m = cmds.xform(geo_long, q=True, ws=True, m=True)
            cmds.xform(tmp, ws=True, m=m)
            geo_child = cmds.parent(geo_long, tmp)[0]
            cmds.rename(tmp, name)
            cmds.rename(geo_child, name)
        except Exception as e:
            cmds.warning(f"Skipped {name}: {e}")

    cmds.inViewMessage(amg="Grouped all ungrouped meshes.", pos="topCenter", fade=True)


# ============================================================
# CREATE SET STRUCTURE  (Shift+Click on Save Set Element)
# ============================================================

def _build_set_folder_name(base_name, element_name, use_underscore):
    """Build the set element folder name from set name + element camelCase name."""
    clean_base = strip_special_for_name(base_name)
    if use_underscore:
        return f"{clean_base}_{element_name}"
    else:
        # capitalise first letter of element_name when appending without underscore
        elem_cap = element_name[0].upper() + element_name[1:] if element_name else element_name
        return f"{clean_base}{elem_cap}"


def _build_set_file_name(set_name, file_type, with_number, is_blockout=False, version=1):
    """
    Build set-level maya file name.
    file_type: "set" | "props"
    with_number: bool  (checkbox "with _01")
    """
    clean = strip_special_for_name(set_name)
    v_str = f"v{version:02d}"
    if is_blockout:
        if with_number:
            return f"prj_set_{clean}_blockout_01_{v_str}.ma"
        else:
            return f"prj_set_{clean}_blockout_{v_str}.ma"
    else:
        if with_number:
            return f"prj_set_{clean}_mod_01_{v_str}.ma"
        else:
            return f"prj_set_{clean}_mod_{v_str}.ma"


def _build_element_file_name(set_name, element_folder_name, with_number, use_underscore, version=1):
    """Build props maya file name for a set element."""
    clean_set = strip_special_for_name(set_name)
    v_str = f"v{version:02d}"
    if use_underscore:
        base = f"{clean_set}_{element_folder_name.replace(clean_set + '_', '')}"
    else:
        base = element_folder_name

    if with_number:
        return f"prj_props_{base}_mod_01_{v_str}.ma"
    else:
        return f"prj_props_{base}_mod_{v_str}.ma"


class CreateSetStructureDialog(QtWidgets.QDialog):
    """
    Panel opened on Shift+Left-Click of Save Set Element button.
    Creates the full folder structure for a new set.
    """
    def __init__(self, root_folder, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Create Set Structure")
        self.setObjectName("PhoenixCreateSetStructureDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(600, 680)

        self._root_folder = root_folder
        self._set_name = os.path.basename(root_folder)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Set name display
        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("Set Name:"))
        self.lbl_set_name = QtWidgets.QLabel(f"<b>{self._set_name}</b>")
        self.lbl_set_name.setToolTip(root_folder)
        name_row.addWidget(self.lbl_set_name)
        name_row.addStretch(1)
        layout.addLayout(name_row)

        sep1 = QtWidgets.QFrame()
        sep1.setFrameShape(QtWidgets.QFrame.HLine)
        layout.addWidget(sep1)

        # Options row
        opts_box = QtWidgets.QGroupBox("Naming & Folder Options")
        opts_layout = QtWidgets.QVBoxLayout(opts_box)

        self.cb_underscore = QtWidgets.QCheckBox("Use underscore separator  (set_element  vs  setElement)")
        self.cb_underscore.setChecked(False)
        opts_layout.addWidget(self.cb_underscore)

        self.cb_with_number = QtWidgets.QCheckBox("Include version number in filename  (_mod_01_v01  vs  _mod_v01)")
        self.cb_with_number.setChecked(False)
        opts_layout.addWidget(self.cb_with_number)

        dir_row = QtWidgets.QHBoxLayout()
        self.cb_use_obj = QtWidgets.QCheckBox("Use 'obj' subfolder name  (mod/obj  vs  mod)")
        self.cb_use_obj.setChecked(False)
        dir_row.addWidget(self.cb_use_obj)
        self.cb_use_srcimgs = QtWidgets.QCheckBox("Use 'srcimgs'  (vs  sourceImages)")
        self.cb_use_srcimgs.setChecked(False)
        dir_row.addWidget(self.cb_use_srcimgs)
        opts_layout.addLayout(dir_row)

        layout.addWidget(opts_box)

        # Set Elements list
        layout.addWidget(QtWidgets.QLabel("Set Element Names  (one per line — spaces/underscores converted to camelCase):"))

        self.elements_edit = QtWidgets.QPlainTextEdit()
        self.elements_edit.setPlaceholderText("flower pot 01\nalcohol A\nbook shelf\n...")
        self.elements_edit.setMinimumHeight(160)
        layout.addWidget(self.elements_edit)

        # Live preview
        layout.addWidget(QtWidgets.QLabel("Preview (first element):"))
        self.lbl_preview = QtWidgets.QLabel("")
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(self.lbl_preview)

        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.HLine)
        layout.addWidget(sep2)

        self.buttons = QtWidgets.QDialogButtonBox()
        self.btn_create = self.buttons.addButton("Create Set Structure", QtWidgets.QDialogButtonBox.AcceptRole)
        self.btn_cancel = self.buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(self.buttons)

        self.elements_edit.textChanged.connect(self._update_preview)
        self.cb_underscore.toggled.connect(self._update_preview)
        self.cb_with_number.toggled.connect(self._update_preview)
        self.buttons.accepted.connect(self._on_create)
        self.buttons.rejected.connect(self.reject)

        self._update_preview()

    def _parse_elements(self):
        raw = self.elements_edit.toPlainText()
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        return lines

    def _update_preview(self):
        lines = self._parse_elements()
        use_underscore = self.cb_underscore.isChecked()
        with_number = self.cb_with_number.isChecked()

        if not lines:
            self.lbl_preview.setText("(add element names above to see preview)")
            return

        first = lines[0]
        camel = camel_case_name(first)
        folder_name = _build_set_folder_name(self._set_name, camel, use_underscore)
        set_file = _build_set_file_name(self._set_name, "set", with_number)
        elem_file = _build_element_file_name(self._set_name, folder_name, with_number, use_underscore)

        preview = (
            f"Set maya file:  {set_file}\n"
            f"Element folder: ...\\setElements\\{folder_name}\\\n"
            f"Element maya:   {elem_file}"
        )
        self.lbl_preview.setText(preview)

    def _on_create(self):
        lines = self._parse_elements()
        use_underscore = self.cb_underscore.isChecked()

        # Check for duplicates
        camel_names = [camel_case_name(l) for l in lines]
        seen = set()
        dupes = []
        for n in camel_names:
            if n in seen:
                dupes.append(n)
            seen.add(n)
        if dupes:
            QtWidgets.QMessageBox.warning(
                self, "Duplicate Names",
                f"Duplicate element names detected:\n{', '.join(dupes)}\n\nPlease make names unique."
            )
            return
        self.accept()

    def get_options(self):
        lines = self._parse_elements()
        return {
            "root_folder": self._root_folder,
            "set_name": self._set_name,
            "elements": [camel_case_name(l) for l in lines],
            "use_underscore": self.cb_underscore.isChecked(),
            "with_number": self.cb_with_number.isChecked(),
            "use_obj": self.cb_use_obj.isChecked(),
            "use_srcimgs": self.cb_use_srcimgs.isChecked(),
        }


def _get_mod_folder_name(use_obj):
    return "obj" if use_obj else "mod"


def _get_images_folder_name(use_srcimgs):
    return "srcimgs" if use_srcimgs else "sourceImages"


def _create_set_structure(opts):
    root = opts["root_folder"]
    set_name = opts["set_name"]
    use_underscore = opts["use_underscore"]
    with_number = opts["with_number"]
    use_obj = opts["use_obj"]
    use_srcimgs = opts["use_srcimgs"]
    element_names = opts["elements"]

    mod_name = _get_mod_folder_name(use_obj)
    imgs_name = _get_images_folder_name(use_srcimgs)

    # Main set folders
    main_dirs = [
        os.path.join(root, "maya"),
        os.path.join(root, "maya", "blockout"),
        os.path.join(root, mod_name),
        os.path.join(root, mod_name, "blockout"),
        os.path.join(root, "renders"),
        os.path.join(root, imgs_name),
        os.path.join(root, "ref"),
        os.path.join(root, "setElements"),
    ]
    for d in main_dirs:
        os.makedirs(d, exist_ok=True)

    # Save main maya file
    set_file = _build_set_file_name(set_name, "set", with_number)
    main_ma_path = os.path.join(root, "maya", set_file)
    cmds.file(rename=main_ma_path)
    cmds.file(save=True, type="mayaAscii")

    # Create set element folders
    set_elements_root = os.path.join(root, "setElements")
    for elem_camel in element_names:
        folder_name = _build_set_folder_name(set_name, elem_camel, use_underscore)
        elem_root = os.path.join(set_elements_root, folder_name)

        elem_dirs = [
            os.path.join(elem_root, "maya"),
            os.path.join(elem_root, mod_name),
            os.path.join(elem_root, mod_name, "blockout"),
            os.path.join(elem_root, "ref"),
            os.path.join(elem_root, imgs_name),
        ]
        for d in elem_dirs:
            os.makedirs(d, exist_ok=True)

        # Create element maya file (empty .ma)
        elem_file = _build_element_file_name(set_name, folder_name, with_number, use_underscore)
        elem_ma_path = os.path.join(elem_root, "maya", elem_file)
        with open(elem_ma_path, 'w') as f:
            f.write("//Maya ASCII 2024 scene\n//Placeholder\n")

    return main_ma_path


def action_create_set_structure(*_):
    """Shift+Left-Click on Save Set Element."""
    if not is_new_empty_scene():
        QtWidgets.QMessageBox.warning(
            maya_main_window(),
            "Not an Empty Scene",
            "This function requires a new, empty (unsaved) scene.\n"
            "Please open a new scene (File > New Scene) before creating a set structure."
        )
        return

    folder = cmds.fileDialog2(dialogStyle=2, fm=3, caption="Select the Set Root Folder")
    if not folder:
        return
    root_folder = folder[0]
    if not os.path.isdir(root_folder):
        cmds.warning("Selected path is not a valid folder.")
        return

    dlg = CreateSetStructureDialog(root_folder)
    result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
    if result != QtWidgets.QDialog.Accepted:
        return

    opts = dlg.get_options()
    try:
        saved_path = _create_set_structure(opts)
        cmds.inViewMessage(
            amg=f"Set structure created! Saved: {os.path.basename(saved_path)}",
            pos="topCenter", fade=True
        )
    except Exception as e:
        QtWidgets.QMessageBox.critical(maya_main_window(), "Error", f"Failed to create set structure:\n{e}")


# ============================================================
# CREATE FOLDER STRUCTURE  (standalone bottom button)
# ============================================================

# Quality / Type token maps — matches the naming convention exactly as specified:
#   hiIntDetectivesOffice   = High Poly + Interior          + "Detectives Office"
#   LowExtParkingOfBuilding = Low Poly  + Exterior           + "Parking Of Building"
#   hiInt-ExtRoomBalcony    = High Poly + Interior/Exterior  + "Room Balcony"
_TOPFOLDER_QUALITY_TOKENS = {
    "High Poly": "hi",
    "Low Poly":  "Low",
}
_TOPFOLDER_TYPE_TOKENS = {
    "Interior":           "Int",
    "Exterior":            "Ext",
    "Interior/Exterior":  "Int-Ext",
}

# NOTE: the model-export and source-images folder names are user-choosable
# (see CreateFolderStructureDialog) and persisted via Maya optionVars so the
# user's preferred default sticks across sessions.
_OPTIONVAR_MOD_DEFAULT = "phoenixSetTools_topFolder_modName"
_OPTIONVAR_IMAGES_DEFAULT = "phoenixSetTools_topFolder_imagesName"

_MOD_FOLDER_CHOICES = ["mod", "obj"]
_IMAGES_FOLDER_CHOICES = ["sourceImages", "srcImages"]

# Built-in fallback defaults, used only until the user sets their own.
_MOD_FOLDER_FALLBACK = "obj"
_IMAGES_FOLDER_FALLBACK = "srcImages"


def _get_default_mod_folder_name():
    try:
        if cmds.optionVar(exists=_OPTIONVAR_MOD_DEFAULT):
            val = cmds.optionVar(q=_OPTIONVAR_MOD_DEFAULT)
            if val in _MOD_FOLDER_CHOICES:
                return val
    except Exception:
        pass
    return _MOD_FOLDER_FALLBACK


def _set_default_mod_folder_name(value):
    if value in _MOD_FOLDER_CHOICES:
        cmds.optionVar(sv=(_OPTIONVAR_MOD_DEFAULT, value))


def _get_default_images_folder_name():
    try:
        if cmds.optionVar(exists=_OPTIONVAR_IMAGES_DEFAULT):
            val = cmds.optionVar(q=_OPTIONVAR_IMAGES_DEFAULT)
            if val in _IMAGES_FOLDER_CHOICES:
                return val
    except Exception:
        pass
    return _IMAGES_FOLDER_FALLBACK


def _set_default_images_folder_name(value):
    if value in _IMAGES_FOLDER_CHOICES:
        cmds.optionVar(sv=(_OPTIONVAR_IMAGES_DEFAULT, value))


def _build_top_set_folder_name(quality_label, type_label, set_name_raw):
    """
    Build the top-level set folder name from the two dropdowns + Set name field.
    Set name is camelCased (spaces removed, 2nd+ words capitalised), then the
    first letter of the whole thing is capitalised so it reads correctly when
    appended after the quality/type tokens.
        "detectives office" -> camelCase "detectivesOffice" -> "DetectivesOffice"
    """
    camel = camel_case_name(set_name_raw)
    cap = (camel[0].upper() + camel[1:]) if camel else camel
    q_tok = _TOPFOLDER_QUALITY_TOKENS.get(quality_label, "hi")
    t_tok = _TOPFOLDER_TYPE_TOKENS.get(type_label, "Int")
    return f"{q_tok}{t_tok}{cap}"


def _create_top_level_folder_structure(parent_dir, quality_label, type_label, set_name_raw,
                                        mod_folder_name, images_folder_name):
    """
    Creates:
        <parent_dir>/<folder_name>/
            maya/
                prj_set_<folder_name>_mod_v01.ma          (copied from EMPTY_MA_SOURCE)
                blockouts/
                    prj_set_<folder_name>_blockout_mod_v01.ma   (copy of the mod file above)
            ref/
            setElements/
            <mod_folder_name>/      ("mod" or "obj" — user-selected)
            <images_folder_name>/   ("sourceImages" or "srcImages" — user-selected)
    Returns a dict with root, folder_name, mod_path, blockout_path.
    """
    if not os.path.isfile(EMPTY_MA_SOURCE):
        raise RuntimeError(f"Template file not found:\n{EMPTY_MA_SOURCE}")

    folder_name = safe_fs_name(_build_top_set_folder_name(quality_label, type_label, set_name_raw))
    root = os.path.join(parent_dir, folder_name)

    maya_dir       = os.path.join(root, "maya")
    blockouts_dir  = os.path.join(maya_dir, "blockouts")
    ref_dir        = os.path.join(root, "ref")
    set_elems_dir  = os.path.join(root, "setElements")
    mod_dir        = os.path.join(root, mod_folder_name)
    imgs_dir       = os.path.join(root, images_folder_name)

    for d in (maya_dir, blockouts_dir, ref_dir, set_elems_dir, mod_dir, imgs_dir):
        os.makedirs(d, exist_ok=True)

    mod_filename      = f"prj_set_{folder_name}_mod_v01.ma"
    blockout_filename = f"prj_set_{folder_name}_blockout_mod_v01.ma"

    mod_path      = os.path.join(maya_dir, mod_filename)
    blockout_path = os.path.join(blockouts_dir, blockout_filename)

    shutil.copyfile(EMPTY_MA_SOURCE, mod_path)
    shutil.copyfile(mod_path, blockout_path)

    return {
        "root": root,
        "folder_name": folder_name,
        "mod_path": mod_path,
        "blockout_path": blockout_path,
    }


class CreateFolderStructureDialog(QtWidgets.QDialog):
    """
    Opened by the 'Create Folder Structure' button.
    Quality + Type dropdowns and a Set name field build the top-level folder name.
    Model-export and source-images folder names are user-choosable dropdowns,
    each with a 'Set Default' button to persist the choice for next time.
    """
    def __init__(self, parent_dir, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Create Folder Structure")
        self.setObjectName("PhoenixCreateFolderStructureDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(480, 440)

        self._parent_dir = parent_dir

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        loc_lbl = QtWidgets.QLabel(f"Location:  {parent_dir}")
        loc_lbl.setWordWrap(True)
        loc_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        loc_lbl.setToolTip(parent_dir)
        layout.addWidget(loc_lbl)

        form = QtWidgets.QFormLayout()

        self.quality_combo = QtWidgets.QComboBox()
        self.quality_combo.addItems(["High Poly", "Low Poly"])
        form.addRow("Quality:", self.quality_combo)

        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(["Interior", "Exterior", "Interior/Exterior"])
        form.addRow("Type:", self.type_combo)

        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText("e.g. detectives office")
        form.addRow("Set name:", self.name_edit)

        layout.addLayout(form)

        # ── Folder naming options (user-choosable, with persisted defaults) ──
        folders_box = QtWidgets.QGroupBox("Folder Naming")
        folders_layout = QtWidgets.QFormLayout(folders_box)

        mod_row = QtWidgets.QHBoxLayout()
        self.mod_combo = QtWidgets.QComboBox()
        self.mod_combo.addItems(_MOD_FOLDER_CHOICES)
        self.mod_combo.setCurrentText(_get_default_mod_folder_name())
        mod_row.addWidget(self.mod_combo, 1)
        self.btn_mod_default = QtWidgets.QPushButton("Set Default")
        self.btn_mod_default.setFixedWidth(90)
        self.btn_mod_default.setToolTip("Remember the current selection as the default for next time.")
        self.btn_mod_default.clicked.connect(self._save_mod_default)
        mod_row.addWidget(self.btn_mod_default)
        folders_layout.addRow("Model folder:", mod_row)

        imgs_row = QtWidgets.QHBoxLayout()
        self.imgs_combo = QtWidgets.QComboBox()
        self.imgs_combo.addItems(_IMAGES_FOLDER_CHOICES)
        self.imgs_combo.setCurrentText(_get_default_images_folder_name())
        imgs_row.addWidget(self.imgs_combo, 1)
        self.btn_imgs_default = QtWidgets.QPushButton("Set Default")
        self.btn_imgs_default.setFixedWidth(90)
        self.btn_imgs_default.setToolTip("Remember the current selection as the default for next time.")
        self.btn_imgs_default.clicked.connect(self._save_imgs_default)
        imgs_row.addWidget(self.btn_imgs_default)
        folders_layout.addRow("Images folder:", imgs_row)

        layout.addWidget(folders_box)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        layout.addWidget(sep)

        layout.addWidget(QtWidgets.QLabel("Preview:"))
        self.lbl_preview = QtWidgets.QLabel("")
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(self.lbl_preview)

        layout.addStretch(1)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Create Folder Structure")
        layout.addWidget(self.buttons)

        self.quality_combo.currentIndexChanged.connect(self._update_preview)
        self.type_combo.currentIndexChanged.connect(self._update_preview)
        self.name_edit.textChanged.connect(self._update_preview)
        self.mod_combo.currentIndexChanged.connect(self._update_preview)
        self.imgs_combo.currentIndexChanged.connect(self._update_preview)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

        self._update_preview()

    def _save_mod_default(self):
        _set_default_mod_folder_name(self.mod_combo.currentText())
        cmds.inViewMessage(
            amg=f"Default model folder set to '{self.mod_combo.currentText()}'",
            pos="topCenter", fade=True, fadeStayTime=2000
        )

    def _save_imgs_default(self):
        _set_default_images_folder_name(self.imgs_combo.currentText())
        cmds.inViewMessage(
            amg=f"Default images folder set to '{self.imgs_combo.currentText()}'",
            pos="topCenter", fade=True, fadeStayTime=2000
        )

    def _update_preview(self):
        set_name_raw = self.name_edit.text().strip()
        if not set_name_raw:
            self.lbl_preview.setText("(enter a set name above to see preview)")
            return
        folder_name = safe_fs_name(_build_top_set_folder_name(
            self.quality_combo.currentText(), self.type_combo.currentText(), set_name_raw
        ))
        mod_file = f"prj_set_{folder_name}_mod_v01.ma"
        blockout_file = f"prj_set_{folder_name}_blockout_mod_v01.ma"
        preview = (
            f"{folder_name}\\\n"
            f"  maya\\{mod_file}\n"
            f"  maya\\blockouts\\{blockout_file}\n"
            f"  ref\\\n"
            f"  setElements\\\n"
            f"  {self.mod_combo.currentText()}\\\n"
            f"  {self.imgs_combo.currentText()}\\"
        )
        self.lbl_preview.setText(preview)

    def _on_ok(self):
        set_name_raw = self.name_edit.text().strip()
        if not set_name_raw:
            QtWidgets.QMessageBox.warning(self, "No Set Name", "Please enter a set name.")
            return
        folder_name = safe_fs_name(_build_top_set_folder_name(
            self.quality_combo.currentText(), self.type_combo.currentText(), set_name_raw
        ))
        target = os.path.join(self._parent_dir, folder_name)
        if os.path.isdir(target):
            QtWidgets.QMessageBox.warning(
                self, "Folder Already Exists",
                f"A folder named '{folder_name}' already exists at this location.\n"
                "Please choose a different set name, or pick another location."
            )
            return
        self.accept()

    def quality(self):       return self.quality_combo.currentText()
    def type_(self):         return self.type_combo.currentText()
    def set_name(self):      return self.name_edit.text().strip()
    def mod_folder_name(self):    return self.mod_combo.currentText()
    def images_folder_name(self): return self.imgs_combo.currentText()


def action_create_folder_structure(*_):
    """
    'Create Folder Structure' button.
    1. Ask where to create the new set folder.
    2. Show CreateFolderStructureDialog (Quality / Type / Set name / folder-naming options).
    3. Build the folder + file structure, open the blockout file, notify the user.
    """
    folder = cmds.fileDialog2(dialogStyle=2, fm=3, caption="Select Location for New Set Folder")
    if not folder:
        return
    parent_dir = folder[0]
    if not os.path.isdir(parent_dir):
        cmds.warning("Selected path is not a valid folder.")
        return

    dlg = CreateFolderStructureDialog(parent_dir)
    result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
    if result != QtWidgets.QDialog.Accepted:
        return

    try:
        info = _create_top_level_folder_structure(
            parent_dir, dlg.quality(), dlg.type_(), dlg.set_name(),
            dlg.mod_folder_name(), dlg.images_folder_name()
        )
    except Exception as e:
        QtWidgets.QMessageBox.critical(maya_main_window(), "Error", f"Failed to create folder structure:\n{e}")
        return

    try:
        cmds.file(info["blockout_path"], open=True, force=True)
    except Exception as e:
        QtWidgets.QMessageBox.critical(
            maya_main_window(), "Error",
            f"Folder structure was created, but the blockout file could not be opened:\n{e}"
        )
        return

    cmds.inViewMessage(
        amg=f"You are now in the blockout file of '{info['folder_name']}'",
        pos="topCenter", fade=True, fadeStayTime=3000
    )


# -----------------------------
# Export dialog (OBJ/FBX)
# -----------------------------
class ExportSetupDialog(QtWidgets.QDialog):
    def __init__(self, groups_long, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Export Groups — Setup")
        self.setObjectName("PhoenixExportSetupDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(560, 740)

        self._groups_long = list(groups_long)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        prefix_row = QtWidgets.QHBoxLayout()
        prefix_row.addWidget(QtWidgets.QLabel("Prefix:"))
        self.prefix_edit = QtWidgets.QLineEdit(get_default_project_name())
        self.prefix_edit.selectAll()
        prefix_row.addWidget(self.prefix_edit, 1)
        self.no_prefix_cb = QtWidgets.QCheckBox("No prefix")
        self.no_prefix_cb.setChecked(False)
        self.no_prefix_cb.setToolTip("Export FBX named after the group only, with no prefix.")
        prefix_row.addWidget(self.no_prefix_cb)
        layout.addLayout(prefix_row)

        self.no_prefix_cb.toggled.connect(lambda v: self.prefix_edit.setEnabled(not v))

        opts_box = QtWidgets.QGroupBox("Export Options")
        opts_layout = QtWidgets.QVBoxLayout(opts_box)

        self.blockout_cb = QtWidgets.QCheckBox("Groups are blockout")
        self.blockout_cb.setChecked(False)
        self.blockout_cb.setToolTip(
            "When checked, exports to mod/<group>_blockout/ instead of mod/"
        )
        opts_layout.addWidget(self.blockout_cb)

        self.create_ma_cb = QtWidgets.QCheckBox("Create .ma files  (copies from setEmptyFile.ma template)")
        self.create_ma_cb.setChecked(False)
        self.create_ma_cb.setToolTip(
            f"Copies {EMPTY_MA_SOURCE} into each group's maya folder,\n"
            "renamed according to prefix + group name."
        )
        opts_layout.addWidget(self.create_ma_cb)

        self.underscore_cb = QtWidgets.QCheckBox("With underscore  (prefix_Group  vs  prefixGroup)")
        self.underscore_cb.setChecked(True)
        opts_layout.addWidget(self.underscore_cb)

        self.with_number_cb = QtWidgets.QCheckBox("Include version number  (_mod_01_v01  vs  _mod_v01)")
        self.with_number_cb.setChecked(False)
        opts_layout.addWidget(self.with_number_cb)

        dir_row2 = QtWidgets.QHBoxLayout()
        self.cb_use_obj = QtWidgets.QCheckBox("Use 'obj' folder  (mod/obj)")
        self.cb_use_obj.setChecked(False)
        dir_row2.addWidget(self.cb_use_obj)
        self.cb_use_srcimgs = QtWidgets.QCheckBox("Use 'srcimgs'  (vs sourceImages)")
        self.cb_use_srcimgs.setChecked(False)
        dir_row2.addWidget(self.cb_use_srcimgs)
        opts_layout.addLayout(dir_row2)

        layout.addWidget(opts_box)

        search_row = QtWidgets.QHBoxLayout()
        search_row.addWidget(QtWidgets.QLabel("Search:"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter groups...")
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)

        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.listw, 1)

        hint = QtWidgets.QLabel("Select group(s) you DON'T want to export.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_select_all = QtWidgets.QPushButton("Select All (Exclude)")
        self.btn_clear = QtWidgets.QPushButton("Clear Selection")
        btn_row.addWidget(self.btn_select_all)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(self.buttons)

        self._populate()
        self.search.textChanged.connect(self._filter)
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_clear.clicked.connect(self.listw.clearSelection)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

    def _populate(self):
        self.listw.clear()
        def short_name(longp): return longp.split("|")[-1]
        for g_long in sorted(self._groups_long, key=lambda x: short_name(x).lower()):
            item = QtWidgets.QListWidgetItem(short_name(g_long))
            item.setData(QtCore.Qt.UserRole, g_long)
            item.setToolTip(g_long)
            self.listw.addItem(item)

    def _filter(self, text):
        t = (text or "").strip().lower()
        for i in range(self.listw.count()):
            item = self.listw.item(i)
            longp = item.data(QtCore.Qt.UserRole)
            item.setHidden(not (t in item.text().lower() or t in str(longp).lower()))

    def _select_all(self):
        self.listw.clearSelection()
        for i in range(self.listw.count()):
            item = self.listw.item(i)
            if not item.isHidden():
                item.setSelected(True)

    def _on_ok(self):
        if not self.no_prefix_cb.isChecked():
            p = (self.prefix_edit.text() or "").strip()
            if not p:
                QtWidgets.QMessageBox.warning(self, "Prefix Required", "Please enter a prefix, or check 'No prefix'.")
                return
        self.accept()

    def prefix(self):
        if self.no_prefix_cb.isChecked():
            return ""
        return (self.prefix_edit.text() or "").strip()
    def is_blockout(self): return bool(self.blockout_cb.isChecked())
    def create_ma_files(self): return bool(self.create_ma_cb.isChecked())
    def use_underscore(self): return bool(self.underscore_cb.isChecked())
    def with_number(self): return bool(self.with_number_cb.isChecked())
    def use_obj_folder(self): return bool(self.cb_use_obj.isChecked())
    def use_srcimgs_folder(self): return bool(self.cb_use_srcimgs.isChecked())
    def excluded_groups_set(self): return {item.data(QtCore.Qt.UserRole) for item in self.listw.selectedItems()}


def _build_export_folder_name(prefix, group_short, use_underscore):
    clean_prefix = strip_special_for_name(prefix)
    clean_group = group_short
    if not clean_prefix:
        return clean_group
    if use_underscore:
        return f"{clean_prefix}_{clean_group}"
    else:
        g_cap = clean_group[0].upper() + clean_group[1:] if clean_group else clean_group
        return f"{clean_prefix}{g_cap}"


def _build_export_ma_name(prefix, group_short, use_underscore, with_number, is_blockout=False, version=1):
    folder_name = _build_export_folder_name(prefix, group_short, use_underscore)
    v_str = f"v{version:02d}"
    suffix = "blockout" if is_blockout else "mod"
    if with_number:
        return f"prj_props_{folder_name}_{suffix}_01_{v_str}.ma"
    else:
        return f"prj_props_{folder_name}_{suffix}_{v_str}.ma"


def export_group_to_structure(base_folder, prefix, group_long, blockout=False,
                               use_underscore=True, with_number=False,
                               use_obj=False, use_srcimgs=False,
                               create_ma=False):
    group_short = group_long.split("|")[-1]
    folder_name = _build_export_folder_name(prefix, group_short, use_underscore)
    root = os.path.join(base_folder, safe_fs_name(folder_name))

    mod_name = _get_mod_folder_name(use_obj)
    imgs_name = _get_images_folder_name(use_srcimgs)

    for d in ("maya", mod_name, imgs_name, "ref"):
        os.makedirs(os.path.join(root, d), exist_ok=True)

    if not ensure_fbx_export_plugin():
        cmds.warning("FBX plugin (fbxmaya) could not be loaded.")
        return None

    if blockout:
        export_dir = os.path.join(root, mod_name, safe_fs_name(f"{group_short}_blockout"))
    else:
        export_dir = os.path.join(root, mod_name)
    os.makedirs(export_dir, exist_ok=True)

    fbx_path = os.path.join(export_dir, safe_fs_name(group_short) + ".fbx")
    cmds.select(group_long, r=True)
    fbx_path_mel = fbx_path.replace("\\", "/")
    mel.eval("FBXResetExport;")
    mel.eval(f'FBXExport -f "{fbx_path_mel}" -s;')

    if create_ma:
        maya_folder = os.path.join(root, "maya")
        ma_name = _build_export_ma_name(prefix, group_short, use_underscore, with_number, blockout)
        ma_dest = os.path.join(maya_folder, ma_name)
        if os.path.isfile(EMPTY_MA_SOURCE):
            shutil.copy2(EMPTY_MA_SOURCE, ma_dest)
        else:
            with open(ma_dest, 'w') as f:
                f.write("//Maya ASCII 2024 scene\n//Placeholder\n")

    return fbx_path




# ============================================================
# SHIFT+CLICK EXPORT — Smooth, Centre, Cleanup, FBX, Revert
# ============================================================

def _get_asset_root_from_scene_path(scene_path):
    """Walk up from the maya file to find the set-element root (parent of 'maya' folder)."""
    if not scene_path:
        return None
    d = os.path.dirname(scene_path)
    if os.path.basename(d).lower() == "maya":
        return os.path.dirname(d)
    return d


def _current_element_root():
    """
    Derive the current set-element root folder from the open scene path.
    Scene is expected to be at:  <element_root>/maya/<filename>.ma
    Returns the element_root path, or None if it cannot be determined.
    """
    scene_path = cmds.file(q=True, sn=True)
    if not scene_path:
        return None
    d = os.path.dirname(scene_path)
    if os.path.basename(d).lower() == "maya":
        return os.path.dirname(d)   # element_root
    return None


def _scene_set_element_roots():
    """
    Return every sibling set-element folder from the current scene's setElements parent.
    Scene is expected at: <set_root>/setElements/<element>/maya/<file>.ma
    So:  scene → maya/ → element/ → setElements/ → (all siblings)
    """
    scene_path = cmds.file(q=True, sn=True)
    if not scene_path:
        return []
    maya_dir    = os.path.dirname(scene_path)           # .../element/maya
    elem_dir    = os.path.dirname(maya_dir)             # .../element
    se_dir      = os.path.dirname(elem_dir)             # .../setElements  (hopefully)

    if os.path.basename(se_dir).lower() == "setelements" and os.path.isdir(se_dir):
        return [
            os.path.join(se_dir, n)
            for n in sorted(os.listdir(se_dir))
            if os.path.isdir(os.path.join(se_dir, n))
        ]

    # Fallback: look for a setElements sibling next to elem_dir
    parent = os.path.dirname(elem_dir)
    se_dir2 = os.path.join(parent, "setElements")
    if os.path.isdir(se_dir2):
        return [
            os.path.join(se_dir2, n)
            for n in sorted(os.listdir(se_dir2))
            if os.path.isdir(os.path.join(se_dir2, n))
        ]
    return []


def _mod_obj_folder_for_element(element_root):
    """
    Return the export folder inside a set element root.
    Checks for 'mod' first, then 'obj' at the top of the element folder.
    Creates 'mod' if neither exists.
    Never looks inside mod/obj — the FBX goes directly in mod/ or obj/.
    """
    for sub in ("mod", "obj"):
        p = os.path.join(element_root, sub)
        if os.path.isdir(p):
            return p
    # Neither exists — create mod/
    p = os.path.join(element_root, "mod")
    os.makedirs(p, exist_ok=True)
    return p


def _get_bounding_box_world(nodes_long):
    """Return combined world bounding box of a list of nodes."""
    all_bb = [cmds.exactWorldBoundingBox(n) for n in nodes_long]
    if not all_bb:
        return None
    minx = min(b[0] for b in all_bb)
    miny = min(b[1] for b in all_bb)
    minz = min(b[2] for b in all_bb)
    maxx = max(b[3] for b in all_bb)
    maxy = max(b[4] for b in all_bb)
    maxz = max(b[5] for b in all_bb)
    return minx, miny, minz, maxx, maxy, maxz


def _centre_groups_to_origin_y0(groups_long):
    """
    Move each group so its bounding-box base sits at Y=0 and its XZ centre is at world origin.
    Returns a dict {group_long: (tx, ty, tz)} of the translation deltas applied,
    so we can revert afterwards.
    """
    deltas = {}
    for g in groups_long:
        bb = cmds.exactWorldBoundingBox(g)
        minx, miny, minz, maxx, maxy, maxz = bb
        cx = (minx + maxx) * 0.5
        cz = (minz + maxz) * 0.5
        dx = -cx
        dy = -miny
        dz = -cz
        cmds.move(dx, dy, dz, g, r=True, ws=True)
        deltas[g] = (dx, dy, dz)
    return deltas


def _smooth_meshes_in_groups(groups_long, divisions):
    """
    Apply polySmooth to every mesh shape inside the groups, matching Maya's own
    MEL output when you select a group and run Mesh > Smooth:
      - Target: mesh shape nodes (by short name), not transforms
      - mth=0  : Maya Catmull-Clark (not OpenSubdiv — mth=2 fails silently)
      - sdt=2  : subdivision type
      - ovb=1, ofb=1 : boundary rules
      - dv     : user-specified divisions
      - kb=1, ksb=1  : keep border / keep selection border
      - sl=1   : smoothness
      - ps=0.1 : push strength (corner preservation)
      - ro=1   : round sharp
      - ch=1   : keep construction history (needed so undo works)
    """
    smooth_nodes = []
    for g in groups_long:
        all_transforms = (
            cmds.listRelatives(g, allDescendents=True, fullPath=True, type="transform") or []
        )
        all_transforms = [g] + all_transforms

        for xform in all_transforms:
            shapes = cmds.listRelatives(xform, shapes=True, fullPath=True, type="mesh") or []
            shapes = [s for s in shapes if not cmds.getAttr(f"{s}.intermediateObject")]
            if not shapes:
                continue

            for shape in shapes:
                # polySmooth needs the short name of the shape, just like Maya's MEL output
                shape_short = shape.split("|")[-1]
                try:
                    cmds.select(shape_short, r=True)
                    result = cmds.polySmooth(
                        shape_short,
                        mth=0,          # Maya Catmull-Clark
                        sdt=2,          # subdivision type
                        ovb=1,          # OpenSubdiv vertex boundary
                        ofb=1,          # OpenSubdiv face boundary
                        ofc=0,          # face-varying
                        ost=0,
                        ocr=0,
                        dv=divisions,   # subdivision level
                        bnr=1,
                        c=1,
                        kb=1,           # keep border
                        ksb=1,          # keep selection border
                        khe=0,
                        kt=1,
                        kmb=1,
                        suv=1,
                        peh=0,
                        sl=1,           # smoothness
                        dpe=1,
                        ps=0.1,         # push strength — corner preservation
                        ro=1,           # round sharp
                        ch=1,           # construction history (required for undo)
                    )
                    if result:
                        smooth_nodes.extend(result)
                except Exception as e:
                    cmds.warning(f"[PhoenixExport] Smooth failed on {shape_short}: {e}")

    cmds.select(clear=True)
    return smooth_nodes


def _run_phoenix_cleanup_on_groups(groups_long):
    """Run the Phoenix cleanup routine on the given groups."""
    import maya.cmds as _cmds

    def _process_node(node):
        _cmds.xform(node, centerPivots=True)
        _cmds.makeIdentity(node, apply=True, t=True, r=True, s=True, n=False, pn=True)
        _cmds.delete(node, constructionHistory=True)

    def _get_all_descendants(root):
        descendants = _cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
        return [root] + descendants

    def _is_group_node(node):
        shapes = _cmds.listRelatives(node, shapes=True, fullPath=True) or []
        return len(shapes) == 0

    def _is_mesh_node(node):
        shapes = _cmds.listRelatives(node, shapes=True, fullPath=True) or []
        return any(_cmds.nodeType(s) == 'mesh' for s in shapes)

    def _is_pos_ref(node):
        short = node.split('|')[-1].lower()
        return 'positionref' in short

    all_nodes = []
    seen = set()
    for r in groups_long:
        for node in _get_all_descendants(r):
            if node not in seen:
                seen.add(node)
                all_nodes.append(node)

    transforms = [n for n in all_nodes
                  if _cmds.nodeType(n) == 'transform' and not _is_pos_ref(n)]
    transforms.sort(key=lambda n: n.count('|'), reverse=True)

    errors = []
    for node in transforms:
        try:
            _process_node(node)
        except Exception as e:
            errors.append(node)
            _cmds.warning(f"[PhoenixExport] Cleanup skipped {node}: {e}")


def _export_groups_as_single_fbx(groups_long, fbx_path):
    """Select all groups and export as one merged FBX."""
    if not ensure_fbx_export_plugin():
        raise RuntimeError("FBX plugin could not be loaded.")
    cmds.select(groups_long, r=True)
    fbx_path_mel = fbx_path.replace("\\", "/")
    mel.eval("FBXResetExport;")
    mel.eval(f'FBXExport -f "{fbx_path_mel}" -s;')


# ── Step 1: mode picker ──────────────────────────────────────────────────────

class ShiftExportModePicker(QtWidgets.QDialog):
    """
    First window shown on Shift+Click Export Groups.
    Two big buttons: Current Session | All Set Elements.
    """
    # Returned by exec() via done()
    MODE_SESSION  = 2
    MODE_ALL      = 3

    def __init__(self, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Phoenix — Export FBX")
        self.setObjectName("PhoenixShiftExportModePicker")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setFixedSize(280, 110)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        lbl = QtWidgets.QLabel("Select export scope:")
        lbl.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(10)

        self.btn_session = QtWidgets.QPushButton("Current Session")
        self.btn_all     = QtWidgets.QPushButton("All Set Elements")

        for b in (self.btn_session, self.btn_all):
            b.setMinimumHeight(40)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self.btn_session.setToolTip(
            "Export groups from the currently open Maya session."
        )
        self.btn_all.setToolTip(
            "Iterate every set element folder found on disk and export each one."
        )

        btn_row.addWidget(self.btn_session)
        btn_row.addWidget(self.btn_all)
        layout.addLayout(btn_row)

        self.btn_session.clicked.connect(lambda: self.done(self.MODE_SESSION))
        self.btn_all.clicked.connect(lambda: self.done(self.MODE_ALL))


# ── Step 2a: Current Session config dialog ───────────────────────────────────

class ShiftExportSessionDialog(QtWidgets.QDialog):
    """
    Config dialog for Current Session mode.
    Destination is always auto-derived from the current scene path:
        <element_root>/mod/   or   <element_root>/obj/
    No browse button — just groups, smooth, and FBX name.
    """
    def __init__(self, groups_long, element_root, dest_folder, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Export FBX — Current Session")
        self.setObjectName("PhoenixShiftExportSessionDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(500, 480)

        self._groups_long  = list(groups_long)
        self._dest_folder  = dest_folder
        self._element_root = element_root

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ── Destination info (read-only) ──────────────────────────
        info_box = QtWidgets.QGroupBox("Export Destination  (auto)")
        info_layout = QtWidgets.QVBoxLayout(info_box)
        dest_lbl = QtWidgets.QLabel(dest_folder)
        dest_lbl.setWordWrap(True)
        dest_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        dest_lbl.setToolTip(dest_folder)
        info_layout.addWidget(dest_lbl)
        layout.addWidget(info_box)

        # ── Group list ────────────────────────────────────────────
        layout.addWidget(QtWidgets.QLabel("Groups to export  (select one or more):"))
        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.listw.setMinimumHeight(150)
        for g in sorted(self._groups_long, key=lambda x: x.split("|")[-1].lower()):
            short = g.split("|")[-1]
            it = QtWidgets.QListWidgetItem(short)
            it.setData(QtCore.Qt.UserRole, g)
            it.setToolTip(g)
            self.listw.addItem(it)
        if self.listw.count():
            self.listw.selectAll()
        layout.addWidget(self.listw, 1)

        # ── Smooth ────────────────────────────────────────────────
        smooth_box = QtWidgets.QGroupBox("Smooth")
        smooth_layout = QtWidgets.QHBoxLayout(smooth_box)
        self.smooth_cb = QtWidgets.QCheckBox("Apply smooth divisions:")
        self.smooth_cb.setChecked(True)
        self.smooth_spin = QtWidgets.QSpinBox()
        self.smooth_spin.setRange(0, 6)
        self.smooth_spin.setValue(2)
        self.smooth_spin.setMinimumWidth(55)
        smooth_layout.addWidget(self.smooth_cb)
        smooth_layout.addWidget(self.smooth_spin)
        smooth_layout.addStretch(1)
        layout.addWidget(smooth_box)
        self.smooth_cb.toggled.connect(self.smooth_spin.setEnabled)

        # ── FBX name ──────────────────────────────────────────────
        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("FBX name:"))
        self.name_edit = QtWidgets.QLineEdit(os.path.basename(element_root) if element_root else "")
        self.name_edit.setPlaceholderText("filename (no extension)")
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        # ── Buttons ───────────────────────────────────────────────
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        self.buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Export FBX")
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

    def _on_ok(self):
        if not self.listw.selectedItems():
            QtWidgets.QMessageBox.warning(self, "No Groups", "Select at least one group.")
            return
        if not self.name_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "No Name", "Enter a name for the FBX.")
            return
        self.accept()

    def selected_groups(self):  return [it.data(QtCore.Qt.UserRole) for it in self.listw.selectedItems()]
    def smooth_enabled(self):   return bool(self.smooth_cb.isChecked())
    def smooth_divisions(self): return int(self.smooth_spin.value())
    def mesh_name(self):        return self.name_edit.text().strip()
    def destination(self):      return self._dest_folder


# ── Step 2b: Per-element config dialog (All Set Elements mode) ───────────────

# Sentinel return codes
_ELEM_EXPORT = QtWidgets.QDialog.Accepted   # = 1
_ELEM_SKIP   = 2
_ELEM_CANCEL = QtWidgets.QDialog.Rejected   # = 0

class ShiftExportElementDialog(QtWidgets.QDialog):
    """
    Shown once per set-element folder when running 'All Set Elements' mode.
    Destination is always <element_root>/mod/ or <element_root>/obj/ — auto-resolved.
    Buttons: Export FBX | Skip | Cancel All.
    """
    def __init__(self, element_root, groups_long, parent=maya_main_window()):
        super().__init__(parent)
        elem_name = os.path.basename(element_root)
        self.setWindowTitle(f"Export FBX — {elem_name}")
        self.setObjectName("PhoenixShiftExportElementDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(480, 460)

        self._element_root = element_root
        self._dest_folder  = _mod_obj_folder_for_element(element_root)
        self._groups_long  = list(groups_long)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Element header + auto dest
        hdr = QtWidgets.QLabel(f"<b>Set Element:</b>  {elem_name}")
        hdr.setToolTip(element_root)
        layout.addWidget(hdr)

        dest_lbl = QtWidgets.QLabel(self._dest_folder)
        dest_lbl.setWordWrap(True)
        dest_lbl.setStyleSheet("color: #aaa; font-size: 11px;")
        dest_lbl.setToolTip(self._dest_folder)
        layout.addWidget(dest_lbl)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        layout.addWidget(sep)

        # ── Group list ────────────────────────────────────────────
        layout.addWidget(QtWidgets.QLabel("Groups to export  (select one or more):"))
        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.listw.setMinimumHeight(130)
        for g in sorted(self._groups_long, key=lambda x: x.split("|")[-1].lower()):
            short = g.split("|")[-1]
            it = QtWidgets.QListWidgetItem(short)
            it.setData(QtCore.Qt.UserRole, g)
            it.setToolTip(g)
            self.listw.addItem(it)
        if self.listw.count():
            self.listw.selectAll()
        layout.addWidget(self.listw, 1)

        # ── Smooth ────────────────────────────────────────────────
        smooth_box = QtWidgets.QGroupBox("Smooth")
        smooth_layout = QtWidgets.QHBoxLayout(smooth_box)
        self.smooth_cb = QtWidgets.QCheckBox("Apply smooth divisions:")
        self.smooth_cb.setChecked(True)
        self.smooth_spin = QtWidgets.QSpinBox()
        self.smooth_spin.setRange(0, 6)
        self.smooth_spin.setValue(2)
        self.smooth_spin.setMinimumWidth(55)
        smooth_layout.addWidget(self.smooth_cb)
        smooth_layout.addWidget(self.smooth_spin)
        smooth_layout.addStretch(1)
        layout.addWidget(smooth_box)
        self.smooth_cb.toggled.connect(self.smooth_spin.setEnabled)

        # ── FBX name ──────────────────────────────────────────────
        name_row = QtWidgets.QHBoxLayout()
        name_row.addWidget(QtWidgets.QLabel("FBX name:"))
        self.name_edit = QtWidgets.QLineEdit(elem_name)
        name_row.addWidget(self.name_edit, 1)
        layout.addLayout(name_row)

        # ── Buttons: Export | Skip | Cancel All ───────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        self.btn_export = QtWidgets.QPushButton("Export FBX")
        self.btn_skip   = QtWidgets.QPushButton("Skip")
        self.btn_cancel = QtWidgets.QPushButton("Cancel All")
        for b in (self.btn_export, self.btn_skip, self.btn_cancel):
            b.setMinimumHeight(36)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.btn_skip.setToolTip("Skip this element and continue to the next.")
        self.btn_cancel.setToolTip("Abort the entire All Set Elements run.")
        btn_row.addWidget(self.btn_export)
        btn_row.addWidget(self.btn_skip)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        self.btn_export.clicked.connect(self._on_export)
        self.btn_skip.clicked.connect(lambda: self.done(_ELEM_SKIP))
        self.btn_cancel.clicked.connect(self.reject)

    def _on_export(self):
        if not self.listw.selectedItems():
            QtWidgets.QMessageBox.warning(self, "No Groups", "Select at least one group.")
            return
        if not self.name_edit.text().strip():
            QtWidgets.QMessageBox.warning(self, "No Name", "Enter a name for the FBX.")
            return
        self.accept()

    def selected_groups(self):  return [it.data(QtCore.Qt.UserRole) for it in self.listw.selectedItems()]
    def smooth_enabled(self):   return bool(self.smooth_cb.isChecked())
    def smooth_divisions(self): return int(self.smooth_spin.value())
    def mesh_name(self):        return self.name_edit.text().strip()
    def destination(self):      return self._dest_folder


# ── Shared export execution ───────────────────────────────────────────────────

def _execute_shift_export(chosen_groups, mesh_name, dest_folder, do_smooth, smooth_divs):
    """
    Core export routine used by both session and all-elements modes.
    1. Centre groups to Y=0 world origin.
    2. Smooth meshes (optional).
    3. Phoenix cleanup (freeze + delete history).
    4. Export as single FBX.
    5. Undo the whole chunk — restores scene to pre-export state.
    Returns the exported fbx path on success.
    """
    if not os.path.isdir(dest_folder):
        os.makedirs(dest_folder, exist_ok=True)

    fbx_path = os.path.join(dest_folder, safe_fs_name(mesh_name) + ".fbx")

    cmds.undoInfo(openChunk=True, chunkName="PhoenixShiftExport")
    try:
        _centre_groups_to_origin_y0(chosen_groups)
        if do_smooth and smooth_divs > 0:
            _smooth_meshes_in_groups(chosen_groups, smooth_divs)
        _run_phoenix_cleanup_on_groups(chosen_groups)
        _export_groups_as_single_fbx(chosen_groups, fbx_path)
    finally:
        cmds.undoInfo(closeChunk=True)

    cmds.undo()   # revert everything: positions, smooth, history
    return fbx_path


# ── Entry point ───────────────────────────────────────────────────────────────

def action_shift_export_groups(*_):
    """
    Shift+Click on Export Groups.
    Step 1 — show mode picker (Current Session | All Set Elements).
    Step 2a — Current Session: pick groups + config → export → revert.
    Step 2b — All Set Elements: iterate every element folder, show per-element
               config dialog with Export / Skip / Cancel All.
    """
    # ── Step 1: mode picker ───────────────────────────────────────
    picker = ShiftExportModePicker()
    mode = picker.exec_() if hasattr(picker, "exec_") else picker.exec()

    # ── Step 2a: Current Session ──────────────────────────────────
    if mode == ShiftExportModePicker.MODE_SESSION:
        transforms = cmds.ls(type="transform", long=True) or []
        groups_long = [t for t in transforms if is_group_transform(t)]
        if not groups_long:
            cmds.inViewMessage(amg="No groups in scene to export.", pos="topCenter", fade=True)
            return

        # Derive destination from scene path
        element_root = _current_element_root()
        if not element_root:
            QtWidgets.QMessageBox.warning(
                maya_main_window(),
                "Cannot Derive Path",
                "The scene does not appear to be inside a set element's maya/ folder.\n"
                "Expected: <element_root>/maya/<file>.ma"
            )
            return
        dest_folder = _mod_obj_folder_for_element(element_root)

        dlg = ShiftExportSessionDialog(groups_long, element_root, dest_folder)
        result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
        if result != QtWidgets.QDialog.Accepted:
            return

        chosen = dlg.selected_groups()
        if not chosen:
            return

        prev_sel = cmds.ls(sl=True, long=True) or []
        try:
            fbx_path = _execute_shift_export(
                chosen, dlg.mesh_name(), dlg.destination(),
                dlg.smooth_enabled(), dlg.smooth_divisions()
            )
            cmds.inViewMessage(
                amg=f"Exported → {os.path.basename(fbx_path)}  (scene reverted)",
                pos="topCenter", fade=True, fadeStayTime=3000
            )
        except Exception as e:
            QtWidgets.QMessageBox.critical(maya_main_window(), "Export Failed", str(e))
        finally:
            try:
                cmds.select(prev_sel, r=True) if prev_sel else cmds.select(clear=True)
            except Exception:
                cmds.select(clear=True)

    # ── Step 2b: All Set Elements ─────────────────────────────────
    elif mode == ShiftExportModePicker.MODE_ALL:
        element_roots = _scene_set_element_roots()
        if not element_roots:
            QtWidgets.QMessageBox.warning(
                maya_main_window(),
                "No Set Elements Found",
                "Could not locate a 'setElements' folder relative to the current scene.\n"
                "Please make sure the scene is saved inside a set element's maya/ folder."
            )
            return

        # Gather current-scene groups (same groups for every element — they share the scene)
        transforms = cmds.ls(type="transform", long=True) or []
        groups_long = [t for t in transforms if is_group_transform(t)]
        if not groups_long:
            cmds.inViewMessage(amg="No groups in scene to export.", pos="topCenter", fade=True)
            return

        exported_count = 0
        skipped_count  = 0

        for elem_root in sorted(element_roots):
            dlg = ShiftExportElementDialog(elem_root, groups_long)
            result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()

            if result == _ELEM_SKIP:
                skipped_count += 1
                continue
            if result == QtWidgets.QDialog.Rejected:   # Cancel All
                break

            # Accepted — export
            chosen = dlg.selected_groups()
            if not chosen:
                skipped_count += 1
                continue

            prev_sel = cmds.ls(sl=True, long=True) or []
            try:
                _execute_shift_export(
                    chosen, dlg.mesh_name(), dlg.destination(),
                    dlg.smooth_enabled(), dlg.smooth_divisions()
                )
                exported_count += 1
            except Exception as e:
                cmds.warning(f"[PhoenixExport] Failed for {os.path.basename(elem_root)}: {e}")
            finally:
                try:
                    cmds.select(prev_sel, r=True) if prev_sel else cmds.select(clear=True)
                except Exception:
                    cmds.select(clear=True)

        if exported_count or skipped_count:
            cmds.inViewMessage(
                amg=f"Export done — {exported_count} exported, {skipped_count} skipped  (scene reverted)",
                pos="topCenter", fade=True, fadeStayTime=3500
            )


@undo_chunk
def action_export_groups(*_):
    scene_path = cmds.file(q=True, sn=True)
    if not scene_path:
        cmds.warning("Please save the scene before exporting.")
        return

    transforms = cmds.ls(type="transform", long=True) or []
    groups_long = [t for t in transforms if is_group_transform(t)]
    if not groups_long:
        cmds.inViewMessage(amg="No groups found to export.", pos="topCenter", fade=True)
        return

    dlg = ExportSetupDialog(groups_long)
    result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
    if result != QtWidgets.QDialog.Accepted:
        return

    prefix = dlg.prefix()
    blockout = dlg.is_blockout()
    excluded = dlg.excluded_groups_set()
    create_ma = dlg.create_ma_files()
    use_underscore = dlg.use_underscore()
    with_number = dlg.with_number()
    use_obj = dlg.use_obj_folder()
    use_srcimgs = dlg.use_srcimgs_folder()

    base = cmds.fileDialog2(dialogStyle=2, fm=3, caption="Choose Base Export Location")
    if not base:
        return
    base_folder = base[0]

    prev_sel = cmds.ls(sl=True, long=True) or []
    for g_long in groups_long:
        if g_long in excluded:
            continue
        try:
            export_group_to_structure(
                base_folder, prefix, g_long, blockout=blockout,
                use_underscore=use_underscore, with_number=with_number,
                use_obj=use_obj, use_srcimgs=use_srcimgs, create_ma=create_ma
            )
        except Exception as e:
            cmds.warning(f"Failed exporting {g_long.split('|')[-1]}: {e}")

    if prev_sel:
        cmds.select(prev_sel, r=True)
    else:
        cmds.select(clear=True)

    cmds.inViewMessage(amg="Export complete.", pos="topCenter", fade=True)


# -----------------------------
# Duplicate + positionRef
# -----------------------------
@undo_chunk
def action_duplicate_with_position_reference(*_):
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Select a mesh transform or a group transform first.")
        return

    src = sel[0]
    src_short = src.split("|")[-1]

    dup = cmds.duplicate(src, rr=True)[0]
    dup = cmds.rename(dup, f"{src_short}_dup")
    dup_long = cmds.ls(dup, long=True)[0]

    bb = _world_bbox(dup_long)
    minx, miny, minz, maxx, maxy, maxz = bb
    sx, sy, sz = (maxx - minx), (maxy - miny), (maxz - minz)
    cx, cy, cz = (minx + maxx) * 0.5, (miny + maxy) * 0.5, (minz + maxz) * 0.5

    cube_name = f"{src_short}_positionRef"
    cube = cmds.polyCube(name=cube_name + "#")[0]
    cube_long = cmds.ls(cube, long=True)[0]

    cmds.xform(cube_long, ws=True, t=(cx, cy, cz))
    cmds.xform(
        cube_long, ws=True,
        s=(sx if sx > 1e-6 else 1e-6,
           sy if sy > 1e-6 else 1e-6,
           sz if sz > 1e-6 else 1e-6)
    )

    cmds.delete(cube_long, ch=True)
    cmds.makeIdentity(cube_long, apply=True, t=True, r=True, s=True, n=False)

    pr_grp = _ensure_position_refs_group()
    cube_long = cmds.parent(cube_long, pr_grp)[0]

    cube_bb = _world_bbox(cube_long)
    pivot_world = _bottom_center_from_bb(cube_bb)

    _add_or_set_double_attr(cube_long, "origPivotX", pivot_world[0])
    _add_or_set_double_attr(cube_long, "origPivotY", pivot_world[1])
    _add_or_set_double_attr(cube_long, "origPivotZ", pivot_world[2])

    cmds.xform(cube_long, ws=True, piv=pivot_world)
    cmds.xform(dup_long, ws=True, piv=pivot_world)

    delta_to_origin = [-pivot_world[0], -pivot_world[1], -pivot_world[2]]
    _move_by_delta_world(dup_long, delta_to_origin)
    _move_by_delta_world(cube_long, delta_to_origin)

    cmds.setAttr(f"{cube_long}.visibility", 0)

    cmds.select(dup_long, r=True)
    cmds.inViewMessage(amg="Duplicated + created positionRef (hidden).", pos="topCenter", fade=True)


class PositionRefPicker(QtWidgets.QDialog):
    def __init__(self, ref_nodes_long, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Restore Position — Choose PositionRef")
        self.setObjectName("PhoenixPositionRefPicker")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(560, 520)

        self._refs = list(ref_nodes_long)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Search:"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Type to filter...")
        row.addWidget(self.search, 1)
        layout.addLayout(row)

        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        layout.addWidget(self.listw, 1)

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(self.buttons)

        self._populate()
        self.search.textChanged.connect(self._filter)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

    def _populate(self):
        self.listw.clear()
        labeled = []
        for r in self._refs:
            label = r.split("|")[-1]
            labeled.append((label.lower(), label, r))
        for _, label, r in sorted(labeled, key=lambda x: x[0]):
            it = QtWidgets.QListWidgetItem(label)
            it.setToolTip(r)
            it.setData(QtCore.Qt.UserRole, r)
            self.listw.addItem(it)
        if self.listw.count():
            self.listw.setCurrentRow(0)

    def _filter(self, text):
        t = (text or "").strip().lower()
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            r = it.data(QtCore.Qt.UserRole)
            it.setHidden(not (t in it.text().lower() or t in str(r).lower()))

    def _on_ok(self):
        if not self.listw.currentItem():
            QtWidgets.QMessageBox.warning(self, "Pick one", "Select a positionRef to use.")
            return
        self.accept()

    def selected_ref(self):
        it = self.listw.currentItem()
        return it.data(QtCore.Qt.UserRole) if it else None


def _list_position_refs_under_group():
    if not cmds.objExists("positionRefs"):
        return []
    grp = _ensure_position_refs_group()
    kids = cmds.listRelatives(grp, children=True, type="transform", fullPath=True) or []
    return [
        k for k in kids
        if cmds.attributeQuery("origPivotX", node=k, exists=True)
        and cmds.attributeQuery("origPivotY", node=k, exists=True)
        and cmds.attributeQuery("origPivotZ", node=k, exists=True)
    ]


@undo_chunk
def action_restore_position(*_):
    sel = cmds.ls(sl=True, long=True) or []
    if not sel:
        cmds.warning("Select the group/mesh you want to move.")
        return
    move_obj = sel[0]

    refs = _list_position_refs_under_group()
    if not refs:
        cmds.warning("No position refs found under top-level group 'positionRefs'.")
        return

    if len(refs) == 1:
        ref = refs[0]
    else:
        dlg = PositionRefPicker(refs)
        result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
        if result != QtWidgets.QDialog.Accepted:
            return
        ref = dlg.selected_ref()

    desired = [
        cmds.getAttr(f"{ref}.origPivotX"),
        cmds.getAttr(f"{ref}.origPivotY"),
        cmds.getAttr(f"{ref}.origPivotZ"),
    ]

    bb_obj = _world_bbox(move_obj)
    cur_pivot = _bottom_center_from_bb(bb_obj)
    cmds.xform(move_obj, ws=True, piv=cur_pivot)

    delta = [desired[0] - cur_pivot[0], desired[1] - cur_pivot[1], desired[2] - cur_pivot[2]]
    _move_by_delta_world(move_obj, delta)

    cmds.select(move_obj, r=True)
    cmds.inViewMessage(amg=f"Restored using: {ref.split('|')[-1]}", pos="topCenter", fade=True)


# Duplicate right-click = Restore Position
def action_duplicate_right_click(*_):
    action_restore_position()


# -----------------------------
# Import Blockout (Left click)
# -----------------------------
class ImportBlockoutDialog(QtWidgets.QDialog):
    def __init__(self, obj_paths, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Import Blockout — Choose OBJ / FBX")
        self.setObjectName("PhoenixImportBlockoutDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(620, 520)

        self._obj_paths = list(obj_paths)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Search:"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter by file/folder name...")
        row.addWidget(self.search, 1)
        layout.addLayout(row)

        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        layout.addWidget(self.listw, 1)

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(self.buttons)

        self._populate()
        self.search.textChanged.connect(self._filter)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

    def _populate(self):
        self.listw.clear()
        labeled = []
        for p in self._obj_paths:
            obj = os.path.basename(p)
            folder = os.path.basename(os.path.dirname(p))
            label = f"{folder} / {obj}"
            labeled.append((label.lower(), label, p))
        for _, label, path in sorted(labeled, key=lambda x: x[0]):
            item = QtWidgets.QListWidgetItem(label)
            item.setToolTip(path)
            item.setData(QtCore.Qt.UserRole, path)
            self.listw.addItem(item)
        if self.listw.count():
            self.listw.setCurrentRow(0)

    def _filter(self, text):
        t = (text or "").strip().lower()
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            p = it.data(QtCore.Qt.UserRole)
            it.setHidden(not (t in it.text().lower() or t in str(p).lower()))

    def _on_ok(self):
        if not self.listw.currentItem():
            QtWidgets.QMessageBox.warning(self, "Select a File", "Please select an OBJ or FBX to import.")
            return
        self.accept()

    def selected_path(self):
        it = self.listw.currentItem()
        return it.data(QtCore.Qt.UserRole) if it else None


def _derive_asset_root_from_scene(scene_path):
    if not scene_path:
        return None
    scene_dir = os.path.dirname(scene_path)
    if os.path.basename(scene_dir).lower() == "maya":
        return os.path.dirname(scene_dir)
    parts = scene_path.replace("/", "\\").split("\\")
    lower = [p.lower() for p in parts]
    if "maya" in lower:
        idx = lower.index("maya")
        if idx > 0:
            return "\\".join(parts[:idx])
    return None


def _find_blockout_objs(asset_root):
    found = []
    for folder_name in ("mod", "obj"):
        search_dir = os.path.join(asset_root, folder_name)
        if not os.path.isdir(search_dir):
            continue
        for entry in os.listdir(search_dir):
            full = os.path.join(search_dir, entry)
            if os.path.isdir(full) and entry.lower().endswith("_blockout"):
                for f in os.listdir(full):
                    if f.lower().endswith(".obj") or f.lower().endswith(".fbx"):
                        found.append(os.path.join(full, f))
    return found


def _import_obj(obj_path):
    before = set(cmds.ls(long=True) or [])
    cmds.file(obj_path, i=True, type="OBJ", ignoreVersion=True, ra=True,
              mergeNamespacesOnClash=False, options="mo=1", pr=True)
    after = set(cmds.ls(long=True) or [])
    new_nodes = list(after - before)

    new_mesh_xforms = []
    for n in new_nodes:
        if cmds.nodeType(n) == "transform" and is_mesh_transform(n):
            new_mesh_xforms.append(n)
    return _top_level_of(new_mesh_xforms)


def _import_fbx(fbx_path):
    if not ensure_fbx_export_plugin():
        cmds.warning("FBX plugin (fbxmaya) could not be loaded.")
        return []

    before = set(cmds.ls(long=True) or [])
    cmds.file(fbx_path, i=True, type="FBX", ignoreVersion=True, ra=True,
              mergeNamespacesOnClash=False, pr=True)
    after = set(cmds.ls(long=True) or [])
    new_nodes = list(after - before)

    new_xforms = []
    for n in new_nodes:
        if cmds.nodeType(n) == "transform":
            new_xforms.append(n)
    return _top_level_of(new_xforms)


@undo_chunk
def action_import_blockout(*_):
    scene_path = cmds.file(q=True, sn=True)
    asset_root = _derive_asset_root_from_scene(scene_path)
    if not asset_root:
        cmds.warning("Could not derive asset root (expected scene inside an asset '/maya' folder).")
        return

    obj_paths = _find_blockout_objs(asset_root)
    if not obj_paths:
        cmds.warning(f"No blockout OBJ/FBX found under: {asset_root}\\(mod|obj)\\*_blockout\\*.obj|*.fbx")
        return

    if len(obj_paths) == 1:
        obj_path = obj_paths[0]
    else:
        dlg = ImportBlockoutDialog(obj_paths)
        result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
        if result != QtWidgets.QDialog.Accepted:
            return
        obj_path = dlg.selected_path()

    if not obj_path or not os.path.isfile(obj_path):
        cmds.warning("Selected file path is invalid.")
        return

    obj_name = os.path.splitext(os.path.basename(obj_path))[0]
    ext = os.path.splitext(obj_path)[1].lower()
    if ext == ".fbx":
        new_mesh_xforms = _import_fbx(obj_path)
    else:
        new_mesh_xforms = _import_obj(obj_path)
    if not new_mesh_xforms:
        cmds.warning("Imported file, but couldn't detect new mesh transforms to group.")
        return

    grp = cmds.group(new_mesh_xforms, name=obj_name)
    cmds.select(grp, r=True)
    cmds.inViewMessage(amg=f"Imported: {os.path.basename(obj_path)} grouped as '{obj_name}'",
                       pos="topCenter", fade=True)


# -----------------------------
# Right-click Import Blockout
# -----------------------------
def _get_set_root_any(scene_path):
    if not scene_path:
        return None
    parts = scene_path.replace("/", "\\").split("\\")
    lower = [p.lower() for p in parts]

    if "setelements" in lower:
        idx = lower.index("setelements")
        if idx > 0:
            return "\\".join(parts[:idx])

    if "maya" in lower:
        idx = lower.index("maya")
        if idx > 0:
            return "\\".join(parts[:idx])

    return None


def _collect_asset_mod_files(set_root):
    out = {}
    if not set_root:
        return out

    set_elements = os.path.join(set_root, "setElements")
    if not os.path.isdir(set_elements):
        return out

    for asset_folder in sorted(os.listdir(set_elements)):
        asset_path = os.path.join(set_elements, asset_folder)
        if not os.path.isdir(asset_path):
            continue

        folder_map = {}

        # ── maya/ folder → .ma / .mb files ──────────────────────────────
        maya_dir = os.path.join(asset_path, "maya")
        if os.path.isdir(maya_dir):
            try:
                maya_files = []
                for f in sorted(os.listdir(maya_dir)):
                    lf = f.lower()
                    if lf.endswith(".ma") or lf.endswith(".mb"):
                        maya_files.append(os.path.join(maya_dir, f))
                if maya_files:
                    folder_map["maya"] = maya_files
            except Exception:
                pass

        # ── mod/ (or obj/) folder → OBJ / FBX files ─────────────────────
        mod_dir = os.path.join(asset_path, "mod")
        if not os.path.isdir(mod_dir):
            mod_dir = os.path.join(asset_path, "obj")

        if os.path.isdir(mod_dir):
            # *_blockout sub-folders
            try:
                for entry in sorted(os.listdir(mod_dir)):
                    full = os.path.join(mod_dir, entry)
                    if os.path.isdir(full) and entry.lower().endswith("_blockout"):
                        files = []
                        for f in sorted(os.listdir(full)):
                            lf = f.lower()
                            if lf.endswith(".obj") or lf.endswith(".fbx"):
                                files.append(os.path.join(full, f))
                        if files:
                            folder_map[entry] = files
            except Exception:
                pass

            # root mod/ files
            try:
                root_files = []
                for f in sorted(os.listdir(mod_dir)):
                    full = os.path.join(mod_dir, f)
                    lf = f.lower()
                    if os.path.isfile(full) and (lf.endswith(".obj") or lf.endswith(".fbx")):
                        root_files.append(full)
                if root_files:
                    folder_map["mod"] = root_files
            except Exception:
                pass

            # final/ sub-folder
            final_dir = os.path.join(mod_dir, "final")
            if os.path.isdir(final_dir):
                try:
                    final_files = []
                    for f in sorted(os.listdir(final_dir)):
                        full = os.path.join(final_dir, f)
                        lf = f.lower()
                        if os.path.isfile(full) and (lf.endswith(".obj") or lf.endswith(".fbx")):
                            final_files.append(full)
                    if final_files:
                        folder_map["final"] = final_files
                except Exception:
                    pass

        if folder_map:
            out[asset_folder] = folder_map

    return out


class _ModFileItemDelegate(QtWidgets.QStyledItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        kind = index.data(QtCore.Qt.UserRole + 1)
        if kind != "file":
            return
        path = index.data(QtCore.Qt.UserRole) or ""
        ext = os.path.splitext(path)[1].upper().lstrip(".")
        if not ext:
            return
        painter.save()
        painter.setPen(QtCore.Qt.gray)
        font = painter.font()
        font.setPointSize(font.pointSize() - 1)
        painter.setFont(font)
        rect = option.rect.adjusted(0, 0, -16, 0)
        painter.drawText(rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, ext)
        painter.restore()


class ImportAssetModFilesPanel(maya.app.general.mayaMixin.MayaQWidgetDockableMixin, QtWidgets.QWidget):
    WINDOW_NAME = "PhoenixImportAssetModFiles"
    ROLE_PATH = QtCore.Qt.UserRole
    ROLE_KIND = QtCore.Qt.UserRole + 1
    ROLE_ASSET = QtCore.Qt.UserRole + 2
    KIND_HEADER = "header"
    KIND_FILE = "file"
    _instance = None

    def __init__(self, asset_to_folders, parent=None):
        super().__init__(parent)
        self.setObjectName(self.WINDOW_NAME)
        self.setWindowTitle("Import — SetElements Files")
        self._data = asset_to_folders

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        filter_row = QtWidgets.QHBoxLayout()
        filter_row.setSpacing(6)
        self.cb_ma = QtWidgets.QCheckBox("MA/MB")
        self.cb_obj = QtWidgets.QCheckBox("OBJ")
        self.cb_fbx = QtWidgets.QCheckBox("FBX")
        self.cb_ma.setChecked(True)
        self.cb_obj.setChecked(True)
        self.cb_fbx.setChecked(True)
        self.cb_ma.toggled.connect(self._rebuild)
        self.cb_obj.toggled.connect(self._rebuild)
        self.cb_fbx.toggled.connect(self._rebuild)
        filter_row.addWidget(self.cb_ma)
        filter_row.addWidget(self.cb_obj)
        filter_row.addWidget(self.cb_fbx)
        filter_row.addSpacing(4)
        self.cb_blockouts = QtWidgets.QCheckBox("Blockouts")
        self.cb_blockouts.setChecked(False)
        self.cb_blockouts.toggled.connect(self._rebuild)
        filter_row.addWidget(self.cb_blockouts)
        filter_row.addSpacing(4)
        self.cb_as_ref = QtWidgets.QCheckBox("As Reference")
        self.cb_as_ref.setChecked(True)
        self.cb_as_ref.setToolTip("Import MA/MB files as references (checked) or directly (unchecked)")
        filter_row.addWidget(self.cb_as_ref)
        filter_row.addSpacing(8)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search...")
        self.search.textChanged.connect(self._rebuild)
        filter_row.addWidget(self.search, 1)
        root.addLayout(filter_row)

        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.listw.setItemDelegate(_ModFileItemDelegate(self.listw))
        root.addWidget(self.listw, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(6)
        self.btn_select_all = QtWidgets.QPushButton("Select All")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_import = QtWidgets.QPushButton("Import Selected")
        self.btn_select_all.clicked.connect(self._select_all_visible)
        self.btn_clear.clicked.connect(self.listw.clearSelection)
        self.btn_import.clicked.connect(self._do_import)
        bottom.addWidget(self.btn_select_all)
        bottom.addWidget(self.btn_clear)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_import)
        root.addLayout(bottom)

        self._rebuild()
        self.search.setFocus()

    def _folder_sort_key(self, folder_label):
        low = folder_label.lower()
        if low == "maya":
            return (0, low)
        if low.endswith("_blockout"):
            return (1, low)
        if low == "mod":
            return (2, low)
        if low == "final":
            return (3, low)
        return (4, low)

    def _want_ext(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in (".ma", ".mb"): return self.cb_ma.isChecked()
        if ext == ".obj": return self.cb_obj.isChecked()
        if ext == ".fbx": return self.cb_fbx.isChecked()
        return False

    def _matches_search(self, query, hay):
        return not query or query in hay.lower()

    def _rebuild(self, *_):
        self.listw.clear()
        query = (self.search.text() or "").strip().lower()
        show_blockouts = self.cb_blockouts.isChecked()

        for asset_folder in sorted(self._data.keys(), key=lambda x: x.lower()):
            folder_map = self._data[asset_folder]
            has_visible = False
            for folder_label in folder_map:
                if not show_blockouts and folder_label.lower().endswith("_blockout"):
                    continue
                for p in folder_map[folder_label]:
                    if not os.path.isfile(p): continue
                    if not self._want_ext(p): continue
                    if self._matches_search(query, f"{asset_folder} {folder_label} {os.path.basename(p)} {p}"):
                        has_visible = True
                        break
                if has_visible: break
            if not has_visible:
                continue

            hdr = QtWidgets.QListWidgetItem(f"--{asset_folder}--")
            hdr.setFlags(QtCore.Qt.NoItemFlags)
            hdr.setData(self.ROLE_KIND, self.KIND_HEADER)
            font = hdr.font(); font.setBold(True); hdr.setFont(font)
            hdr.setForeground(QtCore.Qt.gray)
            self.listw.addItem(hdr)

            for folder_label in sorted(folder_map.keys(), key=self._folder_sort_key):
                if not show_blockouts and folder_label.lower().endswith("_blockout"):
                    continue
                paths = [
                    p for p in folder_map[folder_label]
                    if os.path.isfile(p) and self._want_ext(p)
                    and self._matches_search(query, f"{asset_folder} {folder_label} {os.path.basename(p)} {p}")
                ]
                if not paths: continue
                indent = " " * (len(folder_label) + 2)
                for i, p in enumerate(paths):
                    fname = os.path.basename(p)
                    display = f"{folder_label}: {fname}" if i == 0 else f"{indent}{fname}"
                    item = QtWidgets.QListWidgetItem(display)
                    item.setToolTip(p)
                    item.setData(self.ROLE_PATH, p)
                    item.setData(self.ROLE_KIND, self.KIND_FILE)
                    item.setData(self.ROLE_ASSET, asset_folder)
                    self.listw.addItem(item)

            spacer = QtWidgets.QListWidgetItem("")
            spacer.setFlags(QtCore.Qt.NoItemFlags)
            spacer.setData(self.ROLE_KIND, "spacer")
            spacer.setSizeHint(QtCore.QSize(0, 8))
            self.listw.addItem(spacer)

    def _select_all_visible(self):
        self.listw.clearSelection()
        for i in range(self.listw.count()):
            item = self.listw.item(i)
            if item.data(self.ROLE_KIND) == self.KIND_FILE and not item.isHidden():
                item.setSelected(True)

    def selected_paths(self):
        return [it.data(self.ROLE_PATH) for it in self.listw.selectedItems()
                if it.data(self.ROLE_KIND) == self.KIND_FILE and it.data(self.ROLE_PATH)]

    def _do_import(self):
        selected = self.selected_paths()
        if not selected:
            QtWidgets.QMessageBox.warning(self, "Select file(s)", "Please select at least one file.")
            return
        as_ref = self.cb_as_ref.isChecked()
        imported = 0
        for path in selected:
            if not os.path.isfile(path): continue
            ext = os.path.splitext(path)[1].lower()
            base_name = os.path.splitext(os.path.basename(path))[0]
            try:
                if ext in (".ma", ".mb"):
                    if as_ref:
                        namespace = re.sub(r'[^a-zA-Z0-9_]', '_', base_name)
                        cmds.file(path, reference=True, namespace=namespace)
                    else:
                        cmds.file(path, i=True, ignoreVersion=True, ra=True,
                                  mergeNamespacesOnClash=False, pr=True)
                    imported += 1
                elif ext == ".obj":
                    new_mesh = _import_obj(path)
                    if new_mesh:
                        grp = cmds.group(new_mesh, name=base_name)
                        cmds.select(grp, r=True)
                    imported += 1
                elif ext == ".fbx":
                    new_xforms = _import_fbx(path)
                    if new_xforms:
                        grp = cmds.group(new_xforms, name=base_name)
                        cmds.select(grp, r=True)
                    imported += 1
            except Exception as e:
                cmds.warning(f"Failed to import {os.path.basename(path)}: {e}")
        if imported:
            mode = " (as reference)" if as_ref else ""
            cmds.inViewMessage(amg=f"Imported {imported} file(s){mode}.", pos="topCenter", fade=True)

    @classmethod
    def show_panel(cls, asset_to_folders):
        if cls._instance is not None:
            try: cls._instance.close()
            except Exception: pass
            cls._instance = None
        ws_name = cls.WINDOW_NAME + "WorkspaceControl"
        if cmds.workspaceControl(ws_name, q=True, exists=True):
            cmds.deleteUI(ws_name)
        panel = cls(asset_to_folders)
        cls._instance = panel
        panel.show(dockable=True, floating=True, width=700, height=500)


@undo_chunk
def action_import_blockout_right_click(*_):
    scene_path = cmds.file(q=True, sn=True)
    set_root = _get_set_root_any(scene_path)

    # If auto-detection failed OR derived root has no setElements folder, ask the user
    if not set_root or not os.path.isdir(set_root) or \
            not os.path.isdir(os.path.join(set_root, "setElements")):
        folder = cmds.fileDialog2(
            dialogStyle=2, fm=3,
            caption="Select Set Root Folder (the folder containing 'setElements')"
        )
        if not folder:
            return
        set_root = folder[0]

    if not os.path.isdir(set_root):
        cmds.warning("Selected path is not a valid folder.")
        return

    data = _collect_asset_mod_files(set_root)
    if not data:
        cmds.warning(f"No files found under: {os.path.join(set_root, 'setElements')}")
        return
    ImportAssetModFilesPanel.show_panel(data)


# -----------------------------
# PureRef
# -----------------------------
class PureRefPickerDialog(QtWidgets.QDialog):
    def __init__(self, pur_paths, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Open PureRef — Choose File(s)")
        self.setObjectName("PhoenixPureRefPickerDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(620, 480)
        self._pur_paths = list(pur_paths)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Search:"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter by filename...")
        row.addWidget(self.search, 1)
        layout.addLayout(row)

        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.listw, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_select_all = QtWidgets.QPushButton("Select All")
        self.btn_clear = QtWidgets.QPushButton("Clear Selection")
        btn_row.addWidget(self.btn_select_all)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(self.buttons)

        self._populate()
        self.search.textChanged.connect(self._filter)
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_clear.clicked.connect(self.listw.clearSelection)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

    def _populate(self):
        self.listw.clear()
        labeled = []
        for p in self._pur_paths:
            filename = os.path.basename(p)
            folder = os.path.basename(os.path.dirname(p))
            label = f"{folder} / {filename}"
            labeled.append((label.lower(), label, p))
        for _, label, path in sorted(labeled, key=lambda x: x[0]):
            item = QtWidgets.QListWidgetItem(label)
            item.setToolTip(path)
            item.setData(QtCore.Qt.UserRole, path)
            self.listw.addItem(item)
        if self.listw.count():
            self.listw.setCurrentRow(0)

    def _filter(self, text):
        t = (text or "").strip().lower()
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            p = it.data(QtCore.Qt.UserRole)
            it.setHidden(not (t in it.text().lower() or t in str(p).lower()))

    def _select_all(self):
        self.listw.clearSelection()
        for i in range(self.listw.count()):
            item = self.listw.item(i)
            if not item.isHidden():
                item.setSelected(True)

    def _on_ok(self):
        if not self.listw.selectedItems():
            QtWidgets.QMessageBox.warning(self, "Select a file", "Please select at least one PureRef file.")
            return
        self.accept()

    def selected_paths(self):
        return [item.data(QtCore.Qt.UserRole) for item in self.listw.selectedItems()]


def _find_pureref_files(folder):
    if not folder or not os.path.isdir(folder):
        return []
    return [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".pur")]


def _get_set_root_from_scene(scene_path):
    if not scene_path: return None
    parts = scene_path.replace("/", "\\").split("\\")
    lower = [p.lower() for p in parts]
    if "setelements" in lower:
        idx = lower.index("setelements")
        if idx > 0:
            return "\\".join(parts[:idx])
    return None


def _get_asset_root_from_scene(scene_path):
    if not scene_path: return None
    scene_dir = os.path.dirname(scene_path)
    if os.path.basename(scene_dir).lower() == "maya":
        return os.path.dirname(scene_dir)
    parts = scene_path.replace("/", "\\").split("\\")
    lower = [p.lower() for p in parts]
    if "maya" in lower:
        idx = lower.index("maya")
        if idx > 0:
            return "\\".join(parts[:idx])
    return None


def _open_pureref_files(pur_paths):
    for p in pur_paths:
        if os.path.isfile(p):
            try: os.startfile(p)
            except Exception as e: cmds.warning(f"Failed to open {p}: {e}")


def action_open_pureref_set_level(*_):
    scene_path = cmds.file(q=True, sn=True)
    set_root = _get_set_root_from_scene(scene_path)
    if not set_root:
        cmds.warning("Could not derive set root folder from scene path.")
        return
    ref_folder = os.path.join(set_root, "ref")
    pur_files = _find_pureref_files(ref_folder)
    if not pur_files:
        cmds.warning(f"No PureRef files found in: {ref_folder}")
        return
    if len(pur_files) == 1:
        _open_pureref_files(pur_files)
        cmds.inViewMessage(amg=f"Opened: {os.path.basename(pur_files[0])}", pos="topCenter", fade=True)
    else:
        dlg = PureRefPickerDialog(pur_files)
        result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
        if result == QtWidgets.QDialog.Accepted:
            selected = dlg.selected_paths()
            _open_pureref_files(selected)
            cmds.inViewMessage(amg=f"Opened {len(selected)} PureRef file(s)", pos="topCenter", fade=True)


def action_open_pureref_asset_level(*_):
    scene_path = cmds.file(q=True, sn=True)
    asset_root = _get_asset_root_from_scene(scene_path)
    if not asset_root:
        cmds.warning("Could not derive asset root folder from scene path.")
        return
    ref_folder = os.path.join(asset_root, "ref")
    pur_files = _find_pureref_files(ref_folder)
    if not pur_files:
        set_root = _get_set_root_from_scene(scene_path)
        if set_root:
            set_ref = os.path.join(set_root, "ref")
            pur_files = _find_pureref_files(set_ref)
            if pur_files:
                cmds.warning(f"No PureRef in asset ref folder, using set-level: {set_ref}")
    if not pur_files:
        cmds.warning("No PureRef files found in asset or set ref folders.")
        return
    if len(pur_files) == 1:
        _open_pureref_files(pur_files)
        cmds.inViewMessage(amg=f"Opened: {os.path.basename(pur_files[0])}", pos="topCenter", fade=True)
    else:
        dlg = PureRefPickerDialog(pur_files)
        result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
        if result == QtWidgets.QDialog.Accepted:
            selected = dlg.selected_paths()
            _open_pureref_files(selected)
            cmds.inViewMessage(amg=f"Opened {len(selected)} PureRef file(s)", pos="topCenter", fade=True)


# -----------------------------
# Import Set Elements (Shift+Click on Import Blockout)
# -----------------------------
def _is_scene_in_main_maya_folder(scene_path):
    if not scene_path: return False
    parts = scene_path.replace("/", "\\").split("\\")
    lower = [p.lower() for p in parts]
    if "setelements" in lower: return False
    if len(parts) >= 2:
        return parts[-2].lower() == "maya"
    return False


def _get_set_root_from_main_maya(scene_path):
    if not scene_path: return None
    parts = scene_path.replace("/", "\\").split("\\")
    lower = [p.lower() for p in parts]
    if "maya" in lower:
        idx = lower.index("maya")
        if idx > 0:
            return "\\".join(parts[:idx])
    return None


def _simplify_set_element_name(filename, folder_name):
    name_no_ext, ext = os.path.splitext(filename)
    folder_parts = folder_name.split("_")
    if len(folder_parts) > 1:
        unique_id = folder_parts[-1]
        common_prefix = "_".join(folder_parts[:-1])
        patterns = [
            f"prj_props_{common_prefix}_", f"prj_props_{folder_name}_",
            f"{common_prefix}_", f"{folder_name}_",
        ]
        for pattern in patterns:
            if name_no_ext.startswith(pattern):
                remainder = name_no_ext[len(pattern):]
                if remainder: return remainder + ext
            if name_no_ext.lower().startswith(pattern.lower()):
                remainder = name_no_ext[len(pattern):]
                if remainder: return remainder + ext
        if unique_id in name_no_ext:
            idx = name_no_ext.find(unique_id)
            return name_no_ext[idx:] + ext
    return filename


def _find_set_elements_for_import(set_root):
    if not set_root: return []
    set_elements_folder = os.path.join(set_root, "setElements")
    if not os.path.isdir(set_elements_folder): return []
    results = []
    for entry in os.listdir(set_elements_folder):
        asset_folder = os.path.join(set_elements_folder, entry)
        if not os.path.isdir(asset_folder): continue
        maya_folder = os.path.join(asset_folder, "maya")
        if not os.path.isdir(maya_folder): continue
        for f in os.listdir(maya_folder):
            if f.lower().endswith(".ma") or f.lower().endswith(".mb"):
                full_path = os.path.join(maya_folder, f)
                simplified = _simplify_set_element_name(f, entry)
                display = f"{entry} / {simplified}"
                results.append((full_path, display, entry))
    return results


class ImportSetElementsDialog(QtWidgets.QDialog):
    def __init__(self, element_data, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Import Set Elements")
        self.setObjectName("PhoenixImportSetElementsDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(700, 580)
        self._element_data = list(element_data)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        options_row = QtWidgets.QHBoxLayout()
        self.as_reference_cb = QtWidgets.QCheckBox("Import as Reference")
        self.as_reference_cb.setChecked(True)
        options_row.addWidget(self.as_reference_cb)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        search_row = QtWidgets.QHBoxLayout()
        search_row.addWidget(QtWidgets.QLabel("Search:"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter by filename or folder...")
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)

        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.listw, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_select_all = QtWidgets.QPushButton("Select All")
        self.btn_clear = QtWidgets.QPushButton("Clear Selection")
        btn_row.addWidget(self.btn_select_all)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        hint = QtWidgets.QLabel("Select files to import. Use Ctrl+Click for multi-select.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(self.buttons)

        self._populate()
        self.search.textChanged.connect(self._filter)
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_clear.clicked.connect(self.listw.clearSelection)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)
        self.search.setFocus()

    def _populate(self):
        self.listw.clear()
        sorted_data = sorted(self._element_data, key=lambda x: (x[2].lower(), x[1].lower()))
        for path, display, folder in sorted_data:
            item = QtWidgets.QListWidgetItem(display)
            item.setToolTip(path)
            item.setData(QtCore.Qt.UserRole, path)
            self.listw.addItem(item)

    def _filter(self, text):
        t = (text or "").strip().lower()
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            p = it.data(QtCore.Qt.UserRole)
            it.setHidden(not (t in it.text().lower() or t in str(p).lower()))

    def _select_all(self):
        self.listw.clearSelection()
        for i in range(self.listw.count()):
            item = self.listw.item(i)
            if not item.isHidden():
                item.setSelected(True)

    def _on_ok(self):
        if not self.listw.selectedItems():
            QtWidgets.QMessageBox.warning(self, "Select files", "Please select at least one file to import.")
            return
        self.accept()

    def import_as_reference(self): return self.as_reference_cb.isChecked()
    def selected_paths(self): return [item.data(QtCore.Qt.UserRole) for item in self.listw.selectedItems()]


@undo_chunk
def action_import_set_elements(*_):
    scene_path = cmds.file(q=True, sn=True)
    set_root = _get_set_root_any(scene_path)

    if not set_root or not os.path.isdir(set_root) or \
            not os.path.isdir(os.path.join(set_root, "setElements")):
        folder = cmds.fileDialog2(
            dialogStyle=2, fm=3,
            caption="Select Set Root Folder (the folder containing 'setElements')"
        )
        if not folder:
            return
        set_root = folder[0]

    element_data = _find_set_elements_for_import(set_root)
    if not element_data:
        cmds.warning(f"No Maya files found in: {os.path.join(set_root, 'setElements')}")
        return

    dlg = ImportSetElementsDialog(element_data)
    result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
    if result != QtWidgets.QDialog.Accepted: return

    selected_paths = dlg.selected_paths()
    as_reference = dlg.import_as_reference()
    if not selected_paths: return

    imported_count = 0
    for path in selected_paths:
        if not os.path.isfile(path):
            cmds.warning(f"File not found: {path}")
            continue
        try:
            if as_reference:
                namespace = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.splitext(os.path.basename(path))[0])
                cmds.file(path, reference=True, namespace=namespace)
            else:
                cmds.file(path, i=True, ignoreVersion=True, ra=True, mergeNamespacesOnClash=False, pr=True)
            imported_count += 1
        except Exception as e:
            cmds.warning(f"Failed to import {os.path.basename(path)}: {e}")

    mode = "as references" if as_reference else "directly"
    cmds.inViewMessage(amg=f"Imported {imported_count} file(s) {mode}.", pos="topCenter", fade=True)


# -----------------------------
# Save Set Element
# -----------------------------
def _derive_set_element_filename_from_maya_folder(maya_folder):
    asset_name = os.path.basename(os.path.dirname(maya_folder.rstrip("\\/")))
    asset_name = safe_fs_name(asset_name)
    return f"prj_props_{asset_name}_mod_001_v001_v001".replace("_v001_v001", "_v001")


@undo_chunk
def action_save_set_element(*_):
    folder = cmds.fileDialog2(dialogStyle=2, fm=3, caption="Choose 'maya' folder to save into")
    if not folder: return
    maya_folder = folder[0]
    if not os.path.isdir(maya_folder):
        cmds.warning("Invalid folder selected.")
        return
    filename = _derive_set_element_filename_from_maya_folder(maya_folder)
    fullpath = os.path.join(maya_folder, filename + ".ma")
    os.makedirs(maya_folder, exist_ok=True)
    try:
        cmds.file(rename=fullpath)
        cmds.file(save=True, type="mayaAscii")
        cmds.inViewMessage(amg=f"Saved: {filename}.ma", pos="topCenter", fade=True)
    except Exception as e:
        cmds.warning(f"Save failed: {e}")


# -----------------------------
# Set Element Switcher (Right-click on Save)
# -----------------------------
def _get_set_elements_folder_from_scene(scene_path):
    if not scene_path: return None
    parts = scene_path.replace("/", "\\").split("\\")
    lower = [p.lower() for p in parts]
    if "setelements" in lower:
        idx = lower.index("setelements")
        return "\\".join(parts[:idx + 1])
    return None


def _get_set_root_folder_from_scene(scene_path):
    if not scene_path: return None
    parts = scene_path.replace("/", "\\").split("\\")
    lower = [p.lower() for p in parts]
    if "setelements" in lower:
        idx = lower.index("setelements")
        if idx > 0:
            return "\\".join(parts[:idx])
    return None


def _find_all_set_element_maya_files(set_elements_folder):
    if not set_elements_folder or not os.path.isdir(set_elements_folder): return []
    ma_files = []
    for entry in os.listdir(set_elements_folder):
        asset_folder = os.path.join(set_elements_folder, entry)
        if not os.path.isdir(asset_folder): continue
        maya_folder = os.path.join(asset_folder, "maya")
        if not os.path.isdir(maya_folder): continue
        for f in os.listdir(maya_folder):
            if f.lower().endswith(".ma") or f.lower().endswith(".mb"):
                ma_files.append(os.path.join(maya_folder, f))
    return ma_files


def _find_main_maya_files(set_root_folder):
    if not set_root_folder: return []
    maya_folder = os.path.join(set_root_folder, "maya")
    if not os.path.isdir(maya_folder): return []
    return [os.path.join(maya_folder, f) for f in os.listdir(maya_folder)
            if f.lower().endswith(".ma") or f.lower().endswith(".mb")]


def _find_blockout_maya_files(set_root_folder):
    """
    Checks both 'maya/blockout' (singular — older convention) and
    'maya/blockouts' (plural — Create Folder Structure convention),
    merging results from whichever exist.
    """
    if not set_root_folder: return []
    found = []
    for folder_variant in ("blockout", "blockouts"):
        blockout_folder = os.path.join(set_root_folder, "maya", folder_variant)
        if not os.path.isdir(blockout_folder):
            continue
        for f in os.listdir(blockout_folder):
            if f.lower().endswith(".ma") or f.lower().endswith(".mb"):
                found.append(os.path.join(blockout_folder, f))
    return found


class SetElementSwitcherDialog(QtWidgets.QDialog):
    SECTION_MAIN = "main"
    SECTION_BLOCKOUT = "blockout"
    SECTION_ELEMENTS = "elements"

    def __init__(self, main_files, blockout_files, element_files, current_file, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Switch Set Element")
        self.setObjectName("PhoenixSetElementSwitcherDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(700, 580)

        self._main_files = list(main_files)
        self._blockout_files = list(blockout_files)
        self._element_files = list(element_files)
        self._current_file = current_file

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        current_label = QtWidgets.QLabel(f"Current: {os.path.basename(current_file) if current_file else 'Untitled'}")
        current_label.setToolTip(current_file or "")
        current_label.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(current_label)

        options_row = QtWidgets.QHBoxLayout()
        self.include_blockout_cb = QtWidgets.QCheckBox("Include Blockouts")
        self.include_blockout_cb.setChecked(False)
        self.include_blockout_cb.toggled.connect(self._populate)
        options_row.addWidget(self.include_blockout_cb)
        options_row.addStretch(1)
        layout.addLayout(options_row)

        search_row = QtWidgets.QHBoxLayout()
        search_row.addWidget(QtWidgets.QLabel("Search:"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter by filename or folder...")
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)

        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        layout.addWidget(self.listw, 1)

        hint = QtWidgets.QLabel("Select a file and press Enter (or OK) to save current and open selected.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(self.buttons)

        self._populate()
        self.search.textChanged.connect(self._filter)
        self.listw.itemDoubleClicked.connect(self._on_double_click)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)
        self.search.setFocus()

    def _is_current(self, path):
        if not self._current_file: return False
        return os.path.normcase(os.path.normpath(path)) == os.path.normcase(os.path.normpath(self._current_file))

    def _add_section_header(self, title):
        item = QtWidgets.QListWidgetItem(title)
        item.setFlags(QtCore.Qt.NoItemFlags)
        item.setData(QtCore.Qt.UserRole, None)
        item.setData(QtCore.Qt.UserRole + 1, "header")
        font = item.font(); font.setBold(True); item.setFont(font)
        item.setForeground(QtCore.Qt.gray)
        self.listw.addItem(item)

    def _add_file_item(self, path, display_name, section):
        is_current = self._is_current(path)
        item = QtWidgets.QListWidgetItem(f"  {display_name}" if is_current else f"   {display_name}")
        item.setToolTip(path)
        item.setData(QtCore.Qt.UserRole, path)
        item.setData(QtCore.Qt.UserRole + 1, section)
        if is_current:
            item.setForeground(QtCore.Qt.gray)
        self.listw.addItem(item)

    def _populate(self):
        self.listw.clear()
        include_blockout = self.include_blockout_cb.isChecked()
        first_selectable_row = None

        if self._main_files:
            self._add_section_header("Main File")
            for p in sorted(self._main_files, key=lambda x: os.path.basename(x).lower()):
                self._add_file_item(p, os.path.basename(p), self.SECTION_MAIN)
                if first_selectable_row is None and not self._is_current(p):
                    first_selectable_row = self.listw.count() - 1

        if include_blockout and self._blockout_files:
            self._add_section_header("Blockouts")
            for p in sorted(self._blockout_files, key=lambda x: os.path.basename(x).lower()):
                self._add_file_item(p, os.path.basename(p), self.SECTION_BLOCKOUT)
                if first_selectable_row is None and not self._is_current(p):
                    first_selectable_row = self.listw.count() - 1

        if self._element_files:
            self._add_section_header("Set Elements")
            sorted_elements = sorted(
                self._element_files,
                key=lambda x: (os.path.basename(os.path.dirname(os.path.dirname(x))).lower(),
                               os.path.basename(x).lower())
            )
            for p in sorted_elements:
                filename = os.path.basename(p)
                asset_folder = os.path.basename(os.path.dirname(os.path.dirname(p)))
                simplified_name = _simplify_set_element_name(filename, asset_folder)
                self._add_file_item(p, f"{asset_folder} / {simplified_name}", self.SECTION_ELEMENTS)
                if first_selectable_row is None and not self._is_current(p):
                    first_selectable_row = self.listw.count() - 1

        if first_selectable_row is not None:
            self.listw.setCurrentRow(first_selectable_row)

    def _filter(self, text):
        t = (text or "").strip().lower()
        section_has_visible = {self.SECTION_MAIN: False, self.SECTION_BLOCKOUT: False, self.SECTION_ELEMENTS: False}
        header_rows = {}
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            section = it.data(QtCore.Qt.UserRole + 1)
            if section == "header":
                header_text = it.text().lower()
                if "main" in header_text: header_rows[self.SECTION_MAIN] = i
                elif "blockout" in header_text: header_rows[self.SECTION_BLOCKOUT] = i
                elif "element" in header_text: header_rows[self.SECTION_ELEMENTS] = i
                continue
            p = it.data(QtCore.Qt.UserRole)
            matches = not t or (t in it.text().lower() or t in str(p).lower())
            it.setHidden(not matches)
            if matches and section in section_has_visible:
                section_has_visible[section] = True
        for section, row in header_rows.items():
            self.listw.item(row).setHidden(not section_has_visible.get(section, False))

    def _on_double_click(self, item):
        if item.data(QtCore.Qt.UserRole + 1) == "header": return
        self._on_ok()

    def _on_ok(self):
        current = self.listw.currentItem()
        if not current or current.data(QtCore.Qt.UserRole + 1) == "header":
            QtWidgets.QMessageBox.warning(self, "Select a file", "Please select a Maya file to open.")
            return
        self.accept()

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            current = self.listw.currentItem()
            if current and current.data(QtCore.Qt.UserRole + 1) != "header":
                self._on_ok(); return
        super().keyPressEvent(event)

    def selected_path(self):
        it = self.listw.currentItem()
        if it and it.data(QtCore.Qt.UserRole + 1) != "header":
            return it.data(QtCore.Qt.UserRole)
        return None



# ============================================================
# FILE NAVIGATION  (Prev / Next / Main)
# ============================================================

def _get_ordered_set_files(set_root_folder):
    """
    Return (main_files, element_files) where:
      main_files    = sorted .ma/.mb from <set_root>/maya/  (excludes blockout/)
      element_files = sorted .ma/.mb from <set_root>/setElements/*/maya/
    Both lists are sorted alphabetically for consistent ordering.
    """
    # Main files (top-level maya folder only, not blockout subfolder)
    main_files = []
    maya_dir = os.path.join(set_root_folder, "maya")
    if os.path.isdir(maya_dir):
        for f in sorted(os.listdir(maya_dir)):
            if f.lower().endswith((".ma", ".mb")):
                main_files.append(os.path.join(maya_dir, f))

    # Element files
    element_files = []
    se_dir = os.path.join(set_root_folder, "setElements")
    if os.path.isdir(se_dir):
        for entry in sorted(os.listdir(se_dir)):
            asset_path = os.path.join(se_dir, entry)
            if not os.path.isdir(asset_path):
                continue
            asset_maya = os.path.join(asset_path, "maya")
            if not os.path.isdir(asset_maya):
                continue
            for f in sorted(os.listdir(asset_maya)):
                if f.lower().endswith((".ma", ".mb")):
                    element_files.append(os.path.join(asset_maya, f))

    return main_files, element_files


def _norm(p):
    return os.path.normcase(os.path.normpath(p))


def _is_main_file(scene_path, main_files):
    n = _norm(scene_path)
    return any(_norm(m) == n for m in main_files)


def _save_and_open(target_path):
    """Save current scene (if saved before) then open target."""
    scene_path = cmds.file(q=True, sn=True)
    if scene_path:
        try:
            cmds.file(save=True)
        except Exception as e:
            result = QtWidgets.QMessageBox.question(
                maya_main_window(), "Save Failed",
                f"Could not save current file:\n{e}\n\nOpen anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result != QtWidgets.QMessageBox.Yes:
                return
    try:
        cmds.file(target_path, open=True, force=True)
        cmds.inViewMessage(
            amg=f"Opened: {os.path.basename(target_path)}",
            pos="topCenter", fade=True
        )
    except Exception as e:
        cmds.warning(f"Failed to open {os.path.basename(target_path)}: {e}")


def _resolve_set_root_for_nav():
    """
    Return set_root_folder or None.
    Tries auto-detection first; falls back to folder picker.
    """
    scene_path = cmds.file(q=True, sn=True)
    set_root = _get_set_root_any(scene_path)
    if set_root and os.path.isdir(set_root):
        return set_root
    # Fallback picker
    folder = cmds.fileDialog2(
        dialogStyle=2, fm=3,
        caption="Select Set Root Folder (containing 'maya' and 'setElements')"
    )
    if not folder:
        return None
    return folder[0]


def action_nav_main(*_):
    """Switch to the first main maya file."""
    set_root = _resolve_set_root_for_nav()
    if not set_root:
        return
    main_files, _ = _get_ordered_set_files(set_root)
    if not main_files:
        cmds.warning("No main Maya files found.")
        return
    # If already on a main file, stay (or go to first if multiple)
    scene_path = cmds.file(q=True, sn=True)
    if scene_path and _is_main_file(scene_path, main_files):
        cmds.inViewMessage(amg="Already on main file.", pos="topCenter", fade=True)
        return
    _save_and_open(main_files[0])


def action_nav_next(*_):
    """
    If on a main file  → open first element file.
    If on an element file → open next element file (wraps to first).
    If on nothing known → open first element file.
    """
    set_root = _resolve_set_root_for_nav()
    if not set_root:
        return
    main_files, element_files = _get_ordered_set_files(set_root)
    if not element_files:
        cmds.warning("No set element Maya files found.")
        return

    scene_path = cmds.file(q=True, sn=True)
    norm_scene = _norm(scene_path) if scene_path else ""

    if _is_main_file(scene_path, main_files):
        # On main → go to first element
        target = element_files[0]
    else:
        # Find current in element list
        idx = next((i for i, p in enumerate(element_files) if _norm(p) == norm_scene), None)
        if idx is None:
            target = element_files[0]
        else:
            target = element_files[(idx + 1) % len(element_files)]

    if _norm(target) == norm_scene:
        cmds.inViewMessage(amg="Already on this file.", pos="topCenter", fade=True)
        return
    _save_and_open(target)


def action_nav_prev(*_):
    """
    If on a main file  → open last element file.
    If on an element file → open previous element file (wraps to last).
    If on nothing known → open last element file.
    """
    set_root = _resolve_set_root_for_nav()
    if not set_root:
        return
    main_files, element_files = _get_ordered_set_files(set_root)
    if not element_files:
        cmds.warning("No set element Maya files found.")
        return

    scene_path = cmds.file(q=True, sn=True)
    norm_scene = _norm(scene_path) if scene_path else ""

    if _is_main_file(scene_path, main_files):
        # On main → go to last element
        target = element_files[-1]
    else:
        idx = next((i for i, p in enumerate(element_files) if _norm(p) == norm_scene), None)
        if idx is None:
            target = element_files[-1]
        else:
            target = element_files[(idx - 1) % len(element_files)]

    if _norm(target) == norm_scene:
        cmds.inViewMessage(amg="Already on this file.", pos="topCenter", fade=True)
        return
    _save_and_open(target)


def action_switch_set_element(*_):
    scene_path = cmds.file(q=True, sn=True)

    # Derive set root from scene path (works from main maya/ or setElements/*/maya/)
    set_root_folder = _get_set_root_any(scene_path)

    # If that failed or folder doesn't exist, ask the user to pick it
    if not set_root_folder or not os.path.isdir(set_root_folder):
        folder = cmds.fileDialog2(
            dialogStyle=2, fm=3,
            caption="Select Set Root Folder (the folder containing 'maya' and 'setElements')"
        )
        if not folder:
            return
        set_root_folder = folder[0]

    set_elements_folder = os.path.join(set_root_folder, "setElements")
    if not os.path.isdir(set_elements_folder):
        set_elements_folder = None

    main_files = _find_main_maya_files(set_root_folder)
    blockout_files = _find_blockout_maya_files(set_root_folder)
    element_files = _find_all_set_element_maya_files(set_elements_folder) if set_elements_folder else []

    if not main_files and not blockout_files and not element_files:
        cmds.warning("No Maya files found in the set folder structure.")
        return

    dlg = SetElementSwitcherDialog(main_files, blockout_files, element_files, scene_path)
    result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
    if result != QtWidgets.QDialog.Accepted: return

    selected_path = dlg.selected_path()
    if not selected_path or not os.path.isfile(selected_path):
        cmds.warning("Selected file is invalid.")
        return

    if scene_path and os.path.normcase(os.path.normpath(selected_path)) == os.path.normcase(os.path.normpath(scene_path)):
        cmds.inViewMessage(amg="Already in this file.", pos="topCenter", fade=True)
        return

    if scene_path:
        try:
            cmds.file(save=True)
            cmds.inViewMessage(amg=f"Saved: {os.path.basename(scene_path)}", pos="topCenter", fade=True)
        except Exception as e:
            result2 = QtWidgets.QMessageBox.question(
                maya_main_window(), "Save Failed",
                f"Failed to save current file: {e}\n\nOpen selected file anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            )
            if result2 != QtWidgets.QMessageBox.Yes: return

    try:
        cmds.file(selected_path, open=True, force=True)
        cmds.inViewMessage(amg=f"Opened: {os.path.basename(selected_path)}", pos="topCenter", fade=True)
    except Exception as e:
        cmds.warning(f"Failed to open file: {e}")


# ============================================================
# CLEAN UP BUTTON  (replaces Restore)
# ============================================================

DEFAULT_CAMERAS = {"persp", "top", "front", "side", "perspShape", "topShape", "frontShape", "sideShape"}
POSITION_REFS_GROUP = "positionRefs"


def _get_world_top_level_objects():
    """Return all transforms directly parented to world."""
    all_t = cmds.ls(type="transform", long=True) or []
    return [t for t in all_t if not (cmds.listRelatives(t, parent=True, fullPath=True) or [])]


def _is_default_camera(node_long):
    short = node_long.lstrip("|").split("|")[0]
    return short in DEFAULT_CAMERAS


def _collect_world_children_info():
    """Return list of (long_name, short_name, node_type, is_camera) for world children."""
    top = _get_world_top_level_objects()
    result = []
    for t in top:
        short = t.split("|")[-1]
        shapes = cmds.listRelatives(t, shapes=True, fullPath=True) or []
        node_type = "camera" if any(cmds.nodeType(s) == "camera" for s in shapes) else cmds.nodeType(t)
        is_default_cam = short in DEFAULT_CAMERAS
        result.append((t, short, node_type, is_default_cam))
    return result


# ------ Manual Clean Up Dialog ------

class ManualCleanupDialog(QtWidgets.QDialog):
    """
    Shows all world-level objects for one file.
    User selects what to KEEP, then clicks 'Keep & Next'.
    positionRefs is auto-selected for deletion (not pre-selected in keep list).
    """
    def __init__(self, file_path, objects_info, file_index, total_files, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle(f"Manual Clean Up  [{file_index}/{total_files}]")
        self.setObjectName("PhoenixManualCleanupDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(680, 560)

        self._objects_info = list(objects_info)
        self._result = None  # "keep_and_next" | "skip" | "stop"

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        file_label = QtWidgets.QLabel(f"File: <b>{os.path.basename(file_path)}</b>")
        file_label.setToolTip(file_path)
        file_label.setWordWrap(True)
        layout.addWidget(file_label)

        hint = QtWidgets.QLabel(
            "Select objects to <b>KEEP</b>. Everything else (except default cameras) will be deleted.\n"
            "<i>positionRefs</i> is NOT pre-selected (will be deleted by default)."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        search_row = QtWidgets.QHBoxLayout()
        search_row.addWidget(QtWidgets.QLabel("Search:"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter objects...")
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)

        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.listw, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_sel_all = QtWidgets.QPushButton("Select All")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        btn_row.addWidget(self.btn_sel_all)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        action_row = QtWidgets.QHBoxLayout()
        self.btn_keep_next = QtWidgets.QPushButton("Keep Selected & Proceed")
        self.btn_keep_next.setMinimumHeight(38)
        self.btn_skip = QtWidgets.QPushButton("Skip (no changes)")
        self.btn_stop = QtWidgets.QPushButton("Stop Clean Up")
        self.btn_stop.setStyleSheet("color: #c44;")
        action_row.addWidget(self.btn_keep_next, 2)
        action_row.addWidget(self.btn_skip)
        action_row.addWidget(self.btn_stop)
        layout.addLayout(action_row)

        self._populate()
        self.search.textChanged.connect(self._filter)
        self.btn_sel_all.clicked.connect(self._select_all)
        self.btn_clear.clicked.connect(self.listw.clearSelection)
        self.btn_keep_next.clicked.connect(self._on_keep)
        self.btn_skip.clicked.connect(self._on_skip)
        self.btn_stop.clicked.connect(self._on_stop)

    def _populate(self):
        self.listw.clear()
        for long_name, short_name, node_type, is_default_cam in self._objects_info:
            if is_default_cam:
                continue  # don't show default cameras, they're always kept

            label = f"{short_name}  [{node_type}]"
            item = QtWidgets.QListWidgetItem(label)
            item.setToolTip(long_name)
            item.setData(QtCore.Qt.UserRole, long_name)

            # Auto-select everything except positionRefs for keeping
            if short_name != POSITION_REFS_GROUP:
                item.setSelected(True)

            self.listw.addItem(item)

        # Select all that should be kept by default (non-positionRefs)
        for i in range(self.listw.count()):
            item = self.listw.item(i)
            long_name = item.data(QtCore.Qt.UserRole)
            short = long_name.split("|")[-1]
            if short != POSITION_REFS_GROUP:
                item.setSelected(True)

    def _filter(self, text):
        t = (text or "").strip().lower()
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def _select_all(self):
        for i in range(self.listw.count()):
            item = self.listw.item(i)
            if not item.isHidden():
                item.setSelected(True)

    def _on_keep(self): self._result = "keep_and_next"; self.accept()
    def _on_skip(self): self._result = "skip"; self.accept()
    def _on_stop(self): self._result = "stop"; self.reject()

    def get_result(self): return self._result

    def get_objects_to_keep(self):
        return {item.data(QtCore.Qt.UserRole) for item in self.listw.selectedItems()}


def _delete_unwanted_objects(keep_set):
    """Delete all world-level objects not in keep_set (except default cameras)."""
    top = _get_world_top_level_objects()
    to_delete = []
    for t in top:
        short = t.split("|")[-1]
        if _is_default_camera(t):
            continue
        if t not in keep_set:
            to_delete.append(t)

    if to_delete:
        try:
            cmds.delete(to_delete)
        except Exception as e:
            cmds.warning(f"Delete error: {e}")


def _collect_all_set_element_ma_files(set_elements_folder):
    """Return list of all .ma/.mb files in setElements/*/maya/"""
    files = []
    if not os.path.isdir(set_elements_folder):
        return files
    for entry in sorted(os.listdir(set_elements_folder)):
        asset_path = os.path.join(set_elements_folder, entry)
        if not os.path.isdir(asset_path): continue
        maya_folder = os.path.join(asset_path, "maya")
        if not os.path.isdir(maya_folder): continue
        for f in sorted(os.listdir(maya_folder)):
            if f.lower().endswith(".ma") or f.lower().endswith(".mb"):
                files.append(os.path.join(maya_folder, f))
    return files


def _auto_detect_groups_to_keep(file_path, set_elements_folder):
    """
    Auto mode: figure out which group(s) to keep based on folder name and file name.
    Returns a set of short names to look for.
    """
    # Derive element folder name from file path
    parts = file_path.replace("/", "\\").split("\\")
    lower = [p.lower() for p in parts]
    if "setelements" not in lower:
        return set()
    idx = lower.index("setelements")
    if idx + 1 >= len(parts):
        return set()
    element_folder = parts[idx + 1]

    # Build candidate names: full folder name, name without prefix (after last _)
    candidates = {element_folder}
    # Also try without leading set prefix (split by _ and take last meaningful segment)
    folder_parts = element_folder.split("_")
    if len(folder_parts) > 1:
        candidates.add(folder_parts[-1])
        candidates.add("_".join(folder_parts[1:]))
    return candidates


def action_cleanup(*_):
    """Clean Up button action."""
    folder = cmds.fileDialog2(dialogStyle=2, fm=3, caption="Select setElements Folder")
    if not folder: return
    set_elements_folder = folder[0]
    if not os.path.isdir(set_elements_folder):
        cmds.warning("Selected path is not a valid folder.")
        return

    ma_files = _collect_all_set_element_ma_files(set_elements_folder)
    if not ma_files:
        cmds.warning("No Maya files found in the selected setElements folder.")
        return

    # Mode selection
    mode_dlg = QtWidgets.QDialog(maya_main_window())
    mode_dlg.setWindowTitle("Clean Up Mode")
    mode_dlg.setObjectName("PhoenixCleanUpModeDialog")
    mode_dlg.setWindowFlags(mode_dlg.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
    mode_dlg.setMinimumWidth(420)
    mode_layout = QtWidgets.QVBoxLayout(mode_dlg)
    mode_layout.setContentsMargins(12, 12, 12, 12)
    mode_layout.setSpacing(10)

    mode_layout.addWidget(QtWidgets.QLabel(
        f"<b>Found {len(ma_files)} Maya file(s)</b> in:<br><i>{set_elements_folder}</i>"
    ))
    mode_layout.addWidget(QtWidgets.QLabel(
        "Choose clean-up mode:"
    ))

    mode_group = QtWidgets.QButtonGroup(mode_dlg)
    rb_manual = QtWidgets.QRadioButton("Manual (Recommended) — Review each file before deleting")
    rb_auto = QtWidgets.QRadioButton("Auto — Delete non-relevant objects automatically")
    rb_manual.setChecked(True)
    mode_group.addButton(rb_manual)
    mode_group.addButton(rb_auto)
    mode_layout.addWidget(rb_manual)
    mode_layout.addWidget(rb_auto)

    auto_warn = QtWidgets.QLabel(
        "<font color='#c88'>⚠ Auto mode may delete important data. A backup option will be offered.</font>"
    )
    auto_warn.setWordWrap(True)
    auto_warn.setVisible(False)
    mode_layout.addWidget(auto_warn)
    rb_auto.toggled.connect(lambda checked: auto_warn.setVisible(checked))

    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    btns.accepted.connect(mode_dlg.accept)
    btns.rejected.connect(mode_dlg.reject)
    mode_layout.addWidget(btns)

    mode_result = mode_dlg.exec_() if hasattr(mode_dlg, "exec_") else mode_dlg.exec()
    if mode_result != QtWidgets.QDialog.Accepted: return

    use_auto = rb_auto.isChecked()

    if use_auto:
        backup_result = QtWidgets.QMessageBox.question(
            maya_main_window(), "Auto Mode — Backup?",
            "Auto mode might delete important objects.\n\n"
            "Do you want to create a backup of the setElements folder first?\n"
            "(A copy named 'setElements_backup' will be created next to it)",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel
        )
        if backup_result == QtWidgets.QMessageBox.Cancel:
            return
        if backup_result == QtWidgets.QMessageBox.Yes:
            parent_folder = os.path.dirname(set_elements_folder)
            backup_dest = os.path.join(parent_folder, "setElements_backup")
            try:
                if os.path.isdir(backup_dest):
                    shutil.rmtree(backup_dest)
                shutil.copytree(set_elements_folder, backup_dest)
                cmds.inViewMessage(amg="Backup created: setElements_backup", pos="topCenter", fade=True)
            except Exception as e:
                err = QtWidgets.QMessageBox.critical(
                    maya_main_window(), "Backup Failed",
                    f"Failed to create backup:\n{e}\n\nContinue anyway?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
                )
                if err != QtWidgets.QMessageBox.Yes: return

    # Save current file path so we can return after
    original_scene = cmds.file(q=True, sn=True)

    for i, file_path in enumerate(ma_files):
        if not os.path.isfile(file_path):
            continue

        # Open the file
        try:
            cmds.file(file_path, open=True, force=True)
        except Exception as e:
            cmds.warning(f"Could not open {os.path.basename(file_path)}: {e}")
            continue

        if use_auto:
            # Auto: determine which groups to keep
            candidates = _auto_detect_groups_to_keep(file_path, set_elements_folder)
            top = _get_world_top_level_objects()
            keep_set = set()

            for t in top:
                short = t.split("|")[-1]
                if _is_default_camera(t):
                    keep_set.add(t)
                    continue
                if short == POSITION_REFS_GROUP:
                    continue  # always delete positionRefs in auto
                # Keep if short name matches any candidate (case-insensitive)
                for cand in candidates:
                    if short.lower() == cand.lower() or cand.lower() in short.lower():
                        keep_set.add(t)
                        break

            _delete_unwanted_objects(keep_set)
            try:
                cmds.file(save=True, type="mayaAscii")
            except Exception as e:
                cmds.warning(f"Failed to save {os.path.basename(file_path)}: {e}")
        else:
            # Manual
            objects_info = _collect_world_children_info()
            dlg = ManualCleanupDialog(file_path, objects_info, i + 1, len(ma_files))
            result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()

            action = dlg.get_result()
            if action == "stop":
                cmds.inViewMessage(amg="Clean Up stopped by user.", pos="topCenter", fade=True)
                break
            elif action == "skip":
                continue
            elif action == "keep_and_next":
                keep_set = dlg.get_objects_to_keep()
                # Always keep default cameras
                top = _get_world_top_level_objects()
                for t in top:
                    if _is_default_camera(t):
                        keep_set.add(t)
                _delete_unwanted_objects(keep_set)
                try:
                    cmds.file(save=True, type="mayaAscii")
                except Exception as e:
                    cmds.warning(f"Failed to save {os.path.basename(file_path)}: {e}")

    cmds.inViewMessage(amg="Clean Up complete.", pos="topCenter", fade=True)

    # Optionally return to original scene
    if original_scene and os.path.isfile(original_scene):
        try:
            cmds.file(original_scene, open=True, force=True)
        except Exception:
            pass


# ------------------------------------------------------------
# Notes dropdown widget
# ------------------------------------------------------------
class NotesPanel(maya.app.general.mayaMixin.MayaQWidgetDockableMixin, QtWidgets.QWidget):
    """
    Dockable notes panel — sidebar tab list on the left, rich content on the right.
    Each tab corresponds to one button/feature of Phoenix Set Tools.
    """
    WINDOW_NAME = "PhoenixNotesPanel"
    _instance   = None

    # (tab_label, [(action_label, description), ...])
    # action_label="" means plain italic paragraph (overview), non-empty = action row
    SECTIONS = [
        ("Save Set Element", [
            ("", "Saves the current Maya scene into a set element or set folder structure "
                 "with a standardised file name derived from the asset folder name."),
            ("Left Click",
             "Opens a folder-picker asking you to choose a 'maya' folder.\n"
             "The scene is saved there with an auto-generated name:\n"
             "  prj_props_<assetFolderName>_mod_001_v001.ma"),
            ("Shift + Click",
             "Only works on a brand-new unsaved empty scene.\n"
             "Prompts you to select the set root folder, then opens the Create Set Structure panel:\n"
             "  • Enter element names (one per line) — spaces auto-converted to camelCase.\n"
             "  • Toggle underscore separator: set_element vs setElement.\n"
             "  • Toggle version-number style: _mod_01_v01 vs _mod_v01.\n"
             "  • Choose folder names: mod vs obj, sourceImages vs srcimgs.\n"
             "Creates the full hierarchy including maya/, mod/, renders/, ref/, setElements/,\n"
             "and per-element maya/ + mod/ + ref/ + sourceImages/ sub-folders.\n"
             "Saves the main scene and creates placeholder .ma files in each element's maya/ folder."),
            ("Right Click",
             "Opens the Set Element Switcher showing:\n"
             "  Main File    — <setRoot>/maya/*.ma\n"
             "  Blockouts    — <setRoot>/maya/blockout/*.ma  (toggle checkbox)\n"
             "  Set Elements — <setRoot>/setElements/*/maya/*.ma\n"
             "Selecting a file saves the current scene first then opens the target.\n"
             "Auto-detects set root from scene path; falls back to a folder picker."),
        ]),
        ("Group Ungrouped", [
            ("", "Finds mesh transforms that are direct children of the world (no parent group) "
                 "and wraps each one in a new group with the same name, producing the |name|name hierarchy."),
            ("Left Click",
             "Scans for all top-level mesh transforms.\n"
             "For each: creates an empty group matching the mesh world transform,\n"
             "parents the mesh into it, then renames both group and mesh to the original name.\n"
             "Entire operation is one undo chunk."),
        ]),
        ("Import Blockout", [
            ("", "Three different import workflows depending on modifier or mouse button. "
                 "Scene path is used to auto-derive the asset or set root."),
            ("Left Click",
             "Derives asset root from current scene path (looks for 'maya' folder in path).\n"
             "Scans <assetRoot>/mod/*_blockout/ for .obj files.\n"
             "One file found → imported immediately.\n"
             "Multiple found → searchable single-select picker is shown.\n"
             "Imported meshes are grouped under the OBJ file base name."),
            ("Shift + Click",
             "Shows all .ma/.mb files under <setRoot>/setElements/*/maya/.\n"
             "Supports multi-select.\n"
             "'As Reference' checkbox (default ON) — imports as Maya reference vs direct merge.\n"
             "Auto-detects set root; falls back to a folder picker if needed."),
            ("Right Click",
             "Opens the SetElements Import dockable panel.\n"
             "Collects files from every asset under <setRoot>/setElements/:\n"
             "  maya/           → .ma / .mb files\n"
             "  mod/*_blockout/ → OBJ / FBX\n"
             "  mod/ (root)     → OBJ / FBX\n"
             "  mod/final/      → OBJ / FBX\n"
             "Filter toggles: MA/MB, OBJ, FBX, Blockouts.\n"
             "'As Reference' toggle for MA/MB files.\n"
             "Supports multi-select and Select All.\n"
             "Auto-detects set root; falls back to a folder picker."),
        ]),
        ("Notes", [
            ("", "The Notes button opens this reference panel and doubles as a PureRef launcher."),
            ("Left Click",  "Opens this tabbed reference panel.\nIf already open, raises it to the front."),
            ("Shift + Click",
             "Scans <setRoot>/ref/ for .pur files and opens them in PureRef.\n"
             "Multiple files → multi-select picker is shown.\n"
             "Uses the set-level ref folder (next to maya/ and setElements/)."),
            ("Right Click",
             "Same as Shift+Click but uses <assetRoot>/ref/ first.\n"
             "Falls back to set-level ref folder if nothing is found there."),
        ]),
        ("Export Groups", [
            ("", "Exports scene groups as FBX files into a structured folder hierarchy. "
                 "Operates on all groups in the scene (transforms with child transforms, no shapes). "
                 "The scene must be saved."),
            ("Left Click",
             "Opens the Export Groups Setup dialog — configure:\n"
             "  Prefix                — defaults to scene file name.\n"
             "  Groups are blockout   — exports into mod/<group>_blockout/.\n"
             "  Create .ma files      — copies C:/icons-phoenix/mayaFile/setEmptyFile.ma\n"
             "                          into each group's maya/ folder, renamed to match.\n"
             "  With underscore       — prefix_Group vs prefixGroup.\n"
             "  Version number        — _mod_01_v01 vs _mod_v01.\n"
             "  Use 'obj' folder      — mod/ vs obj/ sub-folder name.\n"
             "  Use 'srcimgs'         — sourceImages/ vs srcimgs/.\n"
             "  Exclude list          — multi-select groups to skip.\n"
             "Then prompts for a base export location and creates per-group folders:\n"
             "  <base>/<prefix>_<group>/maya/\n"
             "  <base>/<prefix>_<group>/mod/  (or mod/<group>_blockout/)\n"
             "  <base>/<prefix>_<group>/sourceImages/\n"
             "  <base>/<prefix>_<group>/ref/\n"
             "Exports each group as FBX; optionally copies the .ma template."),
            ("Right Click",
             "Finds all top-level (world-parent) groups.\n"
             "Multiple groups → searchable multi-select picker.\n"
             "Exports each selected group as FBX into <assetRoot>/mod/final/\n"
             "(folder is created if it does not exist).\n"
             "Asset root = parent of the scene's maya/ folder."),
        ]),
        ("Duplicate", [
            ("", "Duplicates the selection and records its original world position via a hidden "
                 "bounding-box proxy cube (positionRef). Move the duplicate to the origin for clean "
                 "modelling, then restore it precisely later."),
            ("Left Click",
             "Duplicates selected mesh or group as <n>_dup.\n"
             "Computes world bounding box; creates a polyCube (positionRef) scaled to match.\n"
             "Parents positionRef into a top-level 'positionRefs' group and hides it.\n"
             "Stores bottom-centre world position as custom attrs (origPivotX/Y/Z).\n"
             "Moves both duplicate and positionRef so bottom-centre sits at world origin.\n"
             "Selects the duplicate on completion. Fully undoable."),
            ("Right Click",
             "Restore Position workflow.\n"
             "One positionRef found → used automatically.\n"
             "Multiple found → searchable single-select picker.\n"
             "Moves the currently selected object so its bottom-centre matches\n"
             "the stored origPivot of the chosen positionRef."),
        ]),
        ("Clean Up", [
            ("", "Batch-cleans Maya files across all set element folders. "
                 "Opens each .ma/.mb one by one in the current Maya session, "
                 "removes unwanted scene content, and re-saves. "
                 "Prompts you to select the setElements folder first."),
            ("Manual Mode",
             "Recommended. For each file, shows a panel listing every world-level object\n"
             "(default cameras are always excluded from deletion).\n"
             "positionRefs is NOT pre-selected — it will be deleted by default.\n"
             "Select every object to KEEP, then:\n"
             "  Keep Selected & Proceed — deletes the rest and saves the file.\n"
             "  Skip                   — moves to the next file with no changes.\n"
             "  Stop Clean Up          — exits immediately."),
            ("Auto Mode",
             "Automatically determines which group to keep based on element folder name.\n"
             "Matches world-level groups whose name contains the element folder name\n"
             "(case-insensitive, also tries the suffix after the last underscore).\n"
             "Deletes all other world objects including positionRefs, unmatched groups,\n"
             "and non-default cameras.\n"
             "Before processing, offers to back up the entire setElements folder\n"
             "as 'setElements_backup'. Strongly recommended."),
        ]),
        ("Prev / Main / Next", [
            ("", "Navigation buttons for cycling through set element files without the switcher dialog. "
                 "Auto-detects set root from scene path; falls back to folder picker. "
                 "Current scene is always saved before switching."),
            ("◀  Prev",
             "On a set element file: opens the previous element (alphabetical order, wraps to last).\n"
             "On the main file: jumps to the last element file."),
            ("Main",
             "Opens the first .ma/.mb found in <setRoot>/maya/ (the main set file).\n"
             "If already on the main file, shows a message and does nothing."),
            ("Next  ▶",
             "On a set element file: opens the next element (alphabetical order, wraps to first).\n"
             "On the main file: jumps to the first element file."),
        ]),
        ("Group & Rename", [
            ("", "Quick helper for setting up a standard group hierarchy: "
                 "selection → geo group → named outer group. "
                 "Matches the pipeline convention outerName > geo > meshes."),
            ("Left Click",
             "Opens a dialog pre-filled with the current scene file name as the outer group name.\n"
             "Edit and press Enter or click Rename.\n"
             "Result: <outerName> (group) > geo (group) > your selected objects.\n"
             "Outer group is selected on completion."),
        ]),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(self.WINDOW_NAME)

        root_layout = QtWidgets.QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Left sidebar ──────────────────────────────────────────────────
        sidebar = QtWidgets.QWidget()
        sidebar.setFixedWidth(160)
        sidebar.setObjectName("NotesSidebar")
        sidebar.setStyleSheet(
            "#NotesSidebar { background: palette(window); border-right: 1px solid palette(mid); }"
        )
        side_layout = QtWidgets.QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 8, 0, 8)
        side_layout.setSpacing(1)

        self._tab_buttons = []
        for i, (label, _) in enumerate(self.SECTIONS):
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            btn.setMinimumHeight(36)
            btn.setStyleSheet("""
                QPushButton {
                    text-align: left;
                    padding: 7px 14px;
                    border: none;
                    border-radius: 0px;
                    background: transparent;
                }
                QPushButton:hover {
                    background: palette(highlight);
                    color: palette(highlightedText);
                }
                QPushButton:checked {
                    background: palette(highlight);
                    color: palette(highlightedText);
                    font-weight: bold;
                }
            """)
            btn.clicked.connect(lambda checked, n=i: self._stack.setCurrentIndex(n))
            side_layout.addWidget(btn)
            self._tab_buttons.append(btn)

        side_layout.addStretch(1)
        root_layout.addWidget(sidebar)

        # ── Right content stack ───────────────────────────────────────────
        self._stack = QtWidgets.QStackedWidget()
        root_layout.addWidget(self._stack, 1)

        for tab_label, entries in self.SECTIONS:
            self._stack.addWidget(self._make_page(tab_label, entries))

        if self._tab_buttons:
            self._tab_buttons[0].setChecked(True)
        self._stack.setCurrentIndex(0)

    def _make_page(self, title, entries):
        outer = QtWidgets.QWidget()
        outer_v = QtWidgets.QVBoxLayout(outer)
        outer_v.setContentsMargins(0, 0, 0, 0)
        outer_v.setSpacing(0)

        # Title bar
        title_bar = QtWidgets.QWidget()
        title_bar.setObjectName("NotesTitleBar")
        title_bar.setStyleSheet("#NotesTitleBar { background: palette(mid); }")
        title_bar.setFixedHeight(38)
        tb_h = QtWidgets.QHBoxLayout(title_bar)
        tb_h.setContentsMargins(16, 0, 16, 0)
        title_lbl = QtWidgets.QLabel(title)
        f = title_lbl.font(); f.setBold(True); f.setPointSize(f.pointSize() + 2)
        title_lbl.setFont(f)
        tb_h.addWidget(title_lbl)
        outer_v.addWidget(title_bar)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer_v.addWidget(scroll, 1)

        container = QtWidgets.QWidget()
        scroll.setWidget(container)
        vbox = QtWidgets.QVBoxLayout(container)
        vbox.setContentsMargins(16, 14, 16, 16)
        vbox.setSpacing(8)

        for action_label, desc in entries:
            if not action_label:
                para = QtWidgets.QLabel(desc)
                para.setWordWrap(True)
                para.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                f2 = para.font(); f2.setItalic(True); para.setFont(f2)
                para.setStyleSheet("color: palette(placeholderText); padding-bottom: 4px;")
                vbox.addWidget(para)
            else:
                card = QtWidgets.QFrame()
                card.setObjectName("NotesCard")
                card.setStyleSheet(
                    "#NotesCard {"
                    "  background: palette(base);"
                    "  border: 1px solid palette(mid);"
                    "  border-radius: 5px;"
                    "}"
                )
                card_h = QtWidgets.QHBoxLayout(card)
                card_h.setContentsMargins(0, 0, 12, 0)
                card_h.setSpacing(0)

                tag = QtWidgets.QLabel(action_label)
                tag.setFixedWidth(130)
                tag.setAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignTop)
                tag.setObjectName("NotesTag")
                tag.setStyleSheet(
                    "#NotesTag {"
                    "  background: palette(highlight);"
                    "  color: palette(highlightedText);"
                    "  font-weight: bold;"
                    "  padding: 10px 6px;"
                    "  border-top-left-radius: 4px;"
                    "  border-bottom-left-radius: 4px;"
                    "  min-height: 32px;"
                    "}"
                )

                desc_lbl = QtWidgets.QLabel(desc)
                desc_lbl.setWordWrap(True)
                desc_lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                desc_lbl.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
                desc_lbl.setContentsMargins(12, 8, 0, 8)

                card_h.addWidget(tag)
                card_h.addWidget(desc_lbl, 1)
                vbox.addWidget(card)

        vbox.addStretch(1)
        return outer

    @classmethod
    def open_panel(cls, tab_index=0):
        ws_name = cls.WINDOW_NAME + "WorkspaceControl"

        # If workspace control already exists, just show/raise it
        if cmds.workspaceControl(ws_name, q=True, exists=True):
            cmds.workspaceControl(ws_name, e=True, restore=True)
            # Switch tab on the live instance if we can reach it
            if cls._instance is not None:
                try:
                    cls._instance._switch_tab(tab_index)
                except Exception:
                    pass
            return

        # Create fresh
        if cls._instance is not None:
            try:
                cls._instance.close()
            except Exception:
                pass
            cls._instance = None

        panel = cls()
        cls._instance = panel
        panel.show(
            dockable=True,
            floating=True,
            width=900,
            height=660,
            uiScript=""  # no restore script needed for simple use
        )
        panel._switch_tab(tab_index)

    def _switch_tab(self, index):
        if 0 <= index < len(self._tab_buttons):
            self._tab_buttons[index].setChecked(True)
            self._stack.setCurrentIndex(index)

    @classmethod
    def open_panel_at(cls, tab_index):
        cls.open_panel(tab_index=tab_index)


class NotesButton(QtWidgets.QPushButton):
    """
    Styled push-button for the Notes action.
    Left-click  → open NotesPanel
    Shift+click → PureRef set-level
    Right-click → PureRef asset-level
    """
    def __init__(self, on_shift_click=None, on_right_click=None, parent=None):
        super().__init__("Notes", parent)
        self._on_shift_click = on_shift_click
        self._on_right_click = on_right_click
        self.setMinimumHeight(26)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_right)
        self.setToolTip(
            "Left-Click: Open reference panel\n"
            "Shift+Click: Open PureRef (set-level ref)\n"
            "Right-Click: Open PureRef (asset-level ref)"
        )

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            if event.modifiers() & QtCore.Qt.ShiftModifier:
                if callable(self._on_shift_click):
                    self._on_shift_click()
                event.accept()
                return
            NotesPanel.open_panel()
            event.accept()
            return
        super().mousePressEvent(event)

    def _on_right(self):
        if callable(self._on_right_click):
            self._on_right_click()
# ------------------------------------------------------------
# FBX Right-click export picker
# ------------------------------------------------------------
class TopGroupFBXPicker(QtWidgets.QDialog):
    def __init__(self, groups_long, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("FBX Export — Choose Top-Level Group(s)")
        self.setObjectName("PhoenixTopGroupFBXPicker")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(620, 560)
        self._groups_long = list(groups_long)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Search:"))
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter top-level groups...")
        row.addWidget(self.search, 1)
        layout.addLayout(row)

        self.listw = QtWidgets.QListWidget()
        self.listw.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        layout.addWidget(self.listw, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_select_all = QtWidgets.QPushButton("Select All")
        self.btn_clear = QtWidgets.QPushButton("Clear Selection")
        btn_row.addWidget(self.btn_select_all)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(self.buttons)

        self._populate()
        self.search.textChanged.connect(self._filter)
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_clear.clicked.connect(self.listw.clearSelection)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

    def _populate(self):
        self.listw.clear()
        labeled = [(g.split("|")[-1].lower(), g.split("|")[-1], g) for g in self._groups_long]
        for _, short, g in sorted(labeled):
            it = QtWidgets.QListWidgetItem(short)
            it.setToolTip(g)
            it.setData(QtCore.Qt.UserRole, g)
            self.listw.addItem(it)
        if self.listw.count():
            self.listw.setCurrentRow(0)

    def _filter(self, text):
        t = (text or "").strip().lower()
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            g = it.data(QtCore.Qt.UserRole)
            it.setHidden(not (t in it.text().lower() or t in str(g).lower()))

    def _select_all(self):
        self.listw.clearSelection()
        for i in range(self.listw.count()):
            it = self.listw.item(i)
            if not it.isHidden(): it.setSelected(True)

    def _on_ok(self):
        if not self.listw.selectedItems():
            QtWidgets.QMessageBox.warning(self, "Select group(s)", "Please select at least one top-level group.")
            return
        self.accept()

    def selected_groups(self):
        return [it.data(QtCore.Qt.UserRole) for it in self.listw.selectedItems()]


def _fbx_export_selected(path):
    path = path.replace("\\", "/")
    mel.eval("FBXResetExport;")
    mel.eval(f'FBXExport -f "{path}" -s;')


@undo_chunk
def action_export_top_group_fbx_final(*_):
    if not ensure_fbx_export_plugin():
        cmds.warning("FBX plugin (fbxmaya) could not be loaded.")
        return

    scene_path = cmds.file(q=True, sn=True)
    asset_root = _get_asset_root_from_scene(scene_path)
    if not asset_root:
        cmds.warning("Could not derive asset root from scene path.")
        return

    final_dir = os.path.join(asset_root, "mod", "final")
    os.makedirs(final_dir, exist_ok=True)

    groups_long = top_level_groups_only()
    if not groups_long:
        cmds.inViewMessage(amg="No top-level (world-parent) groups found.", pos="topCenter", fade=True)
        return

    if len(groups_long) == 1:
        chosen = groups_long
    else:
        dlg = TopGroupFBXPicker(groups_long)
        result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
        if result != QtWidgets.QDialog.Accepted: return
        chosen = dlg.selected_groups()

    prev_sel = cmds.ls(sl=True, long=True) or []
    exported = 0
    for g in chosen:
        try:
            short = g.split("|")[-1]
            out_path = os.path.join(final_dir, safe_fs_name(short) + ".fbx")
            cmds.select(g, r=True)
            _fbx_export_selected(out_path)
            exported += 1
        except Exception as e:
            cmds.warning(f"FBX export failed for {g.split('|')[-1]}: {e}")

    if prev_sel: cmds.select(prev_sel, r=True)
    else: cmds.select(clear=True)

    if exported:
        cmds.inViewMessage(amg=f"FBX exported to: {final_dir}  ({exported} file(s))", pos="topCenter", fade=True)


# -----------------------------
# Group & Rename
# -----------------------------
class GroupRenameDialog(QtWidgets.QDialog):
    def __init__(self, default_name, parent=maya_main_window()):
        super().__init__(parent)
        self.setWindowTitle("Group & Rename")
        self.setObjectName("PhoenixGroupRenameDialog")
        self.setWindowFlags(self.windowFlags() ^ QtCore.Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(350)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        layout.addWidget(QtWidgets.QLabel("Outer group name:"))
        self.name_edit = QtWidgets.QLineEdit(default_name)
        self.name_edit.selectAll()
        self.name_edit.returnPressed.connect(self.accept)
        layout.addWidget(self.name_edit)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_rename = QtWidgets.QPushButton("Rename")
        self.btn_cancel = QtWidgets.QPushButton("Cancel")
        self.btn_rename.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_rename)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

    def get_name(self): return self.name_edit.text().strip()


@undo_chunk
def action_group_and_rename(*_):
    sel = cmds.ls(sl=True, long=True)
    if not sel:
        cmds.warning("Nothing selected. Select objects to group.")
        return
    scene = cmds.file(q=True, sn=True, shortName=True) or ""
    default_name = os.path.splitext(scene)[0] if scene else "untitled"
    dlg = GroupRenameDialog(default_name)
    result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
    if result != QtWidgets.QDialog.Accepted: return
    outer_name = dlg.get_name()
    if not outer_name:
        cmds.warning("No name entered.")
        return
    geo_grp = cmds.group(sel, name="geo")
    outer_grp = cmds.group(geo_grp, name=outer_name)
    cmds.select(outer_grp, r=True)
    cmds.inViewMessage(amg=f"Created: {outer_name} > geo", pos="topCenter", fade=True)


# ============================================================
# MAIN UI — retro pixel panel (same look as Phoenix Autosave)
#   Double-click the SET TOOLS title  -> Settings
#     RETRO STYLE  on/off  (off = plain default Maya/Qt look)
#     DOCKABLE     on/off  (on  = Maya workspace control)
#     Presets / Customize colours / Shelf icon (film clapper)
#   All settings persist via optionVars.
# ============================================================
import sys
import copy
import json
import base64
import zlib

try:
    from PySide6 import QtGui
except Exception:
    from PySide2 import QtGui

_ST_OBJECT_NAME = "PhoenixSetToolsUI"
_ST_DOCK_NAME   = "PhoenixSetToolsDock"
_ST_DOCK_WS     = _ST_DOCK_NAME + "WorkspaceControl"
_ST_SHELF_TAG   = "phxSetTools"

_OPT_ST_THEME = "phoenixSetTools_theme_json"
_OPT_ST_ICON  = "phoenixSetTools_shelf_icon"
_OPT_ST_RETRO = "phoenixSetTools_retro_style"
_OPT_ST_DOCK  = "phoenixSetTools_dockable"
_OPT_ST_SCRIPT = "phoenixSetTools_script_path"

_ST_WIDTH = 360


def _st_get_bool(name, default):
    try:
        if cmds.optionVar(exists=name):
            return bool(cmds.optionVar(q=name))
    except Exception:
        pass
    return default


def _st_set_bool(name, value):
    try:
        cmds.optionVar(iv=(name, int(bool(value))))
    except Exception:
        pass


def _st_retro_on():
    return _st_get_bool(_OPT_ST_RETRO, True)


def _st_dock_on():
    return _st_get_bool(_OPT_ST_DOCK, False)


# ── themes ───────────────────────────────────────────────────────────────
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
            if cmds.optionVar(exists=_OPT_ST_THEME):
                data = json.loads(cmds.optionVar(q=_OPT_ST_THEME) or "{}")
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
        cmds.optionVar(sv=(_OPT_ST_THEME, json.dumps({"cur": st["cur"], "themes": slim})))
    except Exception:
        pass


def _T():
    st = _theme_state()
    return st["themes"][st["cur"]]


def _c(key, theme=None):
    return QtGui.QColor((theme or _T())[key])


# ── font ─────────────────────────────────────────────────────────────────
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
    """Pixel box: hard offset shadow, solid border, flat fill."""
    if sh and shc is not None:
        p.fillRect(QtCore.QRect(x + sh, y + sh, w, h), shc)
    p.fillRect(QtCore.QRect(x, y, w, h), ink)
    p.fillRect(QtCore.QRect(x + bw, y + bw, w - 2 * bw, h - 2 * bw), fill)


def _sel_frame(p, rect, col):
    p.fillRect(QtCore.QRect(rect.x(), rect.y(), rect.width(), 3), col)
    p.fillRect(QtCore.QRect(rect.x(), rect.bottom() - 2, rect.width(), 3), col)
    p.fillRect(QtCore.QRect(rect.x(), rect.y(), 3, rect.height()), col)
    p.fillRect(QtCore.QRect(rect.right() - 2, rect.y(), 3, rect.height()), col)


# ── painted widgets ──────────────────────────────────────────────────────
class _Label(QtWidgets.QWidget):
    def __init__(self, text, px=13, key="ink", align=None, bold=False, spacing=1.0, parent=None):
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
    def __init__(self, text, parent=None):
        super(_PixelButton, self).__init__(parent)
        self.setText(text)
        self._hot = False
        self._hover = False
        self._font = _px(12, False, 0.5)
        self.setFixedHeight(36)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.NoFocus)

    def sizeHint(self):
        fm = QtGui.QFontMetrics(self._font)
        return QtCore.QSize(_text_w(fm, self.text()) + 24, 36)

    def minimumSizeHint(self):
        return QtCore.QSize(30, 36)

    def setHot(self, hot):
        if hot != self._hot:
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
        if self._hot:
            fill, fg = _c("acc"), _c("acci")
        else:
            fill, fg = _c("fld"), _c("ink")
        if self._hover and not down:
            fill = fill.darker(109)
        _pbox(p, off, off, w, h, fill, _c("ink"), 3, sh, _c("sh"))
        p.setFont(self._font)
        p.setPen(fg)
        p.drawText(QtCore.QRect(off, off, w, h), QtCore.Qt.AlignCenter, self.text())
        p.end()


class _WinBtn(QtWidgets.QAbstractButton):
    _X = ("10001", "01010", "00100", "01010", "10001")

    def __init__(self, kind, parent=None):
        super(_WinBtn, self).__init__(parent)
        self._kind = kind
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
        if self._kind == "min":
            p.fillRect(QtCore.QRect(6, 10, 10, 3), glyph)
        else:
            x0, y0 = 6, 5
            for r, row in enumerate(self._X):
                for c, ch in enumerate(row):
                    if ch == "1":
                        p.fillRect(QtCore.QRect(x0 + c * 2, y0 + r * 2, 2, 2), glyph)
        p.end()


class _BigTitle(QtWidgets.QWidget):
    doubleClicked = QtCore.Signal()

    def __init__(self, text, parent=None):
        super(_BigTitle, self).__init__(parent)
        self._text = text
        self._font = _px(26, True, 2.0)
        self.setFixedHeight(QtGui.QFontMetrics(self._font).height() + 6)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Double-click for settings")

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setFont(self._font)
        r = self.rect().adjusted(0, 0, -3, -3)
        p.setPen(_c("tsh")); p.drawText(r.translated(3, 3), QtCore.Qt.AlignCenter, self._text)
        p.setPen(_c("ink")); p.drawText(r, QtCore.Qt.AlignCenter, self._text)
        p.end()


class _SceneBox(QtWidgets.QWidget):
    """Pixel field with a file icon; name centred, scrolls sideways when too long."""
    def __init__(self, parent=None):
        super(_SceneBox, self).__init__(parent)
        self._text = ""
        self._off = 0
        self.setFixedHeight(34)
        self._font = _px(11, False, 0.5)
        self._timer = QtCore.QTimer(self); self._timer.setInterval(140)
        self._timer.timeout.connect(self._step)
        self._timer.start()

    def setText(self, t):
        t = (t or "").upper()
        if t != self._text:
            self._text, self._off = t, 0
            self.update()

    def _inner(self):
        return QtCore.QRect(32, 3, self.width() - 3 - 64, 25)

    def _tw(self):
        return _text_w(QtGui.QFontMetrics(self._font), self._text)

    def _overflow(self):
        return self._tw() > self._inner().width()

    def _step(self):
        if self._overflow():
            self._off = (self._off + 6) % (self._tw() + 40)
            self.update()
        elif self._off:
            self._off = 0
            self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        ink = _c("ink")
        _pbox(p, 0, 0, self.width() - 3, 31, _c("fld"), ink, 3, 3, _c("sh"))
        x, y = 11, 7
        p.fillRect(QtCore.QRect(x, y, 10, 2), ink)
        p.fillRect(QtCore.QRect(x, y, 2, 18), ink)
        p.fillRect(QtCore.QRect(x, y + 16, 14, 2), ink)
        p.fillRect(QtCore.QRect(x + 12, y + 4, 2, 14), ink)
        p.fillRect(QtCore.QRect(x + 10, y + 2, 2, 2), ink)
        p.fillRect(QtCore.QRect(x + 8, y + 2, 2, 4), ink)
        p.fillRect(QtCore.QRect(x + 8, y + 4, 4, 2), ink)
        p.setFont(self._font); p.setPen(ink)
        r = self._inner()
        if self._overflow():
            tw = self._tw()
            p.setClipRect(r)
            for k in (0, 1):
                x0 = r.left() - self._off + k * (tw + 40)
                p.drawText(QtCore.QRect(x0, r.top(), tw + 4, r.height()),
                           QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self._text)
        else:
            p.drawText(r, QtCore.Qt.AlignCenter, self._text)
        p.end()


class _Toggle(QtWidgets.QWidget):
    """Dark pill switch: label on the left, pixel track on the right."""
    toggled = QtCore.Signal(bool)

    def __init__(self, text, on=False, parent=None):
        super(_Toggle, self).__init__(parent)
        self._text, self._on, self._hover = text, bool(on), False
        self._font = _px(11, False, 0.5)
        self.setFixedHeight(34)
        self.setMinimumWidth(60)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def isChecked(self):
        return self._on

    def enterEvent(self, e):
        self._hover = True; self.update()

    def leaveEvent(self, e):
        self._hover = False; self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.rect().contains(e.pos()):
            self._on = not self._on
            self.update()
            self.toggled.emit(self._on)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        nb = _c("nb")
        if self._hover:
            nb = nb.lighter(118)
        p.setPen(QtCore.Qt.NoPen); p.setBrush(nb)
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 7, 7)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        col = _c("nt") if self._on else _c("nd")
        p.setFont(self._font); p.setPen(col)
        p.drawText(QtCore.QRect(10, 0, self.width() - 52, self.height()),
                   QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, self._text)
        x0 = self.width() - 10 - 30
        y0 = (self.height() - 14) // 2
        p.fillRect(QtCore.QRect(x0, y0, 30, 14), col)
        p.fillRect(QtCore.QRect(x0 + 2, y0 + 2, 26, 10), nb)
        p.fillRect(QtCore.QRect(x0 + (18 if self._on else 4), y0 + 4, 6, 6), col)
        p.end()


class _HeaderBar(QtWidgets.QWidget):
    def __init__(self, text, close_cb, parent=None):
        super(_HeaderBar, self).__init__(parent)
        self.setFixedHeight(38)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(14, 6, 8, 10)
        h.setSpacing(6)
        lab = _Label(text, 12)
        lab.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        h.addWidget(lab); h.addStretch(1)
        x = _WinBtn("close"); x.setToolTip("Back")
        x.clicked.connect(lambda *_: close_cb())
        h.addWidget(x)

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.fillRect(QtCore.QRect(0, self.height() - 4, self.width(), 4), _c("ink"))
        p.end()


# ── theme panel widgets ──────────────────────────────────────────────────
class _ThemeCard(QtWidgets.QWidget):
    clicked = QtCore.Signal(int)

    def __init__(self, idx, parent=None):
        super(_ThemeCard, self).__init__(parent)
        self._idx = idx
        self.setFixedHeight(50)
        self.setMinimumWidth(50)
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
                   fm.elidedText(t["n"].upper(), QtCore.Qt.ElideRight, max(10, w - 16)))
        sx, sy, sw = m + 6, m + h - 6 - 10, w - 12
        p.fillRect(QtCore.QRect(sx, sy, sw, 10), QtGui.QColor(t["ink"]))
        keys = ("fld", "acc", "nb", "tsh")
        iw = (sw - 4) / 4.0
        for i, k in enumerate(keys):
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
    """4 per row (window is narrower than Autosave), last row centred."""
    COLS, GX, GY = 4, 8, 8

    def __init__(self, swatches, parent=None):
        super(_SwatchGrid, self).__init__(parent)
        self._items = swatches
        for s in swatches:
            s.setParent(self)
        rows = (len(swatches) + self.COLS - 1) // self.COLS
        self.setFixedHeight(rows * 46 + (rows - 1) * self.GY)

    def resizeEvent(self, e):
        W = self.width()
        iw = int((W - (self.COLS - 1) * self.GX) / self.COLS)
        n = len(self._items)
        for i, s in enumerate(self._items):
            r, c = divmod(i, self.COLS)
            in_row = min(self.COLS, n - r * self.COLS)
            row_w = in_row * iw + (in_row - 1) * self.GX
            x0 = (W - row_w) // 2
            s.setGeometry(x0 + c * (iw + self.GX), r * (46 + self.GY), iw, 46)
        super(_SwatchGrid, self).resizeEvent(e)


# ── shelf icon: pixel film clapper, one colour version per theme ─────────
_ST_ICON_MAP = [
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


def _st_icon_pixels(theme):
    col = {"#": theme["ink"], "b": theme["bg"], "s": theme["mid"], "l": theme["nt"], "a": theme["acc"]}
    out = []
    for row in _ST_ICON_MAP:
        row = row.ljust(16)[:16]
        out.append([col.get(ch) for ch in row])
    return out


def _st_icon_image(theme, scale=2):
    img = QtGui.QImage(16 * scale, 16 * scale, QtGui.QImage.Format_ARGB32)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    for y, row in enumerate(_st_icon_pixels(theme)):
        for x, c in enumerate(row):
            if c:
                p.fillRect(x * scale, y * scale, scale, scale, QtGui.QColor(c))
    p.end()
    return img


def _st_icon_choice():
    try:
        if cmds.optionVar(exists=_OPT_ST_ICON):
            return max(0, min(len(_THEMES_BASE) - 1, int(cmds.optionVar(q=_OPT_ST_ICON))))
    except Exception:
        pass
    return 0


def _st_write_icon(idx):
    theme = _theme_state()["themes"][idx]
    sig = "".join(theme[k] for k in ("ink", "bg", "mid", "nt", "acc")).replace("#", "") + "|".join(_ST_ICON_MAP)
    tag = format(zlib.crc32(sig.encode("ascii")) & 0xFFFFFF, "06x")
    folder = cmds.internalVar(userBitmapsDir=True)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "phxSetTools_{}_{}.png".format(idx, tag)).replace("\\", "/")
    if not os.path.exists(path):
        _st_icon_image(theme, 2).save(path, "PNG")
    for f in os.listdir(folder):
        if f.startswith("phxSetTools_") and f.endswith(".png"):
            full = os.path.join(folder, f).replace("\\", "/")
            if full != path:
                try: os.remove(full)
                except Exception: pass
    return path


def _st_shelf_top():
    try:
        return mel.eval("$_phx_st_tmp = $gShelfTopLevel")
    except Exception:
        return None


def _st_shelf_buttons():
    top = _st_shelf_top()
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
                        cmds.shelfButton(path, q=True, docTag=True) == _ST_SHELF_TAG:
                    out.append(path)
            except Exception:
                pass
    return out


def st_set_shelf_icon(idx=None):
    """Pick the shelf icon (0-5, theme order) and update every Set Tools shelf button."""
    if idx is None:
        idx = _st_icon_choice()
    idx = max(0, min(len(_THEMES_BASE) - 1, int(idx)))
    try: cmds.optionVar(iv=(_OPT_ST_ICON, idx))
    except Exception: pass
    path = _st_write_icon(idx)
    for b in _st_shelf_buttons():
        try: cmds.shelfButton(b, e=True, image=path, image1=path)
        except Exception: pass
    return path


def _st_script_path():
    """Path of this .py file — from __file__, the saved optionVar, or asked once."""
    path = globals().get("__file__")
    if path and os.path.isfile(path) and path.lower().endswith(".py"):
        path = os.path.abspath(path)
    else:
        path = None
        try:
            if cmds.optionVar(exists=_OPT_ST_SCRIPT):
                saved = cmds.optionVar(q=_OPT_ST_SCRIPT)
                if saved and os.path.isfile(saved):
                    path = saved
        except Exception:
            pass
    if not path:
        res = cmds.fileDialog2(fileFilter="Python (*.py)", fm=1, dialogStyle=2,
                               caption="Locate the Phoenix Set Tools .py file for the shelf button")
        if not res:
            return None
        path = res[0]
    path = path.replace("\\", "/")
    try: cmds.optionVar(sv=(_OPT_ST_SCRIPT, path))
    except Exception: pass
    return path


def st_install_shelf_button():
    """Adds the Set Tools button to the current shelf (or updates the icon if present)."""
    icon = _st_write_icon(_st_icon_choice())
    existing = _st_shelf_buttons()
    if existing:
        for b in existing:
            cmds.shelfButton(b, e=True, image=icon, image1=icon)
        return existing[0]
    mod = __name__
    if mod != "__main__" and mod in sys.modules:
        command = ("import sys, importlib\n"
                   "if '{0}' in sys.modules:\n"
                   "    importlib.reload(sys.modules['{0}'])\n"
                   "else:\n"
                   "    import {0}").format(mod)
    else:
        path = _st_script_path()
        if not path:
            return None
        command = ('_p = r"{0}"\n'
                   'exec(compile(open(_p, encoding="utf-8").read(), _p, "exec"), '
                   '{{"__name__": "__main__", "__file__": _p}})').format(path)
    top = _st_shelf_top()
    cur = cmds.tabLayout(top, q=True, selectTab=True)
    parent = cmds.shelfLayout(cur, q=True, fullPathName=True)
    btn = cmds.shelfButton(parent=parent, image=icon, image1=icon, label="Set Tools",
                           annotation="Phoenix Set Tools", imageOverlayLabel="",
                           sourceType="python", docTag=_ST_SHELF_TAG, command=command)
    try:
        mel.eval("saveAllShelves $gShelfTopLevel;")
    except Exception:
        pass
    return btn


class _IconTile(QtWidgets.QWidget):
    clicked = QtCore.Signal(int)

    def __init__(self, idx, parent=None):
        super(_IconTile, self).__init__(parent)
        self._idx = idx
        self.setFixedHeight(42)
        self.setMinimumWidth(40)
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
        if _st_icon_choice() == self._idx:
            _sel_frame(p, QtCore.QRect(x0 - 7, y0 - 7, size + 14, size + 14), _c("acc"))
        for y, row in enumerate(_st_icon_pixels(_theme_state()["themes"][self._idx])):
            for x, c in enumerate(row):
                if c:
                    p.fillRect(QtCore.QRect(x0 + x * px, y0 + y * px, px, px), QtGui.QColor(c))
        p.end()


# ── click routing: left / shift+left / right ─────────────────────────────
class _ClickRouter(QtCore.QObject):
    def __init__(self, btn, left=None, shift=None, right=None):
        super(_ClickRouter, self).__init__(btn)
        self._shift = shift
        if left:
            btn.clicked.connect(lambda *_: left())
        if right:
            btn.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda *_: right())
        if shift:
            btn.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.MouseButtonPress \
                and ev.button() == QtCore.Qt.LeftButton \
                and (ev.modifiers() & QtCore.Qt.ShiftModifier):
            self._shift()
            return True
        return False


def _open_notes(*_):
    NotesPanel.open_panel()


# (label_retro, label_plain, left, shift, right, tooltip)
_ST_MAIN_BUTTONS = [
    ("SAVE ELEMENT", "Save Set Element",
     action_save_set_element, action_create_set_structure, action_switch_set_element,
     "Left-Click: Save scene to selected 'maya' folder with auto-generated name\n"
     "Shift+Click: Create full set folder structure (requires new empty scene)\n"
     "Right-Click: Switch between set element files"),
    ("GROUP UNGROUPED", "Group Ungrouped",
     action_group_all_ungrouped, None, None,
     "Wrap each top-level mesh in a group with matching name"),
    ("IMPORT BLOCKOUT", "Import Blockout",
     action_import_blockout, action_import_set_elements, action_import_blockout_right_click,
     "Left-Click: Import blockout OBJ from asset's mod folder\n"
     "Shift+Click: Import set elements as references\n"
     "Right-Click: Browse/import OBJ+FBX from setElements asset mod folders"),
    ("EXPORT GROUPS", "Export Groups",
     action_export_groups, action_shift_export_groups, action_export_top_group_fbx_final,
     "Left-Click: Export groups as FBX (with structure)\n"
     "Shift+Click: Select groups → centre Y=0, smooth, cleanup, export merged FBX to set element mod/obj, revert\n"
     "Right-Click: Export top-level groups as FBX into mod/final"),
    ("DUPLICATE", "Duplicate",
     action_duplicate_with_position_reference, None, action_duplicate_right_click,
     "Left-Click: Duplicate selection to origin with position reference cube\n"
     "Right-Click: Restore position using stored reference"),
    ("CLEAN UP", "Clean Up",
     action_cleanup, None, None,
     "Open setElements folder and clean up each Maya file:\n"
     "- Manual: review each file and choose what to keep\n"
     "- Auto: automatically removes non-relevant objects and positionRefs"),
]
_ST_NOTES_TIP = ("Left-Click: Open notes panel\n"
                 "Shift+Click: Open PureRef (set-level ref)\n"
                 "Right-Click: Open PureRef (asset-level ref)")
_ST_GRP_TIP = ("Group selection into 'geo', wrap in outer group.\n"
               "Defaults outer group name to current scene file name.")
_ST_CREATE_TIP = ("Create a new top-level set folder (maya/blockouts, ref, setElements, "
                  "mod/obj, sourceImages/srcImages — configurable in the dialog) and open its blockout file.")


def _st_rebuild_later():
    """Re-open the UI after a style/dock change (deferred so the current widget can die safely)."""
    QtCore.QTimer.singleShot(0, show_phoenix_ui)


# ── panel body (shared by every host) ────────────────────────────────────
class _SetToolsBody(QtWidgets.QWidget):
    def __init__(self, host, retro, docked, parent=None):
        super(_SetToolsBody, self).__init__(parent)
        self._host, self._retro, self._docked = host, retro, docked
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if retro:
            _ensure_font()
            _theme_state()
            self.page_main = self._build_retro_main()
            self.page_settings = self._build_retro_settings()
        else:
            self.page_main = self._build_plain_main()
            self.page_settings = self._build_plain_settings()
        lay.addWidget(self.page_main)
        lay.addWidget(self.page_settings)
        lay.addStretch(1)
        self.page_settings.hide()

        if retro:
            self._scene_timer = QtCore.QTimer(self)
            self._scene_timer.setInterval(1000)
            self._scene_timer.timeout.connect(self._refresh_scene)
            self._scene_timer.start()
            self._refresh_scene()

    # background only when docked (floating host paints its own rounded frame)
    def paintEvent(self, e):
        if self._retro and self._docked:
            p = QtGui.QPainter(self)
            p.fillRect(self.rect(), _c("bg"))
            p.end()

    def mousePressEvent(self, e):
        fw = self.focusWidget()
        if fw is not None:
            fw.clearFocus()
        e.ignore()

    def _refresh_scene(self):
        try:
            sn = cmds.file(q=True, sn=True, shortName=True) or "UNTITLED"
        except Exception:
            sn = "UNTITLED"
        self.scene.setText(sn)

    def _show_page(self, idx):
        self.page_main.setVisible(idx == 0)
        self.page_settings.setVisible(idx == 1)
        if not self._docked:
            QtCore.QTimer.singleShot(0, self._fit_host)

    def _fit_host(self):
        try:
            self._host.adjustSize()
        except Exception:
            pass

    # ── retro: main page ─────────────────────────────────────────────────
    def _build_retro_main(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(14, 8, 14, 14)
        v.setSpacing(0)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(6)
        top.addStretch(1)
        if not self._docked:
            b_min = _WinBtn("min"); b_min.setToolTip("Minimize")
            b_min.clicked.connect(lambda *_: self._host.showMinimized())
            b_x = _WinBtn("close"); b_x.setToolTip("Close")
            b_x.clicked.connect(lambda *_: self._host.close())
            top.addWidget(b_min); top.addWidget(b_x)
            v.addLayout(top)
        else:
            v.addSpacing(6)

        title = _BigTitle("SET TOOLS")
        title.doubleClicked.connect(lambda: self._show_page(1))
        v.addWidget(title)
        v.addSpacing(2)
        v.addWidget(_Label("MADE BY VIBEPHOENIX", 10, "mid", QtCore.Qt.AlignCenter))
        v.addSpacing(10)

        v.addWidget(_Label("Scene", 12)); v.addSpacing(5)
        self.scene = _SceneBox()
        v.addWidget(self.scene); v.addSpacing(10)

        v.addWidget(_Label("Set element", 12)); v.addSpacing(5)
        g = QtWidgets.QGridLayout()
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10); g.setVerticalSpacing(5)
        items = list(_ST_MAIN_BUTTONS) + [
            ("NOTES", None, _open_notes, action_open_pureref_set_level, action_open_pureref_asset_level, _ST_NOTES_TIP),
            ("GROUP + RENAME", None, action_group_and_rename, None, None, _ST_GRP_TIP),
        ]
        for i, (lab, _plain, left, shift, right, tip) in enumerate(items):
            b = _PixelButton(lab)
            b.setToolTip(tip)
            _ClickRouter(b, left, shift, right)
            g.addWidget(b, i // 2, i % 2)
        g.setColumnStretch(0, 1); g.setColumnStretch(1, 1)
        v.addLayout(g); v.addSpacing(10)

        v.addWidget(_Label("Navigate", 12)); v.addSpacing(5)
        nav = QtWidgets.QGridLayout()
        nav.setHorizontalSpacing(10)
        for i, (lab, fn, tip) in enumerate((
                ("< PREV", action_nav_prev, "Previous set element file.\nFrom main file: jumps to last element."),
                ("MAIN", action_nav_main, "Switch to the main set Maya file."),
                ("NEXT >", action_nav_next, "Next set element file.\nFrom main file: jumps to first element."))):
            b = _PixelButton(lab)
            b.setToolTip(tip)
            _ClickRouter(b, fn)
            nav.addWidget(b, 0, i); nav.setColumnStretch(i, 1)
        v.addLayout(nav); v.addSpacing(10)

        b_cfs = _PixelButton("CREATE FOLDER STRUCTURE")
        b_cfs.setToolTip(_ST_CREATE_TIP)
        _ClickRouter(b_cfs, action_create_folder_structure)
        v.addWidget(b_cfs)
        return page

    # ── retro: settings page ─────────────────────────────────────────────
    def _build_retro_settings(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(_HeaderBar("SETTINGS", self._close_settings))
        body = QtWidgets.QVBoxLayout()
        body.setContentsMargins(14, 10, 14, 14)
        body.setSpacing(0)
        v.addLayout(body)

        body.addWidget(_Label("Window", 12)); body.addSpacing(5)
        row = QtWidgets.QHBoxLayout(); row.setSpacing(10)
        t_style = _Toggle("RETRO STYLE", True)
        t_style.setToolTip("Off = plain default Maya look, no custom styling")
        t_dock = _Toggle("DOCKABLE", _st_dock_on())
        t_dock.setToolTip("On = Maya workspace control (dock it anywhere).\n"
                          "Off = frameless floating window with rounded corners.")
        t_style.toggled.connect(self._set_retro)
        t_dock.toggled.connect(self._set_dock)
        row.addWidget(t_style, 1); row.addWidget(t_dock, 1)
        body.addLayout(row); body.addSpacing(10)

        body.addWidget(_Label("Presets", 12)); body.addSpacing(2)
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2); grid.setVerticalSpacing(2)
        self.cards = []
        for i in range(len(_THEMES_BASE)):
            c = _ThemeCard(i)
            c.clicked.connect(self._pick_theme)
            grid.addWidget(c, i // 3, i % 3)
            grid.setColumnStretch(i % 3, 1)
            self.cards.append(c)
        body.addLayout(grid); body.addSpacing(8)

        self.cust_lbl = _Label("Customize", 12)
        body.addWidget(self.cust_lbl); body.addSpacing(5)
        sw = []
        for k, lab in _THEME_KEYS:
            s = _Swatch(k, lab)
            s.clicked.connect(self._edit_color)
            sw.append(s)
        body.addWidget(_SwatchGrid(sw)); body.addSpacing(10)

        body.addWidget(_Label("Shelf icon", 12)); body.addSpacing(2)
        icons = QtWidgets.QGridLayout(); icons.setHorizontalSpacing(4)
        self.icon_tiles = []
        for i in range(len(_THEMES_BASE)):
            t = _IconTile(i)
            t.clicked.connect(self._pick_icon)
            icons.addWidget(t, 0, i); icons.setColumnStretch(i, 1)
            self.icon_tiles.append(t)
        body.addLayout(icons); body.addSpacing(10)

        btns = QtWidgets.QGridLayout(); btns.setHorizontalSpacing(10)
        b_reset = _PixelButton("RESET THEME")
        b_done  = _PixelButton("DONE"); b_done.setHot(True)
        b_reset.setToolTip("Put the current theme back to its original colours")
        b_reset.clicked.connect(lambda *_: self._reset_theme())
        b_done.clicked.connect(lambda *_: self._close_settings())
        for i, b in enumerate((b_reset, b_done)):
            btns.addWidget(b, 0, i); btns.setColumnStretch(i, 1)
        body.addLayout(btns)
        self._update_cust_label()
        return page

    def _update_cust_label(self):
        self.cust_lbl.setText("Customize / " + _T()["n"].upper())

    def _repaint_all(self):
        for w in self._host.findChildren(QtWidgets.QWidget):
            w.update()
        self._host.update()

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
            st_set_shelf_icon(idx)
        except Exception as e:
            cmds.warning("[SetTools] shelf icon error: {}".format(e))
        for t in self.icon_tiles:
            t.update()

    def _close_settings(self):
        _save_theme_state()
        try:
            if _st_shelf_buttons():
                st_set_shelf_icon(_st_icon_choice())
        except Exception:
            pass
        self._show_page(0)

    def _set_retro(self, on):
        _st_set_bool(_OPT_ST_RETRO, on)
        _st_rebuild_later()

    def _set_dock(self, on):
        _st_set_bool(_OPT_ST_DOCK, on)
        _st_rebuild_later()

    # ── plain (default Qt) pages ─────────────────────────────────────────
    def _build_plain_main(self):
        page = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(page)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10); grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
        for i, (_lab, plain, left, shift, right, tip) in enumerate(_ST_MAIN_BUTTONS):
            b = QtWidgets.QPushButton(plain)
            b.setMinimumHeight(46)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.setToolTip(tip)
            _ClickRouter(b, left, shift, right)
            grid.addWidget(b, i // 2, i % 2)
        root.addLayout(grid)

        notes_row = QtWidgets.QHBoxLayout(); notes_row.setSpacing(4)
        b_notes = QtWidgets.QPushButton("Notes")
        b_notes.setMinimumHeight(26)
        b_notes.setToolTip(_ST_NOTES_TIP)
        _ClickRouter(b_notes, _open_notes, action_open_pureref_set_level, action_open_pureref_asset_level)
        b_grp = QtWidgets.QPushButton("Group && Rename")
        b_grp.setMinimumHeight(28)
        b_grp.setToolTip(_ST_GRP_TIP)
        _ClickRouter(b_grp, action_group_and_rename)
        b_set = QtWidgets.QPushButton("Settings")
        b_set.setMinimumHeight(26)
        b_set.setToolTip("Retro style / dockable")
        b_set.clicked.connect(lambda *_: self._show_page(1))
        for b in (b_notes, b_grp, b_set):
            b.setMinimumHeight(28)
            b.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
            notes_row.addWidget(b, 1)
        root.addLayout(notes_row)

        nav = QtWidgets.QHBoxLayout(); nav.setSpacing(4)
        for lab, fn, tip in (("◀  Prev", action_nav_prev, "Previous set element file.\nFrom main file: jumps to last element."),
                             ("Main", action_nav_main, "Switch to the main set Maya file."),
                             ("Next  ▶", action_nav_next, "Next set element file.\nFrom main file: jumps to first element.")):
            b = QtWidgets.QPushButton(lab)
            b.setMinimumHeight(26)
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            b.setToolTip(tip)
            _ClickRouter(b, fn)
            nav.addWidget(b)
        root.addLayout(nav)

        b_cfs = QtWidgets.QPushButton("Create Folder Structure")
        b_cfs.setMinimumHeight(40)
        b_cfs.setToolTip(_ST_CREATE_TIP)
        _ClickRouter(b_cfs, action_create_folder_structure)
        root.addWidget(b_cfs)
        return page

    def _build_plain_settings(self):
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)
        hdr = QtWidgets.QLabel("<b>Settings</b>")
        v.addWidget(hdr)
        cb_retro = QtWidgets.QCheckBox("Retro style")
        cb_retro.setChecked(False)
        cb_retro.setToolTip("Use the retro pixel look (themes, colours, shelf icon)")
        cb_dock = QtWidgets.QCheckBox("Dockable")
        cb_dock.setChecked(_st_dock_on())
        cb_dock.setToolTip("Open as a Maya workspace control you can dock anywhere")
        cb_retro.toggled.connect(self._set_retro)
        cb_dock.toggled.connect(self._set_dock)
        v.addWidget(cb_retro)
        v.addWidget(cb_dock)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        b_close = QtWidgets.QPushButton("Close")
        b_close.clicked.connect(lambda *_: self._show_page(0))
        row.addWidget(b_close)
        v.addLayout(row)
        self.scene = None
        return page


# ── hosts ────────────────────────────────────────────────────────────────
class _FramelessHost(QtWidgets.QWidget):
    """Retro, not dockable: frameless window with rounded corners, drag anywhere empty."""
    RADIUS = 14
    BORDER = 3

    def __init__(self, parent=None):
        super(_FramelessHost, self).__init__(parent or maya_main_window())
        _ensure_font()
        self.setObjectName(_ST_OBJECT_NAME)
        self.setWindowTitle("Phoenix — Set Tools")
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setFixedWidth(_ST_WIDTH)
        lay = QtWidgets.QVBoxLayout(self)
        b = self.BORDER
        lay.setContentsMargins(b, b, b, b)
        lay.setSpacing(0)
        self.body = _SetToolsBody(self, retro=True, docked=False)
        lay.addWidget(self.body)
        self._press = None
        self._origin = None
        self._moving = False
        self.adjustSize()

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


class _PlainHost(QtWidgets.QDialog):
    """No style, not dockable: the original default Qt dialog."""
    def __init__(self, parent=None):
        super(_PlainHost, self).__init__(parent or maya_main_window())
        self.setObjectName(_ST_OBJECT_NAME)
        self.setWindowTitle("Phoenix — Set Tools")
        # explicit flags: "~Hint" on PySide6 enums flips every other flag (incl. Frameless),
        # which removed the title bar and its close button
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowTitleHint | QtCore.Qt.WindowSystemMenuHint |
                            QtCore.Qt.WindowMinimizeButtonHint | QtCore.Qt.WindowCloseButtonHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setMinimumWidth(280)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.body = _SetToolsBody(self, retro=False, docked=False)
        lay.addWidget(self.body)


class _DockHost(maya.app.general.mayaMixin.MayaQWidgetDockableMixin, QtWidgets.QWidget):
    """Dockable: Maya workspace control (retro or plain content)."""
    def __init__(self, retro, parent=None):
        super(_DockHost, self).__init__(parent=parent)
        self.setObjectName(_ST_DOCK_NAME)
        self.setWindowTitle("Phoenix Set Tools")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.body = _SetToolsBody(self, retro=retro, docked=True)
        if retro:
            self.body.setMinimumWidth(280)
        scroll.setWidget(self.body)
        lay.addWidget(scroll)


def _st_close_existing():
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w.objectName() == _ST_OBJECT_NAME:
                w.close(); w.deleteLater()
        except Exception:
            pass
    try:
        if cmds.workspaceControl(_ST_DOCK_WS, q=True, exists=True):
            cmds.deleteUI(_ST_DOCK_WS)
    except Exception:
        pass


def show_phoenix_ui():
    _st_close_existing()
    retro = _st_retro_on()
    if _st_dock_on():
        host = _DockHost(retro)
        host.show(dockable=True, floating=True, retain=False,
                  width=_ST_WIDTH if retro else 300, height=600 if retro else 300)
        return host
    host = _FramelessHost() if retro else _PlainHost()
    host.show()
    host.raise_()
    host.activateWindow()
    return host


# Kept so older shelf buttons that call PhoenixSetToolsUI() still work.
def PhoenixSetToolsUI(*_a, **_k):
    return show_phoenix_ui()


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
    "BAADAAMRBQYXKzMRIREnMxEjfQF3+n19AnH9j30BdwAAAgB9AAACcQJxAAsADwA1QDIABgAEAwYEZwAHBwFfAAEBFU0CAQAAA18I"
    "BQIDAxYDTgAADw4NDAALAAsREREREQkHGyszETM1MxUzESM1IxURMzUjfX36fX36+voB9H19/gz6+gF3eP//AH0AAAJxA+gCJgAB"
    "AAAABgDOfQAAAwB9AAACcQPoAAsAFwAbAFhAVQABAAQAAQRnAgEADgUCAwcAA2cADAAKCQwKZwANDQdfAAcHFU0IAQYGCV8PCwIJ"
    "CRYJTgwMAAAbGhkYDBcMFxYVFBMSERAPDg0ACwALEREREREQBxsrEzUzNTMVMxUjNSMVAxEzNTMVMxEjNSMVETM1I319+n19+n19"
    "+n19+vr6Au59fX19fX39EgH0fX3+DPr6AXd4AAAEAH0AAAJxA2sAAwAHABMAFwBVQFICAQANAwwDAQUAAWcACgAIBwoIZwALCwVf"
    "AAUFFU0GAQQEB18OCQIHBxYHTggIBAQAABcWFRQIEwgTEhEQDw4NDAsKCQQHBAcGBQADAAMRDwcXKwE1MxUhNTMVAxEzNTMVMxEj"
    "NSMVETM1IwH0ff4MfX19+n19+vr6Au59fX19/RIB9H19/gz6+gF3eP//AH0AAAJxA+gCJgABAAAABgDNfQAABAB9AAACcQPoAAMA"
    "CwAXABsAWEBVAwEBAgEABAEAZwAEDgEFBwQFZwAMAAoJDApnAA0NB18ABwcVTQgBBgYJXw8LAgkJFglODAwEBBsaGRgMFwwXFhUU"
    "ExIREA8ODQQLBAsRERIREBAHGysBIzUzBTUjNTMVMxUBETM1MxUzESM1IxURMzUjAnF9ff6JfX36/ol9+n19+vr6A2t9+n19fX39"
    "EgH0fX3+DPr6AXd4AP//AH0AAAJxA+gCJgABAAAABgDQAAAAAgB9AAAC7gJxABEAFQCFS7AuUFhALgkBAwcBBAUDBGcKAQICAV8A"
    "AQEVTQAAAAZfCwgCBgYWTQAFBQZfCwgCBgYWBk4bQDQACgADAgpyCQEDBwEEBQMEZwACAgFfAAEBFU0AAAAGXwsIAgYGFk0ABQUG"
    "XwsIAgYGFgZOWUAVAAAVFBMSABEAERERERERERERDAceKzMRMzUhFSMVMxUjFTMVITUjFREzNSN9fQH0+vr6+v6JfX19AfR9fX19"
    "fX36+gF3eAAAAwB9AAACcQJxAAcACwAPAD1AOgAEAAcCBAdnAAEAAgYBAmcABQUAXwAAABVNAAYGA18IAQMDFgNOAAAPDg0MCwoJ"
    "CAAHAAcREREJBxkrMxEhFTMRIxUDMzUjETM1I30Bd319+vX19fUCcX3+iX0Bd33+iX0AAAMAfQAAAnECcQAHAA8AEwBNQEoFAQIK"
    "AQMIAgNnAAgMCQIEBggEZwAAAAFfAAEBFU0ABgYHXwsBBwcWB04QEAgIAAAQExATEhEIDwgPDg0MCwoJAAcABxEREQ0HGSsBNSM1"
    "MxUzFQE1IxEzETMVNTUzFQH0+vp9/ol9ffp9AXd9fX19/ol9AXf+iX19fX0A//8Aff8GAnECcQImAAoAAAAGANL2AAACAH0AAAJx"
    "AnEABwALADFALgABAAIEAQJnAAUFAF8AAAAVTQAEBANfBgEDAxYDTgAACwoJCAAHAAcREREHBxkrMxEhFTMRIxUnMxEjfQF3fX36"
    "9fUCcX3+iX19AXcAAgB9AAAC7gJxAAsAEwBBQD4IAQEJAQAEAQBnAAMABAYDBGcABwcCXwACAhVNAAYGBV8KAQUFFgVOAAATEhEQ"
    "Dw4NDAALAAsREREREQsHGyszNSM1MzUhFTMRIxUnMxEjFTMVI/p9fQF3fX369fV9ffp9+n3+iX19AXd9fQAAAQB9AAAB9AJxAAsA"
    "L0AsAAIAAwQCA2cAAQEAXwAAABVNAAQEBV8GAQUFFgVOAAAACwALEREREREHBxsrMxEhFSMVMxUjFTMVfQF3+vr6+gJxfX19fX0A"
    "//8AfQAAAfQD6AImAA4AAAAGAM59AP//AH0AAAH0A+gCJgAOAAAABgDPAAD//wB9AAAB9ANrAiYADgAAAQYAzACDAAmxAQK4/4Ow"
    "NSsA//8AfQAAAfQD6AImAA4AAAAGAM0AAAABAH0AAAH0AnEACQApQCYAAgADBAIDZwABAQBfAAAAFU0FAQQEFgROAAAACQAJERER"
    "EQYHGiszESEVIxUzFSMVfQF3+vr6AnF9fX36AAAEAH0AAAJxAnEAAwAHAA0AEQBRQE4ABQAEAwUEZwACCwYKAwMHAgNnCQEBAQBf"
    "AAAAFU0ABwcIXwwBCAgWCE4ODggIBAQAAA4RDhEQDwgNCA0MCwoJBAcEBwYFAAMAAxENBxcrEzUhFQERMxEzNSM1MxUFNTMV+gF3"
    "/gx9+n36/on6AfR9ff6JAXf+iX19+n19fQAAAQB9AAACcQJxAAsAJ0AkAAEABAMBBGcCAQAAFU0GBQIDAxYDTgAAAAsACxERERER"
    "BwcbKzMRMxUzNTMRIzUjFX19+n19+gJx+vr9j/r6AAABAH0AAAD6AnEAAwAZQBYAAAAVTQIBAQEWAU4AAAADAAMRAwcXKzMRMxF9"
    "fQJx/Y8A//8AfQAAAXcD6AImABYAAAAGAM4AAP//AAAAAAF3A+gCJgAWAAAABgDPgwD//wAAAAABdwNrAiYAFgAAAQYAzIODAAmx"
    "AQK4/4OwNSsA//8AAAAAAPoD6AImABYAAAAGAM2DAAACAH0AAAJxAnEAAwALADdANAADAQEDVwIGAgEBAF8AAAAVTQAEBAVfBwEF"
    "BRYFTgQEAAAECwQLCgkIBwYFAAMAAxEIBxcrJREzEQU1IzUzFTMVAfR9/ol9ffp9AfT+DH19fX19AAADAH0AAAJxAnEADwATABcA"
    "VUBSAAoOAQsBCgtnAAEABgIBBmcAAgAFAwIFZw0BCQkAXwgBAAAVTQADAwRfDAcCBAQWBE4UFBAQAAAUFxQXFhUQExATEhEADwAP"
    "EREREREREQ8HHSszETMVMxUzFTMVIzUjNSMVEzUzFQc1MxV9fX19fX19ffp9+n0Ccfp9fX19ffoB9H19fX19AAABAH0AAAH0AnEA"
    "BQAfQBwAAAAVTQABAQJgAwECAhYCTgAAAAUABRERBAcYKzMRMxEzFX19+gJx/gx9AAACAH0AAALuAnEACwATAD5AOwcBAQYBBAIB"
    "BGcAAgADBQIDZwgBAAAVTQsJCgMFBRYFTgwMAAAMEwwTEhEQDw4NAAsACxERERERDAcbKzMRMxUzFTMVIzUjESERIzUzNTMRfX19"
    "fX19AXd9fX0CcX19fX3+iQF3fX39jwAAAQB9AAAC7gJxABMAO0A4AAEACAIBCGcAAgAHAwIHZwADAAYFAwZnBAEAABVNCgkCBQUW"
    "BU4AAAATABMRERERERERERELBx8rMxEzFTMVMxUzETMRIzUjNSM1IxF9fX19fX19fX19AnF9fX0Bd/2PfX19/okA//8AfQAAAu4D"
    "6AImAB8AAAAGANB9AAACAH0AAAJxAnEACwAPADVAMgMBAQQBAAUBAGcABwcCXwACAhVNAAYGBV8IAQUFFgVOAAAPDg0MAAsACxER"
    "ERERCQcbKzM1IxEzNTMVMxEjFSczESP6fX36fX36+vp9AXd9ff6JfYIBbQD//wB9AAACcQPoAiYAIQAAAAYAzn0A//8AfQAAAnED"
    "6AImACEAAAAGAM8+AP//AH0AAAJxA2sCJgAhAAABBgDMPoMACbECArj/g7A1KwD//wB9AAACcQPoAiYAIQAAAAYAzX0AAAIAfQAA"
    "Au4CcQALABcAlkuwClBYQDYACAsKCwhyAAYJBwcGcgAKAAkGCglnAwEBBAEABQEAZwALCwJfAAICFU0ABwcFYAwBBQUWBU4bQDgA"
    "CAsKCwgKgAAGCQcJBgeAAAoACQYKCWcDAQEEAQAFAQBnAAsLAl8AAgIVTQAHBwVgDAEFBRYFTllAGgAAFxYVFBMSERAPDg0MAAsA"
    "CxERERERDQcbKzM1IxEzNSEVMxEjFSUzFTM1IxUjNTM1I/p9fQF3fX3+iX36fX19+n0Bd319/ol9+nj1fX14AP//AH0AAAJxA+gC"
    "JgAhAAAABgDQAAAAAgB9AAAC7gJxAA8AEwA/QDwABAAFAAQFZwABAAAGAQBnCQEDAwJfAAICFU0IAQYGB18KAQcHFgdOAAATEhEQ"
    "AA8ADxERERERERELBx0rMzUjETM1IRUjFTMVIxUzFSUzESP6fX0B9Pr6+vr+EXh4fQF3fX19fX19fgF3AAIAfQAAAnECcQAJAA0A"
    "NUAyAAEAAgUBAmcABQADBAUDZwAGBgBfAAAAFU0HAQQEFgROAAANDAsKAAkACREREREIBxorMxEhFTMVIxUjFREzNSN9AXd9ffr1"
    "9QJxfX19+gF3fQACAH0AAAH0AnEACwAPADlANgABAAcCAQdnAAIAAwYCA2cABgAEBQYEZwAAABVNCAEFBRYFTgAADw4NDAALAAsR"
    "EREREQkHGyszETMVMxUzFSMVIxU1MzUjfX19fX19eHgCcX19fX19+n0AAgB9/4MCcQJxAA8AEwA+QDsEAQIFAQEAAgFnAAYKAQcG"
    "B2MACQkDXwADAxVNAAgIAF8AAAAWAE4AABMSERAADwAPEREREREREQsHHSsFNSM1IxEzNTMVMxEjFTMVJTMRIwH0+n19+n19ff6J"
    "+vp9fX0Bd319/ol9ff8BbQAAAgB9AAACcQJxAA8AEwB/S7AKUFhALgAFBgMGBXIAAQACCAECZwAIAAYFCAZnAAkJAF8AAAAVTQAD"
    "AwRfCgcCBAQWBE4bQC8ABQYDBgUDgAABAAIIAQJnAAgABgUIBmcACQkAXwAAABVNAAMDBF8KBwIEBBYETllAFAAAExIREAAPAA8R"
    "ERERERERCwcdKzMRIRUzFSMVMxUjNSM1IxURMzUjfQF3fX19fX199fUCcX19+n19ffoBd30AAwB9AAACcQJxAAMADwATAFVAUgAE"
    "AAMFBANnAAUAAgYFAmcABgsBBwgGB2cKAQEBAF8AAAAVTQAICAlfDAEJCRYJThAQBAQAABATEBMSEQQPBA8ODQwLCgkIBwYFAAMA"
    "AxENBxcrEzUhFQM1IzUjNTMVMxUzFQU1IRX6AXd9+n19+n3+DAF3AfR9ff6JfX19fX19fX19AAABAH0AAAH0AnEABwAhQB4CAQAA"
    "AV8AAQEVTQQBAwMWA04AAAAHAAcREREFBxkrMxEjNSEVIxH6fQF3fQH0fX3+DAACAH0AAAJxAnEABwALADBALQcFAgAAAV8EAQEB"
    "FU0AAgIDXwYBAwMWA04ICAAACAsICwoJAAcABxEREQgHGSszNSMRMxEzFTURMxH6fX36fX0B9P4MfX0B9P4M//8AfQAAAnED6AIm"
    "AC8AAAAGAM59AP//AH0AAAJxA+gCJgAvAAAABgDPPgD//wB9AAACcQNrAiYALwAAAQYAzD6DAAmxAgK4/4OwNSsA//8AfQAAAnED"
    "6AImAC8AAAAGAM19AAADAH0AAALuAnEACwAPABMARUBCCAEDDAkCAAQDAGcLBwIBAQJfBgECAhVNAAQEBV8KAQUFFgVOEBAMDAAA"
    "EBMQExIRDA8MDw4NAAsACxERERERDQcbKyE1IzUjNTMVMxUzFRM1MxUHNTMVAXd9fX19fX19+n19+vr6+n0Bd/r6+vr6AAADAH0A"
    "AALuAnEABwALABMARkBDAAcAAAdXBgsFAwAAAV8EAQEBFU0IAQICA18MCQoDAwMWA04MDAgIAAAMEwwTEhEQDw4NCAsICwoJAAcA"
    "BxEREQ0HGSszNSMRMxEzFTcRMxEHNSMRMxEzFfp9fX36ffp9fX19AfT+DH19AfT+DH19AXf+iX0ABAB9AAAC7gJxABMAGwAfACMA"
    "akBnCgEFEw0CAgYFAmcABgABBwYBZw4BBxQPAgAIBwBnDAEDAwRfCwEEBBVNEAEICAlfFRESAwkJFglOICAcHBQUAAAgIyAjIiEc"
    "HxwfHh0UGxQbGhkYFxYVABMAExERERERERERERYHHyshNSM1IzUjNSM1MxUzFTMVMxUzFQM1MzUzFSMVBTUzFQc1MxUCcX19fX19"
    "fX19ffp9fX3+iX36fX19fX19fX19fX0Bd319fX36fX19fX0AAwB9AAAC7gJxAAsADwATAEVAQggBAwwJAgAEAwBnCwcCAQECXwYB"
    "AgIVTQAEBAVfCgEFBRYFThAQDAwAABATEBMSEQwPDA8ODQALAAsREREREQ0HGyshESM1IzUzFTMVMxETNTMVBzUzFQF3fX19fX19"
    "ffp9AXd9fX19/okB9H19fX19AP//AH0AAALuA+gCJgA3AAAABwDOALwAAAADAH0AAAH0AnEABQAJAA8AiUuwClBYQC4IAQIAAwAC"
    "cgAFBAYGBXIAAwkBBAUDBGcAAAABXwABARVNAAYGB2AKAQcHFgdOG0AwCAECAAMAAgOAAAUEBgQFBoAAAwkBBAUDBGcAAAABXwAB"
    "ARVNAAYGB2AKAQcHFgdOWUAdCgoGBgAACg8KDw4NDAsGCQYJCAcABQAFERELBxgrATUjNSEVBzUzFQc1MxUzFQF3+gF3+n36ffoB"
    "d319+n19ffr6fX0AAAIAfQAAAnECcQALAA8ANUAyAAYABAMGBGcABwcBXwABARVNAgEAAANfCAUCAwMWA04AAA8ODQwACwALERER"
    "EREJBxsrMxEzNTMVMxEjNSMVETM1I319+n19+vr6AfR9ff4M+voBd3j//wB9AAACcQPoAiYAOgAAAAYAzn0AAAMAfQAAAnED6AAL"
    "ABcAGwBYQFUAAQAEAAEEZwIBAA4FAgMHAANnAAwACgkMCmcADQ0HXwAHBxVNCAEGBglfDwsCCQkWCU4MDAAAGxoZGAwXDBcWFRQT"
    "EhEQDw4NAAsACxEREREREAcbKxM1MzUzFTMVIzUjFQMRMzUzFTMRIzUjFREzNSN9ffp9ffp9ffp9ffr6+gLufX19fX19/RIB9H19"
    "/gz6+gF3eAAABAB9AAACcQNrAAMABwATABcAVUBSAgEADQMMAwEFAAFnAAoACAcKCGcACwsFXwAFBRVNBgEEBAdfDgkCBwcWB04I"
    "CAQEAAAXFhUUCBMIExIREA8ODQwLCgkEBwQHBgUAAwADEQ8HFysBNTMVITUzFQMRMzUzFTMRIzUjFREzNSMB9H3+DH19ffp9ffr6"
    "+gLufX19ff0SAfR9ff4M+voBd3j//wB9AAACcQPoAiYAOgAAAAYAzX0AAAQAfQAAAnED6AADAAsAFwAbAFhAVQMBAQIBAAQBAGcA"
    "BA4BBQcEBWcADAAKCQwKZwANDQdfAAcHFU0IAQYGCV8PCwIJCRYJTgwMBAQbGhkYDBcMFxYVFBMSERAPDg0ECwQLERESERAQBxsr"
    "ASM1MwU1IzUzFTMVAREzNTMVMxEjNSMVETM1IwJxfX3+iX19+v6Jffp9ffr6+gNrffp9fX19/RIB9H19/gz6+gF3eAD//wB9AAAC"
    "cQPoAiYAOgAAAAYA0AAAAAIAfQAAAu4CcQARABUAhUuwLlBYQC4JAQMHAQQFAwRnCgECAgFfAAEBFU0AAAAGXwsIAgYGFk0ABQUG"
    "XwsIAgYGFgZOG0A0AAoAAwIKcgkBAwcBBAUDBGcAAgIBXwABARVNAAAABl8LCAIGBhZNAAUFBl8LCAIGBhYGTllAFQAAFRQTEgAR"
    "ABEREREREREREQwHHiszETM1IRUjFTMVIxUzFSE1IxURMzUjfX0B9Pr6+vr+iX19fQH0fX19fX19+voBd3gAAAMAfQAAAnECcQAH"
    "AAsADwA9QDoABAAHAgQHZwABAAIGAQJnAAUFAF8AAAAVTQAGBgNfCAEDAxYDTgAADw4NDAsKCQgABwAHERERCQcZKzMRIRUzESMV"
    "AzM1IxEzNSN9AXd9ffr19fX1AnF9/ol9AXd9/ol9AAADAH0AAAJxAnEABwAPABMATUBKBQECCgEDCAIDZwAIDAkCBAYIBGcAAAAB"
    "XwABARVNAAYGB18LAQcHFgdOEBAICAAAEBMQExIRCA8IDw4NDAsKCQAHAAcRERENBxkrATUjNTMVMxUBNSMRMxEzFTU1MxUB9Pr6"
    "ff6JfX36fQF3fX19ff6JfQF3/ol9fX19AP//AH3/BgJxAnECJgBDAAAABgDS9gAAAgB9AAACcQJxAAcACwAxQC4AAQACBAECZwAF"
    "BQBfAAAAFU0ABAQDXwYBAwMWA04AAAsKCQgABwAHERERBwcZKzMRIRUzESMVJzMRI30Bd319+vX1AnF9/ol9fQF3AAIAfQAAAu4C"
    "cQALABMAQUA+CAEBCQEABAEAZwADAAQGAwRnAAcHAl8AAgIVTQAGBgVfCgEFBRYFTgAAExIREA8ODQwACwALERERERELBxsrMzUj"
    "NTM1IRUzESMVJzMRIxUzFSP6fX0Bd319+vX1fX36ffp9/ol9fQF3fX0AAAEAfQAAAfQCcQALAC9ALAACAAMEAgNnAAEBAF8AAAAV"
    "TQAEBAVfBgEFBRYFTgAAAAsACxERERERBwcbKzMRIRUjFTMVIxUzFX0Bd/r6+voCcX19fX19AP//AH0AAAH0A+gCJgBHAAAABgDO"
    "fQD//wB9AAAB9APoAiYARwAAAAYAzwAA//8AfQAAAfQDawImAEcAAAEGAMwAgwAJsQECuP+DsDUrAP//AH0AAAH0A+gCJgBHAAAA"
    "BgDNAAAAAQB9AAAB9AJxAAkAKUAmAAIAAwQCA2cAAQEAXwAAABVNBQEEBBYETgAAAAkACREREREGBxorMxEhFSMVMxUjFX0Bd/r6"
    "+gJxfX19+gAABAB9AAACcQJxAAMABwANABEAUUBOAAUABAMFBGcAAgsGCgMDBwIDZwkBAQEAXwAAABVNAAcHCF8MAQgIFghODg4I"
    "CAQEAAAOEQ4REA8IDQgNDAsKCQQHBAcGBQADAAMRDQcXKxM1IRUBETMRMzUjNTMVBTUzFfoBd/4Mffp9+v6J+gH0fX3+iQF3/ol9"
    "ffp9fX0AAAEAfQAAAnECcQALACdAJAABAAQDAQRnAgEAABVNBgUCAwMWA04AAAALAAsREREREQcHGyszETMVMzUzESM1IxV9ffp9"
    "ffoCcfr6/Y/6+gAAAQB9AAAA+gJxAAMAGUAWAAAAFU0CAQEBFgFOAAAAAwADEQMHFyszETMRfX0Ccf2PAAACAH0AAAF3A+gABwAL"
    "ADRAMQABAAIAAQJnAAAGAQMEAANnAAQEFU0HAQUFFgVOCAgAAAgLCAsKCQAHAAcREREIBxkrEzUzNTMVIxUDETMRfX19fX19Au59"
    "fX19/RICcf2PAAIAAAAAAXcD6AALAA8AOEA1AAEABAABBGcCAQAIBQIDBgADZwAGBhVNCQEHBxYHTgwMAAAMDwwPDg0ACwALERER"
    "EREKBxsrETUzNTMVMxUjNSMVEREzEX19fX19fQLufX19fX19/RICcf2PAAMAAAAAAXcDawADAAcACwA1QDICAQAHAwYDAQQAAWcA"
    "BAQVTQgBBQUWBU4ICAQEAAAICwgLCgkEBwQHBgUAAwADEQkHFysRNTMVMzUzFQMRMxF9fX36fQLufX19ff0SAnH9jwACAAAAAAD6"
    "A+gABwALADRAMQABAAACAQBnAAIGAQMEAgNnAAQEFU0HAQUFFgVOCAgAAAgLCAsKCQAHAAcREREIBxkrEzUjNTMVMxUDETMRfX19"
    "fX19Au59fX19/RICcf2PAAIAfQAAAnECcQADAAsAN0A0AAMBAQNXAgYCAQEAXwAAABVNAAQEBV8HAQUFFgVOBAQAAAQLBAsKCQgH"
    "BgUAAwADEQgHFyslETMRBTUjNTMVMxUB9H3+iX19+n0B9P4MfX19fX0AAAMAfQAAAnECcQAPABMAFwBVQFIACg4BCwEKC2cAAQAG"
    "AgEGZwACAAUDAgVnDQEJCQBfCAEAABVNAAMDBF8MBwIEBBYEThQUEBAAABQXFBcWFRATEBMSEQAPAA8RERERERERDwcdKzMRMxUz"
    "FTMVMxUjNSM1IxUTNTMVBzUzFX19fX19fX19+n36fQJx+n19fX19+gH0fX19fX0AAAEAfQAAAfQCcQAFAB9AHAAAABVNAAEBAmAD"
    "AQICFgJOAAAABQAFEREEBxgrMxEzETMVfX36AnH+DH0AAAIAfQAAAu4CcQALABMAPkA7BwEBBgEEAgEEZwACAAMFAgNnCAEAABVN"
    "CwkKAwUFFgVODAwAAAwTDBMSERAPDg0ACwALEREREREMBxsrMxEzFTMVMxUjNSMRIREjNTM1MxF9fX19fX0Bd319fQJxfX19ff6J"
    "AXd9ff2PAAABAH0AAALuAnEAEwA7QDgAAQAIAgEIZwACAAcDAgdnAAMABgUDBmcEAQAAFU0KCQIFBRYFTgAAABMAExERERERERER"
    "EQsHHyszETMVMxUzFTMRMxEjNSM1IzUjEX19fX19fX19fX0CcX19fQF3/Y99fX3+iQD//wB9AAAC7gPoAiYAWAAAAAYA0H0AAAIA"
    "fQAAAnECcQALAA8ANUAyAwEBBAEABQEAZwAHBwJfAAICFU0ABgYFXwgBBQUWBU4AAA8ODQwACwALEREREREJBxsrMzUjETM1MxUz"
    "ESMVJzMRI/p9ffp9ffr6+n0Bd319/ol9ggFtAP//AH0AAAJxA+gCJgBaAAAABgDOfQAAAwB9AAACcQPoAAsAFwAbAFhAVQABAAQA"
    "AQRnAgEADgUCAwgAA2cJAQcKAQYLBwZnAA0NCF8ACAgVTQAMDAtfDwELCxYLTgwMAAAbGhkYDBcMFxYVFBMSERAPDg0ACwALERER"
    "EREQBxsrEzUzNTMVMxUjNSMVETUjETM1MxUzESMVJzMRI319+n19+n19+n19+vr6Au59fX19fX39En0Bd319/ol9ggFtAAAEAH0A"
    "AAJxA2sAAwAHABMAFwBVQFICAQANAwwDAQYAAWcHAQUIAQQJBQRnAAsLBl8ABgYVTQAKCglfDgEJCRYJTggIBAQAABcWFRQIEwgT"
    "EhEQDw4NDAsKCQQHBAcGBQADAAMRDwcXKwE1MxUhNTMVETUjETM1MxUzESMVJzMRIwH0ff4MfX19+n19+vr6Au59fX19/RJ9AXd9"
    "ff6JfYIBbf//AH0AAAJxA+gCJgBaAAAABgDNfQAAAgB9AAAC7gJxAAsAFwCWS7AKUFhANgAICwoLCHIABgkHBwZyAAoACQYKCWcD"
    "AQEEAQAFAQBnAAsLAl8AAgIVTQAHBwVgDAEFBRYFThtAOAAICwoLCAqAAAYJBwkGB4AACgAJBgoJZwMBAQQBAAUBAGcACwsCXwAC"
    "AhVNAAcHBWAMAQUFFgVOWUAaAAAXFhUUExIREA8ODQwACwALERERERENBxsrMzUjETM1IRUzESMVJTMVMzUjFSM1MzUj+n19AXd9"
    "ff6Jffp9fX36fQF3fX3+iX36ePV9fXgA//8AfQAAAnED6AImAFoAAAAGANAAAAACAH0AAALuAnEADwATAD9APAAEAAUABAVnAAEA"
    "AAYBAGcJAQMDAl8AAgIVTQgBBgYHXwoBBwcWB04AABMSERAADwAPEREREREREQsHHSszNSMRMzUhFSMVMxUjFTMVJTMRI/p9fQH0"
    "+vr6+v4ReHh9AXd9fX19fX1+AXcAAgB9AAACcQJxAAkADQA1QDIAAQACBQECZwAFAAMEBQNnAAYGAF8AAAAVTQcBBAQWBE4AAA0M"
    "CwoACQAJEREREQgHGiszESEVMxUjFSMVETM1I30Bd319+vX1AnF9fX36AXd9AAIAfQAAAfQCcQALAA8AOUA2AAEABwIBB2cAAgAD"
    "BgIDZwAGAAQFBgRnAAAAFU0IAQUFFgVOAAAPDg0MAAsACxERERERCQcbKzMRMxUzFTMVIxUjFTUzNSN9fX19fX14eAJxfX19fX36"
    "fQACAH3/gwJxAnEADwATAD5AOwQBAgUBAQACAWcABgoBBwYHYwAJCQNfAAMDFU0ACAgAXwAAABYATgAAExIREAAPAA8RERERERER"
    "CwcdKwU1IzUjETM1MxUzESMVMxUlMxEjAfT6fX36fX19/on6+n19fQF3fX3+iX19/wFtAAACAH0AAAJxAnEADwATAH9LsApQWEAu"
    "AAUGAwYFcgABAAIIAQJnAAgABgUIBmcACQkAXwAAABVNAAMDBF8KBwIEBBYEThtALwAFBgMGBQOAAAEAAggBAmcACAAGBQgGZwAJ"
    "CQBfAAAAFU0AAwMEXwoHAgQEFgROWUAUAAATEhEQAA8ADxERERERERELBx0rMxEhFTMVIxUzFSM1IzUjFREzNSN9AXd9fX19fX31"
    "9QJxfX36fX19+gF3fQADAH0AAAJxAnEAAwAPABMAVUBSAAQAAwUEA2cABQACBgUCZwAGCwEHCAYHZwoBAQEAXwAAABVNAAgICV8M"
    "AQkJFglOEBAEBAAAEBMQExIRBA8EDw4NDAsKCQgHBgUAAwADEQ0HFysTNSEVAzUjNSM1MxUzFTMVBTUhFfoBd336fX36ff4MAXcB"
    "9H19/ol9fX19fX19fX0AAAEAfQAAAnECcQAXAElARgIBAAADCQADZwAJAAgECQhnAAQABQcEBWcACgoBXwABARVNAAcHBl8MCwIG"
    "BhYGTgAAABcAFxYVFBMRERERERERERENBx8rMxEzNTMVMxUjFTMVIxUjNTM1IzUzNSMRfX36fX19fX19fX36AfR9fX19fX19fX19"
    "/gwAAAEAfQAAAfQCcQAHACFAHgIBAAABXwABARVNBAEDAxYDTgAAAAcABxEREQUHGSszESM1IRUjEfp9AXd9AfR9ff4MAAIAfQAA"
    "AnECcQAHAAsAMEAtBwUCAAABXwQBAQEVTQACAgNfBgEDAxYDTggIAAAICwgLCgkABwAHERERCAcZKzM1IxEzETMVNREzEfp9ffp9"
    "fQH0/gx9fQH0/gz//wB9AAACcQPoAiYAaQAAAAYAzn0AAAMAfQAAAnED6AALABMAFwBRQE4AAQAEAAEEZwIBAAwFAgMHAANnDgsC"
    "BgYHXwoBBwcVTQAICAlfDQEJCRYJThQUDAwAABQXFBcWFQwTDBMSERAPDg0ACwALEREREREPBxsrEzUzNTMVMxUjNSMVETUjETMR"
    "MxU1ETMRfX36fX36fX36fQLufX19fX19/RJ9AfT+DH19AfT+DAAEAH0AAAJxA2sAAwAHAA8AEwBOQEsCAQALAwoDAQUAAWcNCQIE"
    "BAVfCAEFBRVNAAYGB18MAQcHFgdOEBAICAQEAAAQExATEhEIDwgPDg0MCwoJBAcEBwYFAAMAAxEOBxcrATUzFSE1MxURNSMRMxEz"
    "FTURMxEB9H3+DH19ffp9Au59fX19/RJ9AfT+DH19AfT+DAD//wB9AAACcQPoAiYAaQAAAAYAzX0AAAMAfQAAAu4CcQALAA8AEwBF"
    "QEIIAQMMCQIABAMAZwsHAgEBAl8GAQICFU0ABAQFXwoBBQUWBU4QEAwMAAAQExATEhEMDwwPDg0ACwALERERERENBxsrITUjNSM1"
    "MxUzFTMVEzUzFQc1MxUBd319fX19fX36fX36+vr6fQF3+vr6+voAAAMAfQAAAu4CcQAHAAsAEwBGQEMABwAAB1cGCwUDAAABXwQB"
    "AQEVTQgBAgIDXwwJCgMDAxYDTgwMCAgAAAwTDBMSERAPDg0ICwgLCgkABwAHERERDQcZKzM1IxEzETMVNxEzEQc1IxEzETMV+n19"
    "ffp9+n19fX0B9P4MfX0B9P4MfX0Bd/6JfQAEAH0AAALuAnEAEwAbAB8AIwBqQGcKAQUTDQICBgUCZwAGAAEHBgFnDgEHFA8CAAgH"
    "AGcMAQMDBF8LAQQEFU0QAQgICV8VERIDCQkWCU4gIBwcFBQAACAjICMiIRwfHB8eHRQbFBsaGRgXFhUAEwATERERERERERERFgcf"
    "KyE1IzUjNSM1IzUzFTMVMxUzFTMVAzUzNTMVIxUFNTMVBzUzFQJxfX19fX19fX19+n19ff6Jffp9fX19fX19fX19fQF3fX19ffp9"
    "fX19fQADAH0AAALuAnEACwAPABMARUBCCAEDDAkCAAQDAGcLBwIBAQJfBgECAhVNAAQEBV8KAQUFFgVOEBAMDAAAEBMQExIRDA8M"
    "Dw4NAAsACxERERERDQcbKyERIzUjNTMVMxUzERM1MxUHNTMVAXd9fX19fX19+n0Bd319fX3+iQH0fX19fX0A//8AfQAAAu4D6AIm"
    "AHEAAAAHAM4A+gAA//8AfQAAAu4DawImAHEAAAEGAMx9gwAJsQMCuP+DsDUrAAADAH0AAAH0AnEABQAJAA8AiUuwClBYQC4IAQIA"
    "AwACcgAFBAYGBXIAAwkBBAUDBGcAAAABXwABARVNAAYGB2AKAQcHFgdOG0AwCAECAAMAAgOAAAUEBgQFBoAAAwkBBAUDBGcAAAAB"
    "XwABARVNAAYGB2AKAQcHFgdOWUAdCgoGBgAACg8KDw4NDAsGCQYJCAcABQAFERELBxgrATUjNSEVBzUzFQc1MxUzFQF3+gF3+n36"
    "ffoBd319+n19ffr6fX0AAAIAfQD6AfQCcQAJAA0AMEAtAwEBAAAFAQBnAAUHAQQFBGQABgYCXwACAiUGTgAADQwLCgAJAAkRERER"
    "CAgaKzc1IzUzNTMVMxUnMzUj+n19fX31eHj6fX19ffp9eAACAH0A+gH0AnEACwAPADJALwMBAQQBAAUBAGcABggBBQYFYwAHBwJf"
    "AAICJQdOAAAPDg0MAAsACxERERERCQgbKzc1IzUzNTMVMxUjFSczNSP6fX19fX14c3P6fX19fX19gnMAAgB9AAACcQJxAAsADwA1"
    "QDIDAQEEAQAFAQBnAAcHAl8AAgIVTQAGBgVfCAEFBRYFTgAADw4NDAALAAsREREREQkHGyszNSMRMzUzFTMRIxUnMxEj+n19+n19"
    "+vr6fQF3fX3+iX2CAW0AAAEAfQAAAfQCcQAJACdAJAABAQJfAAICFU0DAQAABF8FAQQEFgROAAAACQAJEREREQYHGiszNTMRIzUz"
    "ETMVfX19+n19AXd9/gx9AAADAH0AAAJxAnEABwALABEAi0uwClBYQC8ABgUHBwZyAAIJAQMEAgNnAAQKAQUGBAVnAAAAAV8AAQEV"
    "TQAHBwhgCwEICBYIThtAMAAGBQcFBgeAAAIJAQMEAgNnAAQKAQUGBAVnAAAAAV8AAQEVTQAHBwhgCwEICBYITllAHgwMCAgAAAwR"
    "DBEQDw4NCAsICwoJAAcABxEREQwHGSsBNSE1IRUzFQU1MxUFNTMVIRUB9P6JAXd9/on6/ol9AXcBd319fX19fX36+n19AAADAH0A"
    "AAJxAnEABwAPABMAU0BQAAIKAQMFAgNnAAUABAYFBGcABgsBBwgGB2cAAAABXwABARVNAAgICV8MAQkJFglOEBAICAAAEBMQExIR"
    "CA8IDw4NDAsKCQAHAAcRERENBxkrATUhNSEVMxUHNSM1MxUzFQU1IRUB9P6JAXd9ffr6ff4MAXcBd319fX36fX19fX19fQABAH0A"
    "AAJxAnEADQApQCYEAQIFAQAGAgBoAwEBARVNBwEGBhYGTgAAAA0ADREREREREQgHHCshNSMRMxUzNTMVMxUjFQF3+n19fX19+gF3"
    "+vr6ffoAAAIAfQAAAnECcQALAA8AQEA9AAMAAAQDAGcABAgBBQYEBWcAAgIBXwABARVNAAYGB18JAQcHFgdODAwAAAwPDA8ODQAL"
    "AAsREREREQoHGyslNSERIRUhFTMVMxUFNSEVAfT+iQH0/on6ff4MAXd9fQF3fX19fX19fQAAAgB9AAACcQJxAA8AEwBEQEEAAQQA"
    "AVcABAAJCAQJZwAFBgEABwUAZwADAwJfAAICFU0ACAgHXwoBBwcWB04AABMSERAADwAPEREREREREQsHHSszNSMRMzUzFSMVMxUz"
    "FSMVJzM1I/p9ffr6+n19+vr6fQF3fX19fX19gnMAAQB9AAACcQJxAA0AaEuwClBYQCUABAIBAgRyAAEABQABBWcAAgIDXwADAxVN"
    "AAAABl8HAQYGFgZOG0AmAAQCAQIEAYAAAQAFAAEFZwACAgNfAAMDFU0AAAAGXwcBBgYWBk5ZQA8AAAANAA0REREREREIBxwrMzUz"
    "NTM1ITUhFSMVIxX6fX3+iQH0fX36fX19+n36AAMAfQAAAnECcQATABcAGwBPQEwFAQMGAQIBAwJnAAoADQwKDWcHAQEIAQAJAQBn"
    "AAsLBF8ABAQVTQAMDAlfDgEJCRYJTgAAGxoZGBcWFRQAEwATERERERERERERDwcfKzM1IzUzNSM1MzUzFTMVIxUzFSMVAzM1IxEz"
    "NSP6fX19ffp9fX19+vr6+vp9fX19fX19fX19AXxz/pNzAAIAfQAAAnECcQAPABMAREBBAAIBAwJXAAgAAQYIAWcFAQMABgADBmcA"
    "CQkEXwAEBBVNAAAAB18KAQcHFgdOAAATEhEQAA8ADxERERERERELBx0rMzUzNSM1IzUzNTMVMxEjFQMzNSP6+vp9ffp9ffr6+n19"
    "fX19ff6JfQF8cwAAAQB9AH0BdwJxAAUAH0AcAAAAAV8AAQElTQMBAgImAk4AAAAFAAUREQQIGCs3ESM1MxH6ffp9AXd9/gwAAQB9"
    "AH0B9AJxAA8Aa0uwClBYQCYAAQUAAAFyAAQABQEEBWcAAgIDXwADAyVNBgEAAAdgCAEHByYHThtAJwABBQAFAQCAAAQABQEEBWcA"
    "AgIDXwADAyVNBgEAAAdgCAEHByYHTllAEAAAAA8ADxEREREREREJCB0rNzUzNTM1IzUzFTMVIxUzFX19ffr6fX19fX19fX19fX19"
    "AAEAfQB9AfQCcQALAGVLsApQWEAkAAQCAQIEAYAAAQACAXAAAgIDXwADAyVNAAAABV8GAQUFJgVOG0AlAAQCAQIEAYAAAQACAQB+"
    "AAICA18AAwMlTQAAAAVfBgEFBSYFTllADgAAAAsACxERERERBwgbKzc1MzUjNSM1MxUzEX36fX36fX19fX19ff6JAAAEAH0AAAPo"
    "AnEAEQAVABkAHQBssQZkREBhCQEDBAYDVwAEAAIFBAJoCwEFEQwQCgQGAQUGZw0BAQAIAVcHAQAICABXBwEAAAhgEg4PAwgACFAa"
    "GhYWEhIAABodGh0cGxYZFhkYFxIVEhUUEwARABERERERERERERMHHiuxBgBEITUzNTM1ITUzFTcVMxUjFTMVJREzETM1MxUHNTMV"
    "AnF9ff6Jffp9fX38lX19ffp9fX19+n4BfX19ffoBd/6JfX36+voABQB9AAAD6AJxAAMABwARABUAGQBpsQZkREBeAgEADgEDBQAD"
    "ZwkHAgUQCg0DAQYFAWcLAQYABAgGBGgLAQYGCGARDA8DCAYIUBYWEhIICAQEAAAWGRYZGBcSFRIVFBMIEQgREA8ODQwLCgkEBwQH"
    "BgUAAwADERIHFyuxBgBENxEzETc1MxUTNSM1MxUzNTMRJTUzFQc1MxV9ffp9+vp9fX39j336ffoBd/6Jffr6/ol9+n19/on6fX36"
    "+voAAAMAfQAABGUCcQADABUAHwBzsQZkREBoAAUHAAVXAAcBCgdXBgEABBACAQgAAWcODAIIAAkDCAlnDQEDCwECCgMCaA4MAggI"
    "Cl8SDxEDCggKTxYWBAQAABYfFh8eHRwbGhkYFwQVBBUUExIREA8ODQwLCgkIBwYFAAMAAxETBxcrsQYARAE1MxUBNSM1MzUjNSM1"
    "MxUzFTMVIxUhNSM1MxUzNTMRAnF9/on6+n19+n19fQH0+n19fQF3+vr+iX19fX19fX19+n36fX3+iQAAAQB9AAAA+gB9AAMAGUAW"
    "AAAAAV8CAQEBFgFOAAAAAwADEQMHFyszNTMVfX19fQAAAgB9/4MBdwB9AAMABwApQCYAAgUBAwIDYwAAAAFfBAEBARYBTgQEAAAE"
    "BwQHBgUAAwADEQYHFyszNTMVBzUzFfp9+n19fX19fQACAH0AfQD6AfQAAwAHAC9ALAAABAEBAgABZwACAwMCVwACAgNfBQEDAgNP"
    "BAQAAAQHBAcGBQADAAMRBgcXKxM1MxUHNTMVfX19fQF3fX36fX0AAwB9AAABdwH0AAMABwALADtAOAAABgEBAgABZwACBwEDBAID"
    "ZwAEBAVfCAEFBRYFTggIBAQAAAgLCAsKCQQHBAcGBQADAAMRCQcXKxM1MxUHNTMVBzUzFfp9fX36fQF3fX36fX19fX0AAAMAfQAA"
    "Au4AfQADAAcACwAvQCwEAgIAAAFfCAUHAwYFAQEWAU4ICAQEAAAICwgLCgkEBwQHBgUAAwADEQkHFyshNTMVITUzFTM1MxUCcX39"
    "j319fX19fX19fQAAAgB9AAAA+gJxAAMABwAsQCkEAQEBAF8AAAAVTQACAgNfBQEDAxYDTgQEAAAEBwQHBgUAAwADEQYHFys3ETMR"
    "BzUzFX19fX36AXf+ifp9fQACAH0AAAD6AnEAAwAHACxAKQQBAQEAXwAAABVNAAICA18FAQMDFgNOBAQAAAQHBAcGBQADAAMRBgcX"
    "KxM1MxUDETMRfX19fQH0fX3+DAF3/okAAwB9AAACcQJxAAcACwAPAEdARAACCAEDBAIDZwAECQEFBgQFZwAAAAFfAAEBFU0ABgYH"
    "XwoBBwcWB04MDAgIAAAMDwwPDg0ICwgLCgkABwAHERERCwcZKwE1ITUhFTMVBTUzFQc1MxUB9P6JAXd9/on6+n0Bd319fX19fX36"
    "fX0AAAMAfQAAAnECcQADAAcADwBJQEYAAgkBAwUCA2cABQAEBgUEZwgBAQEAXwAAABVNAAYGB18KAQcHFgdOCAgEBAAACA8IDw4N"
    "DAsKCQQHBAcGBQADAAMRCwcXKwE1MxUHNTMVBzUjNTMVIRUBd336+vp9fQF3AfR9ffp9ffp9fX19AAABAH0A+gD6AXcAAwAeQBsA"
    "AAEBAFcAAAABXwIBAQABTwAAAAMAAxEDBxcrNzUzFX19+n19AAABAH0A+gD6AXcAAwAeQBsAAAEBAFcAAAABXwIBAQABTwAAAAMA"
    "AxEDBxcrNzUzFX19+n19AAACAH0AAALuAnEAFwAbAExASQwBBA8NAgMFBANnBwEFCgEAAgUAZwgBAgkBAQsCAWcABgYVTQ4BCwsW"
    "C04YGAAAGBsYGxoZABcAFxYVFBMREREREREREREQBx8rITUjFSM1MzUjNTMVMzUzFTMVMxUjNSMVEzUzFQF3fX19fX19fX19fX19"
    "ffp9fX19ffr6fX19+gF3fX0AAAIAfQAAAu4CcQAbAB8AR0BEBwUCAw8IAgIBAwJnDgkCAQwKAgALAQBnBgEEBBVNEA0CCwsWC04A"
    "AB8eHRwAGwAbGhkYFxYVFBMRERERERERERERBx8rMzUjNTM1IzUzNTMVMzUzFTMVIxUzFSMVIzUjFTUzNSP6fX19fX19fX19fX19"
    "fX19fX19fX19fX19fX19fX36fQAAAwB9AAAB9AJxAAMABwALAD1AOgACBwEDBAIDZwYBAQEAXwAAABVNAAQEBV8IAQUFFgVOCAgE"
    "BAAACAsICwoJBAcEBwYFAAMAAxEJBxcrATUzFQc1MxUHNTMVAXd9+n36fQF3+vp9fX36+voAAQB9AAAB9AJxAAsAL0AsAAMAAAQD"
    "AGcAAQECXwACAhVNAAQEBV8GAQUFFgVOAAAACwALEREREREHBxsrITUjNSM1MxUzFTMVAXd9fX19ffp9+vp9+gABAH0A+gH0AXcA"
    "AwAeQBsAAAEBAFcAAAABXwIBAQABTwAAAAMAAxEDBxcrNzUhFX0Bd/p9ff///0UA+gC8AXcABwCZ/sgAAAABAH0A+gJxAXcAAwAe"
    "QBsAAAEBAFcAAAABXwIBAQABTwAAAAMAAxEDBxcrNzUhFX0B9Pp9fQABAH0A+gNrAXcAAwAeQBsAAAEBAFcAAAABXwIBAQABTwAA"
    "AAMAAxEDBxcrNzUhFX0C7vp9fQABAH3/gwJxAAAAAwAmsQZkREAbAAABAQBXAAAAAV8CAQEAAU8AAAADAAMRAwcXK7EGAEQXNSEV"
    "fQH0fX19AAIAfQAAAXcCcQADAAsAOEA1AAMAAgQDAmcGAQEBAF8AAAAVTQAEBAVfBwEFBRYFTgQEAAAECwQLCgkIBwYFAAMAAxEI"
    "BxcrEzUzFQM1IxEzETMV+n19fX19AfR9ff4MfQF3/ol9AAIAfQAAAXcCcQAHAAsANkAzAAIGAQMEAgNnAAAAAV8AAQEVTQAEBAVf"
    "BwEFBRYFTggIAAAICwgLCgkABwAHERERCAcZKzcRIzUzFTMRBzUzFfp9fX36fX0Bd319/ol9fX0AAQB9AAAB9AJxAA8AeEuwClBY"
    "QCwABAMBAwRyAAUABgYFcgABAAAFAQBnAAMDAl8AAgIVTQAGBgdgCAEHBxYHThtALgAEAwEDBAGAAAUABgAFBoAAAQAABQEAZwAD"
    "AwJfAAICFU0ABgYHYAgBBwcWB05ZQBAAAAAPAA8RERERERERCQcdKzM1IzUzNTMVIxUjFTMVMxX6fX36fX19ffp9+n19fX19AAEA"
    "fQAAAfQCcQAPAHhLsApQWEAsAAIDBQMCcgABBgAAAXIABQAGAQUGZwADAwRfAAQEFU0AAAAHYAgBBwcWB04bQC4AAgMFAwIFgAAB"
    "BgAGAQCAAAUABgEFBmcAAwMEXwAEBBVNAAAAB2AIAQcHFgdOWUAQAAAADwAPEREREREREQkHHSszNTM1MzUjNSM1MxUzFSMVfX19"
    "fX36fX19fX19ffp9+gABAH0AAAF3AnEABwAlQCIAAQEAXwAAABVNAAICA18EAQMDFgNOAAAABwAHERERBQcZKzMRMxUjETMVffp9"
    "fQJxff6JfQAAAQB9AAABdwJxAAcAJUAiAAEBAl8AAgIVTQAAAANfBAEDAxYDTgAAAAcABxEREQUHGSszNTMRIzUzEX19ffp9AXd9"
    "/Y8AAAIAff+DAXcAfQADAAcAKUAmAAIFAQMCA2MAAAABXwQBAQEWAU4EBAAABAcEBwYFAAMAAxEGBxcrMzUzFQc1MxX6ffp9fX19"
    "fX0AAgB9AAAB9AD6AAMABwAkQCECAQAAAV8FAwQDAQEWAU4EBAAABAcEBwYFAAMAAxEGBxcrITUzFSE1MxUBd33+iX36+vr6AAIA"
    "fQF3AfQCcQADAAcAJEAhBQMEAwEBAF8CAQAAFQFOBAQAAAQHBAcGBQADAAMRBgcXKwE1MxUhNTMVAXd9/ol9AXf6+vr6AAIAfQF3"
    "AfQCcQADAAcAJEAhBQMEAwEBAF8CAQAAFQFOBAQAAAQHBAcGBQADAAMRBgcXKwE1MxUhNTMVAXd9/ol9AXf6+vr6AAEAfQF3APoC"
    "cQADABlAFgIBAQEAXwAAABUBTgAAAAMAAxEDBxcrEzUzFX19AXf6+gAAAQB9AXcA+gJxAAMAGUAWAgEBAQBfAAAAFQFOAAAAAwAD"
    "EQMHFysTNTMVfX0Bd/r6AAACAH0AfQLuAfQACwAXAEpARwgBAgkBAwECA2cHAQEGAQAEAQBnCgEEBQUEVwoBBAQFXw0LDAMFBAVP"
    "DAwAAAwXDBcWFRQTEhEQDw4NAAsACxERERERDgcbKyU1IzUzNTMVIxUzFSE1IzUzNTMVIxUzFQJxfX19fX3+DH19fX19fX19fX19"
    "fX19fX19fQAAAgB9AHwC7gHzAAsAFwBKQEcIAQIHAQEDAgFnCQEDCgEEAAMEZwYBAAUFAFcGAQAABV8NCwwDBQAFTwwMAAAMFwwX"
    "FhUUExIREA8ODQALAAsREREREQ4HGyslNTM1IzUzFTMVIxUhNTM1IzUzFTMVIxUB9H19fX19/gx9fX19fXx9fX19fX19fX19fX0A"
    "AAEAfQB9AXcB9AALADJALwACAAMBAgNnAAEAAAQBAGcABAUFBFcABAQFXwYBBQQFTwAAAAsACxERERERBwcbKzc1IzUzNTMVIxUz"
    "Ffp9fX19fX19fX19fX0AAAEAfQB9AXcB9AALADJALwACAAEDAgFnAAMABAADBGcAAAUFAFcAAAAFXwYBBQAFTwAAAAsACxERERER"
    "BwcbKzc1MzUjNTMVMxUjFX19fX19fX19fX19fX0AAAIAfQF3AfQCcQADAAcAJEAhBQMEAwEBAF8CAQAAFQFOBAQAAAQHBAcGBQAD"
    "AAMRBgcXKwE1MxUhNTMVAXd9/ol9AXf6+vr6AAEAfQF3APoCcQADABlAFgIBAQEAXwAAABUBTgAAAAMAAxEDBxcrEzUzFX19AXf6"
    "+gAAAwB9AAAC7gJxAAkAEQAVAE9ATAADCQIDVwAJCwEEBQkEZwYBAgAFBwIFZwoBAAABXwABARVNAAcHCF8MAQgIFghOCgoAABUU"
    "ExIKEQoREA8ODQwLAAkACRERERENBxorJTUjNSEVMxUjFQU1IxEzESEVAzM1IwF3fQF3fX3+iX19AXd9eHj6+n19fX36fQF3/ol9"
    "AXd9AAABAH3/gwJxAu4AGwCeS7AKUFhAPAAGBQUGcA4BDQAADXEABAADCQQDZwAJAAoCCQpnAAIAAQsCAWcACAgFXwcBBQUVTQAL"
    "CwBfDAEAABYAThtAOgAGBQaFDgENAA2GAAQAAwkEA2cACQAKAgkKZwACAAELAgFnAAgIBV8HAQUFFU0ACwsAXwwBAAAWAE5ZQBoA"
    "AAAbABsaGRgXFhUUExEREREREREREQ8HHysFNSM1IzUzNSM1MzUzNTMVMxUhFTMVIxUhFSMVAXd9fX19fX19ff6J+voBd319fX19"
    "fX19fX19fX19fX0AAwB9AAAC7gJxAA0AEQAVAD9APAACAAEHAgFnCQEHBQEABAcAZwoBCAgDXwADAxVNCwYCBAQWBE4AABUUExIR"
    "EA8OAA0ADREREREREQwHHCshNSM1IzUzNSERIzUjFQMzNSMXMzUjAXd9fX0B9H199Xh49X19+n19ff2P+voBd319fQAAAgB9/4MC"
    "cQLuABsAHwBgQF0ABgAHBQYHZwAIAA8DCA9nCQEDCgECDgMCZwAOAAELDgFnAAAQAQ0ADWMABAQFXwAFBRVNAAsLDF8ADAwWDE4A"
    "AB8eHRwAGwAbGhkYFxYVFBMRERERERERERERBx8rFzUzNSM1IzUzNSM1MzUzFSMVMxUzFSMVMxUjFQMzNSP6+vp9fX19+vr6fX19"
    "ffXw8H19fX19fX19fX19fX19fQF3fQAAAwB9/4MD6ALuAAsADwAXAF2xBmREQFIAAgAHAQIHZwAIAAkKCAlnAAoNAQsACgtnAwEB"
    "BAEABgEAZwAGBQUGVwAGBgVfDAEFBgVPEBAAABAXEBcWFRQTEhEPDg0MAAsACxERERERDgcbK7EGAEQXNSMRMzUhFTMRIxUlIREh"
    "ExEhFSMVMxX6fX0CcX19/ZQCZ/2ZeAF3+vp9fQJxfX39j319AnH+DAF3fX19AAMAff+DA+gC7gALAA8AFwBdsQZkREBSAAIABwEC"
    "B2cACQAKCAkKZwAIDQELAAgLZwMBAQQBAAYBAGcABgUFBlcABgYFXwwBBQYFTxAQAAAQFxAXFhUUExIRDw4NDAALAAsREREREQ4H"
    "GyuxBgBEFzUjETM1IRUzESMVJSERIRM1MzUzFSMV+n19AnF9ff2UAmf9mXh9+vp9fQJxfX39j319AnH+DPp9ffoAAQB9AXcEZQLu"
    "ABUAN0A0AwEBCQcFAwACAQBnAAIEBAJXAAICBF8LCggGBAQCBE8AAAAVABUUExEREREREREREQwGHysTNSM1IRUzNTMRIzUjFSM1"
    "IxUjNSMV+n0CcX36fX19fX19AXf6fX19/on6+vr6+voAAAIAfQD6AfQCcQALAA8AQLEGZERANQACAAcGAgdnAAYABQZXAwEBBAEA"
    "BQEAZwAGBgVfCAEFBgVPAAAPDg0MAAsACxERERERCQcbK7EGAEQ3NSM1MzUzFTMVIxUnMzUj+n19fX19eHNz+n19fX19fYJzAAEA"
    "ff+DAPoC7gADAB5AGwAAAQEAVwAAAAFfAgEBAAFPAAAAAwADEQMHFysXETMRfX19A2v8lQAAAgB9AAAA+gJxAAMABwAsQCkEAQEB"
    "AF8AAAAVTQACAgNfBQEDAxYDTgQEAAAEBwQHBgUAAwADEQYHFysTNTMVAzUzFX19fX0Bd/r6/on6+gABAH0AAAH0AnEADwAxQC4D"
    "AQEABAUBBGgABQYBAAcFAGcAAgIVTQgBBwcWB04AAAAPAA8RERERERERCQcdKzM1IxEzNTMVMxUjFTMVIxX6fX19ffr6fX0Bd319"
    "fX19fQADAH0AAALuAnEAAwAbAB8Ak0uwLlBYQCgHAQUMAQoCBQpnDwMCAAABXwYEAgEBFU0OCAICAglfEA0LAwkJFglOG0A/BwEF"
    "DAEKAgUKZwMBAAABXwYEAgEBFU0ADw8BXwYEAgEBFU0ADg4JXxANCwMJCRZNCAECAglfEA0LAwkJFglOWUAeBAQfHh0cBBsEGxoZ"
    "GBcWFRQTEREREREREhEQEQcfKwEjNTMBNTMRIzUzFTM1MxUzETMVIzUjFSM1IxU3MxEjAu59ff2PfX19fX19fX19fX19fX0B9H39"
    "j30Bd319fX3+iX19fX19ggFtAAACAH3/gwJxAu4ABwAbAKpLsApQWEA9AAEAAAFwDwENBAQNcQAIAAcJCAdnAAkABgoJBmcACgAL"
    "BQoLZw4BAwMAXwIBAAAVTQAFBQRfDAEEBBYEThtAOwABAAGFDwENBA2GAAgABwkIB2cACQAGCgkGZwAKAAsFCgtnDgEDAwBfAgEA"
    "ABVNAAUFBF8MAQQEFgROWUAkCAgAAAgbCBsaGRgXFhUUExIREA8ODQwLCgkABwAHEREREAcZKxM1MzUzFTMVATUjNSE1IzUjNTMV"
    "MxUzFSMVIxX6fX19/ol9AXf6fX36fX19AfR9fX19/Y99fX19fX19fX19AAMAfQAAAu4CcQAHABcAGwBfQFwHAQIOAQMGAgNnCAEG"
    "CQEFDAYFZwAMEA0CBAoMBGcAAAABXwABARVNAAoKC18PAQsLFgtOGBgICAAAGBsYGxoZCBcIFxYVFBMSERAPDg0MCwoJAAcABxER"
    "EREHGSsBNSM1MxUzFQE1IzUjNTM1MxUzFSMVMxU1NTMVAnH6+n3+iX19fX19ffp9AXd9fX19/ol9fX19fX19fX19fQAAAQB9AAAC"
    "cQJxAA8AM0AwAAIFAQEAAgFnAAQEA18AAwMVTQYBAAAHXwgBBwcWB04AAAAPAA8RERERERERCQcdKzM1MzUjNTM1MxUjFSMVMxV9"
    "fX36+n19+n19ffp9+n19AAACAH0AAALuAnEAAwAXAD9APAAHCAEEAwcEZwkBAwoBAgsDAmcFAQAAAV8GAQEBFU0MAQsLFgtOBAQE"
    "FwQXFhUUExERERERERIREA0HHysBIzUzATUjNTM1IzUjNTMVIRUjFTMVIxUC7n19/ol9fX19fQF3fX19AfR9/Y99fX19fX19fX19"
    "AAEAfQAAAu4CcQALACdAJAMBAQQBAAUBAGcAAgIVTQYBBQUWBU4AAAALAAsREREREQcHGyshNSM1MzUzFTMVIxUBd/r6ffr6+n36"
    "+n36AAIAfQB9AfQB9AADABMAO0A4BAEBAwEABQEAZwAFAAgCBQhnBgECBwcCVwYBAgIHXwoJAgcCB08EBAQTBBMRERERERESERAL"
    "Bx8rASM1MwE1MzUjNTMVMxUzFSM1IxUB9H19/ol9fX19fX19AXd9/ol9fX19fX19fQAAAwB9AAAB9AJxAAMABwALAD1AOgACBwED"
    "BAIDZwYBAQEAXwAAABVNAAQEBV8IAQUFFgVOCAgEBAAACAsICwoJBAcEBwYFAAMAAxEJBxcrEzUzFQc1IRUHNTMV+n36AXf6fQH0"
    "fX36fX36fX0AAgB9AH0B9AH0AAMABwAvQCwAAAQBAQIAAWcAAgMDAlcAAgIDXwUBAwIDTwQEAAAEBwQHBgUAAwADEQYHFysTNSEV"
    "BTUhFX0Bd/6JAXcBd319+n19AAADAH0AAAH0AnEACwAPABMAUUBOAAMAAAQDAGcABAoBBQYEBWcABgsBBwgGB2cAAQECXwACAhVN"
    "AAgICV8MAQkJFglOEBAMDAAAEBMQExIRDA8MDw4NAAsACxERERERDQcbKyU1IzUjNTMVMxUzFQc1MxUHNTMVAXd9fX19ffp9+n36"
    "fX19fX19fX19fX19AAADAH0AAAH0AnEAAwAHABMAVUBSAAILAQMGAgNnAAYABQcGBWcABwAECAcEZwoBAQEAXwAAABVNAAgICV8M"
    "AQkJFglOCAgEBAAACBMIExIREA8ODQwLCgkEBwQHBgUAAwADEQ0HFysBNTMVBzUzFRE1IzUjNTMVMxUzFQF3ffp9fX19fX0B9H19"
    "fX19/ol9fX19fX0AAQB9/4MC7gJxAA8ALEApBAECBQEBAAIBZwYBAAgBBwAHZAADAxUDTgAAAA8ADxEREREREREJBx0rFzUzNSM1"
    "MzUzFTMVIxUzFX36+vp9+vr6fX36ffr6ffp9AAACAH0BdwJxAnEAAwAPADmxBmREQC4DAQEGAQACAQBnBAECBQUCVwQBAgIFXwgH"
    "AgUCBU8EBAQPBA8REREREhEQCQcdK7EGAEQBIzUzBTUzNTMVMxUjNSMVAnF9ff4MfX19fX0B9H36fX19fX19AAEAfQB9AnEBdwAF"
    "AEZLsApQWEAXAwECAAACcQABAAABVwABAQBfAAABAE8bQBYDAQIAAoYAAQAAAVcAAQEAXwAAAQBPWUALAAAABQAFEREEBxgrJTUh"
    "NSEVAfT+iQH0fX19+gABAH0B9AH0Au4ACwA1sQZkREAqAAEABAABBGcCAQADAwBXAgEAAANfBgUCAwADTwAAAAsACxERERERBwcb"
    "K7EGAEQTNTM1MxUzFSM1IxV9fX19fX0B9H19fX19fQAAAgB9/4MCcQJxAAcACwA6QDcHAQUFAF8EAQAAFU0AAQECXwACAhZNBgED"
    "AwBfBAEAABUDTggIAAAICwgLCgkABwAHERERCAcZKxcRMxEzFSMVNxEzEX19+vr6fX0C7v4MfX36AfT+DAADAH0AAALuAnEACwAP"
    "ABMARUBCAAMAAAQDAGcLBwIBAQJfBgECAhVNCAEEBAVfDAkKAwUFFgVOEBAMDAAAEBMQExIRDA8MDw4NAAsACxERERERDQcbKyE1"
    "IzUjNTMVMxUzFQM1MxUBNTMVAfR9+vp9+vp9/ol9+n36+n36AXf6+v6J+voAAAIAfQNrAfQD6AADAAcAMrEGZERAJwIBAAEBAFcC"
    "AQAAAV8FAwQDAQABTwQEAAAEBwQHBgUAAwADEQYHFyuxBgBEATUzFSE1MxUBd33+iX0Da319fX0AAQB9Au4BdwPoAAcAMLEGZERA"
    "JQABAAACAQBnAAIDAwJXAAICA18EAQMCA08AAAAHAAcREREFBxkrsQYARBM1IzUzFTMV+n19fQLufX19fQABAH0C7gF3A+gABwAw"
    "sQZkREAlAAEAAgABAmcAAAMDAFcAAAADXwQBAwADTwAAAAcABxEREQUHGSuxBgBEEzUzNTMVIxV9fX19Au59fX19AAEAfQLuAfQD"
    "6AALADWxBmREQCoAAQAEAAEEZwIBAAMDAFcCAQAAA18GBQIDAANPAAAACwALEREREREHBxsrsQYARBM1MzUzFTMVIzUjFX19fX19"
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
    "AAEAfQAAAnECcQAHACFAHgIBAAABXwABARVNBAEDAxYDTgAAAAcABxEREQUHGSszESM1IRUjEfp9AfR9AfR9ff4MAAEAfQAAAu4C"
    "cQALAClAJgQBAAABXwMBAQEVTQACAgVfBgEFBRYFTgAAAAsACxERERERBwcbKzM1IxEzETMRMxEjFfp9+n36fX0B9P4MAfT+DH0A"
    "//8AfQAAAu4D6AImAC8AAAAGAM59AAACAH0AAALuA+gACwAXAExASQABAAQAAQRnAgEADAUCAwcAA2cKAQYGB18JAQcHFU0ACAgL"
    "Xw0BCwsWC04MDAAADBcMFxYVFBMSERAPDg0ACwALEREREREOBxsrEzUzNSEVMxUjNSMVAzUjETMRMxEzESMVfX0Bd336fX19+n36"
    "fQLufX19fX19/RJ9AfT+DAH0/gx9AP//AH0AAALuA2sCJgAvAAABBgDMQoMACbEBArj/g7A1KwD//wB9AAAC7gPoAiYALwAAAAYA"
    "zX0AAAEAfQAAA2sCcQAPADRAMQYBAAMHAwAHgAUBAQECXwQBAgIVTQADAwdfCAEHBxYHTgAAAA8ADxEREREREREJBx0rITUjNSM1"
    "MxUzNTMVIxUjFQF3fX36+vp9fX36+vr6+vp9AAABAH0AAANrAnEACwApQCYEAQAAAV8DAQEBFU0AAgIFXwYBBQUWBU4AAAALAAsR"
    "EREREQcHGyszNSMRMxUzNTMRIxX6ffr6+n19AfR9ff4MfQAAAQB9AAADawJxABsAjEuwClBYQDEIAQIFAQMCcgkBAQwAAXAABQAM"
    "AAUMZwcBAwMEXwYBBAQVTQoBAAALYA4NAgsLFgtOG0AzCAECBQEFAgGACQEBDAUBDH4ABQAMAAUMZwcBAwMEXwYBBAQVTQoBAAAL"
    "YA4NAgsLFgtOWUAaAAAAGwAbGhkYFxYVFBMREREREREREREPBx8rMzUzNTM1IzUjNTMVMzUzFSMVIxUzFTMVIzUjFX19fX19+vr6"
    "fX19ffr6fX19fX19fX19fX19fX0AAQB9AAADawJxAA8AX0uwClBYQCAGAQADBwEAcgUBAQECXwQBAgIVTQADAwdfCAEHBxYHThtA"
    "IQYBAAMHAwAHgAUBAQECXwQBAgIVTQADAwdfCAEHBxYHTllAEAAAAA8ADxEREREREREJBx0rIREjNSM1MxUzNTMVIxUjEQF3fX36"
    "+vp9fQF3fX19fX19/on//wB9AAADawPoAiYANwAAAAcAzgD6AAAAAQB9AAACcQJxAA8AgkuwClBYQDEABAIBAgRyAAEFAgEFfgAF"
    "AAIFAH4AAAYGAHAAAgIDXwADAxVNAAYGB2AIAQcHFgdOG0AzAAQCAQIEAYAAAQUCAQV+AAUAAgUAfgAABgIABn4AAgIDXwADAxVN"
    "AAYGB2AIAQcHFgdOWUAQAAAADwAPEREREREREQkHHSszNTM1MzUjNSEVIxUjFTMVfX19+gH0fX36+n19ffp9fX0AAAIAfQAAAu4C"
    "cQALAA8ANUAyAAYABAMGBGcABwcBXwABARVNAgEAAANfCAUCAwMWA04AAA8ODQwACwALEREREREJBxsrMxEzNSEVMxEjNSMVETM1"
    "I319AXd9+n19fQH0fX3+DPr6AXd4AP//AH0AAALuA+gCJgA6AAAABgDOfQAAAwB9AAAC7gPoAAsAFwAbAFhAVQABAAQAAQRnAgEA"
    "DgUCAwcAA2cADAAKCQwKZwANDQdfAAcHFU0IAQYGCV8PCwIJCRYJTgwMAAAbGhkYDBcMFxYVFBMSERAPDg0ACwALEREREREQBxsr"
    "EzUzNSEVMxUjNSMVAxEzNSEVMxEjNSMVETM1I319AXd9+n36fQF3ffp9fX0C7n19fX19ff0SAfR9ff4M+voBd3gA//8AfQAAAu4D"
    "awImADoAAAEGAMxCgwAJsQICuP+DsDUrAP//AH0AAALuA+gCJgA6AAAABgDNfQAAAwB9AAAC7gPoAAsAFwAbAFhAVQMBAQQBAAIB"
    "AGcAAg4BBQcCBWcADAAKCQwKZwANDQdfAAcHFU0IAQYGCV8PCwIJCRYJTgwMAAAbGhkYDBcMFxYVFBMSERAPDg0ACwALEREREREQ"
    "BxsrEzUjNTMVMzUzFSMVAREzNSEVMxEjNSMVETM1I/p9+n36ff4MfQF3ffp9fX0C7n19fX19ff0SAfR9ff4M+voBd3gA//8AfQAA"
    "Au4D6AImADoAAAAGANAAAAABAH0AAANrAnEADQA4QDUAAAIDAgADgAADAAQFAwRnAAICAV8AAQEVTQAFBQZgBwEGBhYGTgAAAA0A"
    "DREREREREQgHHCszETM1IRUjFTMVIxUzFX19AnH6+vr6AfR9fX19fX0AAwB9AAAC7gJxAAcACwAPAD1AOgAEAAcCBAdnAAEAAgYB"
    "AmcABQUAXwAAABVNAAYGA18IAQMDFgNOAAAPDg0MCwoJCAAHAAcREREJBxkrMxEhFTMRIxUDMzUjETM1I30B9H19+nh4eHgCcX3+"
    "iX0Bd33+iX0AAAEAfQAAAu4CcQATAD1AOgMBAQAEBwEEZwAHCAEABgcAZwAFBQJfAAICFU0ABgYJXwoBCQkWCU4AAAATABMRERER"
    "ERERERELBx8rMzUjETM1IRUzFSM1IxEzNTMVIxX6fX0Bd336fX36fX0Bd319fX3+iX19ff//AH3/BgLuAnECJgBDAAAABgDSAAAA"
    "AgB9AAAC7gJxAAcACwAxQC4AAQACBAECZwAFBQBfAAAAFU0ABAQDXwYBAwMWA04AAAsKCQgABwAHERERBwcZKzMRIRUzESMVJzMR"
    "I30B9H19+nh4AnF9/ol9fQF3AAMAfQAAA2sCcQALAA8AEwBBQD4GAQEJAQAEAQBnAAMABAgDBGcABwcCXwACAhVNAAgIBV8KAQUF"
    "FgVOAAATEhEQDw4NDAALAAsREREREQsHGyszNSM1MzUhFTMRIxUDMzUjETM1I/p9fQH0fX36fX19ffp9+n3+iX0Bd33+iX0AAQB9"
    "AAACcQJxAAsAL0AsAAIAAwQCA2cAAQEAXwAAABVNAAQEBV8GAQUFFgVOAAAACwALEREREREHBxsrMxEhFSMVMxUjFTMVfQH0+vr6"
    "+gJxfX19fX0A//8AfQAAAnED6AImAEcAAAAGAM59AP//AH0AAAJxA+gCJgBHAAAABgDPAAAAAgB9AAACcQNrAAMADwBEQEEAAAgB"
    "AQIAAWcABAAFBgQFZwADAwJfAAICFU0ABgYHXwkBBwcWB04EBAAABA8EDw4NDAsKCQgHBgUAAwADEQoHFysTNSEVAREhFSMVMxUj"
    "FTMVfQH0/gwB9Pr6+voC7n19/RICcX19fX19//8AfQAAAnED6AImAEcAAAAGAM0AAAABAH0AAAJxAnEACQApQCYAAgADBAIDZwAB"
    "AQBfAAAAFU0FAQQEFgROAAAACQAJEREREQYHGiszESEVIxUzFSMVfQH0+vr6AnF9fX36AAABAH0AAALuAnEADwA4QDUAAQUAAVcA"
    "BQYBAAQFAGcAAwMCXwACAhVNAAQEB18IAQcHFgdOAAAADwAPEREREREREQkHHSszNSMRMzUhFSERMzUzFSMV+n19AfT+iX36fX0B"
    "d319/on6+n0AAQB9AAAC7gJxAAsAJ0AkAAEABAMBBGcCAQAAFU0GBQIDAxYDTgAAAAsACxERERERBwcbKzMRMxUzNTMRIzUjFX36"
    "ffr6fQJx+vr9j/r6AAABAH0AAAF3AnEAAwAZQBYAAAAVTQIBAQEWAU4AAAADAAMRAwcXKzMRMxF9+gJx/Y8AAAIAfQAAAfQD6AAH"
    "AAsANEAxAAEAAgABAmcAAAYBAwQAA2cABAQVTQcBBQUWBU4ICAAACAsICwoJAAcABxEREQgHGSsTNTM1MxUjFQMRMxF9ffp9+voC"
    "7n19fX39EgJx/Y8AAgAAAAAB9APoAAcACwBZS7AKUFhAHAABAAABcAIBAAYBAwQAA2gABAQVTQcBBQUWBU4bQBsAAQABhQIBAAYB"
    "AwQAA2gABAQVTQcBBQUWBU5ZQBQICAAACAsICwoJAAcABxEREQgHGSsRNTM1MxUzFQERMxF9+n3+ifoC7n19fX39EgJx/Y8AAAIA"
    "AAAAAfQDawADAAcAKkAnAAAEAQECAAFnAAICFU0FAQMDFgNOBAQAAAQHBAcGBQADAAMRBgcXKxE1IRUBETMRAfT+ifoC7n19/RIC"
    "cf2PAAACAAAAAAF3A+gABwALADRAMQABAAACAQBnAAIGAQMEAgNnAAQEFU0HAQUFFgVOCAgAAAgLCAsKCQAHAAcREREIBxkrEzUj"
    "NTMVMxUDETMRfX36ffr6Au59fX19/RICcf2PAAEAfQAAAu4CcQALAC5AKwABAAABVwQBAAADXwADAxVNAAICBV8GAQUFFgVOAAAA"
    "CwALEREREREHBxsrMzUjNTMVMxEzESMV+n36ffp9fX19AfT+DH0AAQB9AAAC7gJxABMAfEuwClBYQC0ABAEFAwRyAAUIBgVwAAEA"
    "CAYBCGcAAwMAXwIBAAAVTQAGBgdgCgkCBwcWB04bQC8ABAEFAQQFgAAFCAEFCH4AAQAIBgEIZwADAwBfAgEAABVNAAYGB2AKCQIH"
    "BxYHTllAEgAAABMAExEREREREREREQsHHyszETMVMzUzFSMVIxUzFTMVIzUjFX36ffp9fX19+n0CcX19fX19fX19fQAAAQB9AAAC"
    "cQJxAAUAH0AcAAAAFU0AAQECYAMBAgIWAk4AAAAFAAUREQQHGCszETMRMxV9+voCcf4MfQAAAQB9AAADawJxAAsAJ0AkAAEABAMB"
    "BGcCAQAAFU0GBQIDAxYDTgAAAAsACxERERERBwcbKzMRMxUzNTMRIzUjFX36+vr6+gJxfX39j/r6AAABAH0AAANrAnEADwAxQC4A"
    "AQAGBQEGZwACAAUEAgVnAwEAABVNCAcCBAQWBE4AAAAPAA8RERERERERCQcdKzMRMxUzFTM1MxEjNSM1IxV9+n19+vp9fQJxfX36"
    "/Y99ffoA//8AfQAAA2sD6AImAFgAAAAGANB9AAACAH0AAALuAnEACwAPADVAMgMBAQQBAAUBAGcABwcCXwACAhVNAAYGBV8IAQUF"
    "FgVOAAAPDg0MAAsACxERERERCQcbKzM1IxEzNSEVMxEjFSczESP6fX0Bd319+n19fQF3fX3+iX2CAW3//wB9AAAC7gPoAiYAWgAA"
    "AAYAzn0AAAMAfQAAAu4D6AALABcAGwBYQFUAAQAEAAEEZwIBAA4FAgMIAANnCQEHCgEGCwcGZwANDQhfAAgIFU0ADAwLXw8BCwsW"
    "C04MDAAAGxoZGAwXDBcWFRQTEhEQDw4NAAsACxEREREREAcbKxM1MzUhFTMVIzUjFQM1IxEzNSEVMxEjFSczESN9fQF3ffp9fX19"
    "AXd9ffp9fQLufX19fX19/RJ9AXd9ff6JfYIBbf//AH0AAALuA2sCJgBaAAABBgDMQoMACbECArj/g7A1KwD//wB9AAAC7gPoAiYA"
    "WgAAAAYAzX0AAAMAfQAAA2sCcQALAA8AEwBIQEUABgEJAQYJgAAJAAEJAH4DAQEEAQAIAQBnAAcHAl8AAgIVTQAICAVgCgEFBRYF"
    "TgAAExIREA8ODQwACwALERERERELBxsrMzUjETM1IRUzESMVATM1IxMzNSP6fX0B9H19/ol9fX19fX0Bd319/ol9AXd9/ol9//8A"
    "fQAAAu4D6AImAFoAAAAGANAAAAABAH0AAANrAnEADwA5QDYABAAFAAQFZwABAAAGAQBnAAMDAl8AAgIVTQAGBgdfCAEHBxYHTgAA"
    "AA8ADxEREREREREJBx0rMzUjETM1IRUjFTMVIxUzFfp9fQJx+vr6+n0Bd319fX19fQAAAgB9AAAC7gJxAAkADQA1QDIAAQACBQEC"
    "ZwAFAAMEBQNnAAYGAF8AAAAVTQcBBAQWBE4AAA0MCwoACQAJEREREQgHGiszESEVMxUjFSMVETM1I30B9H19+nh4AnF9fX36AXd9"
    "AAEAfQAAAnECcQALAC1AKgACAAMEAgNnAAEABAUBBGcAAAAVTQYBBQUWBU4AAAALAAsREREREQcHGyszETMVMxUzFSMVIxV9+n19"
    "fX0CcX19fX19AAIAff+DAu4CcQAPABMAPkA7BAECBQEBCAIBZwAGCgEHBgdjAAkJA18AAwMVTQAICABfAAAAFgBOAAATEhEQAA8A"
    "DxERERERERELBx0rBTUjNSMRMzUhFTMRIxUzFSUzESMB9Pp9fQF3fX19/ol9fX19fQF3fX3+iX19+gF3AAIAfQAAAu4CcQANABEA"
    "PUA6AAEAAgcBAmcABwAFAwcFZwAICABfAAAAFU0AAwMEXwkGAgQEFgROAAAREA8OAA0ADREREREREQoHHCszESEVMxUjFTMVIzUj"
    "FREzNSN9AfR9fX36fX19AnF9ffp9fX0Bd30AAQB9AAAC7gJxABMAQ0BAAAMAAgYDAmcABgABBwYBZwAHAAgABwhnAAUFBF8ABAQV"
    "TQAAAAlfCgEJCRYJTgAAABMAExEREREREREREQsHHyszNSE1IzUjNTM1IRUhFTMVMxUjFX0Bd/p9fQH0/on6fX19fX19fX19fX19"
    "AAMAfQAAAu4CcQANABEAFQBJQEYCAQAAAwcAA2cABwAKBAcKZwAEAAUJBAVnAAgIAV8AAQEVTQAJCQZgCwEGBhYGTgAAFRQTEhEQ"
    "Dw4ADQANERERERERDAccKzMRMzUhFTMVIxUzFSMVAzM1IxEzNSN9fQF3fX19ffp9fX19AfR9fX19fX0Bd33+iX0AAQB9AAACcQJx"
    "AAcAIUAeAgEAAAFfAAEBFU0EAQMDFgNOAAAABwAHERERBQcZKzMRIzUhFSMR+n0B9H0B9H19/gwAAQB9AAAC7gJxAAsAKUAmBAEA"
    "AAFfAwEBARVNAAICBV8GAQUFFgVOAAAACwALEREREREHBxsrMzUjETMRMxEzESMV+n36ffp9fQH0/gwB9P4MfQD//wB9AAAC7gPo"
    "AiYAaQAAAAYAzn0AAAIAfQAAAu4D6AALABcATEBJAAEABAABBGcCAQAMBQIDBwADZwoBBgYHXwkBBwcVTQAICAtfDQELCxYLTgwM"
    "AAAMFwwXFhUUExIREA8ODQALAAsREREREQ4HGysTNTM1IRUzFSM1IxUDNSMRMxEzETMRIxV9fQF3ffp9fX36ffp9Au59fX19fX39"
    "En0B9P4MAfT+DH0A//8AfQAAAu4DawImAGkAAAEGAMxCgwAJsQECuP+DsDUrAP//AH0AAALuA+gCJgBpAAAABgDNfQAAAQB9AAAD"
    "awJxAA8ANEAxBgEAAwcDAAeABQEBAQJfBAECAhVNAAMDB18IAQcHFgdOAAAADwAPEREREREREQkHHSshNSM1IzUzFTM1MxUjFSMV"
    "AXd9ffr6+n19ffr6+vr6+n0AAAEAfQAAA2sCcQALAClAJgQBAAABXwMBAQEVTQACAgVfBgEFBRYFTgAAAAsACxERERERBwcbKzM1"
    "IxEzFTM1MxEjFfp9+vr6fX0B9H19/gx9AAABAH0AAANrAnEAGwCMS7AKUFhAMQgBAgUBAwJyCQEBDAABcAAFAAwABQxnBwEDAwRf"
    "BgEEBBVNCgEAAAtgDg0CCwsWC04bQDMIAQIFAQUCAYAJAQEMBQEMfgAFAAwABQxnBwEDAwRfBgEEBBVNCgEAAAtgDg0CCwsWC05Z"
    "QBoAAAAbABsaGRgXFhUUExEREREREREREQ8HHyszNTM1MzUjNSM1MxUzNTMVIxUjFTMVMxUjNSMVfX19fX36+vp9fX19+vp9fX19"
    "fX19fX19fX19fQABAH0AAANrAnEADwBfS7AKUFhAIAYBAAMHAQByBQEBAQJfBAECAhVNAAMDB18IAQcHFgdOG0AhBgEAAwcDAAeA"
    "BQEBAQJfBAECAhVNAAMDB18IAQcHFgdOWUAQAAAADwAPEREREREREQkHHSshESM1IzUzFTM1MxUjFSMRAXd9ffr6+n19AXd9fX19"
    "fX3+if//AH0AAANrA+gCJgBxAAAABwDOAPsAAAACAH0AAANrA2sAAwATAH9LsApQWEApCAECBQkDAnIAAAoBAQQAAWcHAQMDBF8G"
    "AQQEFU0ABQUJXwsBCQkWCU4bQCoIAQIFCQUCCYAAAAoBAQQAAWcHAQMDBF8GAQQEFU0ABQUJXwsBCQkWCU5ZQB4EBAAABBMEExIR"
    "EA8ODQwLCgkIBwYFAAMAAxEMBxcrEzUhFQERIzUjNTMVMzUzFSMVIxH6AfT+iX19+vr6fX0C7n19/RIBd319fX19ff6JAAABAH0A"
    "AAJxAnEADwCCS7AKUFhAMQAEAgECBHIAAQUCAQV+AAUAAgUAfgAABgYAcAACAgNfAAMDFU0ABgYHYAgBBwcWB04bQDMABAIBAgQB"
    "gAABBQIBBX4ABQACBQB+AAAGAgAGfgACAgNfAAMDFU0ABgYHYAgBBwcWB05ZQBAAAAAPAA8RERERERERCQcdKzM1MzUzNSM1IRUj"
    "FSMVMxV9fX36AfR9ffr6fX19+n19fQAAAQB9APoCcQJxAAkAJUAiAAAEAQBXAwEBBQEEAQRjAAICJQJOAAAACQAJEREREQYIGis3"
    "NSM1MzUzFTMV+n19+n36fX19ffoAAQB9APoCcQJxAAsAJ0AkBgEFAAWGAwEBBAEABQEAZwACAiUCTgAAAAsACxERERERBwgbKzc1"
    "IzUzNTMVMxUjFfp9ffp9ffp9fX19fX0AAgB9AAAC7gJxAAsADwA1QDIDAQEEAQAFAQBnAAcHAl8AAgIVTQAGBgVfCAEFBRYFTgAA"
    "Dw4NDAALAAsREREREQkHGyszNSMRMzUhFTMRIxUnMxEj+n19AXd9ffp9fX0Bd319/ol9ggFtAAEAfQAAAnECcQAJACdAJAABAQJf"
    "AAICFU0DAQAABF8FAQQEFgROAAAACQAJEREREQYHGiszNTMRIzUhETMVfX19AXd9fQF3ff4MfQABAH0AAALuAnEAEQB6S7AKUFhA"
    "LQAABgcHAHIABAAFAQQFZwABAAYAAQZnAAICA18AAwMVTQAHBwhgCQEICBYIThtALgAABgcGAAeAAAQABQEEBWcAAQAGAAEGZwAC"
    "AgNfAAMDFU0ABwcIYAkBCAgWCE5ZQBEAAAARABEREREREREREQoHHiszNTM1MzUhNSEVMxUjFSMVIRV9ffr+iQH0fX36AXf6fX19"
    "fX19fX0AAAEAfQAAAu4CcQATAENAQAAFAAYCBQZnAAIAAQcCAWcABwAIAAcIZwADAwRfAAQEFU0AAAAJXwoBCQkWCU4AAAATABMR"
    "ERERERERERELBx8rMzUhNSM1MzUhNSEVMxUjFTMVIxV9AXf6+v6JAfR9fX19fX19fX19fX19fQABAH0AAALuAnEACQAoQCUAAgAA"
    "AlcDAQAAAV8AAQEVTQUBBAQWBE4AAAAJAAkRERERBgcaKyE1IxEhFTMVIxUBd/oB9H19+gF3+n36AAABAH0AAALuAnEADwA5QDYA"
    "BAABBQQBZwAFAAYABQZnAAMDAl8AAgIVTQAAAAdfCAEHBxYHTgAAAA8ADxEREREREREJBx0rMzUhNSERIRUhFTMVMxUjFX0Bd/6J"
    "AnH+ifp9fX19AXd9fX19fQACAH0AAALuAnEADwATAERAQQABBAABVwAEAAkFBAlnAAUGAQAIBQBnAAMDAl8AAgIVTQAICAdfCgEH"
    "BxYHTgAAExIREAAPAA8RERERERERCwcdKzM1IxEzNSEVIxUzFTMVIxUnMzUj+n19AXf6+n19+n19fQF3fX19fX19fX0AAAEAfQAA"
    "Au4CcQANAHNLsApQWEAqAAQCAQIEcgABBQABcAAFAAIFAH4AAgIDXwADAxVNAAAABmAHAQYGFgZOG0AsAAQCAQIEAYAAAQUCAQV+"
    "AAUAAgUAfgACAgNfAAMDFU0AAAAGYAcBBgYWBk5ZQA8AAAANAA0REREREREIBxwrMzUzNTM1ITUhFSMVIxX6fX3+iQJxfX36fX19"
    "+n36AAADAH0AAALuAnEAEwAXABsAT0BMBQEDBgECAQMCZwAKAA0MCg1nBwEBCAEACQEAZwALCwRfAAQEFU0ADAwJXw4BCQkWCU4A"
    "ABsaGRgXFhUUABMAExEREREREREREQ8HHyszNSM1MzUjNTM1IRUzFSMVMxUjFQMzNSMRMzUj+n19fX0Bd319fX36fX19fX19fX19"
    "fX19fX0BfHP+k3MAAAIAfQAAAu4CcQAPABMAREBBAAIIAwJXAAgAAQYIAWcFAQMABgADBmcACQkEXwAEBBVNAAAAB18KAQcHFgdO"
    "AAATEhEQAA8ADxERERERERELBx0rMzUzNSM1IzUzNSEVMxEjFQMzNSP6+vp9fQF3fX36fX19fX19fX3+iX0Bd30AAQB9AH0B9AJx"
    "AAUAH0AcAAAAAV8AAQElTQMBAgImAk4AAAAFAAUREQQIGCs3ESM1IRH6fQF3fQF3ff4MAAABAH0AfQJxAnEADwBrS7AKUFhAJgAB"
    "BQAAAXIABAAFAQQFZwACAgNfAAMDJU0GAQAAB2AIAQcHJgdOG0AnAAEFAAUBAIAABAAFAQQFZwACAgNfAAMDJU0GAQAAB2AIAQcH"
    "JgdOWUAQAAAADwAPEREREREREQkIHSs3NTM1MzUjNSEVMxUjFTMVfX19+gF3fX19fX19fX19fX19AAABAH0AfQJxAnEACwBlS7AK"
    "UFhAJAAEAgECBAGAAAEAAgFwAAICA18AAwMlTQAAAAVfBgEFBSYFThtAJQAEAgECBAGAAAEAAgEAfgACAgNfAAMDJU0AAAAFXwYB"
    "BQUmBU5ZQA4AAAALAAsREREREQcIGys3NTM1IzUjNSEVMxF9+n19AXd9fX19fX19/okAAQB9AAAEZQJxABsAm7EGZERLsApQWEA1"
    "AAoABwcKcgMBAQQAAVcABAALAgQLaAUBAgwGAgAKAgBnCQEHCAgHVwkBBwcIYA4NAggHCFAbQDYACgAHAAoHgAMBAQQAAVcABAAL"
    "AgQLaAUBAgwGAgAKAgBnCQEHCAgHVwkBBwcIYA4NAggHCFBZQBoAAAAbABsaGRgXFhUUExEREREREREREQ8HHyuxBgBEMzUjETMV"
    "MzUzFTMVMxUjFTMVITUzNTM1IxUjFfp9+n36+n19ff4MfX36ffoBd/r6fX19fX19fX19+gAAAQB9AAAEZQJxABEAPLEGZERAMQMB"
    "AQcBAAYBAGcEAQIABgUCBmcEAQICBV8JCAIFAgVPAAAAEQAREREREREREREKBx4rsQYARDM1IxEzFTM1MxUhESM1IzUjFfp9+n36"
    "AXf6+n36AXf6+vr+iX19+gAAAQB9AAAE4gJxABcAm7EGZERLsApQWEA3AAIFBwMCcgADBQQDVwAFAggFVwAHCgAHVwYBBAAKAQQK"
    "ZwABCQEACAEAZwAFBQhfDAsCCAUITxtAOAACBQcFAgeAAAMFBANXAAUCCAVXAAcKAAdXBgEEAAoBBApnAAEJAQAIAQBnAAUFCF8M"
    "CwIIBQhPWUAWAAAAFwAXFhUUExEREREREREREQ0HHyuxBgBEITUjNTM1IzUjNSEVMzUzFSERIzUjNSMVAXf6+n19AXd9+gF3+vp9"
    "fX19fX19ffr+iX19+gAAAQB9AAABdwB9AAMAGUAWAAAAAV8CAQEBFgFOAAAAAwADEQMHFyszNTMVffp9fQAAAQB9/4MB9AB9AAcA"
    "IkAfAAAEAQMAA2MAAQECXwACAhYCTgAAAAcABxEREQUHGSsXNTM1MxUjFX19+n19fX19fQAAAgB9AH0BdwH0AAMABwAvQCwAAAQB"
    "AQIAAWcAAgMDAlcAAgIDXwUBAwIDTwQEAAAEBwQHBgUAAwADEQYHFysTNTMVBzUzFX36+voBd319+n19AAIAfQAAAfQB9AADAAsA"
    "NkAzAAAGAQEDAAFnAAMABAIDBGcAAgIFXwcBBQUWBU4EBAAABAsECwoJCAcGBQADAAMRCAcXKxM1MxUBNTM1MxUjFfr6/ol9+n0B"
    "d319/ol9fX19AAABAH0AAANrAH0AAwAZQBYAAAABXwIBAQEWAU4AAAADAAMRAwcXKzM1IRV9Au59fQACAH0AAAF3AnEAAwAHACxA"
    "KQQBAQEAXwAAABVNAAICA18FAQMDFgNOBAQAAAQHBAcGBQADAAMRBgcXKzcRMxEHNTMVffr6+voBd/6J+n19AAIAfQAAAXcCcQAD"
    "AAcALEApBAEBAQBfAAAAFU0AAgIDXwUBAwMWA04EBAAABAcEBwYFAAMAAxEGBxcrEzUzFQMRMxF9+vr6AfR9ff4MAXf+iQACAH0A"
    "AALuAnEACwAPAEBAPQADAAQAAwRnAAAIAQUGAAVnAAEBAl8AAgIVTQAGBgdfCQEHBxYHTgwMAAAMDwwPDg0ACwALEREREREKBxsr"
    "NzUzNSE1IRUzFSMVBTUzFfr6/okB9H19/on6+n19fX19ffp9fQAAAgB9AAAC7gJxAAMADwBEQEEABAAFAwQFZwADAAIGAwJnCAEB"
    "AQBfAAAAFU0ABgYHXwkBBwcWB04EBAAABA8EDw4NDAsKCQgHBgUAAwADEQoHFysBNTMVATUjNTM1IRUjFSEVAXf6/ol9fQF3+gF3"
    "AfR9ff4MfX19fX19AAEAfQD6AXcBdwADAB5AGwAAAQEAVwAAAAFfAgEBAAFPAAAAAwADEQMHFys3NTMVffr6fX0AAAEAfQD6AXcB"
    "dwADAB5AGwAAAQEAVwAAAAFfAgEBAAFPAAAAAwADEQMHFys3NTMVffr6fX0AAAEAfQAAA2sCcQATADVAMgUBAwYBAgEDAmcHAQEI"
    "AQAJAQBnAAQEFU0KAQkJFglOAAAAEwATERERERERERERCwcfKyE1IzUzNSM1MzUzFTMVIxUzFSMVAXf6fX36+vp9ffp9fX19fX19"
    "fX19AAEAfQAAA2sCcQATADVAMgUBAwYBAgEDAmcHAQEIAQAJAQBnAAQEFU0KAQkJFglOAAAAEwATERERERERERERCwcfKzM1Iycz"
    "NSM1MzUhFTMVIxUzFSMV+noDfX19AfR9fX19fX19fX19fX19fQAAAQB9AAACcQJxAAsAZEuwClBYQCMAAQMEAAFyAAQAAwRwAAMD"
    "Al8AAgIVTQAAAAVgBgEFBRYFThtAJQABAwQDAQSAAAQAAwQAfgADAwJfAAICFU0AAAAFYAYBBQUWBU5ZQA4AAAALAAsREREREQcH"
    "GyszNTM1MzUzFSMVIxV9fX36fX36ffr6ffoAAQB9AAACcQJxAAsAZEuwClBYQCMAAwEABANyAAAEAQBwAAEBAl8AAgIVTQAEBAVg"
    "BgEFBRYFThtAJQADAQABAwCAAAAEAQAEfgABAQJfAAICFU0ABAQFYAYBBQUWBU5ZQA4AAAALAAsREREREQcHGyshNSM1IzUzFTMV"
    "MxUBd319+n19+n36+n36AAABAH0A+gJxAXcAAwAeQBsAAAEBAFcAAAABXwIBAQABTwAAAAMAAxEDBxcrNzUhFX0B9Pp9ff///wYA"
    "+gD6AXcABwCZ/okAAAABAH0A+gLuAXcAAwAeQBsAAAEBAFcAAAABXwIBAQABTwAAAAMAAxEDBxcrNzUhFX0Ccfp9fQABAH0A+gPo"
    "AXcAAwAeQBsAAAEBAFcAAAABXwIBAQABTwAAAAMAAxEDBxcrNzUhFX0Da/p9fQABAH3/gwLuAAAAAwAmsQZkREAbAAABAQBXAAAA"
    "AV8CAQEAAU8AAAADAAMRAwcXK7EGAEQXNSEVfQJxfX19AAEAfQAAAfQCcQALAC9ALAABAAAEAQBnAAMDAl8AAgIVTQAEBAVfBgEF"
    "BRYFTgAAAAsACxERERERBwcbKzM1IxEzNTMVIxEzFfp9ffp9fX0Bd319/ol9AAABAH0AAAH0AnEACwAvQCwAAwAEAAMEZwABAQJf"
    "AAICFU0AAAAFXwYBBQUWBU4AAAALAAsREREREQcHGyszNTMRIzUzFTMRIxV9fX36fX19AXd9ff6JfQAAAQB9AAACcQJxAA8AeEuw"
    "ClBYQCwABAMBAwRyAAUABgYFcgABAAAFAQBnAAMDAl8AAgIVTQAGBgdgCAEHBxYHThtALgAEAwEDBAGAAAUABgAFBoAAAQAABQEA"
    "ZwADAwJfAAICFU0ABgYHYAgBBwcWB05ZQBAAAAAPAA8RERERERERCQcdKzM1IzUzNSEVIxUjFTMVMxX6fX0Bd319fX36ffp9fX19"
    "fQAAAQB9AAACcQJxAA8AeEuwClBYQCwAAgMFAwJyAAEGAAABcgAFAAYBBQZnAAMDBF8ABAQVTQAAAAdgCAEHBxYHThtALgACAwUD"
    "AgWAAAEGAAYBAIAABQAGAQUGZwADAwRfAAQEFU0AAAAHYAgBBwcWB05ZQBAAAAAPAA8RERERERERCQcdKzM1MzUzNSM1IzUhFTMV"
    "IxV9fX19fQF3fX19fX19ffp9+gAAAQB9AAAB9AJxAAcAJUAiAAEBAF8AAAAVTQACAgNfBAEDAxYDTgAAAAcABxEREQUHGSszESEV"
    "IxEzFX0Bd319AnF9/ol9AAEAfQAAAfQCcQAHACVAIgABAQJfAAICFU0AAAADXwQBAwMWA04AAAAHAAcREREFBxkrMzUzESM1IRF9"
    "fX0Bd30Bd339jwABAH3/gwH0AH0ABwAiQB8AAAQBAwADYwABAQJfAAICFgJOAAAABwAHERERBQcZKxc1MzUzFSMVfX36fX19fX19"
    "AAABAH0AAAJxAPoAAwAZQBYAAAABXwIBAQEWAU4AAAADAAMRAwcXKzM1IRV9AfT6+gABAH0BdwJxAnEAAwAZQBYCAQEBAF8AAAAV"
    "AU4AAAADAAMRAwcXKxM1IRV9AfQBd/r6AAEAfQF3AnECcQADABlAFgIBAQEAXwAAABUBTgAAAAMAAxEDBxcrEzUhFX0B9AF3+voA"
    "AQB9AXcBdwJxAAMAGUAWAgEBAQBfAAAAFQFOAAAAAwADEQMHFysTNTMVffoBd/r6AAABAH0BdwF3AnEAAwAZQBYCAQEBAF8AAAAV"
    "AU4AAAADAAMRAwcXKxM1MxV9+gF3+voAAAIAfQB9A2sB9AATABcAQUA+BAECCwEFAQIFZwMBAQgBAAYBAGcKAQYHBwZXCgEGBgdf"
    "DAkCBwYHTwAAFxYVFAATABMRERERERERERENBx8rNzUjNTM1MxUzNTMVIxUzFSM1IxUnMzUj+n19+n36fX36fX14eH19fX19fX19"
    "fX19fX0AAAIAfQB9A2sB9AATABcAQUA+BAECCwEBAwIBZwUBAwgBBgADBmcKAQAHBwBXCgEAAAdfDAkCBwAHTwAAFxYVFAATABMR"
    "ERERERERERENBx8rNzUzNSM1MxUzNTMVMxUjFSM1IxU3MzUjfX19+n36fX36fYJ4eH19fX19fX19fX19fX0AAAEAfQB9AfQB9AAL"
    "ADJALwACAAMBAgNnAAEAAAQBAGcABAUFBFcABAQFXwYBBQQFTwAAAAsACxERERERBwcbKzc1IzUzNTMVIxUzFfp9ffp9fX19fX19"
    "fX0AAAEAfQB9AfQB9AALADJALwACAAEDAgFnAAMABAADBGcAAAUFAFcAAAAFXwYBBQAFTwAAAAsACxERERERBwcbKzc1MzUjNTMV"
    "MxUjFX19ffp9fX19fX19fX0AAAEAfQF3AnECcQADABlAFgIBAQEAXwAAABUBTgAAAAMAAxEDBxcrEzUhFX0B9AF3+voAAQB9AXcB"
    "dwJxAAMAGUAWAgEBAQBfAAAAFQFOAAAAAwADEQMHFysTNTMVffoBd/r6AAABAH0AAANrAnEADwA4QDUABAUBBFcDAQEAAAYBAGcA"
    "BQUCXwACAhVNAAYGB18IAQcHFgdOAAAADwAPEREREREREQkHHSszNSMRMzUhFTMVIxUhFSEV+n19AfR9ff6JAXd9AXd9fX19fX0A"
    "AQB9/4MC7gLuABsAnkuwClBYQDwABgUFBnAOAQ0AAA1xAAQAAwkEA2cACQAKAgkKZwACAAELAgFnAAgIBV8HAQUFFU0ACwsAXwwB"
    "AAAWAE4bQDoABgUGhQ4BDQANhgAEAAMJBANnAAkACgIJCmcAAgABCwIBZwAICAVfBwEFBRVNAAsLAF8MAQAAFgBOWUAaAAAAGwAb"
    "GhkYFxYVFBMREREREREREREPBx8rBTUjNSM1MzUjNTM1MzUzFTMVIRUzFSMVIRUjFQF3fX19fX19+n3+ifr6AXd9fX19fX19fX19"
    "fX19fX19AAEAfQAAA2sCcQAJAClAJgACAAEAAgFnAAAAA18AAwMVTQUBBAQWBE4AAAAJAAkRERERBgcaKyE1IzUjNTM1IREBd319"
    "fQJx+n19ff2PAAIAff+DAu4C7gAbAB8AYEBdAAYABwUGB2cACAAPAwgPZwkBAwoBAg4DAmcADgABCw4BZwAAEAENAA1jAAQEBV8A"
    "BQUVTQALCwxfAAwMFgxOAAAfHh0cABsAGxoZGBcWFRQTEREREREREREREQcfKxc1MzUjNSM1MzUjNTM1IRUjFTMVMxUjFTMVIxUD"
    "MzUj+vr6fX19fQF3+vp9fX19+n19fX19fX19fX19fX19fX19AXd9AAQAff+DBGUC7gALAA8AEwAXAFixBmREQE0AAgAHAQIHZwAG"
    "AAkIBglnAAgACwAIC2cDAQEEAQAKAQBnAAoFBQpXAAoKBV8MAQUKBU8AABcWFRQTEhEQDw4NDAALAAsREREREQ0HGyuxBgBEFzUj"
    "ETM1IRUzESMVASE1IRMzNSMDITUh+n19Au59ff2PAfT+DPr6+voB9P4MfX0CcX19/Y99AnF9/ol9/ol9AAADAH3/gwRlAu4ACwAR"
    "ABcAWLEGZERATQACAAgBAghnAAcACgYHCmcABgALAAYLZwMBAQQBAAkBAGcACQUFCVcACQkFXwwBBQkFTwAAFxYVFBMSERAPDg0M"
    "AAsACxERERERDQcbK7EGAEQXNSMRMzUhFTMRIxUBMzUhNSERIREjFSP6fX0C7n19/Y99AXf+DAH0+vp9fQJxfX39j30B9H19/Y8B"
    "d/oAAQB9AXcEZQLuABUAN0A0AwEBCQcFAwACAQBnAAIEBAJXAAICBF8LCggGBAQCBE8AAAAVABUUExEREREREREREQwGHysTNSM1"
    "IRUzNTMRIzUjFSM1IxUjNSMV+n0CcX36fX19fX19AXf6fX19/on6+vr6+voAAAEAfQD6AnECcQALADSxBmREQCkAAgEFAlcDAQEE"
    "AQAFAQBnAAICBV8GAQUCBU8AAAALAAsREREREQcHGyuxBgBENzUjNTM1MxUzFSMV+n19+n19+n19fX19fQAAAQB9/4MBdwLuAAMA"
    "HkAbAAABAQBXAAAAAV8CAQEAAU8AAAADAAMRAwcXKxcRMxF9+n0Da/yVAAACAH0AAAF3AnEAAwAHACxAKQQBAQEAXwAAABVNAAIC"
    "A18FAQMDFgNOBAQAAAQHBAcGBQADAAMRBgcXKxM1MxUDNTMVffr6+gF3+vr+ifr6AAEAfQAAAnECcQAPADFALgMBAQAEBQEEaAAF"
    "BgEABwUAZwACAhVNCAEHBxYHTgAAAA8ADxEREREREREJBx0rMzUjETM1MxUzFSMVMxUjFfp9ffp9+vp9fQF3fX19fX19AAEAfQAA"
    "A2sCcQALAClAJgMBAQECXwACAhVNBAEAAAVfBgEFBRYFTgAAAAsACxERERERBwcbKzM1MxEjNSEVIxEzFX19fQLufX19AXd9ff6J"
    "fQABAH3/gwLuAu4AGwCeS7AKUFhAPAAGBQUGcA4BDQAADXEABAADCQQDZwAJAAIKCQJoAAoACwEKC2cACAgFXwcBBQUVTQABAQBf"
    "DAEAABYAThtAOgAGBQaFDgENAA2GAAQAAwkEA2cACQACCgkCaAAKAAsBCgtnAAgIBV8HAQUFFU0AAQEAXwwBAAAWAE5ZQBoAAAAb"
    "ABsaGRgXFhUUExEREREREREREQ8HHysXNSM1ITUjNSM1MzUzNTMVMxUhFTMVMxUjFSMV+n0Bd/p9fX36ff6J+n19fX19fX19fX19"
    "fX19fX19fQAAAwB9AAADawJxABMAFwAbAE9ATAUBAwAGAgMGZwoBAg0BAQcCAWcABwgBAAwHAGcACwsEXwAEBBVNAAwMCV8OAQkJ"
    "FglOAAAbGhkYFxYVFAATABMREREREREREREPBx8rITUjNSM1MzUzNSEVMxUjFTMVIxUDMzUjETM1IwF3fX19fQF3ffr6ffp4eHh4"
    "fX19fX19fX19fQF3ff6JfQABAH0AAALuAnEADwAzQDAAAgUBAQACAWcABAQDXwADAxVNBgEAAAdfCAEHBxYHTgAAAA8ADxERERER"
    "EREJBx0rMzUzNSM1MzUhFSMVIxUzFX19ffoBd319+n19ffp9+n19AAEAfQAAA2sCcQAXAHlLsApQWEAqCAECBQEDAnIJAQEKAQAL"
    "AQBnBwEDAwRfBgEEBBVNAAUFC18MAQsLFgtOG0ArCAECBQEFAgGACQEBCgEACwEAZwcBAwMEXwYBBAQVTQAFBQtfDAELCxYLTllA"
    "FgAAABcAFxYVFBMRERERERERERENBx8rITUjNTM1IzUjNTMVMzUzFSMVIxUzFSMVAXd9fX19+vr6fX19fX19fX19fX19fX19fQAB"
    "AH0AAANrAnEACwAnQCQDAQEEAQAFAQBnAAICFU0GAQUFFgVOAAAACwALEREREREHBxsrITUjNTM1MxUzFSMVAXf6+vr6+vp9+vp9"
    "+gABAH0AfQJxAfQACwAtQCoAAgMBAQACAWcEAQAFBQBXBAEAAAVfBgEFAAVPAAAACwALEREREREHBxsrNzUzNSM1IRUjFTMVfX19"
    "AfR9fX19fX19fX0AAAMAfQAAAnECcQADAAcACwA9QDoAAgcBAwQCA2cGAQEBAF8AAAAVTQAEBAVfCAEFBRYFTggIBAQAAAgLCAsK"
    "CQQHBAcGBQADAAMRCQcXKxM1MxUFNSEVBTUzFfr6/okB9P6J+gH0fX36fX36fX0AAgB9AH0CcQH0AAMABwAvQCwAAAQBAQIAAWcA"
    "AgMDAlcAAgIDXwUBAwIDTwQEAAAEBwQHBgUAAwADEQYHFysTNSEVBTUhFX0B9P4MAfQBd319+n19AAABAH0AAAJxAnEAEwCKS7AK"
    "UFhANAACBQYDAnIAAQcIAAFyAAYABwEGB2cABQAIAAUIZwADAwRfAAQEFU0AAAAJYAoBCQkWCU4bQDYAAgUGBQIGgAABBwgHAQiA"
    "AAYABwEGB2cABQAIAAUIZwADAwRfAAQEFU0AAAAJYAoBCQkWCU5ZQBIAAAATABMRERERERERERELBx8rMzUzNTM1IzUjNTMVMxUz"
    "FSMVIxV9fX19ffp9fX19fX19fX19fX19fQABAH0AAAJxAnEAEwCKS7AKUFhANAAGAwIFBnIABwEACAdyAAIAAQcCAWcAAwAACAMA"
    "ZwAFBQRfAAQEFU0ACAgJYAoBCQkWCU4bQDYABgMCAwYCgAAHAQABBwCAAAIAAQcCAWcAAwAACAMAZwAFBQRfAAQEFU0ACAgJYAoB"
    "CQkWCU5ZQBIAAAATABMRERERERERERELBx8rITUjNSM1MzUzNTMVIxUjFTMVMxUBd319fX36fX19fX19fX19fX19fX0AAAEAff+D"
    "A2sCcQAPACxAKQQBAgUBAQACAWcGAQAIAQcAB2QAAwMVA04AAAAPAA8RERERERERCQcdKxc1MzUjNTM1MxUzFSMVMxV9+vr6+vr6"
    "+n19+n36+n36fQAAAQB9AXcC7gJxAAcAMLEGZERAJQABAAIAAQJnAAADAwBXAAAAA18EAQMAA08AAAAHAAcREREFBxkrsQYARBM1"
    "MzUhFSMVfX0B9H0Bd319fX0AAAEAfQB9Au4BdwAFAEZLsApQWEAXAwECAAACcQABAAABVwABAQBfAAABAE8bQBYDAQIAAoYAAQAA"
    "AVcAAQEAXwAAAQBPWUALAAAABQAFEREEBxgrJTUhNSEVAfT+iQJxfX19+gABAH0B9AJxAu4ABwBTsQZkREuwClBYQBkAAQAAAXAC"
    "AQADAwBXAgEAAANgBAEDAANQG0AYAAEAAYUCAQADAwBXAgEAAANgBAEDAANQWUAMAAAABwAHERERBQcZK7EGAEQTNTM1MxUzFX19"
    "+n0B9H19fX0AAAEAff+DAu4CcQALADNAMAADAwBfAgEAABVNAAEBBF8ABAQWTQYBBQUAXwIBAAAVBU4AAAALAAsREREREQcHGysX"
    "ETMRMxEzESMVIxV9+n36ffp9Au7+DAH0/gx9fQABAH0AAANrAnEACwApQCYDAQEBAl8AAgIVTQQBAAAFXwYBBQUWBU4AAAALAAsR"
    "EREREQcHGyszNTM1IzUhFSMVMxX6ffoCcX36+n36+n36AAIAOwNrAqwD6AADAAcAMrEGZERAJwIBAAEBAFcCAQAAAV8FAwQDAQAB"
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


show_phoenix_ui()
