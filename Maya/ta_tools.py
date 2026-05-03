# -*- coding: utf-8 -*-
"""
JAM TA Tools for Autodesk Maya
Author: Juan Abia Merino

Install/Run:
    1. Save this file where Maya can accesss.
    2. In Maya's Python tab (Script Editor):

        import sys
        sys.path.append(r"path to the file")
        import ta_tools as jamta
        jamta.show()    
"""

from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMaya as om
except Exception:
    #Allow syntax checkin outside Maya
    cmds = None
    mel = None
    om = None

WINDOW_NAME = "JAM_TA_TOOLS_MAYA_WINDOW"
WINDOW_TITLE = "JAM TA Tools"

X_AXIS_ITEMS = ["None", "X", "-X", "Mid"]
Y_AXIS_ITEMS = ["None", "Y", "-Y", "Mid"]
Z_AXIS_ITEMS = ["None", "Z", "-Z", "Mid"]
COUNT_MODES = ["Estimated Game Verts", "Tris", "Faces", "Verts"]

# ---
# General Helpers
# ---

def _require_maya() -> None:
    if cmds is None or mel is None or om is None:
        raise RuntimeError("This tool must be run inside Autodesk Maya")

def _long_name(node: str) -> str:
    result = cmds.ls(node, long = True) or []
    return result[0] if result else node

def _short_name(node: str) -> str:
    return node.split("|")[-1]

def _strip_namespace(name: str) -> str:
    return name.split(":")[-1]

def _unique_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out = []

    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)

    return out

def _flatten(value):
    if value is None:
        return[]
    
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_flatten(item))
        return out
    
    return [value]

def _round_tuple(values: Sequence[float], precision: int = 6) -> Tuple[float, ...]:
    return tuple(round(float(v), precision) for v in values)

def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default
    
def _status(message: str, warning: bool = False) -> None:
    if warning:
        cmds.warning(message)
    else:
        print("JAM TA Tools: " + message)

# ---
# Mesh selection and geometry helpers
# ---

def get_mesh_shapes(transform: str, no_intermediate: bool = True) -> List[str]:
    """
    Return mesh shape nodes below a transform
    """

    transform = _long_name(transform)

    shapes = cmds.listRelatives(
        transform,
        shapes = True,
        fullPath = True,
        type = "mesh",
    ) or []

    if not no_intermediate:
        return shapes
    
    out = []

    for shape in shapes:
        try:
            if not cmds.getAttr(shape + ".intermediateObject"):
                out.append(shape)
        except Exception:
            out.append(shape)

    return out

def node_to_mesh_transform(node: str) -> Optional[str]:
    """
    Return the transform if node is/contains a visible mesh shape
    """

    if not cmds.objExists(node):
        return None
    
    node_type = cmds.nodeType(node)

    if node_type == "mesh":
        parents = cmds.listRelatives(node, parent = True, fullPath = True) or []
        return parents[0] if parents else None
    
    if node_type == "transform" and get_mesh_shapes(node):
        return _long_name(node)
    
    return None

def get_selected_mesh_transforms() -> List[str]:
    """
    Return selected transforms that contain non-intermediate mesh shapes
    """

    selection = cmds.ls(selection=True, long = True) or []

    # If components are selected, try to resolve them back to owning objects
    if selection:
        converted = (
            cmds.ls(
                cmds.polyListComponentConversion(selection, toVertex=True),
                objectsOnly = True,
                long = True
            )
            or []
        )
        selection.extend(converted)

    transforms = []

    for node in selection:
        transform = node_to_mesh_transform(node)

        if transform:
            transforms.append(transform)

    return _unique_preserve_order(transforms)

def get_active_mesh_transform() -> Optional[str]:
    selection = cmds.ls(selection=True, long = True) or []

    if not selection:
        return None
    
    for node in selection:
        transform = node_to_mesh_transform(node)

        if transform:
            return transform
        
    return None

def get_world_bbox(obj: str) -> Optional[Tuple[float, float, float,
                                                float, float, float]]:
    """
    Return world bounding box as xMin, yMin, zMin, xMax, yMax, zMax
    """

    try:
        bbox = cmds.xform(obj, query = True, worldSpace = True, boundingBox = True)
    except Exception:
        return None
    
    if not bbox or len(bbox) != 6:
        return None
    
    return tuple(float(v) for v in bbox)

def combine_bboxes(bboxes: Iterable[Sequence[float]],) -> Optional[Tuple[float,float,float,
                                                                         float,float,float]]:
    bboxes = [b for b in bboxes if b and len(b) == 6]

    if not bboxes:
        return None
    
    return(
        min(b[0] for b in bboxes),
        min(b[1] for b in bboxes),
        min(b[2] for b in bboxes),
        max(b[3] for b in bboxes),
        max(b[4] for b in bboxes),
        max(b[5] for b in bboxes),
    )

