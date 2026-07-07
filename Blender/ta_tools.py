bl_info = {
    "name": "JAM TA Tools",
    "blender": (2, 80, 0),
    "category": "Object",
    "version": (1, 1, 0),
    "author": "Juan Abia Merino",
    "description": (
        "Game-art TA toolkit: batch rename, poly budgets with engine-vertex "
        "estimation, origin tools, mesh/UV validation, texel density, planar "
        "UV projection, batch FBX export and rigging helpers"
    ),
}

import json
import math
import os

import bpy
import bmesh
from mathutils import Vector


def get_extreme_vertex(obj, axis_str):
    """
    Finds the axis-most vertex of the given axis (+-X, +-Y, +-Z) or the mid point
    """

    if not obj.data.vertices:
        return obj.matrix_world.translation.copy()

    bpy.ops.object.mode_set(mode="OBJECT")

    if axis_str == "MID":
        local_coords = [v.co for v in obj.data.vertices]
        median_co = sum(local_coords, Vector()) / len(local_coords)

        return obj.matrix_world @ median_co

    axis_index = {'X': 0, 'Y': 1, 'Z': 2}[axis_str[-1]]
    find_max = not axis_str.startswith('-')

    coords = [v.co[axis_index] for v in obj.data.vertices]
    target_value = max(coords) if find_max else min(coords)
    target_index = coords.index(target_value)
    vertex = obj.data.vertices[target_index]

    return obj.matrix_world @ vertex.co


def get_selection_extreme_vertex(objs, axis_str):
    mesh_objs = [obj for obj in objs if
                 obj.type == "MESH" and obj.data.vertices]

    if not mesh_objs:
        return None

    if axis_str == "MID":
        coords = []

        for obj in mesh_objs:
            for vert in obj.data.vertices:
                coords.append(obj.matrix_world @ vert.co)

        return sum(coords, Vector()) / len(coords)

    axis_index = {'X': 0, 'Y': 1, 'Z': 2}[axis_str[-1]]
    find_max = not axis_str.startswith('-')

    extreme_points = [
        get_extreme_vertex(obj, axis_str)
        for obj in mesh_objs
    ]

    if find_max:
        return max(extreme_points, key=lambda co: co[axis_index])

    return min(extreme_points, key=lambda co: co[axis_index])


def update_axis(self, context):
    # This function just forces the UI redraw
    pass


def get_mesh_metrics(obj, context, mode):
    """
    Returns VERTS, FACES, or TRIS count.
    Uses evaluated mesh when ee_count_modifiers is enabled
    """

    if obj is None or obj.type != "MESH":
        return 0

    if mode == "ENGINE":
        return get_engine_vertex_estimate(obj, context)

    scene = context.scene

    if scene.ee_count_modifiers:
        graph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(graph)
        mesh = eval_obj.to_mesh()
        should_clear = True
    else:
        mesh = obj.data
        eval_obj = None
        should_clear = False

    try:
        if mesh is None:
            return 0

        if mode == "VERTS":
            return len(mesh.vertices)

        if mode == "FACES":
            return len(mesh.polygons)

        mesh.calc_loop_triangles()
        return len(mesh.loop_triangles)

    finally:
        if should_clear and eval_obj:
            if hasattr(eval_obj, "to_mesh_clear"):
                eval_obj.to_mesh_clear()
            else:
                bpy.data.meshes.remove(mesh)


def get_rounded_tuple(values, precision=6):
    return tuple(round(v, precision) for v in values)


def get_loop_normal(mesh, loop_index):
    """
    Gets a per-corner normal in a way that works across Blender versions.
    """

    if hasattr(mesh, "corner_normals") and len(mesh.corner_normals) > loop_index:
        normal = mesh.corner_normals[loop_index].vector
        return get_rounded_tuple(normal)

    loop = mesh.loops[loop_index]
    return get_rounded_tuple(loop.normal)


def get_valid_uv_layers(mesh):
    """
    Returns only UV layers whose data length matches the mesh loop count.
    Prevents uv_layer.data[loop_index] out-of-range errors.
    """
    loop_count = len(mesh.loops)

    valid_layers = []

    for uv_layer in mesh.uv_layers:
        if len(uv_layer.data) == loop_count:
            valid_layers.append(uv_layer)

    return valid_layers


def get_engine_vertex_estimate(obj, context):
    """
    Estimates engine-side render vertices.

    This catches common vertex splits caused by:
    - UV seams
    - Split normals / Hard edges
    - Multiple UV channels
    - Material boundaries (optional)
    """

    if obj is None or obj.type != "MESH":
        return 0

    scene = context.scene

    if scene.ee_count_modifiers:
        graph = context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(graph)
        mesh = eval_obj.to_mesh()
        should_clear = True
    else:
        if obj.mode == "EDIT":
            try:
                obj.update_from_editmode()
            except Exception:
                pass

        mesh = obj.data
        eval_obj = None
        should_clear = False

    try:
        if mesh is None:
            return 0

        mesh.calc_loop_triangles()
        if hasattr(mesh, "calc_normals_split"):
            mesh.calc_normals_split()

        uv_layers = get_valid_uv_layers(mesh)

        unique_render_vertices = set()

        for poly in mesh.polygons:
            material_index = poly.material_index

            for loop_index in poly.loop_indices:
                loop = mesh.loops[loop_index]

                key = [
                    loop.vertex_index,
                    get_loop_normal(mesh, loop_index),
                ]

                for uv_layer in uv_layers:
                    if loop_index >= len(uv_layer.data):
                        continue

                    uv = uv_layer.data[loop_index].uv
                    key.append(get_rounded_tuple((uv.x, uv.y)))

                if scene.ee_count_material_splits:
                    key.append(material_index)

                unique_render_vertices.add(tuple(key))

        return len(unique_render_vertices)

    finally:
        if should_clear and eval_obj:
            if hasattr(eval_obj, "to_mesh_clear"):
                eval_obj.to_mesh_clear()
            else:
                bpy.data.meshes.remove(mesh)


def get_selected_meshes(context):
    return [obj for obj in context.selected_objects if obj.type == "MESH"]


def get_selection_metric(context):
    scene = context.scene
    mesh_objects = get_selected_meshes(context)

    return sum(
        get_mesh_metrics(obj, context, scene.ee_budget_count_mode)
        for obj in mesh_objects
    )


def get_poly_classification(count, scene):
    budget = max(scene.ee_poly_budget, 1)
    margin = int(budget * (scene.ee_poly_margin / 100.0))

    warning_start = max(0, budget - margin)
    high_limit = budget + margin

    if count > high_limit:
        return {
            "prefix": scene.ee_hp_prefix,
            "state": "HIGH",
            "label": "High",
            "icon": "CANCEL",
            "limit": high_limit,
        }

    if count >= warning_start:
        return {
            "prefix": scene.ee_lp_prefix,
            "state": "MARGIN",
            "label": "Near budget",
            "icon": "ERROR",
            "limit": high_limit,
        }

    return {
        "prefix": scene.ee_lp_prefix,
        "state": "LOW",
        "label": "Low",
        "icon": "CHECKMARK",
        "limit": high_limit,
    }


def remove_existing_poly_prefix(name, prefixes):
    for prefix in prefixes:
        if not prefix:
            continue

        token = prefix + "_"

        if name.startswith(token):
            return name[len(token):]

    return name


def get_export_name_with_prefix(context, base_name):
    scene = context.scene
    count = get_selection_metric(context)
    classification = get_poly_classification(count, scene)

    clean_name = remove_existing_poly_prefix(
        base_name,
        [scene.ee_lp_prefix, scene.ee_hp_prefix]
    )

    export_name = f"{classification['prefix']}_{clean_name}"

    return export_name, count, classification


def get_object_export_name(context, obj):
    """
    Export name for a single object, with LP/HP prefix classification
    based on that object's own count (used by batch export).
    """
    scene = context.scene

    if not scene.ee_auto_poly_prefix:
        return obj.name

    count = get_mesh_metrics(obj, context, scene.ee_budget_count_mode)
    classification = get_poly_classification(count, scene)

    clean_name = remove_existing_poly_prefix(
        obj.name,
        [scene.ee_lp_prefix, scene.ee_hp_prefix]
    )

    return f"{classification['prefix']}_{clean_name}"


def get_budget_count_label(scene):
    return {
        "ENGINE": "engine verts",
        "TRIS": "tris",
        "FACES": "faces",
        "VERTS": "verts",
    }.get(scene.ee_budget_count_mode, "items")


def get_budget_signature(context):
    """
    Used only to warn that the cached count may be stale.
    Checks selection and count settings, not mesh changes.
    """
    scene = context.scene
    selected_meshes = get_selected_meshes(context)

    object_part = "|".join(
        f"{obj.name}:{obj.as_pointer()}:{obj.data.as_pointer()}"
        for obj in selected_meshes
    )

    return "|".join([
        scene.ee_budget_count_mode,
        str(scene.ee_count_modifiers),
        str(scene.ee_count_material_splits),
        object_part,
    ])


