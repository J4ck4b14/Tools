# -*- coding: utf-8 -*-
"""
JAM TA Tools for Autodesk Maya
Author: Juan Abia Merino

A single-file toolkit for game-art workflows in Maya: batch renaming,
poly-budget tracking with engine-vertex estimation, pivot placement,
mesh and UV validation (non-manifold geometry, lamina faces, scale
issues, missing/flipped/out-of-range UVs), texel density checking and
matching, validated FBX export (single file or batch with move-to-origin)
and rigging helpers (IK handle + pole vector placement, pose-driven
muscle joints). Settings persist between sessions and batch operations
are grouped into single undo steps.

Install/Run:
    1. Save this file somewhere Maya can access
       (e.g. your Documents/maya/scripts folder).
    2. In Maya's Script Editor (Python tab):

        import sys
        sys.path.append(r"path/to/folder/containing/this/file")
        import ta_tools as jamta
        jamta.show()
"""

from __future__ import annotations

import math
import os
import re
from functools import wraps
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
    import maya.mel as mel
    import maya.api.OpenMaya as om
except Exception:
    # Allow syntax checking / linting outside Maya
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
    result = cmds.ls(node, long=True) or []
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


def undoable(func):
    """
    Wrap an operation in a single undo chunk so a whole batch
    (e.g. renaming 50 objects) undoes with one Ctrl+Z.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        if cmds is None:
            return func(*args, **kwargs)

        cmds.undoInfo(openChunk=True, chunkName=func.__name__)

        try:
            return func(*args, **kwargs)
        finally:
            cmds.undoInfo(closeChunk=True)

    return wrapper


# ---
# Settings persistence (optionVar)
# ---

_OPTIONVAR_PREFIX = "JAMTATools_"


def _save_option(name: str, value) -> None:
    if cmds is None:
        return

    cmds.optionVar(stringValue=(_OPTIONVAR_PREFIX + name, str(value)))


def _load_option(name: str, default: str = "") -> str:
    full_name = _OPTIONVAR_PREFIX + name

    if cmds is not None and cmds.optionVar(exists=full_name):
        return str(cmds.optionVar(query=full_name))

    return default

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
        shapes=True,
        fullPath=True,
        type="mesh",
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
        parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
        return parents[0] if parents else None

    if node_type == "transform" and get_mesh_shapes(node):
        return _long_name(node)

    return None


def get_selected_mesh_transforms() -> List[str]:
    """
    Return selected transforms that contain non-intermediate mesh shapes
    """

    selection = cmds.ls(selection=True, long=True) or []

    # If components are selected, try to resolve them back to owning objects
    if selection:
        converted = (
            cmds.ls(
                cmds.polyListComponentConversion(selection, toVertex=True),
                objectsOnly=True,
                long=True
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
    selection = cmds.ls(selection=True, long=True) or []

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
        bbox = cmds.xform(obj, query=True, worldSpace=True, boundingBox=True)
    except Exception:
        return None

    if not bbox or len(bbox) != 6:
        return None

    return tuple(float(v) for v in bbox)


def combine_bboxes(
        bboxes: Iterable[Sequence[float]],
) -> Optional[Tuple[float, float, float, float, float, float]]:
    bboxes = [b for b in bboxes if b and len(b) == 6]

    if not bboxes:
        return None

    return (
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
        query=True,
        worldSpace=True,
        rotatePivot=True,
    )

    return float(pivot[0]), float(pivot[1]), float(pivot[2])


def set_pivot_world(obj: str, pivot: Sequence[float]) -> None:
    """
    Set rotate and scale pivot in world space without moving the object
    """

    cmds.xform(
        obj,
        worldSpace=True,
        rotatePivot=pivot,
        scalePivot=pivot,
    )

# ---
# RENAME
# ---


@undoable
def rename_selected(
        prefix: str,
        item_name: str,
        start_index: int = 1,
        rename_shapes: bool = True,
) -> List[str]:
    meshes = get_selected_mesh_transforms()

    if not meshes:
        raise RuntimeError("No mesh transforms selected")

    # Track objects by UUID: renaming a parent transform invalidates the
    # stored DAG paths of any selected children, so paths captured up front
    # can go stale mid-loop. UUIDs survive renames.
    uuids = [cmds.ls(obj, uuid=True)[0] for obj in meshes]

    renamed = []
    width = max(2, len(str(start_index + len(meshes) - 1)))

    for offset, uuid in enumerate(uuids):
        index = start_index + offset

        current_paths = cmds.ls(uuid, long=True) or []
        if not current_paths:
            continue
        obj = current_paths[0]

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
    This is an estimate. The final vertex count depends on the engine importer,
    tangent generation, compression, lightmap UVs, skinning data, export
    settings, etc. -- but it is a close approximation for budget tracking.
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
            _shaders, shader_indices = mesh_fn.getConnectedShaders(dag.instanceNumber())
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
    classification = classify_budget(
        count, budget, margin_percent, lp_prefix, hp_prefix,
    )

    clean_name = remove_existing_poly_prefix(
        _strip_namespace(_short_name(base_name)),
        [lp_prefix, hp_prefix],
    )

    return "{0}_{1}".format(classification["prefix"], clean_name), classification

# ---
# Pivot tool
# ---


@undoable
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
            moved += 1

    return moved


@undoable
def move_pivot_to_selected_vertices() -> int:
    """
    Move each selected mesh object's pivot to its selected vertex position.

    If multiple vertices are selected, the pivot is moved to their
    average world position.
    """

    selected_vertices = cmds.ls(
        selection=True,
        flatten=True,
        long=True,
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
                point = cmds.pointPosition(vertex, world=True)
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


def _uv_signed_area(us: Sequence[float], vs: Sequence[float]) -> float:
    """
    Signed area of a UV polygon via the shoelace formula.
    A negative value means the face's UV winding is flipped.
    """

    area = 0.0
    count = len(us)

    for i in range(count):
        j = (i + 1) % count
        area += us[i] * vs[j] - us[j] * vs[i]

    return area * 0.5


def check_shape_uvs(shape: str, tolerance: float = 0.001) -> Dict[str, object]:
    """
    UV checks for one mesh shape: missing UVs, UVs outside the 0-1
    range, flipped faces and zero-area faces (in the default UV set).

    Note: full UV overlap detection is intentionally out of scope here;
    it needs spatial acceleration to stay usable on production meshes.
    """

    result = {
        "missing_uvs": False,
        "uvs_out_of_range": False,
        "flipped_uv_faces": [],
        "zero_uv_faces": [],
    }

    if _safe_int(cmds.polyEvaluate(shape, uvcoord=True), 0) == 0:
        result["missing_uvs"] = True
        return result

    bbox2d = None

    try:
        bbox2d = cmds.polyEvaluate(shape, boundingBox2d=True)
    except Exception:
        bbox2d = None

    if bbox2d and len(bbox2d) == 2:
        (u_min, u_max), (v_min, v_max) = bbox2d

        if (u_min < -tolerance or v_min < -tolerance
                or u_max > 1.0 + tolerance or v_max > 1.0 + tolerance):
            result["uvs_out_of_range"] = True

    try:
        dag = _get_dag_path(shape)
        iterator = om.MItMeshPolygon(dag)
    except Exception:
        return result

    flipped = []
    zero_area = []

    while not iterator.isDone():
        face_index = iterator.index()

        try:
            if iterator.hasUVs():
                us, vs = iterator.getUVs()
                area = _uv_signed_area(us, vs)

                if abs(area) < 1e-9:
                    zero_area.append("{0}.f[{1}]".format(shape, face_index))
                elif area < 0.0:
                    flipped.append("{0}.f[{1}]".format(shape, face_index))
            else:
                result["missing_uvs"] = True
        except Exception:
            pass

        iterator.next()

    result["flipped_uv_faces"] = flipped
    result["zero_uv_faces"] = zero_area

    return result


def check_mesh_validation(
        obj: str,
        include_uv_checks: bool = True,
) -> Dict[str, object]:
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
        lamina_faces = []

    # Transform-level checks for game export
    scale = cmds.xform(obj, query=True, relative=True, scale=True)

    negative_scale = any(float(v) < 0.0 for v in scale)
    non_frozen_scale = any(abs(float(v) - 1.0) > 0.0001 for v in scale)

    # UV-level checks, aggregated across the object's shapes
    missing_uvs = False
    uvs_out_of_range = False
    flipped_uv_faces = []
    zero_uv_faces = []

    if include_uv_checks:
        for shape in get_mesh_shapes(obj):
            uv_result = check_shape_uvs(shape)

            missing_uvs = missing_uvs or bool(uv_result["missing_uvs"])
            uvs_out_of_range = (
                uvs_out_of_range or bool(uv_result["uvs_out_of_range"])
            )
            flipped_uv_faces.extend(uv_result["flipped_uv_faces"])
            zero_uv_faces.extend(uv_result["zero_uv_faces"])

    return {
        "object": obj,
        "non_manifold_edges": non_manifold_edges,
        "non_manifold_vertices": non_manifold_vertices,
        "lamina_faces": lamina_faces,
        "negative_scale": negative_scale,
        "non_frozen_scale": non_frozen_scale,
        "missing_uvs": missing_uvs,
        "uvs_out_of_range": uvs_out_of_range,
        "flipped_uv_faces": flipped_uv_faces,
        "zero_uv_faces": zero_uv_faces,
        "issue_count": (
            len(non_manifold_edges) + len(non_manifold_vertices) + len(lamina_faces)
            + int(negative_scale) + int(non_frozen_scale)
            + int(missing_uvs) + int(uvs_out_of_range)
            + len(flipped_uv_faces) + len(zero_uv_faces)
        ),
    }


def validate_selection(
        select_first_issue: bool = True,
        include_uv_checks: bool = True,
) -> Tuple[List[Dict[str, object]], str]:
    meshes = get_selected_mesh_transforms()

    if not meshes:
        raise RuntimeError("No mesh transforms selected")

    results = [
        check_mesh_validation(obj, include_uv_checks=include_uv_checks)
        for obj in meshes
    ]

    problem_results = [r for r in results if int(r["issue_count"]) > 0]

    if not problem_results:
        checked = "geometry and UV" if include_uv_checks else "geometry"

        return [], (
            "Clean: no {0} issues found on {1} object(s)".format(
                checked, len(meshes),
            )
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

        if r.get("missing_uvs"):
            flags.append("missing UVs")

        if r.get("uvs_out_of_range"):
            flags.append("UVs outside 0-1")

        if r.get("flipped_uv_faces"):
            flags.append(
                "flipped UVs: {0}".format(len(r["flipped_uv_faces"]))
            )

        if r.get("zero_uv_faces"):
            flags.append(
                "zero-area UVs: {0}".format(len(r["zero_uv_faces"]))
            )

        lines.append(
            "{0}: {1}".format(_short_name(str(r["object"])), ", ".join(flags))
        )

    if select_first_issue:
        first = problem_results[0]

        components = []
        components.extend(first["non_manifold_edges"])
        components.extend(first["non_manifold_vertices"])
        components.extend(first["lamina_faces"])
        components.extend(first.get("flipped_uv_faces", []))
        components.extend(first.get("zero_uv_faces", []))

        if components:
            cmds.select(components, replace=True)

            try:
                cmds.selectMode(component=True)
            except Exception:
                pass

        else:
            cmds.select(first["object"], replace=True)

    return problem_results, " | ".join(lines)

# ---
# Combine / export
# ---


@undoable
def combine_selected_meshes() -> Optional[str]:
    meshes = get_selected_mesh_transforms()

    if len(meshes) < 2:
        raise RuntimeError("Select at least two mesh transforms to combine")

    result = cmds.polyUnite(
        meshes, constructionHistory=False, mergeUVSets=True,
    )

    if not result:
        return None

    combined = result[0]

    cmds.delete(combined, constructionHistory=True)
    cmds.select(combined, replace=True)

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
        include_uv_checks: bool = True,
) -> str:
    meshes = get_selected_mesh_transforms()

    if not meshes:
        raise RuntimeError("No mesh transforms selected for export")

    export_folder = os.path.abspath(os.path.expanduser(export_folder))

    if not os.path.isdir(export_folder):
        raise RuntimeError("Invalid export folder: {0}".format(export_folder))

    if validate_before_export:
        problems, message = validate_selection(
            select_first_issue=False,
            include_uv_checks=include_uv_checks,
        )

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


def export_object_fbx(
        obj: str,
        export_folder: str,
        export_name: str,
        move_to_origin: bool = True,
        validate_before_export: bool = True,
        block_on_validation_errors: bool = True,
        include_uv_checks: bool = True,
) -> str:
    """
    Export a single object to its own FBX file, optionally moving its
    pivot to the world origin for the export and restoring the original
    position afterwards (the standard game-art batch export workflow).
    """

    original_selection = cmds.ls(selection=True, long=True) or []

    cmds.select(obj, replace=True)

    original_translation = None

    try:
        if move_to_origin:
            pivot = cmds.xform(
                obj, query=True, worldSpace=True, rotatePivot=True,
            )
            original_translation = cmds.xform(
                obj, query=True, worldSpace=True, translation=True,
            )

            cmds.xform(
                obj,
                worldSpace=True,
                translation=(
                    original_translation[0] - pivot[0],
                    original_translation[1] - pivot[1],
                    original_translation[2] - pivot[2],
                ),
            )

        return export_selected_fbx(
            export_folder,
            export_name,
            validate_before_export=validate_before_export,
            block_on_validation_errors=block_on_validation_errors,
            include_uv_checks=include_uv_checks,
        )

    finally:
        if move_to_origin and original_translation is not None:
            cmds.xform(
                obj, worldSpace=True, translation=original_translation,
            )

        if original_selection:
            try:
                cmds.select(original_selection, replace=True)
            except Exception:
                pass


# ---
# Texel density
# ---

def _object_area_totals(obj: str) -> Tuple[float, float]:
    """
    Total world-space surface area and UV area across an object's
    mesh shapes (default UV set).
    """

    total_world = 0.0
    total_uv = 0.0

    for shape in get_mesh_shapes(obj):
        try:
            dag = _get_dag_path(shape)
            iterator = om.MItMeshPolygon(dag)
        except Exception:
            continue

        while not iterator.isDone():
            try:
                total_world += iterator.getArea(om.MSpace.kWorld)

                if iterator.hasUVs():
                    us, vs = iterator.getUVs()
                    total_uv += abs(_uv_signed_area(us, vs))
            except Exception:
                pass

            iterator.next()

    return total_world, total_uv


def get_object_texel_density(obj: str, texture_size: int) -> Optional[float]:
    """
    Average texel density of an object in pixels per world unit:

        density = texture_size * sqrt(uv_area / world_area)
    """

    world_area, uv_area = _object_area_totals(obj)

    if world_area <= 0.0 or uv_area <= 0.0:
        return None

    return float(texture_size) * math.sqrt(uv_area / world_area)


def get_selection_texel_density(texture_size: int) -> Optional[float]:
    """
    Area-weighted average texel density across the current selection.
    """

    total_world = 0.0
    total_uv = 0.0

    for obj in get_selected_mesh_transforms():
        world_area, uv_area = _object_area_totals(obj)
        total_world += world_area
        total_uv += uv_area

    if total_world <= 0.0 or total_uv <= 0.0:
        return None

    return float(texture_size) * math.sqrt(total_uv / total_world)


@undoable
def set_selection_texel_density(
        target_density: float,
        texture_size: int,
) -> int:
    """
    Uniformly scale each selected object's UVs (around their UV
    bounding-box center) so its texel density matches the target.
    """

    meshes = get_selected_mesh_transforms()

    if not meshes:
        raise RuntimeError("No mesh transforms selected")

    if target_density <= 0.0:
        raise RuntimeError("Target texel density must be greater than zero")

    adjusted = 0

    for obj in meshes:
        current = get_object_texel_density(obj, texture_size)

        if not current or current <= 0.0:
            _status(
                "Skipped {0}: no UV or surface area".format(_short_name(obj)),
                warning=True,
            )
            continue

        factor = float(target_density) / current

        if abs(factor - 1.0) < 0.0001:
            continue

        for shape in get_mesh_shapes(obj):
            try:
                bbox2d = cmds.polyEvaluate(shape, boundingBox2d=True)
                (u_min, u_max), (v_min, v_max) = bbox2d
            except Exception:
                continue

            cmds.polyEditUV(
                shape + ".map[*]",
                pivotU=(u_min + u_max) * 0.5,
                pivotV=(v_min + v_max) * 0.5,
                scaleU=factor,
                scaleV=factor,
            )

        adjusted += 1

    return adjusted


# ---
# Rigging helpers
# ---

@undoable
def create_ik_with_pole_vector(pole_distance: float = 1.0) -> Tuple[str, str]:
    """
    Create an RP-solver IK handle between the first and last selected
    joints, with a pole vector locator placed on the chain's bend plane.

    Placement math: the middle joint is projected onto the start->end
    axis; the rejection vector (mid - projection) points away from the
    chain on its bend plane, which is exactly where the pole vector
    belongs to preserve the current bend direction.
    """

    joints = cmds.ls(selection=True, type="joint", long=True) or []

    if len(joints) < 2:
        raise RuntimeError(
            "Select the start joint and the end joint of the chain"
        )

    start = joints[0]
    end = joints[-1]

    # Walk up from the end joint to confirm it descends from the start
    chain = [end]
    current = end

    while True:
        parents = cmds.listRelatives(
            current, parent=True, fullPath=True, type="joint",
        ) or []

        if not parents:
            break

        current = parents[0]
        chain.append(current)

        if current == start:
            break

    if chain[-1] != start:
        raise RuntimeError(
            "The last selected joint must be a descendant of the first"
        )

    chain.reverse()  # start -> end

    if len(chain) < 3:
        raise RuntimeError(
            "The chain needs at least one middle joint for a pole vector"
        )

    positions = [
        om.MVector(
            *cmds.xform(j, query=True, worldSpace=True, rotatePivot=True)
        )
        for j in chain
    ]

    start_pos = positions[0]
    end_pos = positions[-1]
    mid_pos = positions[len(positions) // 2]

    axis = end_pos - start_pos
    to_mid = mid_pos - start_pos

    if axis.length() < 1e-6:
        raise RuntimeError("Start and end joints are at the same position")

    axis_normal = axis.normal()

    # Vector rejection: component of to_mid perpendicular to the chain axis
    projection = axis_normal * (to_mid * axis_normal)
    pole_direction = to_mid - projection

    chain_length = sum(
        (positions[i + 1] - positions[i]).length()
        for i in range(len(positions) - 1)
    )

    if pole_direction.length() < 1e-6:
        # Perfectly straight chain: any perpendicular works, so warn
        fallback = om.MVector(0.0, 0.0, 1.0)

        if abs(axis_normal * fallback) > 0.999:
            fallback = om.MVector(0.0, 1.0, 0.0)

        pole_direction = axis_normal ^ fallback

        _status(
            "Chain is straight; pole vector direction is arbitrary",
            warning=True,
        )

    pole_position = (
        mid_pos
        + pole_direction.normal() * (chain_length * 0.5 * float(pole_distance))
    )

    base_name = _strip_namespace(_short_name(chain[0]))

    handle, _effector = cmds.ikHandle(
        startJoint=chain[0],
        endEffector=chain[-1],
        solver="ikRPsolver",
        name="IK_{0}".format(base_name),
    )

    locator = cmds.spaceLocator(name="PV_{0}".format(base_name))[0]

    cmds.xform(
        locator,
        worldSpace=True,
        translation=(pole_position.x, pole_position.y, pole_position.z),
    )

    cmds.poleVectorConstraint(locator, handle)
    cmds.select(handle, replace=True)

    return handle, locator


@undoable
def create_muscle_helper(
        rotate_axis: str = "Z",
        max_angle: float = 90.0,
        bulge_scale: float = 1.4,
) -> str:
    """
    Create a pose-driven 'muscle' joint between two selected joints --
    the classic bicep setup.

    Select the upper joint (e.g. shoulder), then the bend joint
    (e.g. elbow). A helper joint is parented under the upper joint at
    the muscle position, and set-driven keys make it bulge as the bend
    joint rotates. Keys are set at both +max_angle and -max_angle so
    the rig's bend direction does not matter.

    Assumes the bend joint's rotation is zero in the rest pose. To see
    the bulge on a mesh, add the helper joint to the skinCluster and
    paint its weights over the muscle area.
    """

    joints = cmds.ls(selection=True, type="joint", long=True) or []

    if len(joints) != 2:
        raise RuntimeError(
            "Select exactly two joints: the upper joint, then the bend joint"
        )

    upper, bend = joints
    axis = str(rotate_axis).upper()

    if axis not in ("X", "Y", "Z"):
        raise RuntimeError("Rotate axis must be X, Y or Z")

    upper_pos = om.MVector(
        *cmds.xform(upper, query=True, worldSpace=True, rotatePivot=True)
    )
    bend_pos = om.MVector(
        *cmds.xform(bend, query=True, worldSpace=True, rotatePivot=True)
    )

    muscle_pos = (upper_pos + bend_pos) * 0.5

    base_name = _strip_namespace(_short_name(upper))

    cmds.select(clear=True)

    helper = cmds.joint(name="MUSCLE_{0}".format(base_name))
    helper = cmds.parent(helper, upper)[0]
    helper = _long_name(helper)

    cmds.xform(
        helper,
        worldSpace=True,
        translation=(muscle_pos.x, muscle_pos.y, muscle_pos.z),
    )

    driver_attribute = "{0}.rotate{1}".format(bend, axis)

    for scale_axis in ("X", "Y", "Z"):
        driven_attribute = "{0}.scale{1}".format(helper, scale_axis)

        cmds.setDrivenKeyframe(
            driven_attribute,
            currentDriver=driver_attribute,
            driverValue=0.0,
            value=1.0,
        )
        cmds.setDrivenKeyframe(
            driven_attribute,
            currentDriver=driver_attribute,
            driverValue=float(max_angle),
            value=float(bulge_scale),
        )
        cmds.setDrivenKeyframe(
            driven_attribute,
            currentDriver=driver_attribute,
            driverValue=-float(max_angle),
            value=float(bulge_scale),
        )

    cmds.select(helper, replace=True)

    return helper


# ---
# UI
# ---


# Controls whose values persist between Maya sessions via optionVar.
# "allow_combine" is deliberately excluded: destructive toggles should
# always reset to off.
_PERSISTED_CONTROLS = (
    ("rs_prefix", "textFieldGrp", "text"),
    ("rs_name", "textFieldGrp", "text"),
    ("budget_mode", "optionMenuGrp", "value"),
    ("budget", "intFieldGrp", "value1"),
    ("margin", "floatFieldGrp", "value1"),
    ("mat_splits", "checkBox", "value"),
    ("auto_prefix", "checkBox", "value"),
    ("lp_prefix", "textFieldGrp", "text"),
    ("hp_prefix", "textFieldGrp", "text"),
    ("select_issue", "checkBox", "value"),
    ("uv_checks", "checkBox", "value"),
    ("export_path", "textFieldButtonGrp", "text"),
    ("validate_export", "checkBox", "value"),
    ("block_export", "checkBox", "value"),
    ("batch_mode", "checkBox", "value"),
    ("move_origin", "checkBox", "value"),
    ("td_size", "intFieldGrp", "value1"),
    ("td_target", "floatFieldGrp", "value1"),
    ("pole_mult", "floatFieldGrp", "value1"),
    ("muscle_axis", "optionMenuGrp", "value"),
    ("muscle_angle", "floatFieldGrp", "value1"),
    ("muscle_bulge", "floatFieldGrp", "value1"),
)


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
            title=WINDOW_TITLE,
            sizeable=True,
            widthHeight=(430, 780),
        )

        scroll = cmds.scrollLayout(childResizable=True)

        root = cmds.columnLayout(
            adjustableColumn=True,
            rowSpacing=8,
            parent=scroll,
        )

        cmds.text(
            label="JAM TA Tools",
            align="center",
            height=28,
            font="boldLabelFont",
        )

        cmds.separator(style="in")

        self._build_rename_section(root)
        self._build_budget_section(root)
        self._build_pivot_section(root)
        self._build_validation_section(root)
        self._build_export_section(root)
        self._build_texel_density_section(root)
        self._build_rigging_section(root)
        self._build_status_section(root)

        self._apply_saved_settings()

        # Save settings when the window is closed
        cmds.scriptJob(
            uiDeleted=[WINDOW_NAME, self.save_settings],
            runOnce=True,
        )

        cmds.showWindow(window)

        self.refresh_budget_marker()

    # --- UI BUILDERS ---

    def _build_rename_section(self, parent: str) -> None:
        frame = cmds.frameLayout(
            label="Rename Objects",
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

        self.controls["rs_prefix"] = cmds.textFieldGrp(
            label="Prefix",
            text="SM",
            columnWidth=[(1, 90), (2, 280),],
            parent=col,
        )

        self.controls["rs_name"] = cmds.textFieldGrp(
            label="Item name",
            text="Prop",
            columnWidth=[(1, 90), (2, 280),],
            parent=col,
        )

        self.controls["rs_start"] = cmds.intFieldGrp(
            label="Start index",
            value1=1,
            columnWidth=[(1, 90), (2, 80),],
            parent=col,
        )

        self.controls["rs_shapes"] = cmds.checkBox(
            label="Rename shape nodes too?",
            value=True,
            parent=col,
        )

        cmds.button(
            label="Rename Selected",
            command=lambda *_: self.on_rename_selected(),
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
            command=lambda *_: self.on_pivot_to_selected_vertex(),
            parent=col
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

        self.controls["uv_checks"] = cmds.checkBox(
            label="Include UV checks (missing, 0-1 range, flipped)",
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

        self.controls["batch_mode"] = cmds.checkBox(
            label="Batch: one FBX per selected object",
            value=False,
            parent=col,
        )

        self.controls["move_origin"] = cmds.checkBox(
            label="Move to origin on export (batch only)",
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

    def _build_texel_density_section(self, parent: str) -> None:
        frame = cmds.frameLayout(
            label="Texel Density",
            collapsable=True,
            collapse=True,
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
            label="Density is measured in pixels per world unit.",
            align="left",
            parent=col,
        )

        self.controls["td_size"] = cmds.intFieldGrp(
            label="Texture size",
            value1=2048,
            columnWidth=[(1, 90), (2, 100)],
            parent=col,
        )

        self.controls["td_target"] = cmds.floatFieldGrp(
            label="Target TD",
            value1=10.24,
            precision=3,
            columnWidth=[(1, 90), (2, 100)],
            parent=col,
        )

        row = cmds.rowLayout(
            numberOfColumns=2,
            adjustableColumn=2,
            columnWidth2=(200, 200),
            parent=col,
        )

        cmds.button(
            label="Check Selection TD",
            command=lambda *_: self.on_check_texel_density(),
            parent=row,
        )

        cmds.button(
            label="Set Selection TD",
            command=lambda *_: self.on_set_texel_density(),
            parent=row,
        )

        cmds.setParent(col)

        self.controls["td_result"] = cmds.text(
            label="",
            align="left",
            parent=col,
        )

    def _build_rigging_section(self, parent: str) -> None:
        frame = cmds.frameLayout(
            label="Rigging Helpers",
            collapsable=True,
            collapse=True,
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
            label="IK: select the start joint, then the end joint.",
            align="left",
            parent=col,
        )

        self.controls["pole_mult"] = cmds.floatFieldGrp(
            label="Pole distance",
            value1=1.0,
            precision=2,
            columnWidth=[(1, 90), (2, 100)],
            parent=col,
        )

        cmds.button(
            label="Create IK Handle + Pole Vector",
            command=lambda *_: self.on_create_ik(),
            parent=col,
        )

        cmds.separator(style="in", parent=col)

        cmds.text(
            label="Muscle: select the upper joint, then the bend joint.",
            align="left",
            parent=col,
        )

        self.controls["muscle_axis"] = cmds.optionMenuGrp(
            label="Bend axis",
            columnWidth=[(1, 90), (2, 100)],
            parent=col,
        )

        for item in ("X", "Y", "Z"):
            cmds.menuItem(label=item)

        cmds.optionMenuGrp(
            self.controls["muscle_axis"],
            edit=True,
            value="Z",
        )

        self.controls["muscle_angle"] = cmds.floatFieldGrp(
            label="Max angle",
            value1=90.0,
            precision=1,
            columnWidth=[(1, 90), (2, 100)],
            parent=col,
        )

        self.controls["muscle_bulge"] = cmds.floatFieldGrp(
            label="Bulge scale",
            value1=1.4,
            precision=2,
            columnWidth=[(1, 90), (2, 100)],
            parent=col,
        )

        cmds.button(
            label="Create Muscle Helper Joint",
            command=lambda *_: self.on_create_muscle(),
            parent=col,
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

    # ---------------- Settings persistence ----------------

    def save_settings(self) -> None:
        for key, control_type, flag in _PERSISTED_CONTROLS:
            control = self.controls.get(key)
            command = getattr(cmds, control_type, None)

            if not control or command is None:
                continue

            try:
                if not command(control, exists=True):
                    continue

                value = command(control, query=True, **{flag: True})
            except Exception:
                continue

            _save_option(key, value)

    def _apply_saved_settings(self) -> None:
        for key, control_type, flag in _PERSISTED_CONTROLS:
            raw = _load_option(key, "")

            if raw == "":
                continue

            control = self.controls.get(key)
            command = getattr(cmds, control_type, None)

            if not control or command is None:
                continue

            try:
                if control_type == "checkBox":
                    command(control, edit=True, value=raw in ("1", "True"))
                elif control_type == "intFieldGrp":
                    command(control, edit=True, value1=int(float(raw)))
                elif control_type == "floatFieldGrp":
                    command(control, edit=True, value1=float(raw))
                else:
                    # textFieldGrp / textFieldButtonGrp / optionMenuGrp;
                    # optionMenuGrp raises if the saved value is no longer
                    # a menu item, which the except silently absorbs
                    command(control, edit=True, **{flag: raw})
            except Exception:
                pass

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

            include_uv = cmds.checkBox(
                self.controls["uv_checks"],
                query=True,
                value=True,
            )

            problems, message = validate_selection(
                select_first_issue=select_first,
                include_uv_checks=include_uv,
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

            mode = self.get_count_mode()
            count_splits = self.get_count_material_splits()

            auto_prefix = cmds.checkBox(
                self.controls["auto_prefix"], query=True, value=True,
            )
            batch_mode = cmds.checkBox(
                self.controls["batch_mode"], query=True, value=True,
            )
            move_origin = cmds.checkBox(
                self.controls["move_origin"], query=True, value=True,
            )
            include_uv = cmds.checkBox(
                self.controls["uv_checks"], query=True, value=True,
            )
            validate = cmds.checkBox(
                self.controls["validate_export"], query=True, value=True,
            )
            block = cmds.checkBox(
                self.controls["block_export"], query=True, value=True,
            )

            export_folder = cmds.textFieldButtonGrp(
                self.controls["export_path"], query=True, text=True,
            )

            if batch_mode:
                exported = []

                for obj in meshes:
                    count = get_mesh_metric(
                        obj, mode, count_material_splits=count_splits,
                    )

                    filepath = export_object_fbx(
                        obj,
                        export_folder,
                        self._export_name_for(obj, count, auto_prefix),
                        move_to_origin=move_origin,
                        validate_before_export=validate,
                        block_on_validation_errors=block,
                        include_uv_checks=include_uv,
                    )

                    exported.append(filepath)

                self.set_status(
                    "Batch exported {0} file(s) to {1}".format(
                        len(exported), export_folder,
                    )
                )

            else:
                active = get_active_mesh_transform() or meshes[0]

                count = get_selection_metric(
                    mode, count_material_splits=count_splits,
                )

                filepath = export_selected_fbx(
                    export_folder,
                    self._export_name_for(active, count, auto_prefix),
                    validate_before_export=validate,
                    block_on_validation_errors=block,
                    include_uv_checks=include_uv,
                )

                self.set_status(
                    "Exported {0} with {1:,} {2}.".format(
                        os.path.basename(filepath), count, mode.lower(),
                    )
                )

            self.save_settings()

        except Exception as exc:
            self.set_status(
                "Export failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

    def _export_name_for(self, obj: str, count: int, auto_prefix: bool) -> str:
        base_name = _short_name(obj)

        if not auto_prefix:
            return _strip_namespace(base_name)

        export_name, _classification = export_name_with_prefix(
            base_name,
            count,
            self.get_budget(),
            self.get_margin(),
            self.get_lp_prefix(),
            self.get_hp_prefix(),
        )

        return export_name

    def on_check_texel_density(self) -> None:
        try:
            texture_size = cmds.intFieldGrp(
                self.controls["td_size"], query=True, value1=True,
            )

            density = get_selection_texel_density(texture_size)

            if density is None:
                message = "TD: no UV or surface area found on selection"
            else:
                message = "TD: {0:.3f} px/unit at {1}px textures".format(
                    density, texture_size,
                )

            cmds.text(
                self.controls["td_result"],
                edit=True,
                label=message,
            )

            self.set_status(message)

        except Exception as exc:
            self.set_status(
                "Texel density check failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

    def on_set_texel_density(self) -> None:
        try:
            texture_size = cmds.intFieldGrp(
                self.controls["td_size"], query=True, value1=True,
            )
            target = cmds.floatFieldGrp(
                self.controls["td_target"], query=True, value1=True,
            )

            adjusted = set_selection_texel_density(target, texture_size)

            self.set_status(
                "Adjusted texel density on {0} object(s).".format(adjusted)
            )

            self.on_check_texel_density()

        except Exception as exc:
            self.set_status(
                "Set texel density failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

    def on_create_ik(self) -> None:
        try:
            multiplier = cmds.floatFieldGrp(
                self.controls["pole_mult"], query=True, value1=True,
            )

            handle, locator = create_ik_with_pole_vector(
                pole_distance=multiplier,
            )

            self.set_status(
                "Created {0} with pole vector {1}.".format(
                    _short_name(handle), _short_name(locator),
                )
            )

        except Exception as exc:
            self.set_status(
                "Create IK failed: {0}".format(exc)
            )
            cmds.warning(str(exc))

    def on_create_muscle(self) -> None:
        try:
            axis = cmds.optionMenuGrp(
                self.controls["muscle_axis"], query=True, value=True,
            )
            max_angle = cmds.floatFieldGrp(
                self.controls["muscle_angle"], query=True, value1=True,
            )
            bulge = cmds.floatFieldGrp(
                self.controls["muscle_bulge"], query=True, value1=True,
            )

            helper = create_muscle_helper(
                rotate_axis=axis,
                max_angle=max_angle,
                bulge_scale=bulge,
            )

            self.set_status(
                "Created {0}. Add it to the skinCluster and paint "
                "weights to see the bulge.".format(_short_name(helper))
            )

        except Exception as exc:
            self.set_status(
                "Create muscle helper failed: {0}".format(exc)
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
