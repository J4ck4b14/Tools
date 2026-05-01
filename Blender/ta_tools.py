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

class TA_PT_rename_selected(bpy.types.Operator):
    bl_idname = "ta.rename_selected"
    bl_label = "Rename Selected"

    def execute(self, context):
        prefix = context.scene.rs_prefix
        itemName = context.scene.rs_name
        for index, obj in enumerate(context.selected_objects, start=1):
            obj.name = f"{prefix}_{itemName}_{index:03d}"

        self.report({"INFO"}, "Renamed selected objects")
        return{"FINISHED"}

def get_extreme_vertex(obj, axis_str):
        """Finds the axis-most vertex of the given axis (+-X, +-Y, +-Z)"""
        axis_index = {'X': 0, 'Y': 1, 'Z':2}[axis_str[-1]]
        find_max = not axis_str.startswith('-')

        bpy.ops.object.mode_set(mode="OBJECT")

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

class TA_PT_export_selected(bpy.types.Operator):
    bl_idname = "ta.export_selected"
    bl_label = "Export Selected"

    def execute(self, context):
        unify = context.scene.ee_unify
        if unify == True and \
           bpy.context.active_object and \
           bpy.context.active_object.type == "MESH":
            bpy.ops.object.join()
        
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
        
        print(f"Axes most coordinate = {target_co}")

        offset = target_co - obj.matrix_world.translation

        #Move mesh geometry against the origin movement, so the visual stays the same
        for v in mesh.vertices:
            v.co -= offset

        if obj.matrix_world.translation == target_co:
            print("No changes were needed.")
        else:
            obj.matrix_world.translation = target_co

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

        layout.prop(scene, "ee_unify")

        col = layout.column()
        col.label(text="Select origin axis")
        row = layout.row()
        row.prop(scene, "x_axis", text="")
        row.prop(scene, "y_axis", text="")
        row.prop(scene, "z_axis", text="")

        layout.operator("ta.export_selected")

def register():
    bpy.utils.register_class(TA_PT_rename_selected)
    bpy.utils.register_class(TA_PT_export_selected)
    bpy.utils.register_class(TA_PT_tools_panel)
    bpy.utils.register_class(TA_PT_rename_tool_panel)
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
        items = [("NA", "None", ""), ("X", "X", ""), ("-X", "-X", "")],
        update=update_axis
    )

    bpy.types.Scene.y_axis = bpy.props.EnumProperty(
        items = [("NA", "None", ""), ("Y", "Y", ""), ("-Y", "-Y", "")],
        update=update_axis
    )

    bpy.types.Scene.z_axis = bpy.props.EnumProperty(
        items = [("NA", "None", ""), ("Z", "Z", ""), ("-Z", "-Z", "")],
        update=update_axis
    )

def unregister():
    del bpy.types.Scene.z_axis
    del bpy.types.Scene.y_axis
    del bpy.types.Scene.x_axis
    del bpy.types.Scene.ee_unify
    del bpy.types.Scene.rs_name
    del bpy.types.Scene.rs_prefix
    bpy.utils.unregister_class(TA_PT_easy_export)
    bpy.utils.unregister_class(TA_PT_rename_tool_panel)
    bpy.utils.unregister_class(TA_PT_tools_panel)
    bpy.utils.unregister_class(TA_PT_export_selected)
    bpy.utils.unregister_class(TA_PT_rename_selected)

if __name__ == "__main__":
    register()