def bbox_axis_value(bbox: Sequence[float], axis_index: int, mode: str) -> float:
    """
    Resolve one axis value from bbox using Maya/World axes
    """

    if mode == "Mid":
        return (float(bbox[axis_index]) + float(bbox[axis_index + 3])) * 0.5
    
    # Positive = max, negative = min
    if mode.startswith("-"):
        return float(bbox[axis_index])
    
    return float(bbox[axis_index + 3])

def current_pivot(obj: str) -> Tuple[float, float, float]:
    pivot = cmds.xform(
        obj,
        query = True,
        worldSpace = True,
        rotatePivot = True,
    )

    return float(pivot[0]), float(pivot[1]), float(pivot[2]) 

def set_pivot_world(obj: str, pivot: Sequence[float]) -> None:
    """
    Set rotate and scale pivot in world space without moving the object
    """

    cmds.xform(
        obj,
        worldSpace = True,
        rotatePivot = pivot,
        scalePivot = pivot,
    )

# ---
# RENAME
# ---

def rename_selected(
        prefix: str,
        item_name: str,
        start_index: int = 1,
        rename_shapes: bool = True,
) -> List[str]:
    meshes = get_selected_mesh_transforms()

    if not meshes:
        raise RuntimeError("No mesh transforms selected")
    
    renamed = []
    width = max(2, len(str(start_index + len(meshes) - 1)))

    for offset, obj in enumerate(meshes):
        index = start_index + offset

        new_name = "{0}_{1}_{2:0{3}d}".format(
            prefix,
            item_name,
            index,
            width,
        )

        new_transform = cmds.rename(obj, new_name)
        new_transform = _long_name(new_transform)

        renamed.append(new_transform)

        if rename_shapes:
            shapes = get_mesh_shapes(new_transform, no_intermediate=False)

            for shape_index, shape in enumerate(shapes, start=1):
                suffix = "Shape" if shape_index == 1 else "Shape{0}".format(shape_index)

                try:
                    cmds.rename(shape, new_name + suffix)
                except Exception:
                    pass

    return renamed

# ---
# Budget / metric counting
# ---

def _get_dag_path(shape: str):
    sel = om.MSelectionList()
    sel.add(shape)

    return sel.getDagPath(0)

def get_shape_metric(
        shape: str,
        mode: str,
        count_material_splits: bool = True,
) -> int:
    if mode == "Verts":
        return _safe_int(cmds.polyEvaluate(shape, vertex=True), 0)
    
    if mode == "Faces":
        return _safe_int(cmds.polyEvaluate(shape, face=True), 0)
    
    if mode == "Tris":
        return _safe_int(cmds.polyEvaluate(shape, triangle=True), 0)
    
    return estimate_game_vertices(
        shape,
        count_material_splits=count_material_splits,
    )

def get_mesh_metric(
        obj: str,
        mode: str,
        count_material_splits: bool = True,
) -> int:
    total = 0

    for shape in get_mesh_shapes(obj):
        total += get_shape_metric(
            shape,
            mode,
            count_material_splits=count_material_splits,
        )
    
    return total

def get_selection_metric(
        mode: str,
        count_material_splits: bool = True,
) -> int:
    return sum(
        get_mesh_metric(
            obj,
            mode,
            count_material_splits=count_material_splits
        )
        for obj in get_selected_mesh_transforms()
    )

def estimate_game_vertices(
        shape: str,
        count_material_splits: bool = True,
) -> int:
    """
    Estimate engine/render vertices using per-face-vertex split attributes.

    Split key:
        vertex id + face-vertex normal + every UV set + optional shader/material index

    REMINDER:
    This is an estimate. The final vertex count depends on engine importer, tangent generation, compression,
    lightmap UVs, skinning data, export settings, ... but it's a pretty good approach.
    """

    dag = _get_dag_path(shape)
    mesh_fn = om.MFnMesh(dag)
    iterator = om.MItMeshPolygon(dag)

    uv_sets = []
    try:
        uv_sets = list(mesh_fn.getUVSetNames())
    except Exception:
        uv_sets = []

    shader_indices = []
    if count_material_splits:
        try:
            _shaders, shader_indices = mesh_fn.getConnectedShaders(dag.instanceNumber(),)
        except Exception:
            shader_indices = []

    unique_keys = set()

    while not iterator.isDone():
        face_index = iterator.index()
        vertex_ids = iterator.getVertices()

        material_index = -1
        if count_material_splits and face_index < len(shader_indices):
            material_index = int(shader_indices[face_index])

        for local_index, vertex_id in enumerate(vertex_ids):
            key = [int(vertex_id)]

            try:
                normal = iterator.getNormal(local_index, om.MSpace.kWorld)
                key.append(
                    _round_tuple((normal.x, normal.y, normal.z))
                )
            except Exception:
                key.append((0.0, 0.0, 0.0))

            for uv_set in uv_sets:
                try:
                    uv = iterator.getUV(local_index, uv_set)
                    key.append(_round_tuple((uv[0], uv[1])))
                except Exception:
                    key.append(None)

            if count_material_splits:
                key.append(material_index)

            unique_keys.add(tuple(key))

        iterator.next()

    return len(unique_keys)