def draw_budget_marker(layout, context):
    scene = context.scene
    mesh_objects = get_selected_meshes(context)

    row = layout.row(align=True)
    row.scale_y = 0.75

    if not mesh_objects:
        row.label(text="Poly budget: No mesh selected", icon="INFO")
        return

    if not scene.ee_budget_cached_valid:
        row.label(text="Poly budget: Not calculated", icon="INFO")
        return

    cached_signature = get_budget_signature(context)
    is_stale = cached_signature != scene.ee_budget_cached_signature

    count = scene.ee_budget_cached_count
    classification = get_poly_classification(count, scene)
    count_label = get_budget_count_label(scene)

    if classification["state"] == "HIGH":
        row.alert = True

    if is_stale:
        row.alert = True
        row.label(
            text=(
                f"Budget may be stale · "
                f"{count:,}/{classification['limit']:,} {count_label} · "
                f"Press Calculate"
            ),
            icon="FILE_REFRESH"
        )
        return

    row.label(
        text=(
            f"{classification['label']} · "
            f"{count:,}/{classification['limit']:,} {count_label} · "
            f"Export as {classification['prefix']}_"
        ),
        icon=classification["icon"]
    )


def get_non_manifold_edges(obj):
    """
    Returns non-manifold edge data for a mesh object.

    Handled categories:
    - wire: edge with no faces
    - boundary: edge has just one face
    - multiface: edge has more than two faces
    - non_contiguous: edge has two faces but is still not manifold
      (weird, but can happen)
    """

    if obj is None or obj.type != "MESH":
        return None

    mesh = obj.data

    bm = bmesh.new()
    bm.from_mesh(mesh)

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.edges.index_update()

    bad_edges = []
    wire_edges = []
    boundary_edges = []
    multiface_edges = []
    non_contiguous_edges = []

    try:
        for edge in bm.edges:
            face_count = len(edge.link_faces)

            if edge.is_manifold:
                continue

            bad_edges.append(edge.index)

            if face_count == 0:
                wire_edges.append(edge.index)
            elif face_count == 1:
                boundary_edges.append(edge.index)
            elif face_count > 2:
                multiface_edges.append(edge.index)
            else:
                non_contiguous_edges.append(edge.index)

        return {
            "bad_edges": bad_edges,
            "wire_edges": wire_edges,
            "boundary_edges": boundary_edges,
            "multiface_edges": multiface_edges,
            "non_contiguous_edges": non_contiguous_edges,
        }
    finally:
        bm.free()


def ta_get_edit_mesh_objects(context):
    """
    Returns mesh objects in edit mode.
    Supports multi-object edit mode when available.
    """
    if context.mode != "EDIT_MESH":
        return []

    objects = getattr(context, "objects_in_mode_unique_data", None)

    if objects:
        return [obj for obj in objects if obj and obj.type == "MESH"]

    if context.active_object and context.active_object.type == "MESH":
        return [context.active_object]

    return []


def ta_get_bmesh_uv_layer(bm, uv_map_name):
    """
    Gets or creates a BMesh UV layer.
    """
    uv_map_name = uv_map_name.strip()

    if uv_map_name:
        uv_layer = bm.loops.layers.uv.get(uv_map_name)

        if uv_layer is None:
            uv_layer = bm.loops.layers.uv.new(uv_map_name)

        return uv_layer

    return bm.loops.layers.uv.verify()


def ta_get_selected_face_islands(faces):
    """
    Splits selected faces into connected face islands.
    This is geometry-island based, not existing UV-island based.
    """
    selected = set(faces)
    visited = set()
    islands = []

    for start_face in faces:
        if start_face in visited:
            continue

        island = []
        stack = [start_face]
        visited.add(start_face)

        while stack:
            face = stack.pop()
            island.append(face)

            for edge in face.edges:
                for linked_face in edge.link_faces:
                    if linked_face in selected and linked_face not in visited:
                        visited.add(linked_face)
                        stack.append(linked_face)

        islands.append(island)

    return islands


def ta_get_average_face_normal(obj, faces, space):
    """
    Area-weighted average normal for selected faces.
    Used by the Normal / Best Fit projection.
    """
    normal = Vector((0.0, 0.0, 0.0))

    normal_matrix = None

    if space == "WORLD":
        normal_matrix = obj.matrix_world.to_3x3().inverted().transposed()

    for face in faces:
        face_normal = face.normal.copy()

        if space == "WORLD":
            face_normal = normal_matrix @ face_normal

        if face_normal.length_squared == 0.0:
            continue

        face_normal.normalize()
        normal += face_normal * max(face.calc_area(), 0.000001)

    if normal.length_squared == 0.0:
        return Vector((0.0, 0.0, 1.0))

    normal.normalize()
    return normal


def ta_get_basis_from_normal(normal):
    """
    Builds stable U/V projection axes from a normal.
    """
    normal = normal.normalized()

    reference = Vector((0.0, 0.0, 1.0))

    if abs(normal.dot(reference)) > 0.95:
        reference = Vector((0.0, 1.0, 0.0))

    u_axis = reference.cross(normal)

    if u_axis.length_squared == 0.0:
        u_axis = Vector((1.0, 0.0, 0.0))
    else:
        u_axis.normalize()

    v_axis = normal.cross(u_axis)

    if v_axis.length_squared == 0.0:
        v_axis = Vector((0.0, 1.0, 0.0))
    else:
        v_axis.normalize()

    return u_axis, v_axis


def ta_get_projection_basis(obj, faces, projection_mode, space):
    """
    Returns U and V axes for planar projection.
    """
    if projection_mode == "XY":
        return Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0))

    if projection_mode == "XZ":
        return Vector((1.0, 0.0, 0.0)), Vector((0.0, 0.0, 1.0))

    if projection_mode == "YZ":
        return Vector((0.0, 1.0, 0.0)), Vector((0.0, 0.0, 1.0))

    normal = ta_get_average_face_normal(obj, faces, space)
    return ta_get_basis_from_normal(normal)


def ta_get_loop_coord(obj, loop, space):
    co = loop.vert.co.copy()

    if space == "WORLD":
        return obj.matrix_world @ co

    return co


def ta_normalize_projected_uv(u, v, bounds, preserve_aspect, padding):
    min_u, max_u, min_v, max_v = bounds

    span_u = max(max_u - min_u, 0.000001)
    span_v = max(max_v - min_v, 0.000001)

    if preserve_aspect:
        size = max(span_u, span_v)

        center_u = (min_u + max_u) * 0.5
        center_v = (min_v + max_v) * 0.5

        min_u = center_u - size * 0.5
        min_v = center_v - size * 0.5

        span_u = size
        span_v = size

    final_u = (u - min_u) / span_u
    final_v = (v - min_v) / span_v

    padding = max(0.0, min(padding, 0.49))
    usable_space = 1.0 - padding * 2.0

    final_u = padding + final_u * usable_space
    final_v = padding + final_v * usable_space

    return final_u, final_v


def ta_apply_uv_post_transform(u, v, rotation, flip_u, flip_v):
    """
    Applies simple DCC-style UV post transforms.
    """
    if rotation == "90":
        u, v = v, 1.0 - u
    elif rotation == "180":
        u, v = 1.0 - u, 1.0 - v
    elif rotation == "270":
        u, v = 1.0 - v, u

    if flip_u:
        u = 1.0 - u

    if flip_v:
        v = 1.0 - v

    return u, v


def ta_project_faces_to_uv(
    obj,
    faces,
    uv_layer,
    projection_mode,
    space,
    preserve_aspect,
    padding,
    rotation,
    flip_u,
    flip_v
):
    """
    Projects a group of selected faces into UV space.
    """
    if not faces:
        return 0

    u_axis, v_axis = ta_get_projection_basis(
        obj,
        faces,
        projection_mode,
        space
    )

    projected_loops = []

    min_u = float("inf")
    max_u = float("-inf")
    min_v = float("inf")
    max_v = float("-inf")

    for face in faces:
        for loop in face.loops:
            co = ta_get_loop_coord(obj, loop, space)

            raw_u = co.dot(u_axis)
            raw_v = co.dot(v_axis)

            projected_loops.append((loop, raw_u, raw_v))

            min_u = min(min_u, raw_u)
            max_u = max(max_u, raw_u)
            min_v = min(min_v, raw_v)
            max_v = max(max_v, raw_v)

    bounds = min_u, max_u, min_v, max_v

    for loop, raw_u, raw_v in projected_loops:
        final_u, final_v = ta_normalize_projected_uv(
            raw_u,
            raw_v,
            bounds,
            preserve_aspect,
            padding
        )

        final_u, final_v = ta_apply_uv_post_transform(
            final_u,
            final_v,
            rotation,
            flip_u,
            flip_v
        )

        loop[uv_layer].uv = (final_u, final_v)

    return len(faces)


