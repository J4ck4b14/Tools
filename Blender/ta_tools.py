bl_info = {
    "name": "JAM TA Tools",
    "blender": (2, 80, 0),
    "category": "Object",
    "version": (1, 0, 0),
    "author": "Juan Abia Merino",
    "description": "Tools",
}

import bpy
from mathutils import Vector

def get_extreme_vertex(obj, axis_str):
        """Finds the axis-most vertex of the given axis (+-X, +-Y, +-Z) or the mid point"""
        bpy.ops.object.mode_set(mode="OBJECT")

        if axis_str == "MID":
            local_coords = [v.co for v in obj.data.vertices]
            median_co = sum(local_coords, Vector()) / len(local_coords)
            
            world_co = obj.matrix_world @ median_co
            print(world_co)
            return world_co

        axis_index = {'X': 0, 'Y': 1, 'Z':2}[axis_str[-1]]
        find_max = not axis_str.startswith('-')

        coords = [v.co[axis_index] for v in obj.data.vertices]
        target_value = max(coords) if find_max else min(coords)
        target_index = coords.index(target_value)
        vertex = obj.data.vertices[target_index]

        world_co = obj.matrix_world @ vertex.co

        print(world_co)
        return world_co

def update_axis(self, context):
    #This function just forces the UI redraw
    pass