def classify_budget(
        count: int,
        budget: int,
        margin_percent: float,
        lp_prefix: str,
        hp_prefix: str,
) -> Dict[str, object]:
    budget = max(int(budget), 1)
    margin = int(budget * (float(margin_percent) / 100.0))

    warning_start = max(0, budget - margin)
    high_limit = budget + margin

    if count > high_limit:
        return {
            "prefix": hp_prefix,
            "state": "HIGH",
            "label": "High",
            "limit": high_limit,
        }
    
    if count >= warning_start:
        return {
            "prefix": lp_prefix,
            "state": "MARGIN",
            "label": "Near budget",
            "limit": high_limit,
        }
    
    return {
            "prefix": lp_prefix,
            "state": "LOW",
            "label": "Low",
            "limit": high_limit,
        }

def remove_existing_poly_prefix(
        name: str,
        prefixes: Sequence[str],
) -> str:
    clean = name

    for prefix in prefixes:
        if not prefix:
            continue

        token = prefix + "_"

        if clean.startswith(token):
            clean = clean[len(token):]

    return clean

def export_name_with_prefix(
        base_name: str,
        count: int,
        budget: int,
        margin_percent: float,
        lp_prefix: str,
        hp_prefix: str,
):
    classification = classify_budget(count, budget, margin_percent, lp_prefix, hp_prefix,)

    clean_name = remove_existing_poly_prefix(
        _strip_namespace(_short_name(base_name)),
        [lp_prefix, hp_prefix],
    )

    return "{0}_{1}".format(classification["prefix"], clean_name), classification

# ---
# Pivot tool
# ---

def set_selected_pivots(
        x_axis: str = "None",
        y_axis: str = "None",
        z_axis: str = "None",
        individual: bool = False,
) -> int:
    meshes = get_selected_mesh_transforms()

    if not meshes:
        raise RuntimeError("No mesh transforms selected")
    
    axis_settings = [
            (x_axis, 0),
            (y_axis, 1),
            (z_axis, 2)
    ]

    axis_settings = [(mode, index) for mode, index in axis_settings if mode != "None"]

    if not axis_settings:
        raise RuntimeError("No pivot axis selected")
    
    moved = 0

    if individual:
        for obj in meshes:
            bbox = get_world_bbox(obj)

            if not bbox:
                continue

            pivot = list(current_pivot(obj))

            for mode, axis_index in axis_settings:
                pivot[axis_index] = bbox_axis_value(
                    bbox, axis_index, mode,
                )

            set_pivot_world(obj, pivot)
            moved += 1

    else:
        shared_bbox = combine_bboxes(get_world_bbox(obj) for obj in meshes)

        if not shared_bbox:
            raise RuntimeError("Could not calculate selection bounding box")
        
        shared_values = {}

        for mode, axis_index in axis_settings:
            shared_values[axis_index] = bbox_axis_value(
                shared_bbox, axis_index, mode,
            )

        for obj in meshes:
            pivot = list(current_pivot(obj))

            for axis_index, value in shared_values.items():
                pivot[axis_index] = value

            set_pivot_world(obj, pivot)
            moved+=1

    return moved

def move_pivot_to_selected_vertices() -> int:
    """
    Move each selected mesh object's pivot to its selected vertex position.

    If multiple vertex are selected, the pivot is moved to the average world-position.
    """

    selected_vertices = cmds.ls(
        selection = True,
        flatten = True,
        long = True,
    ) or []

    selected_vertices = [
        component for component in selected_vertices if ".vtx[" in component
    ]

    if not selected_vertices:
        raise RuntimeError("No vertices selected")
    
    vertices_by_object = {}

    for vertex in selected_vertices:
        node = vertex.split(".")[0]
        transform = node_to_mesh_transform(node)

        if not transform:
            continue

        vertices_by_object.setdefault(transform, []).append(vertex)

    if not vertices_by_object:
        raise RuntimeError("Could not find a mesh object from the selected vertices")
    
    moved_count = 0

    for obj, vertices in vertices_by_object.items():
        points = []

        for vertex in vertices:
            try:
                point = cmds.pointPosition(vertex, world = True)
                points.append(point)

            except Exception:
                pass

        if not points:
            continue

        pivot = [
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
            sum(point[2] for point in points) / len(points),
        ]

        set_pivot_world(obj, pivot)
        moved_count += 1

    return moved_count