# ---
# Settings persistence (JSON defaults shared across sessions/files)
# ---

# Scene properties saved by "Save Defaults" and restored by "Apply
# Defaults". "ee_unify" is deliberately excluded: destructive toggles
# should always reset to off.
TA_PERSISTED_PROPS = (
    "rs_prefix",
    "rs_name",
    "ee_poly_budget",
    "ee_poly_margin",
    "ee_budget_count_mode",
    "ee_count_material_splits",
    "ee_count_modifiers",
    "ee_auto_poly_prefix",
    "ee_lp_prefix",
    "ee_hp_prefix",
    "ee_export_path",
    "ee_validate_export",
    "ee_block_export",
    "ee_batch_export",
    "ee_move_to_origin",
    "ta_uv_checks",
    "ta_uv_map_name",
    "ta_uv_projection_mode",
    "ta_uv_projection_space",
    "ta_uv_fit_mode",
    "ta_uv_preserve_aspect",
    "ta_uv_padding",
    "ta_td_texture_size",
    "ta_td_target",
    "ta_ik_chain_count",
    "ta_pole_distance",
    "ta_muscle_axis",
    "ta_muscle_max_angle",
    "ta_muscle_bulge",
)


def ta_get_config_path():
    """
    Returns the path of the JSON file that stores cross-session defaults,
    inside Blender's per-user config folder.
    """
    config_dir = bpy.utils.user_resource("CONFIG")

    return os.path.join(config_dir, "jam_ta_tools.json")


def ta_save_defaults(scene):
    data = {}

    for prop_name in TA_PERSISTED_PROPS:
        if hasattr(scene, prop_name):
            data[prop_name] = getattr(scene, prop_name)

    config_path = ta_get_config_path()

    with open(config_path, "w", encoding="utf-8") as config_file:
        json.dump(data, config_file, indent=2)

    return config_path


def ta_apply_defaults(scene):
    config_path = ta_get_config_path()

    if not os.path.isfile(config_path):
        return 0

    with open(config_path, "r", encoding="utf-8") as config_file:
        data = json.load(config_file)

    applied = 0

    for prop_name, value in data.items():
        if prop_name not in TA_PERSISTED_PROPS:
            continue

        if not hasattr(scene, prop_name):
            continue

        try:
            setattr(scene, prop_name, value)
            applied += 1
        except (TypeError, ValueError):
            # Saved value no longer matches the property (e.g. renamed
            # enum item); skip it instead of failing the whole load
            continue

    return applied


# ---
# UV validation
# ---

def ta_uv_signed_area(uvs):
    """
    Signed area of a UV polygon via the shoelace formula.
    A negative value means the face's UV winding is flipped.
    """
    area = 0.0
    count = len(uvs)

    for i in range(count):
        j = (i + 1) % count
        area += uvs[i][0] * uvs[j][1] - uvs[j][0] * uvs[i][1]

    return area * 0.5


def ta_check_object_uvs(obj, tolerance=0.001):
    """
    UV checks for one mesh object (active UV layer): missing UVs, UVs
    outside the 0-1 range, flipped faces and zero-area faces.

    Note: full UV overlap detection is intentionally out of scope; it
    needs spatial acceleration to stay usable on production meshes.
    """
    result = {
        "missing_uvs": False,
        "uvs_out_of_range": False,
        "flipped_faces": [],
        "zero_faces": [],
    }

    if obj is None or obj.type != "MESH":
        return result

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    try:
        uv_layer = bm.loops.layers.uv.active

        if uv_layer is None:
            result["missing_uvs"] = True
            return result

        bm.faces.ensure_lookup_table()

        for face in bm.faces:
            uvs = [loop[uv_layer].uv.copy() for loop in face.loops]

            for uv in uvs:
                if (uv.x < -tolerance or uv.y < -tolerance
                        or uv.x > 1.0 + tolerance or uv.y > 1.0 + tolerance):
                    result["uvs_out_of_range"] = True
                    break

            area = ta_uv_signed_area(uvs)

            if abs(area) < 1e-9:
                result["zero_faces"].append(face.index)
            elif area < 0.0:
                result["flipped_faces"].append(face.index)

        return result

    finally:
        bm.free()


def ta_uv_issue_count(uv_result):
    return (
        int(uv_result["missing_uvs"])
        + int(uv_result["uvs_out_of_range"])
        + len(uv_result["flipped_faces"])
        + len(uv_result["zero_faces"])
    )


def ta_run_export_validation(context, objects):
    """
    Runs geometry (and optionally UV) validation on the given objects.
    Returns a list of human-readable issue lines; empty means clean.
    """
    issues = []

    for obj in objects:
        flags = []

        result = get_non_manifold_edges(obj)

        if result and result["bad_edges"]:
            flags.append(f"{len(result['bad_edges'])} non-manifold edges")

        if context.scene.ta_uv_checks:
            uv_result = ta_check_object_uvs(obj)

            if uv_result["missing_uvs"]:
                flags.append("missing UVs")

            if uv_result["uvs_out_of_range"]:
                flags.append("UVs outside 0-1")

            if uv_result["flipped_faces"]:
                flags.append(f"flipped UVs: {len(uv_result['flipped_faces'])}")

            if uv_result["zero_faces"]:
                flags.append(f"zero-area UVs: {len(uv_result['zero_faces'])}")

        if flags:
            issues.append(f"{obj.name}: " + ", ".join(flags))

    return issues


# ---
# Texel density
# ---

def ta_object_area_totals(obj):
    """
    Total world-space surface area and UV area (active UV layer) of a
    mesh object. World scale is included by transforming the BMesh.
    """
    total_world = 0.0
    total_uv = 0.0

    if obj is None or obj.type != "MESH":
        return total_world, total_uv

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    try:
        bm.transform(obj.matrix_world)

        uv_layer = bm.loops.layers.uv.active

        for face in bm.faces:
            total_world += face.calc_area()

            if uv_layer is not None:
                uvs = [loop[uv_layer].uv.copy() for loop in face.loops]
                total_uv += abs(ta_uv_signed_area(uvs))

        return total_world, total_uv

    finally:
        bm.free()


def ta_get_object_texel_density(obj, texture_size):
    """
    Average texel density of an object in pixels per world unit:

        density = texture_size * sqrt(uv_area / world_area)
    """
    world_area, uv_area = ta_object_area_totals(obj)

    if world_area <= 0.0 or uv_area <= 0.0:
        return None

    return float(texture_size) * math.sqrt(uv_area / world_area)


def ta_get_selection_texel_density(context, texture_size):
    """
    Area-weighted average texel density across the selection.
    """
    total_world = 0.0
    total_uv = 0.0

    for obj in get_selected_meshes(context):
        world_area, uv_area = ta_object_area_totals(obj)
        total_world += world_area
        total_uv += uv_area

    if total_world <= 0.0 or total_uv <= 0.0:
        return None

    return float(texture_size) * math.sqrt(total_uv / total_world)