def get_mesh_metrics(obj, context, mode):
    """
    Returns VERTS, FACES, or TRIS count.
    Uses evaluated mesh whenn ee_count_modifiers is enabled
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
        mesh = obj.data
        eval_obj = None
        should_clear = False
    
    try:
        if mesh is None:
            return 0
        
        mesh.calc_loop_triangles()
        if hasattr(mesh, "calc_normals_split"):
            mesh.calc_normals_split()

        uv_layers = list(mesh.uv_layers)

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
        return{
            "prefix": scene.ee_hp_prefix,
            "state": "HIGH",
            "label": "High",
            "icon": "CANCEL",
            "limit": high_limit,
        }
    
    if count >= warning_start:
        return{
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


def draw_budget_marker(layout, context):
    scene = context.scene
    mesh_objects = get_selected_meshes(context)

    row = layout.row(align=True)
    row.scale_y = 0.75

    if not mesh_objects:
        row.label(text = "Poly budget: No mesh selected", icon="INFO")
        return
    
    count = get_selection_metric(context)
    classification = get_poly_classification(count, scene)

    count_label = {
        "ENGINE": "engine verts",
        "TRIS": "tris",
        "FACES": "faces",
        "VERTS": "verts"
    }.get(scene.ee_budget_count_mode, "items")

    if classification["state"] == "HIGH":
        row.alert = True

    row.label(
        text = (
            f"{classification['label']} · "
            f"{count:,}/{classification['limit']:,} {count_label} · "
            f"Export as {classification['prefix']}_"
        ),
        icon = classification["icon"]
    )

class TA_OT_rename_selected(bpy.types.Operator):
    bl_idname = "ta.rename_selected"
    bl_label = "Rename Selected"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        prefix = context.scene.rs_prefix
        itemName = context.scene.rs_name
        for index, obj in enumerate(context.selected_objects, start=1):
            obj.name = f"{prefix}_{itemName}_{index:02d}"

        self.report({"INFO"}, "Renamed selected objects")
        return{"FINISHED"}

class TA_OT_export_selected(bpy.types.Operator):
    bl_idname = "ta.export_selected"
    bl_label = "Export Selected"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        import os

        scene = context.scene

        export_path = bpy.path.abspath(context.scene.ee_export_path)

        if not os.path.isdir(export_path):
            self.report({"WARNING"}, "Invalid export folder")
            return {"CANCELLED"}

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

        filepath = os.path.join(export_path, export_name + ".fbx")

        bpy.ops.export_scene.fbx(
            filepath=filepath,
            use_selection = True,
            apply_unit_scale = True,
            bake_space_transform = False,
            object_types = {"MESH", "EMPTY", "ARMATURE"},
            use_mesh_modifiers = True,
            add_leaf_bones = False,
            path_mode="AUTO"
        )

        if classification:
            self.report(
                {"INFO"},
                f"Exported {export_name}.fbx as {classification['prefix']} with {count:,} elements"
            )
        else:
            self.report(
                {"INFO"},
                f"Exported {export_name}.fbx"
            )

        return {"FINISHED"}

class TA_PT_tools_panel(bpy.types.Panel):
    bl_label = "TA Tools"
    bl_idname = "TA_PT_tools_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "TA Tools"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

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
        return context.active_object is not None and context.active_object.type == "MESH"

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
        return context.active_object is not None and context.active_object.type == "MESH"

    def execute(self, context):
        if not context.active_object or context.active_object.type != "MESH":
            self.report({"WARNING"}, "No active mesh object")
            return {"CANCELLED"}

        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

        obj = context.active_object
        mesh = obj.data
        target_co = obj.matrix_world.translation.copy()

        x_axis = context.scene.x_axis
        if x_axis != "NA":
            obj = context.active_object
            extreme_vertex = Vector((get_extreme_vertex(obj, x_axis)))
            target_co.x = extreme_vertex.x
        
        y_axis = context.scene.y_axis
        if y_axis != "NA":
            obj = context.active_object
            extreme_vertex = Vector((get_extreme_vertex(obj, y_axis)))
            target_co.y = extreme_vertex.y
        
        z_axis = context.scene.z_axis
        if z_axis != "NA":
            obj = context.active_object
            extreme_vertex = Vector((get_extreme_vertex(obj, z_axis)))
            target_co.z = extreme_vertex.z

        offset = target_co - obj.matrix_world.translation

        #Move mesh geometry against the origin movement, so the visual stays the same
        for v in mesh.vertices:
            v.co -= offset

        if obj.matrix_world.translation == target_co:
            self.report({"INFO"}, "No changes were needed")
        else:
            obj.matrix_world.translation = target_co
            self.report({"INFO"}, "Origin moved succesfully")
        
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

        row = box.row(align = True)
        row.prop(scene, "ee_budget_count_mode", text="")
        row.prop(scene, "ee_poly_budget")

        if scene.ee_budget_count_mode == "ENGINE":
            box.prop(scene, "ee_count_material_splits")

        box.prop(scene, "ee_poly_margin")
        box.prop(scene, "ee_count_modifiers")
        box.prop(scene, "ee_auto_poly_prefix")

        row = box.row(align = True)
        row.prop(scene, "ee_lp_prefix")
        row.prop(scene, "ee_hp_prefix")

        try:
            draw_budget_marker(box, context)
        except Exception as error:
            error_row = box.row()
            error_row.alert = True
            error_row.label(text=f"Budget marker error: {error}", icon = "ERROR")
        
        layout.separator()

        layout.label(text="Select origin axis")
        row = layout.row()
        row.prop(scene, "x_axis", text="")
        row.prop(scene, "y_axis", text="")
        row.prop(scene, "z_axis", text="")

        layout.operator("ta.move_origin")
        layout.prop(scene, "individual_toggle")

        layout.separator()
        layout.prop(scene, "ee_export_path")
        layout.operator("ta.export_selected", text = "Export")

def register():
    bpy.utils.register_class(TA_OT_rename_selected)
    bpy.utils.register_class(TA_OT_export_selected)
    bpy.utils.register_class(TA_PT_tools_panel)
    bpy.utils.register_class(TA_PT_rename_tool_panel)
    bpy.utils.register_class(TA_OT_move_origin)
    bpy.utils.register_class(TA_OT_unify)
    bpy.utils.register_class(TA_PT_easy_export)

    bpy.types.Scene.rs_prefix = bpy.props.StringProperty(
        name = "Prefix",
        default = "SM"
    )

    bpy.types.Scene.rs_name = bpy.props.StringProperty(
        name = "Item name",
        default = "Prop"
    )

    bpy.types.Scene.ee_unify = bpy.props.BoolProperty(
        name = "Unify objects?",
        default = False
    )

    bpy.types.Scene.x_axis = bpy.props.EnumProperty(
        items = [("NA", "None", ""), ("X", "X", ""), ("-X", "-X", ""), ("MID", "Mid", "")],
        update=update_axis
    )

    bpy.types.Scene.y_axis = bpy.props.EnumProperty(
        items = [("NA", "None", ""), ("Y", "Y", ""), ("-Y", "-Y", ""), ("MID", "Mid", "")],
        update=update_axis
    )

    bpy.types.Scene.z_axis = bpy.props.EnumProperty(
        items = [("NA", "None", ""), ("Z", "Z", ""), ("-Z", "-Z", ""), ("MID", "Mid", "")],
        update=update_axis
    )

    bpy.types.Scene.individual_toggle = bpy.props.BoolProperty(
        name = "Apply individual origin transform?",
        default = False
    )

    bpy.types.Scene.ee_poly_budget = bpy.props.IntProperty(
        name = "Poly Budget",
        description = "Poly count threshold for LP/HP classification",
        default = 3000,
        min = 1
    )

    bpy.types.Scene.ee_poly_margin = bpy.props.FloatProperty(
        name = "Margin (%)",
        description = "Allowed percentage over the budget",
        default = 10.0,
        min = 0.0,
        soft_max = 100.0,
    )

    bpy.types.Scene.ee_budget_count_mode = bpy.props.EnumProperty(
        name = "Count Mode",
        items = [
            ("TRIS", "Triangles", "Use triangle count"),
            ("FACES", "Faces", "Use polygon/face count"),
            ("VERTS", "Vertices", "Use vertices count"),
            ("ENGINE", "Engine verts", "Estimated in-game/render vertex count")
        ],
        default = "ENGINE"
    )

    bpy.types.Scene.ee_count_material_splits = bpy.props.BoolProperty(
        name = "Count Material Splits",
        description = "Include material section changes in the engine vertex estimate (recommended: ON)",
        default = True
    )

    bpy.types.Scene.ee_count_modifiers = bpy.props.BoolProperty(
        name = "Count Modifiers",
        description = "Count the evaluated mesh with modifiers applied",
        default = True
    )

    bpy.types.Scene.ee_auto_poly_prefix = bpy.props.BoolProperty(
        name = "Auto HP/LP Prefix",
        description = "Automatically assign prefix during export based on poly budget",
        default = True
    )

    bpy.types.Scene.ee_lp_prefix = bpy.props.StringProperty(
        name = "LP Prefix",
        default = "LP"
    )

    bpy.types.Scene.ee_hp_prefix = bpy.props.StringProperty(
        name = "HP Prefix",
        default = "HP"
    )

    bpy.types.Scene.ee_export_path = bpy.props.StringProperty(
        name = "Export to:",
        subtype = "DIR_PATH",
        default = "//"
    )

def unregister():
    del bpy.types.Scene.ee_export_path
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

    bpy.utils.unregister_class(TA_PT_easy_export)
    bpy.utils.unregister_class(TA_OT_unify)
    bpy.utils.unregister_class(TA_OT_move_origin)
    bpy.utils.unregister_class(TA_PT_rename_tool_panel)
    bpy.utils.unregister_class(TA_PT_tools_panel)
    bpy.utils.unregister_class(TA_OT_export_selected)
    bpy.utils.unregister_class(TA_OT_rename_selected)

if __name__ == "__main__":
    register()