# ---
# Validation -> Manifolds
# ---

_COMPONENT_RE = re.compile(r"[^\s#]+\.(?:e|vtx|f)\[[^\]]+\]")

def _polyinfo_components(lines: Optional[Sequence[str]]) -> List[str]:
    if not lines:
        return []
    
    components = []

    for line in lines:
        components.extend(_COMPONENT_RE.findall(line))

    return components

def check_mesh_validation(obj: str) -> Dict[str, object]:
    """
    Return validation data for one mesh transform
    """

    non_manifold_edges = []
    non_manifold_vertices = []
    lamina_faces = []

    try:
        non_manifold_edges = _polyinfo_components(
            cmds.polyInfo(obj, nonManifoldEdges=True),
        )
    except Exception:
        non_manifold_edges = []

    try:
        non_manifold_vertices = _polyinfo_components(
            cmds.polyInfo(obj, nonManifoldVertices=True),
        )
    except Exception:
        non_manifold_vertices = []

    try:
        lamina_faces = _polyinfo_components(
            cmds.polyInfo(obj, laminaFaces=True),
        )
    except Exception:
        lamina_faces= []

    # Transform-level checks for game export
    scale = cmds.xform(obj, query=True, relative=True, scale=True,)

    negative_scale = any(float(v) < 0.0 for v in scale)
    non_frozen_scale = any(abs(float(v) -1) > 0.0001 for v in scale)

    return {
        "object": obj,
        "non_manifold_edges": non_manifold_edges,
        "non_manifold_vertices": non_manifold_vertices,
        "lamina_faces": lamina_faces,
        "negative_scale": negative_scale,
        "non_frozen_scale": non_frozen_scale,
        "issue_count": (
            len(non_manifold_edges) + len(non_manifold_vertices) + len(lamina_faces)
            + int(negative_scale) + int(non_frozen_scale)
        ),
    }

def validate_selection(
        select_first_issue: bool = True,
) -> Tuple[List[Dict[str, object]], str]:
    meshes = get_selected_mesh_transforms()

    if not meshes:
        raise RuntimeError("No mesh transforms selected")
    
    results = [check_mesh_validation(obj) for obj in meshes]

    problem_results = [r for r in results if int(r["issue_count"]) > 0]

    if not problem_results:
        return [], (
            "Clean: no non-manifold edges, non-manifold vertices, lamina faces, "
            "negative scale, or non-frozen scale found"
        )
    
    lines = []

    for r in problem_results:
        flags = []

        if r["non_manifold_edges"]:
            flags.append(
                "non-manifold edges: {0}".format(len(r["non_manifold_edges"]))
            )
        
        if r["non_manifold_vertices"]:
            flags.append(
                "non-manifold verts: {0}".format(len(r["non_manifold_vertices"]))
            )

        if r["lamina_faces"]:
            flags.append(
                "lamina faces: {0}".format(len(r["lamina_faces"]))
            )

        if r["negative_scale"]:
            flags.append("negative scale")

        if r["non_frozen_scale"]:
            flags.append("non-frozen scale")

        lines.append(
            "{0}: {1}".format(_short_name(str(r["object"])), ", ".join(flags),)
        )

    if select_first_issue:
        first = problem_results[0]

        components = []
        components.extend(first["non_manifold_edges"])
        components.extend(first["non_manifold_vertices"])
        components.extend(first["lamina_faces"])

        if components:
            cmds.select(components, replace = True)

            try:
                cmds.selectMode(component = True)
            except Exception:
                pass

        else:
            cmds.select(first["object"], replace = True)

    return problem_results, " | ".join(lines)

# ---
# Combine / export
# ---

def combine_selected_meshes() -> Optional[str]:
    meshes = get_selected_mesh_transforms()

    if len(meshes) < 2:
        raise RuntimeError("Select at least two mesh transforms to combine")
    
    result = cmds.polyUnite(
        meshes, constructionHistory = False, mergeUVSets = True,
    )

    if not result:
        return None
    
    combined = result[0]

    cmds.delete(combined, constructionHistory=True)
    cmds.select(combined, replace = True)

    return _long_name(combined)

def _ensure_fbx_plugin() -> None:
    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya")