def ta_set_object_texel_density(obj, factor):
    """
    Uniformly scales the object's active UV layer around its UV
    bounding-box center by the given factor.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    try:
        uv_layer = bm.loops.layers.uv.active

        if uv_layer is None:
            return False

        min_u = min_v = float("inf")
        max_u = max_v = float("-inf")

        for face in bm.faces:
            for loop in face.loops:
                uv = loop[uv_layer].uv
                min_u = min(min_u, uv.x)
                max_u = max(max_u, uv.x)
                min_v = min(min_v, uv.y)
                max_v = max(max_v, uv.y)

        if min_u > max_u:
            return False

        center_u = (min_u + max_u) * 0.5
        center_v = (min_v + max_v) * 0.5

        for face in bm.faces:
            for loop in face.loops:
                uv = loop[uv_layer].uv
                uv.x = center_u + (uv.x - center_u) * factor
                uv.y = center_v + (uv.y - center_v) * factor

        bm.to_mesh(obj.data)
        obj.data.update()

        return True

    finally:
        bm.free()


# ---
# Rigging helpers
# ---

def ta_signed_angle(vector_u, vector_v, normal):
    """
    Angle between two vectors, signed by the given normal.
    """
    angle = vector_u.angle(vector_v)

    if vector_u.cross(vector_v).dot(normal) < 0.0:
        angle = -angle

    return angle


def ta_get_pole_angle(base_bone, ik_bone, pole_location):
    """
    Computes the IK constraint pole_angle so the chain does not snap
    when the pole target is assigned (all inputs in armature space).
    """
    pole_normal = (ik_bone.tail - base_bone.head).cross(
        pole_location - base_bone.head
    )
    projected_pole_axis = pole_normal.cross(base_bone.tail - base_bone.head)

    return ta_signed_angle(
        base_bone.x_axis,
        projected_pole_axis,
        base_bone.tail - base_bone.head,
    )


def ta_create_empty(name, world_location, collection, display_size=0.2):
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.empty_display_size = display_size

    collection.objects.link(empty)

    empty.matrix_world.translation = world_location

    return empty


class TA_OT_rename_selected(bpy.types.Operator):
    bl_idname = "ta.rename_selected"
    bl_label = "Rename Selected"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        prefix = context.scene.rs_prefix
        item_name = context.scene.rs_name

        for index, obj in enumerate(context.selected_objects, start=1):
            obj.name = f"{prefix}_{item_name}_{index:02d}"

        self.report({"INFO"}, "Renamed selected objects")
        return {"FINISHED"}


class TA_OT_export_selected(bpy.types.Operator):
    bl_idname = "ta.export_selected"
    bl_label = "Export Selected"
    bl_description = (
        "Export the selection to FBX; optionally validate first, "
        "batch one file per object and move objects to the origin"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        scene = context.scene

        export_path = bpy.path.abspath(scene.ee_export_path)

        if not os.path.isdir(export_path):
            self.report({"WARNING"}, "Invalid export folder")
            return {"CANCELLED"}

        selected_meshes = get_selected_meshes(context)

        if not selected_meshes:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        if scene.ee_validate_export:
            issues = ta_run_export_validation(context, selected_meshes)

            if issues:
                scene.ta_validation_message = " | ".join(issues)

                if scene.ee_block_export:
                    self.report(
                        {"ERROR"},
                        "Export blocked by validation: " + " | ".join(issues),
                    )
                    return {"CANCELLED"}

                self.report(
                    {"WARNING"},
                    "Validation issues found; exporting anyway",
                )

        if scene.ee_batch_export:
            return self.execute_batch(context, export_path, selected_meshes)

        return self.execute_single(context, export_path)

    def execute_single(self, context, export_path):
        scene = context.scene

        if not context.active_object:
            self.report({"WARNING"}, "No active object")
            return {"CANCELLED"}

        base_name = context.active_object.name

        if scene.ee_auto_poly_prefix:
            export_name, count, classification = get_export_name_with_prefix(
                context,
                base_name
            )
        else:
            export_name = base_name
            count = get_selection_metric(context)
            classification = None

        self.export_fbx(os.path.join(export_path, export_name + ".fbx"))

        if classification:
            self.report(
                {"INFO"},
                f"Exported {export_name}.fbx as {classification['prefix']} "
                f"with {count:,} elements"
            )
        else:
            self.report({"INFO"}, f"Exported {export_name}.fbx")

        return {"FINISHED"}

    def execute_batch(self, context, export_path, selected_meshes):
        """
        Exports one FBX per object, optionally moving each object to the
        world origin for the export and restoring it afterwards (the
        standard game-art batch export workflow).
        """
        scene = context.scene

        original_active = context.view_layer.objects.active
        original_selection = list(context.selected_objects)

        exported = 0

        try:
            for obj in selected_meshes:
                for other in context.selected_objects:
                    other.select_set(False)

                obj.select_set(True)
                context.view_layer.objects.active = obj

                export_name = get_object_export_name(context, obj)
                filepath = os.path.join(export_path, export_name + ".fbx")

                if scene.ee_move_to_origin:
                    original_matrix = obj.matrix_world.copy()
                    obj.matrix_world.translation = (0.0, 0.0, 0.0)

                    try:
                        self.export_fbx(filepath)
                    finally:
                        obj.matrix_world = original_matrix
                else:
                    self.export_fbx(filepath)

                exported += 1

        finally:
            for other in context.selected_objects:
                other.select_set(False)

            for obj in original_selection:
                obj.select_set(True)

            context.view_layer.objects.active = original_active

        self.report(
            {"INFO"},
            f"Batch exported {exported} file(s) to {export_path}"
        )

        return {"FINISHED"}

    def export_fbx(self, filepath):
        bpy.ops.export_scene.fbx(
            filepath=filepath,
            use_selection=True,
            apply_unit_scale=True,
            bake_space_transform=False,
            object_types={"MESH", "EMPTY", "ARMATURE"},
            use_mesh_modifiers=True,
            add_leaf_bones=False,
            path_mode="AUTO"
        )


class TA_PT_tools_panel(bpy.types.Panel):
    bl_label = "TA Tools"
    bl_idname = "TA_PT_tools_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TA Tools"

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.operator("ta.save_defaults", text="Save Defaults", icon="EXPORT")
        row.operator("ta.apply_defaults", text="Apply Defaults", icon="IMPORT")


class TA_PT_rename_tool_panel(bpy.types.Panel):
    bl_label = "Rename Objects"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TA Tools"
    bl_parent_id = "TA_PT_tools_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "rs_prefix")
        layout.prop(scene, "rs_name")
        layout.operator("ta.rename_selected")


class TA_OT_unify(bpy.types.Operator):
    bl_idname = "ta.unify"
    bl_label = "Unify"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        active = context.active_object

        return active is not None and active.type == "MESH"

    def execute(self, context):
        if not context.scene.ee_unify:
            self.report({"INFO"}, "Unify is disabled")
            return {"CANCELLED"}

        if context.active_object and context.active_object.type == "MESH":
            bpy.ops.object.join()
            self.report({"INFO"}, "Objects unified")
            return {"FINISHED"}

        self.report({"INFO"}, "No active mesh object")
        return {"CANCELLED"}


class TA_OT_move_origin(bpy.types.Operator):
    bl_idname = "ta.move_origin"
    bl_label = "Move Origin"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        active = context.active_object

        return active is not None and active.type == "MESH"

    def execute(self, context):
        selected_meshes = get_selected_meshes(context)
        if not selected_meshes:
            self.report({"WARNING"}, "No selected mesh objects")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        context.view_layer.objects.active = selected_meshes[0]
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

        scene = context.scene

        axis = [
            (scene.x_axis, 0),
            (scene.y_axis, 1),
            (scene.z_axis, 2),
        ]

        moved_count = 0

        if scene.individual_toggle:
            for obj in selected_meshes:
                target_co = obj.matrix_world.translation.copy()

                for axis_choice, axis_index in axis:
                    if axis_choice == "NA":
                        continue

                    extreme_vertex = get_extreme_vertex(obj, axis_choice)
                    target_co[axis_index] = extreme_vertex[axis_index]

                offset = target_co - obj.matrix_world.translation

                for vert in obj.data.vertices:
                    vert.co -= offset

                if obj.matrix_world.translation != target_co:
                    obj.matrix_world.translation = target_co
                    obj.data.update()
                    moved_count += 1

            self.report(
                {"INFO"},
                f"Moved origins individually on {moved_count} object(s)"
            )

        else:
            shared_values = {}

            for axis_choice, axis_index in axis:
                if axis_choice == "NA":
                    continue

                extreme_vertex = get_selection_extreme_vertex(
                    selected_meshes,
                    axis_choice
                )

                if extreme_vertex is not None:
                    shared_values[axis_index] = extreme_vertex[axis_index]

            for obj in selected_meshes:
                target_co = obj.matrix_world.translation.copy()

                for axis_index, value in shared_values.items():
                    target_co[axis_index] = value

                offset = target_co - obj.matrix_world.translation

                for vert in obj.data.vertices:
                    vert.co -= offset

                if obj.matrix_world.translation != target_co:
                    obj.matrix_world.translation = target_co
                    obj.data.update()
                    moved_count += 1

            self.report(
                {"INFO"},
                f"Moved origins to shared point on {moved_count} object(s)"
            )

        return {"FINISHED"}


class TA_OT_check_non_manifold(bpy.types.Operator):
    bl_idname = "ta.check_non_manifold"
    bl_label = "Check Non-Manifold"
    bl_description = "Checks selected geometry for non-manifolds"
    bl_options = {"REGISTER", "UNDO"}

    select_first_issue: bpy.props.BoolProperty(
        name="Select First Issue",
        description="Select first problematic object and highlight its non-manifold",
        default=True
    )

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        selected_meshes = get_selected_meshes(context)

        if not selected_meshes:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        problem_objects = []
        total_bad_edges = 0

        for obj in selected_meshes:
            result = get_non_manifold_edges(obj)

            if result is None:
                continue

            bad_count = len(result["bad_edges"])

            if bad_count == 0:
                continue

            total_bad_edges += bad_count

            problem_objects.append({
                "object": obj,
                "result": result,
                "bad_count": bad_count,
            })

        if not problem_objects:
            context.scene.ta_validation_message = "Clean: no non-manifolds"
            self.report({"INFO"}, "No non-manifold geometry found")
            return {"FINISHED"}

        report_lines = []

        for item in problem_objects:
            obj = item["object"]
            result = item["result"]

            wire_count = len(result["wire_edges"])
            boundary_count = len(result["boundary_edges"])
            multiface_count = len(result["multiface_edges"])
            non_contiguous_count = len(result["non_contiguous_edges"])

            line = (
                f"{obj.name}: "
                f"{item['bad_count']} bad edges "
                f"(wire: {wire_count}, "
                f"boundary: {boundary_count}, "
                f"multiface: {multiface_count}, "
                f"other: {non_contiguous_count})"
            )

            report_lines.append(line)

        print("TA Tools - Non-Manifold Report")
        print("------------------------------")
        for line in report_lines:
            print(line)

        context.scene.ta_validation_message = " | ".join(report_lines)

        if self.select_first_issue:
            first_problem = problem_objects[0]
            obj = first_problem["object"]
            bad_edges = first_problem["result"]["bad_edges"]

            for selected_obj in context.selected_objects:
                selected_obj.select_set(False)

            obj.select_set(True)
            context.view_layer.objects.active = obj

            mesh = obj.data

            for vert in mesh.vertices:
                vert.select = False

            for edge in mesh.edges:
                edge.select = False

            for poly in mesh.polygons:
                poly.select = False

            for edge_index in bad_edges:
                if edge_index < len(mesh.edges):
                    mesh.edges[edge_index].select = True

            mesh.update()
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_mode(type="EDGE")

        self.report(
            {"WARNING"},
            f"Found {total_bad_edges} non-manifold edges "
            f"in {len(problem_objects)} object(s)"
        )

        return {"FINISHED"}


class TA_OT_calculate_budget(bpy.types.Operator):
    bl_label = "Calculate Budget"
    bl_idname = "ta.calculate_budget"
    bl_description = "Calculates the budget based on the user's settings."
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        scene = context.scene
        selected_meshes = get_selected_meshes(context)

        if not selected_meshes:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        count = get_selection_metric(context)
        classification = get_poly_classification(count, scene)

        scene.ee_budget_cached_valid = True
        scene.ee_budget_cached_count = count
        scene.ee_budget_cached_mode = scene.ee_budget_count_mode
        scene.ee_budget_cached_signature = get_budget_signature(context)
        scene.ee_budget_cached_state = classification["state"]

        self.report(
            {"INFO"},
            f"Budget calculated: {count:,} {get_budget_count_label(scene)}"
        )

        return {"FINISHED"}


class TA_OT_planar_project_selected_uv(bpy.types.Operator):
    bl_idname = "ta.planar_project_selected_uv"
    bl_label = "Planar Project Selected UVs"
    bl_description = "Planar-project UVs for selected edit-mode faces"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and bool(ta_get_edit_mesh_objects(context))

    def execute(self, context):
        scene = context.scene
        edit_mesh_objects = ta_get_edit_mesh_objects(context)

        if not edit_mesh_objects:
            self.report({"WARNING"}, "Enter Edit Mode and select mesh faces")
            return {"CANCELLED"}

        total_faces = 0
        total_islands = 0

        for obj in edit_mesh_objects:
            mesh = obj.data
            bm = bmesh.from_edit_mesh(mesh)

            bm.faces.ensure_lookup_table()
            bm.edges.ensure_lookup_table()
            bm.verts.ensure_lookup_table()

            selected_faces = [
                face for face in bm.faces
                if face.select and not face.hide
            ]

            if not selected_faces:
                continue

            uv_layer = ta_get_bmesh_uv_layer(
                bm,
                scene.ta_uv_map_name
            )

            if scene.ta_uv_fit_mode == "ISLANDS":
                face_groups = ta_get_selected_face_islands(selected_faces)
            else:
                face_groups = [selected_faces]

            for faces in face_groups:
                projected_count = ta_project_faces_to_uv(
                    obj=obj,
                    faces=faces,
                    uv_layer=uv_layer,
                    projection_mode=scene.ta_uv_projection_mode,
                    space=scene.ta_uv_projection_space,
                    preserve_aspect=scene.ta_uv_preserve_aspect,
                    padding=scene.ta_uv_padding,
                    rotation=scene.ta_uv_rotation,
                    flip_u=scene.ta_uv_flip_u,
                    flip_v=scene.ta_uv_flip_v
                )

                if projected_count > 0:
                    total_faces += projected_count
                    total_islands += 1

            bmesh.update_edit_mesh(mesh)

            if scene.ta_uv_map_name.strip():
                uv_map = mesh.uv_layers.get(scene.ta_uv_map_name)

                if uv_map:
                    mesh.uv_layers.active = uv_map

        if total_faces == 0:
            self.report({"WARNING"}, "No selected faces found")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Planar UV projected {total_faces} face(s) in {total_islands} group(s)"
        )

        return {"FINISHED"}


class TA_OT_save_defaults(bpy.types.Operator):
    bl_idname = "ta.save_defaults"
    bl_label = "Save Defaults"
    bl_description = (
        "Save the current TA Tools settings as defaults for all "
        "future sessions and files"
    )

    def execute(self, context):
        try:
            config_path = ta_save_defaults(context.scene)
        except OSError as error:
            self.report({"ERROR"}, f"Could not save defaults: {error}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Defaults saved to {config_path}")
        return {"FINISHED"}


class TA_OT_apply_defaults(bpy.types.Operator):
    bl_idname = "ta.apply_defaults"
    bl_label = "Apply Defaults"
    bl_description = "Apply previously saved TA Tools defaults to this scene"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            applied = ta_apply_defaults(context.scene)
        except (OSError, ValueError) as error:
            self.report({"ERROR"}, f"Could not apply defaults: {error}")
            return {"CANCELLED"}

        if applied == 0:
            self.report({"INFO"}, "No saved defaults found")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Applied {applied} saved setting(s)")
        return {"FINISHED"}


class TA_OT_check_uvs(bpy.types.Operator):
    bl_idname = "ta.check_uvs"
    bl_label = "Check UVs"
    bl_description = (
        "Check selected meshes for missing UVs, UVs outside 0-1, "
        "flipped faces and zero-area faces"
    )
    bl_options = {"REGISTER", "UNDO"}

    select_first_issue: bpy.props.BoolProperty(
        name="Select First Issue",
        description="Select the first problematic object and its bad faces",
        default=True
    )

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        selected_meshes = get_selected_meshes(context)

        if not selected_meshes:
            self.report({"WARNING"}, "No mesh objects selected")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        problem_objects = []
        report_lines = []

        for obj in selected_meshes:
            uv_result = ta_check_object_uvs(obj)

            if ta_uv_issue_count(uv_result) == 0:
                continue

            flags = []

            if uv_result["missing_uvs"]:
                flags.append("missing UVs")

            if uv_result["uvs_out_of_range"]:
                flags.append("UVs outside 0-1")

            if uv_result["flipped_faces"]:
                flags.append(f"flipped: {len(uv_result['flipped_faces'])}")

            if uv_result["zero_faces"]:
                flags.append(f"zero-area: {len(uv_result['zero_faces'])}")

            problem_objects.append({"object": obj, "result": uv_result})
            report_lines.append(f"{obj.name}: " + ", ".join(flags))

        if not problem_objects:
            context.scene.ta_validation_message = "Clean: no UV issues"
            self.report({"INFO"}, "No UV issues found")
            return {"FINISHED"}

        context.scene.ta_validation_message = " | ".join(report_lines)

        if self.select_first_issue:
            first = problem_objects[0]
            obj = first["object"]
            bad_faces = (
                first["result"]["flipped_faces"]
                + first["result"]["zero_faces"]
            )

            for selected_obj in context.selected_objects:
                selected_obj.select_set(False)

            obj.select_set(True)
            context.view_layer.objects.active = obj

            mesh = obj.data

            for vert in mesh.vertices:
                vert.select = False

            for edge in mesh.edges:
                edge.select = False

            for poly in mesh.polygons:
                poly.select = False

            for face_index in bad_faces:
                if face_index < len(mesh.polygons):
                    mesh.polygons[face_index].select = True

            mesh.update()

            if bad_faces:
                bpy.ops.object.mode_set(mode="EDIT")
                bpy.ops.mesh.select_mode(type="FACE")

        self.report(
            {"WARNING"},
            f"UV issues in {len(problem_objects)} object(s); see panel"
        )

        return {"FINISHED"}


class TA_OT_check_texel_density(bpy.types.Operator):
    bl_idname = "ta.check_texel_density"
    bl_label = "Check TD"
    bl_description = (
        "Compute the area-weighted texel density of the selection "
        "(pixels per world unit)"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        scene = context.scene

        density = ta_get_selection_texel_density(
            context,
            scene.ta_td_texture_size
        )

        if density is None:
            scene.ta_td_result = "TD: no UV or surface area on selection"
            self.report({"WARNING"}, scene.ta_td_result)
            return {"CANCELLED"}

        scene.ta_td_result = (
            f"TD: {density:.3f} px/unit "
            f"at {scene.ta_td_texture_size}px textures"
        )

        self.report({"INFO"}, scene.ta_td_result)
        return {"FINISHED"}


class TA_OT_set_texel_density(bpy.types.Operator):
    bl_idname = "ta.set_texel_density"
    bl_label = "Set TD"
    bl_description = (
        "Scale each selected object's UVs (around their UV bounds "
        "center) to match the target texel density"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        scene = context.scene
        target = scene.ta_td_target

        if target <= 0.0:
            self.report({"ERROR"}, "Target texel density must be > 0")
            return {"CANCELLED"}

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        adjusted = 0

        for obj in get_selected_meshes(context):
            current = ta_get_object_texel_density(
                obj,
                scene.ta_td_texture_size
            )

            if not current or current <= 0.0:
                continue

            factor = target / current

            if abs(factor - 1.0) < 0.0001:
                continue

            if ta_set_object_texel_density(obj, factor):
                adjusted += 1

        self.report({"INFO"}, f"Adjusted texel density on {adjusted} object(s)")

        bpy.ops.ta.check_texel_density()

        return {"FINISHED"}


class TA_OT_create_ik_pole(bpy.types.Operator):
    bl_idname = "ta.create_ik_pole"
    bl_label = "Create IK + Pole Vector"
    bl_description = (
        "Add an IK constraint to the active pose bone with target and "
        "pole empties; the pole is placed on the chain's bend plane and "
        "pole_angle is computed so the chain does not snap"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "POSE"
            and context.active_pose_bone is not None
        )

    def execute(self, context):
        scene = context.scene
        armature = context.active_object
        end_bone = context.active_pose_bone

        chain_count = max(2, scene.ta_ik_chain_count)

        # Walk up the parents to collect the chain (end bone included)
        chain = [end_bone]
        current = end_bone

        while len(chain) < chain_count and current.parent:
            current = current.parent
            chain.append(current)

        if len(chain) < 2:
            self.report({"ERROR"}, "The active bone needs at least one parent")
            return {"CANCELLED"}

        base_bone = chain[-1]

        # Joint positions root->tip in armature space, e.g.
        # [shoulder, elbow, wrist] for a two-bone arm
        positions = [bone.head.copy() for bone in reversed(chain)]
        positions.append(end_bone.tail.copy())

        start_pos = positions[0]
        end_pos = positions[-1]
        mid_pos = positions[len(positions) // 2]

        axis = end_pos - start_pos
        to_mid = mid_pos - start_pos

        if axis.length < 1e-6:
            self.report({"ERROR"}, "Chain start and end are at the same position")
            return {"CANCELLED"}

        axis_normal = axis.normalized()

        # Vector rejection: component of to_mid perpendicular to the axis
        projection = axis_normal * to_mid.dot(axis_normal)
        pole_direction = to_mid - projection

        chain_length = sum(
            (positions[i + 1] - positions[i]).length
            for i in range(len(positions) - 1)
        )

        if pole_direction.length < 1e-6:
            fallback = Vector((0.0, 0.0, 1.0))

            if abs(axis_normal.dot(fallback)) > 0.999:
                fallback = Vector((0.0, 1.0, 0.0))

            pole_direction = axis_normal.cross(fallback)

            self.report(
                {"WARNING"},
                "Chain is straight; pole direction is arbitrary"
            )

        pole_pos = (
            mid_pos
            + pole_direction.normalized()
            * (chain_length * 0.5 * scene.ta_pole_distance)
        )

        world_matrix = armature.matrix_world

        target_empty = ta_create_empty(
            f"IK_{end_bone.name}",
            world_matrix @ end_bone.tail,
            context.collection
        )

        pole_empty = ta_create_empty(
            f"PV_{end_bone.name}",
            world_matrix @ pole_pos,
            context.collection
        )

        constraint = end_bone.constraints.new("IK")
        constraint.target = target_empty
        constraint.pole_target = pole_empty
        constraint.chain_count = len(chain)
        constraint.pole_angle = ta_get_pole_angle(
            base_bone,
            end_bone,
            pole_pos
        )

        self.report(
            {"INFO"},
            f"IK on {end_bone.name} (chain {len(chain)}) with pole "
            f"angle {math.degrees(constraint.pole_angle):.1f}°"
        )

        return {"FINISHED"}


class TA_OT_create_muscle_helper(bpy.types.Operator):
    bl_idname = "ta.create_muscle_helper"
    bl_label = "Create Muscle Helper"
    bl_description = (
        "Create a deform bone on the parent of the active pose bone, "
        "driven to bulge as the active bone bends -- the classic bicep "
        "setup. Add the new bone to your vertex groups to see the effect"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            context.mode == "POSE"
            and context.active_pose_bone is not None
            and context.active_pose_bone.parent is not None
        )

    def execute(self, context):
        scene = context.scene
        armature = context.active_object

        bend_name = context.active_pose_bone.name
        upper_name = context.active_pose_bone.parent.name

        max_angle = scene.ta_muscle_max_angle

        if max_angle <= 0.0:
            self.report({"ERROR"}, "Max angle must be greater than zero")
            return {"CANCELLED"}

        helper_name = f"MUSCLE_{upper_name}"

        # Build the helper bone in edit mode, halfway along the upper bone
        bpy.ops.object.mode_set(mode="EDIT")

        edit_bones = armature.data.edit_bones
        upper_edit = edit_bones[upper_name]

        helper_edit = edit_bones.new(helper_name)
        helper_name = helper_edit.name  # Blender may append .001

        direction = upper_edit.tail - upper_edit.head

        helper_edit.head = upper_edit.head + direction * 0.5
        helper_edit.tail = helper_edit.head + direction * 0.25
        helper_edit.parent = upper_edit
        helper_edit.use_deform = True

        bpy.ops.object.mode_set(mode="POSE")

        # Drive the helper's scale from the bend bone's local rotation:
        # scale goes 1.0 -> bulge as |angle| goes 0 -> max_angle
        max_radians = math.radians(max_angle)
        bulge = scene.ta_muscle_bulge

        expression = (
            f"1.0 + ({bulge:.4f} - 1.0) "
            f"* min(abs(rot) / {max_radians:.6f}, 1.0)"
        )

        data_path = f'pose.bones["{helper_name}"].scale'

        for axis_index in range(3):
            fcurve = armature.driver_add(data_path, axis_index)
            driver = fcurve.driver
            driver.type = "SCRIPTED"

            variable = driver.variables.new()
            variable.name = "rot"
            variable.type = "TRANSFORMS"

            target = variable.targets[0]
            target.id = armature
            target.bone_target = bend_name
            target.transform_type = f"ROT_{scene.ta_muscle_axis}"
            target.transform_space = "LOCAL_SPACE"

            if hasattr(target, "rotation_mode"):
                target.rotation_mode = "AUTO"

            driver.expression = expression

        self.report(
            {"INFO"},
            f"Created {helper_name}; add it to the mesh's vertex groups "
            f"and paint weights to see the bulge"
        )

        return {"FINISHED"}


class TA_PT_easy_export(bpy.types.Panel):
    bl_label = "Easy Export"
    bl_idname = "TA_PT_easy_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TA Tools"
    bl_parent_id = "TA_PT_tools_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        row = layout.row()
        row.prop(scene, "ee_unify")
        row.operator("ta.unify")

        layout.separator()

        box = layout.box()
        box.label(text="Budget")

        row = box.row(align=True)
        row.prop(scene, "ee_budget_count_mode", text="")
        row.prop(scene, "ee_poly_budget")

        if scene.ee_budget_count_mode == "ENGINE":
            box.prop(scene, "ee_count_material_splits")

        box.prop(scene, "ee_poly_margin")
        box.prop(scene, "ee_count_modifiers")
        box.prop(scene, "ee_auto_poly_prefix")

        row = box.row(align=True)
        row.prop(scene, "ee_lp_prefix")
        row.prop(scene, "ee_hp_prefix")

        box.operator("ta.calculate_budget", icon="FILE_REFRESH")

        try:
            draw_budget_marker(box, context)
        except Exception as error:
            error_row = box.row()
            error_row.alert = True
            error_row.label(text=f"Budget marker error: {error}", icon="ERROR")

        layout.separator()

        layout.label(text="Select origin axis")
        row = layout.row()
        row.prop(scene, "x_axis", text="")
        row.prop(scene, "y_axis", text="")
        row.prop(scene, "z_axis", text="")

        layout.operator("ta.move_origin")
        layout.prop(scene, "individual_toggle")

        layout.separator()
        validation_box = layout.box()
        validation_box.label(text="Validation", icon="CHECKMARK")

        row = validation_box.row(align=True)
        row.operator("ta.check_non_manifold", text="Check Non-Manifold", icon="ERROR")
        row.operator("ta.check_uvs", text="Check UVs", icon="UV")

        validation_box.prop(scene, "ta_uv_checks")

        if scene.ta_validation_message:
            warning_row = validation_box.row()
            warning_row.alert = "Clean:" not in scene.ta_validation_message
            warning_row.label(text=scene.ta_validation_message, icon="INFO")

        layout.separator()

        uv_box = layout.box()
        uv_box.label(text="UV Planar Projection", icon="GROUP_UVS")

        uv_box.prop(scene, "ta_uv_map_name")

        row = uv_box.row(align=True)
        row.prop(scene, "ta_uv_projection_mode", text="Projection")
        row.prop(scene, "ta_uv_projection_space", text="Space")

        uv_box.prop(scene, "ta_uv_fit_mode")
        uv_box.prop(scene, "ta_uv_preserve_aspect")
        uv_box.prop(scene, "ta_uv_padding")

        row = uv_box.row(align=True)
        row.prop(scene, "ta_uv_rotation")
        row.prop(scene, "ta_uv_flip_u")
        row.prop(scene, "ta_uv_flip_v")

        uv_box.operator(
            "ta.planar_project_selected_uv",
            text="Project Selected Faces",
            icon="GROUP_UVS"
        )

        layout.separator()

        export_box = layout.box()
        export_box.label(text="Export", icon="EXPORT")

        export_box.prop(scene, "ee_export_path")

        row = export_box.row(align=True)
        row.prop(scene, "ee_validate_export")
        row.prop(scene, "ee_block_export")

        row = export_box.row(align=True)
        row.prop(scene, "ee_batch_export")

        sub = row.row(align=True)
        sub.enabled = scene.ee_batch_export
        sub.prop(scene, "ee_move_to_origin")

        export_box.operator("ta.export_selected", text="Export")


class TA_PT_texel_density(bpy.types.Panel):
    bl_label = "Texel Density"
    bl_idname = "TA_PT_texel_density"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TA Tools"
    bl_parent_id = "TA_PT_tools_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.label(text="Pixels per world unit")

        layout.prop(scene, "ta_td_texture_size")
        layout.prop(scene, "ta_td_target")

        row = layout.row(align=True)
        row.operator("ta.check_texel_density")
        row.operator("ta.set_texel_density")

        if scene.ta_td_result:
            layout.label(text=scene.ta_td_result, icon="INFO")


class TA_PT_rigging(bpy.types.Panel):
    bl_label = "Rigging Helpers"
    bl_idname = "TA_PT_rigging"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TA Tools"
    bl_parent_id = "TA_PT_tools_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.label(text="IK: active pose bone = chain end")

        layout.prop(scene, "ta_ik_chain_count")
        layout.prop(scene, "ta_pole_distance")
        layout.operator("ta.create_ik_pole", icon="CON_KINEMATIC")

        layout.separator()

        layout.label(text="Muscle: active pose bone = bend joint")

        layout.prop(scene, "ta_muscle_axis")
        layout.prop(scene, "ta_muscle_max_angle")
        layout.prop(scene, "ta_muscle_bulge")
        layout.operator("ta.create_muscle_helper", icon="BONE_DATA")


def register():
    bpy.utils.register_class(TA_OT_rename_selected)
    bpy.utils.register_class(TA_OT_export_selected)
    bpy.utils.register_class(TA_OT_move_origin)
    bpy.utils.register_class(TA_OT_unify)
    bpy.utils.register_class(TA_OT_check_non_manifold)
    bpy.utils.register_class(TA_OT_calculate_budget)
    bpy.utils.register_class(TA_OT_planar_project_selected_uv)

    bpy.utils.register_class(TA_OT_save_defaults)
    bpy.utils.register_class(TA_OT_apply_defaults)
    bpy.utils.register_class(TA_OT_check_uvs)
    bpy.utils.register_class(TA_OT_check_texel_density)
    bpy.utils.register_class(TA_OT_set_texel_density)
    bpy.utils.register_class(TA_OT_create_ik_pole)
    bpy.utils.register_class(TA_OT_create_muscle_helper)

    bpy.utils.register_class(TA_PT_tools_panel)
    bpy.utils.register_class(TA_PT_rename_tool_panel)
    bpy.utils.register_class(TA_PT_easy_export)
    bpy.utils.register_class(TA_PT_texel_density)
    bpy.utils.register_class(TA_PT_rigging)

    bpy.types.Scene.rs_prefix = bpy.props.StringProperty(
        name="Prefix",
        default="SM"
    )

    bpy.types.Scene.rs_name = bpy.props.StringProperty(
        name="Item name",
        default="Prop"
    )

    bpy.types.Scene.ee_unify = bpy.props.BoolProperty(
        name="Unify objects?",
        default=False
    )

    bpy.types.Scene.x_axis = bpy.props.EnumProperty(
        items=[("NA", "None", ""), ("X", "X", ""), ("-X", "-X", ""), ("MID", "Mid", "")],
        update=update_axis
    )

    bpy.types.Scene.y_axis = bpy.props.EnumProperty(
        items=[("NA", "None", ""), ("Y", "Y", ""), ("-Y", "-Y", ""), ("MID", "Mid", "")],
        update=update_axis
    )

    bpy.types.Scene.z_axis = bpy.props.EnumProperty(
        items=[("NA", "None", ""), ("Z", "Z", ""), ("-Z", "-Z", ""), ("MID", "Mid", "")],
        update=update_axis
    )

    bpy.types.Scene.individual_toggle = bpy.props.BoolProperty(
        name="Apply individual origin transform?",
        default=False
    )

    bpy.types.Scene.ee_poly_budget = bpy.props.IntProperty(
        name="Poly Budget",
        description="Poly count threshold for LP/HP classification",
        default=3000,
        min=1
    )

    bpy.types.Scene.ee_poly_margin = bpy.props.FloatProperty(
        name="Margin (%)",
        description="Allowed percentage over the budget",
        default=10.0,
        min=0.0,
        soft_max=100.0,
    )

    bpy.types.Scene.ee_budget_count_mode = bpy.props.EnumProperty(
        name="Count Mode",
        items=[
            ("TRIS", "Triangles", "Use triangle count"),
            ("FACES", "Faces", "Use polygon/face count"),
            ("VERTS", "Vertices", "Use vertices count"),
            ("ENGINE", "Engine verts", "Estimated in-game/render vertex count")
        ],
        default="ENGINE"
    )

    bpy.types.Scene.ee_count_material_splits = bpy.props.BoolProperty(
        name="Count Material Splits",
        description=(
            "Include material section changes in the engine vertex "
            "estimate (recommended: ON)"
        ),
        default=True
    )

    bpy.types.Scene.ee_count_modifiers = bpy.props.BoolProperty(
        name="Count Modifiers",
        description="Count the evaluated mesh with modifiers applied",
        default=True
    )

    bpy.types.Scene.ee_auto_poly_prefix = bpy.props.BoolProperty(
        name="Auto HP/LP Prefix",
        description="Automatically assign prefix during export based on poly budget",
        default=True
    )

    bpy.types.Scene.ee_lp_prefix = bpy.props.StringProperty(
        name="LP Prefix",
        default="LP"
    )

    bpy.types.Scene.ee_hp_prefix = bpy.props.StringProperty(
        name="HP Prefix",
        default="HP"
    )

    bpy.types.Scene.ee_budget_cached_valid = bpy.props.BoolProperty(
        name="Budget Cached Valid",
        default=False
    )

    bpy.types.Scene.ee_budget_cached_count = bpy.props.IntProperty(
        name="Cached Budget Count",
        default=0,
        min=0
    )

    bpy.types.Scene.ee_budget_cached_mode = bpy.props.StringProperty(
        name="Cached Budget Mode",
        default=""
    )

    bpy.types.Scene.ee_budget_cached_signature = bpy.props.StringProperty(
        name="Cached Budget Signature",
        default=""
    )

    bpy.types.Scene.ee_budget_cached_state = bpy.props.StringProperty(
        name="Cached Budget State",
        default=""
    )

    bpy.types.Scene.ee_export_path = bpy.props.StringProperty(
        name="Export to:",
        subtype="DIR_PATH",
        default="//"
    )

    bpy.types.Scene.ta_validation_message = bpy.props.StringProperty(
        name="Validation Message",
        default=""
    )

    bpy.types.Scene.ta_uv_map_name = bpy.props.StringProperty(
        name="UV Map",
        description="UV map to create or overwrite",
        default="TA_Planar"
    )

    bpy.types.Scene.ta_uv_projection_mode = bpy.props.EnumProperty(
        name="Projection",
        description="Planar projection mode",
        items=[
            ("XY", "XY", "Project onto XY plane"),
            ("XZ", "XZ", "Project onto XZ plane"),
            ("YZ", "YZ", "Project onto YZ plane"),
            ("NORMAL", "Normal", "Best-fit projection from selected face normals"),
        ],
        default="NORMAL"
    )

    bpy.types.Scene.ta_uv_projection_space = bpy.props.EnumProperty(
        name="Space",
        description="Use local or world coordinates for projection",
        items=[
            ("LOCAL", "Local", "Use object-local coordinates"),
            ("WORLD", "World", "Use world coordinates"),
        ],
        default="LOCAL"
    )

    bpy.types.Scene.ta_uv_fit_mode = bpy.props.EnumProperty(
        name="Fit",
        description="How selected faces are fitted into UV space",
        items=[
            ("SELECTION", "Selection", "Fit all selected faces as one projection"),
            ("ISLANDS", "Islands", "Fit each connected selected face island separately"),
        ],
        default="SELECTION"
    )

    bpy.types.Scene.ta_uv_preserve_aspect = bpy.props.BoolProperty(
        name="Preserve Aspect",
        description="Preserve projected proportions",
        default=True
    )

    bpy.types.Scene.ta_uv_padding = bpy.props.FloatProperty(
        name="Padding",
        description="Padding inside the 0-1 UV area",
        default=0.02,
        min=0.0,
        max=0.49,
        soft_max=0.25
    )

    bpy.types.Scene.ta_uv_rotation = bpy.props.EnumProperty(
        name="Rotate",
        description="Rotate projected UVs",
        items=[
            ("0", "0°", "No rotation"),
            ("90", "90°", "Rotate 90 degrees"),
            ("180", "180°", "Rotate 180 degrees"),
            ("270", "270°", "Rotate 270 degrees"),
        ],
        default="0"
    )

    bpy.types.Scene.ta_uv_flip_u = bpy.props.BoolProperty(
        name="Flip U",
        default=False
    )

    bpy.types.Scene.ta_uv_flip_v = bpy.props.BoolProperty(
        name="Flip V",
        default=False
    )

    bpy.types.Scene.ta_uv_checks = bpy.props.BoolProperty(
        name="Include UV checks on export",
        description=(
            "Also run UV validation (missing, 0-1 range, flipped, "
            "zero-area) when validating before export"
        ),
        default=True
    )

    bpy.types.Scene.ee_validate_export = bpy.props.BoolProperty(
        name="Validate",
        description="Run validation before exporting",
        default=True
    )

    bpy.types.Scene.ee_block_export = bpy.props.BoolProperty(
        name="Block on issues",
        description="Cancel the export if validation finds issues",
        default=True
    )

    bpy.types.Scene.ee_batch_export = bpy.props.BoolProperty(
        name="Batch (one FBX per object)",
        description="Export each selected object to its own FBX file",
        default=False
    )

    bpy.types.Scene.ee_move_to_origin = bpy.props.BoolProperty(
        name="Move to origin",
        description=(
            "Move each object to the world origin for its export and "
            "restore it afterwards (batch only)"
        ),
        default=True
    )

    bpy.types.Scene.ta_td_texture_size = bpy.props.IntProperty(
        name="Texture size",
        description="Texture resolution in pixels used for the density",
        default=2048,
        min=1
    )

    bpy.types.Scene.ta_td_target = bpy.props.FloatProperty(
        name="Target TD",
        description="Target texel density in pixels per world unit",
        default=10.24,
        min=0.001,
        precision=3
    )

    bpy.types.Scene.ta_td_result = bpy.props.StringProperty(
        name="TD Result",
        default=""
    )

    bpy.types.Scene.ta_ik_chain_count = bpy.props.IntProperty(
        name="Chain length",
        description="How many bones the IK chain includes",
        default=2,
        min=2
    )

    bpy.types.Scene.ta_pole_distance = bpy.props.FloatProperty(
        name="Pole distance",
        description="Pole distance as a factor of half the chain length",
        default=1.0,
        min=0.1,
        precision=2
    )

    bpy.types.Scene.ta_muscle_axis = bpy.props.EnumProperty(
        name="Bend axis",
        description="Local rotation axis of the bend bone that drives the bulge",
        items=[
            ("X", "X", "Local X rotation"),
            ("Y", "Y", "Local Y rotation"),
            ("Z", "Z", "Local Z rotation"),
        ],
        default="X"
    )

    bpy.types.Scene.ta_muscle_max_angle = bpy.props.FloatProperty(
        name="Max angle",
        description="Bend angle (degrees) at which the bulge is fully applied",
        default=90.0,
        min=1.0,
        soft_max=180.0
    )

    bpy.types.Scene.ta_muscle_bulge = bpy.props.FloatProperty(
        name="Bulge scale",
        description="Helper bone scale at the max bend angle",
        default=1.4,
        min=0.1,
        precision=2
    )


def unregister():
    del bpy.types.Scene.ta_muscle_bulge
    del bpy.types.Scene.ta_muscle_max_angle
    del bpy.types.Scene.ta_muscle_axis
    del bpy.types.Scene.ta_pole_distance
    del bpy.types.Scene.ta_ik_chain_count
    del bpy.types.Scene.ta_td_result
    del bpy.types.Scene.ta_td_target
    del bpy.types.Scene.ta_td_texture_size
    del bpy.types.Scene.ee_move_to_origin
    del bpy.types.Scene.ee_batch_export
    del bpy.types.Scene.ee_block_export
    del bpy.types.Scene.ee_validate_export
    del bpy.types.Scene.ta_uv_checks
    del bpy.types.Scene.ta_uv_flip_v
    del bpy.types.Scene.ta_uv_flip_u
    del bpy.types.Scene.ta_uv_rotation
    del bpy.types.Scene.ta_uv_padding
    del bpy.types.Scene.ta_uv_preserve_aspect
    del bpy.types.Scene.ta_uv_fit_mode
    del bpy.types.Scene.ta_uv_projection_space
    del bpy.types.Scene.ta_uv_projection_mode
    del bpy.types.Scene.ta_uv_map_name
    del bpy.types.Scene.ta_validation_message
    del bpy.types.Scene.ee_export_path
    del bpy.types.Scene.ee_budget_cached_state
    del bpy.types.Scene.ee_budget_cached_signature
    del bpy.types.Scene.ee_budget_cached_mode
    del bpy.types.Scene.ee_budget_cached_count
    del bpy.types.Scene.ee_budget_cached_valid
    del bpy.types.Scene.ee_hp_prefix
    del bpy.types.Scene.ee_lp_prefix
    del bpy.types.Scene.ee_auto_poly_prefix
    del bpy.types.Scene.ee_count_modifiers
    del bpy.types.Scene.ee_count_material_splits
    del bpy.types.Scene.ee_budget_count_mode
    del bpy.types.Scene.ee_poly_margin
    del bpy.types.Scene.ee_poly_budget
    del bpy.types.Scene.individual_toggle
    del bpy.types.Scene.z_axis
    del bpy.types.Scene.y_axis
    del bpy.types.Scene.x_axis
    del bpy.types.Scene.ee_unify
    del bpy.types.Scene.rs_name
    del bpy.types.Scene.rs_prefix

    bpy.utils.unregister_class(TA_PT_rigging)
    bpy.utils.unregister_class(TA_PT_texel_density)
    bpy.utils.unregister_class(TA_PT_easy_export)
    bpy.utils.unregister_class(TA_PT_rename_tool_panel)
    bpy.utils.unregister_class(TA_PT_tools_panel)

    bpy.utils.unregister_class(TA_OT_create_muscle_helper)
    bpy.utils.unregister_class(TA_OT_create_ik_pole)
    bpy.utils.unregister_class(TA_OT_set_texel_density)
    bpy.utils.unregister_class(TA_OT_check_texel_density)
    bpy.utils.unregister_class(TA_OT_check_uvs)
    bpy.utils.unregister_class(TA_OT_apply_defaults)
    bpy.utils.unregister_class(TA_OT_save_defaults)

    bpy.utils.unregister_class(TA_OT_planar_project_selected_uv)
    bpy.utils.unregister_class(TA_OT_calculate_budget)
    bpy.utils.unregister_class(TA_OT_check_non_manifold)
    bpy.utils.unregister_class(TA_OT_unify)
    bpy.utils.unregister_class(TA_OT_move_origin)
    bpy.utils.unregister_class(TA_OT_export_selected)
    bpy.utils.unregister_class(TA_OT_rename_selected)


if __name__ == "__main__":
    register()