def _mel_string(path: str) -> str:
    return path.replace("\\", "/").replace('"', '\\"')

def export_selected_fbx(
        export_folder: str,
        export_name: str,
        validate_before_export: bool = True,
        block_on_validation_errors: bool = True,
) -> str:
    meshes = get_selected_mesh_transforms()

    if not meshes:
        raise RuntimeError("No mesh transforms selected for export")
    
    export_folder = os.path.abspath(os.path.expanduser(export_folder))

    if not os.path.isdir(export_folder):
        raise RuntimeError("Invalid export folder: {0}".format(export_folder))
    
    if validate_before_export:
        problems, message = validate_selection(select_first_issue=False)

        if problems and block_on_validation_errors:
            raise RuntimeError("Export blocked by validation: {0}".format(message))
        
    _ensure_fbx_plugin()

    filepath = os.path.join(export_folder, export_name + ".fbx")

    filepath = os.path.normpath(filepath)

    # IMPORTANT:
    # These settings are the game-art defaults I've set.
    # Your team should adapt these to their needs.
    try:
        mel.eval("FBXResetExport")
    except Exception:
        pass

    fbx_commands = [
        "FBXExportSmoothingGroups -v true",
        "FBXExportSmoothMesh -v false",
        "FBXExportTangents -v true",
        "FBXExportTriangulate -v false",
        "FBXExportInputConnections -v false",
        "FBXExportConstraints -v false",
        "FBXExportCameras -v false",
        "FBXExportLights -v false",
        "FBXExportEmbeddedTextures -v false",
    ]

    for command in fbx_commands:
        try:
            mel.eval(command)
        except Exception:
            pass

    mel.eval('FBXExport -f "{0}" -s'.format(_mel_string(filepath)))

    return filepath

# ---
# UI
# ---

class JAMTAToolsUI(object):
    def __init__(self):
        _require_maya()
        self.controls: Dict[str, str] = {}

    def show(self) -> None:
        if cmds.window(WINDOW_NAME, exists=True):
            cmds.deleteUI(WINDOW_NAME, window=True)

        self.controls.clear()

        window = cmds.window(
            WINDOW_NAME,
            title = WINDOW_TITLE,
            sizeable = True,
            widthHeight=(420, 669),
        )

        root = cmds.columnLayout(adjustableColumn=True, rowSpacing=8,)

        cmds.text(
            label = "JAM TA Tools",
            align = "center",
            height = 28,
            font = "boldLabelFont",
        )

        cmds.separator(style = "in")

        self._build_rename_section(root)
        self._build_budget_section(root)
        self._build_pivot_section(root)
        self._build_validation_section(root)
        self._build_export_section(root)
        self._build_status_section(root)

        cmds.showWindow(window)

        self.refresh_budget_marker()

    # --- UI BUILDERS ---

    def _build_rename_section(self, parent: str) -> None:
        frame = cmds.frameLayout(
            label = "Rename Objects",
            collapsable = True,
            collapse = False,
            marginWidth = 8,
            marginHeight = 8,
            parent = parent,
        )

        col = cmds.columnLayout(
            adjustableColumn = True,
            rowSpacing = 4,
            parent = frame,
        )

        self.controls["rs_prefix"] = cmds.textFieldGrp(
            label = "Prefix",
            text = "SM",
            columnWidth = [(1, 90), (2, 280),],
            parent = col,
        )

        self.controls["rs_name"] = cmds.textFieldGrp(
            label = "Item name",
            text = "Prop",
            columnWidth = [(1, 90), (2, 280),],
            parent = col,
        )

        self.controls["rs_start"] = cmds.intFieldGrp(
            label = "Start index",
            value1 = 1,
            columnWidth = [(1, 90), (2, 80),],
            parent = col,
        )

        self.controls["rs_shapes"] = cmds.checkBox(
            label = "Rename shape nodes too?",
            value = True,
            parent = col,
        )

        cmds.button(
            label="Rename Selected",
            command = lambda *_: self.on_rename_selected(),
            parent=col,
        )

    def _build_budget_section(self, parent: str) -> None:
        frame = cmds.frameLayout(
            label="Budget",
            collapsable=True,
            collapse=False,
            marginWidth=8,
            marginHeight=8,
            parent=parent,
        )

        col = cmds.columnLayout(
            adjustableColumn=True,
            rowSpacing=4,
            parent=frame,
        )

        self.controls["budget_mode"] = cmds.optionMenuGrp(
            label="Count mode",
            columnWidth=[
                (1, 90),
                (2, 220),
            ],
            changeCommand=lambda *_: self.refresh_budget_marker(),
            parent=col,
        )

        for item in COUNT_MODES:
            cmds.menuItem(label=item)

        self.controls["budget"] = cmds.intFieldGrp(
            label="Budget",
            value1=3000,
            columnWidth=[
                (1, 90),
                (2, 100),
            ],
            changeCommand=lambda *_: self.refresh_budget_marker(),
            parent=col,
        )

        self.controls["margin"] = cmds.floatFieldGrp(
            label="Margin %",
            value1=10.0,
            columnWidth=[
                (1, 90),
                (2, 100),
            ],
            changeCommand=lambda *_: self.refresh_budget_marker(),
            parent=col,
        )

        self.controls["mat_splits"] = cmds.checkBox(
            label="Count material/shader splits",
            value=True,
            changeCommand=lambda *_: self.refresh_budget_marker(),
            parent=col,
        )

        self.controls["auto_prefix"] = cmds.checkBox(
            label="Auto LP/HP prefix on export",
            value=True,
            parent=col,
        )

        row = cmds.rowLayout(
            numberOfColumns=2,
            adjustableColumn=2,
            columnWidth2=(200, 200),
            parent=col,
        )

        self.controls["lp_prefix"] = cmds.textFieldGrp(
            label="LP",
            text="LP",
            columnWidth=[
                (1, 35),
                (2, 110),
            ],
            parent=row,
        )

        self.controls["hp_prefix"] = cmds.textFieldGrp(
            label="HP",
            text="HP",
            columnWidth=[
                (1, 35),
                (2, 110),
            ],
            parent=row,
        )

        cmds.setParent(col)

        cmds.button(
            label="Refresh Budget",
            command=lambda *_: self.refresh_budget_marker(),
            parent=col,
        )

        self.controls["budget_marker"] = cmds.text(
            label="Poly budget: No mesh selected",
            align="left",
            parent=col,
        )

    def _build_pivot_section(self, parent: str) -> None:
        frame = cmds.frameLayout(
            label="Pivot / Origin Placement",
            collapsable=True,
            collapse=False,
            marginWidth=8,
            marginHeight=8,
            parent=parent,
        )

        col = cmds.columnLayout(
            adjustableColumn=True,
            rowSpacing=4,
            parent=frame,
        )

        cmds.text(
            label="Set pivot by world-space selection/object bounding box.",
            align="left",
            parent=col,
        )

        row = cmds.rowLayout(
            numberOfColumns=3,
            adjustableColumn=3,
            columnWidth3=(130, 130, 130),
            parent=col,
        )

        self.controls["x_axis"] = self._axis_menu(
            "X axis",
            X_AXIS_ITEMS,
            parent=row,
        )

        self.controls["y_axis"] = self._axis_menu(
            "Y axis",
            Y_AXIS_ITEMS,
            parent=row,
        )

        self.controls["z_axis"] = self._axis_menu(
            "Z axis",
            Z_AXIS_ITEMS,
            parent=row,
        )

        cmds.setParent(col)

        self.controls["individual"] = cmds.checkBox(
            label="Apply individually per object",
            value=False,
            parent=col,
        )

        cmds.button(
            label="Move Pivot",
            command=lambda *_: self.on_move_pivot(),
            parent=col,
        )

        cmds.button(
            label="Move Pivot to Selected Vertex",
            command = lambda *_: self.on_pivot_to_selected_vertex(),
            parent = col
        )

    def _axis_menu(self, label: str, items: List[str], parent: str) -> str:
        control = cmds.optionMenuGrp(
            label=label,
            columnWidth=[
                (1, 45),
                (2, 70),
            ],
            parent=parent,
        )

        for item in items:
            cmds.menuItem(label=item)

        cmds.optionMenuGrp(
            control,
            edit=True,
            value="None",
        )

        return control

    def _build_validation_section(self, parent: str) -> None:
        frame = cmds.frameLayout(
            label="Validation",
            collapsable=True,
            collapse=False,
            marginWidth=8,
            marginHeight=8,
            parent=parent,
        )

        col = cmds.columnLayout(
            adjustableColumn=True,
            rowSpacing=4,
            parent=frame,
        )

        self.controls["select_issue"] = cmds.checkBox(
            label="Select first issue",
            value=True,
            parent=col,
        )

        cmds.button(
            label="Check Non-Manifold / Export Issues",
            command=lambda *_: self.on_validate(),
            parent=col,
        )

        self.controls["validation_message"] = cmds.text(
            label="",
            align="left",
            parent=col,
        )

    def _build_export_section(self, parent: str) -> None:
        frame = cmds.frameLayout(
            label="Easy Export",
            collapsable=True,
            collapse=False,
            marginWidth=8,
            marginHeight=8,
            parent=parent,
        )

        col = cmds.columnLayout(
            adjustableColumn=True,
            rowSpacing=4,
            parent=frame,
        )

        self.controls["allow_combine"] = cmds.checkBox(
            label="Allow destructive combine",
            value=False,
            parent=col,
        )

        cmds.button(
            label="Combine Selected Meshes",
            command=lambda *_: self.on_combine(),
            parent=col,
        )

        cmds.separator(
            style="in",
            parent=col,
        )

        self.controls["export_path"] = cmds.textFieldButtonGrp(
            label="Export to",
            text=cmds.workspace(query=True, rootDirectory=True),
            buttonLabel="Browse",
            columnWidth=[
                (1, 90),
                (2, 230),
                (3, 70),
            ],
            buttonCommand=lambda *_: self.on_browse_export_path(),
            parent=col,
        )

        self.controls["validate_export"] = cmds.checkBox(
            label="Validate before export",
            value=True,
            parent=col,
        )

        self.controls["block_export"] = cmds.checkBox(
            label="Block export on validation issues",
            value=True,
            parent=col,
        )

        cmds.button(
            label="Export Selected FBX",
            command=lambda *_: self.on_export(),
            parent=col,
        )

    def _build_status_section(self, parent: str) -> None:
        cmds.separator(
            style="in",
            parent=parent,
        )

        self.controls["status"] = cmds.text(
            label="Ready.",
            align="left",
            parent=parent,
        )

    # --- UI Getters --

    def get_count_mode(self) -> str:
        return cmds.optionMenuGrp(
            self.controls["budget_mode"],
            query=True,
            value=True,
        )

    def get_budget(self) -> int:
        return cmds.intFieldGrp(
            self.controls["budget"],
            query=True,
            value1=True,
        )

    def get_margin(self) -> float:
        return cmds.floatFieldGrp(
            self.controls["margin"],
            query=True,
            value1=True,
        )

    def get_lp_prefix(self) -> str:
        return (
            cmds.textFieldGrp(
                self.controls["lp_prefix"],
                query=True,
                text=True,
            ).strip()
            or "LP"
        )

    def get_hp_prefix(self) -> str:
        return (
            cmds.textFieldGrp(
                self.controls["hp_prefix"],
                query=True,
                text=True,
            ).strip()
            or "HP"
        )

    def get_count_material_splits(self) -> bool:
        return cmds.checkBox(
            self.controls["mat_splits"],
            query=True,
            value=True,
        )

    def set_status(self, message: str) -> None:
        cmds.text(
            self.controls["status"],
            edit=True,
            label=message,
        )

        _status(message)

    def set_validation_message(self, message: str) -> None:
        cmds.text(
            self.controls["validation_message"],
            edit=True,
            label=message,
        )

    # ---------------- UI callbacks ----------------

    def on_rename_selected(self) -> None:
        try:
            prefix = (
                cmds.textFieldGrp(
                    self.controls["rs_prefix"],
                    query=True,
                    text=True,
                ).strip()
                or "SM"
            )

            item_name = (
                cmds.textFieldGrp(
                    self.controls["rs_name"],
                    query=True,
                    text=True,
                ).strip()
                or "Prop"
            )

            start_index = cmds.intFieldGrp(
                self.controls["rs_start"],
                query=True,
                value1=True,
            )

            rename_shapes = cmds.checkBox(
                self.controls["rs_shapes"],
                query=True,
                value=True,
            )

            renamed = rename_selected(
                prefix,
                item_name,
                start_index=start_index,
                rename_shapes=rename_shapes,
            )

            self.set_status(
                "Renamed {0} object(s).".format(
                    len(renamed)
                )
            )

            self.refresh_budget_marker()

        except Exception as exc:
            self.set_status(
                "Rename failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

    def refresh_budget_marker(self) -> None:
        try:
            meshes = get_selected_mesh_transforms()

            if not meshes:
                cmds.text(
                    self.controls["budget_marker"],
                    edit=True,
                    label="Poly budget: No mesh selected",
                )
                return

            mode = self.get_count_mode()

            count = get_selection_metric(
                mode,
                count_material_splits=self.get_count_material_splits(),
            )

            classification = classify_budget(
                count,
                self.get_budget(),
                self.get_margin(),
                self.get_lp_prefix(),
                self.get_hp_prefix(),
            )

            label = "{0} · {1:,}/{2:,} {3} · Export as {4}_".format(
                classification["label"],
                count,
                classification["limit"],
                mode.lower(),
                classification["prefix"],
            )

            cmds.text(
                self.controls["budget_marker"],
                edit=True,
                label=label,
            )

        except Exception as exc:
            cmds.text(
                self.controls["budget_marker"],
                edit=True,
                label="Budget marker error: {0}".format(exc),
            )

    def on_move_pivot(self) -> None:
        try:
            x_axis = cmds.optionMenuGrp(
                self.controls["x_axis"],
                query=True,
                value=True,
            )

            y_axis = cmds.optionMenuGrp(
                self.controls["y_axis"],
                query=True,
                value=True,
            )

            z_axis = cmds.optionMenuGrp(
                self.controls["z_axis"],
                query=True,
                value=True,
            )

            individual = cmds.checkBox(
                self.controls["individual"],
                query=True,
                value=True,
            )

            moved = set_selected_pivots(
                x_axis,
                y_axis,
                z_axis,
                individual=individual,
            )

            self.set_status(
                "Moved pivot on {0} object(s).".format(moved)
            )

        except Exception as exc:
            self.set_status(
                "Move pivot failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

    def on_pivot_to_selected_vertex(self) -> None:
        try:
            moved = move_pivot_to_selected_vertices()

            self.set_status(
                "Moved pivot to selected vertex position on {0} object(s)".format(moved)
            )
        except Exception as exc:
            self.set_status(
                "Move pivot to vertex failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

    def on_validate(self) -> None:
        try:
            select_first = cmds.checkBox(
                self.controls["select_issue"],
                query=True,
                value=True,
            )

            problems, message = validate_selection(
                select_first_issue=select_first,
            )

            self.set_validation_message(message)

            if problems:
                self.set_status(
                    "Validation found issues in {0} object(s).".format(
                        len(problems)
                    )
                )
                cmds.warning(message)
            else:
                self.set_status(message)

        except Exception as exc:
            self.set_validation_message(
                "Validation failed: {0}".format(exc)
            )
            self.set_status(
                "Validation failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

    def on_combine(self) -> None:
        try:
            allowed = cmds.checkBox(
                self.controls["allow_combine"],
                query=True,
                value=True,
            )

            if not allowed:
                raise RuntimeError(
                    "Enable 'Allow destructive combine' first."
                )

            combined = combine_selected_meshes()

            self.set_status(
                "Combined selected meshes into {0}.".format(
                    _short_name(combined)
                    if combined
                    else "new mesh"
                )
            )

            self.refresh_budget_marker()

        except Exception as exc:
            self.set_status(
                "Combine failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

    def on_browse_export_path(self) -> None:
        folder = cmds.fileDialog2(
            fileMode=3,
            caption="Choose FBX Export Folder",
        )

        if folder:
            cmds.textFieldButtonGrp(
                self.controls["export_path"],
                edit=True,
                text=folder[0],
            )

    def on_export(self) -> None:
        try:
            meshes = get_selected_mesh_transforms()

            if not meshes:
                raise RuntimeError("No mesh transforms selected.")

            active = get_active_mesh_transform() or meshes[0]
            base_name = _short_name(active)

            mode = self.get_count_mode()

            count = get_selection_metric(
                mode,
                count_material_splits=self.get_count_material_splits(),
            )

            if cmds.checkBox(
                self.controls["auto_prefix"],
                query=True,
                value=True,
            ):
                export_name, classification = export_name_with_prefix(
                    base_name,
                    count,
                    self.get_budget(),
                    self.get_margin(),
                    self.get_lp_prefix(),
                    self.get_hp_prefix(),
                )
            else:
                classification = None
                export_name = _strip_namespace(base_name)

            export_folder = cmds.textFieldButtonGrp(
                self.controls["export_path"],
                query=True,
                text=True,
            )

            filepath = export_selected_fbx(
                export_folder,
                export_name,
                validate_before_export=cmds.checkBox(
                    self.controls["validate_export"],
                    query=True,
                    value=True,
                ),
                block_on_validation_errors=cmds.checkBox(
                    self.controls["block_export"],
                    query=True,
                    value=True,
                ),
            )

            if classification:
                self.set_status(
                    "Exported {0} as {1} with {2:,} {3}.".format(
                        os.path.basename(filepath),
                        classification["prefix"],
                        count,
                        mode.lower(),
                    )
                )
            else:
                self.set_status(
                    "Exported {0}.".format(
                        os.path.basename(filepath)
                    )
                )

        except Exception as exc:
            self.set_status(
                "Export failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

_UI_INSTANCE: Optional[JAMTAToolsUI] = None

def show() -> JAMTAToolsUI:
    """
    Open the JAM TA Tools window
    """

    global _UI_INSTANCE

    _UI_INSTANCE = JAMTAToolsUI()
    _UI_INSTANCE.show()

    return _UI_INSTANCE

if __name__ == "__main__":
    show()