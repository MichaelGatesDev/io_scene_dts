import bpy
import os
from bpy_extras.io_utils import unpack_list

from .DtsShape import DtsShape
from .DtsTypes import *
from .write_report import write_debug_report
from .util import default_materials, resolve_texture, get_rgb_colors

import operator
from functools import reduce
from random import random

blockhead_nodes = ("HeadSkin", "chest", "Larm", "Lhand", "Rarm", "Rhand", "pants", "LShoe", "RShoe")

for name, color in default_materials.items():
    default_materials[name] = (color[0] / 255, color[1] / 255, color[2] / 255)

def import_material(color_source, dmat, filepath):
    bmat = bpy.data.materials.new(dmat.name)

    texname = resolve_texture(filepath, dmat.name)

    if texname is not None:
        try:
            teximg = bpy.data.images.load(texname)
        except:
            print("Cannot load image", texname)

        texslot = bmat.texture_slots.add()
        tex = texslot.texture = bpy.data.textures.new(dmat.name, "IMAGE")
        tex.image = teximg
    elif dmat.name.lower() in default_materials:
        bmat.diffuse_color = default_materials[dmat.name.lower()]
    else: # give it a random color
        bmat.diffuse_color = color_source.__next__()
        bmat.diffuse_intensity = 1

    if dmat.flags & Material.SelfIlluminating:
        bmat.use_shadeless = True
    if dmat.flags & Material.Translucent:
        bmat.use_transparency = True

    if dmat.flags & (Material.Additive | Material.Subtractive):
        bmat["blendMode"] = "both"
    elif dmat.flags & Material.Additive:
        bmat["blendMode"] = "additive"
    elif dmat.flags & Material.Subtractive:
        bmat["blendMode"] = "subtractive"
    elif dmat.flags & Material.Translucent:
        bmat["blendMode"] = "none"

    if not (dmat.flags & Material.SWrap):
        bmat["noSWrap"] = True
    if not (dmat.flags & Material.TWrap):
        bmat["noTWrap"] = True
    if not (dmat.flags & Material.NeverEnvMap):
        bmat["envMap"] = True
    if not (dmat.flags & Material.NoMipMap):
        bmat["mipMap"] = True
    if dmat.flags & Material.IFLMaterial:
        bmat["ifl"] = True

    # TODO: MipMapZeroBorder, IFLFrame, DetailMap, BumpMap, ReflectanceMap
    # AuxilaryMask?

    return bmat

class index_pass:
    def __getitem__(self, item):
        return item

def create_bmesh(dmesh, materials, shape):
    me = bpy.data.meshes.new("Mesh")

    faces = []
    material_indices = {}

    indices_pass = index_pass()

    for prim in dmesh.primitives:
        if prim.type & Primitive.Indexed:
            indices = dmesh.indices
        else:
            indices = indices_pass

        dmat = None

        if not (prim.type & Primitive.NoMaterial):
            dmat = shape.materials[prim.type & Primitive.MaterialMask]

            if dmat not in material_indices:
                material_indices[dmat] = len(me.materials)
                me.materials.append(materials[dmat])

        if prim.type & Primitive.Strip:
            even = True
            for i in range(prim.firstElement + 2, prim.firstElement + prim.numElements):
                if even:
                    faces.append(((indices[i], indices[i - 1], indices[i - 2]), dmat))
                else:
                    faces.append(((indices[i - 2], indices[i - 1], indices[i]), dmat))
                even = not even
        elif prim.type & Primitive.Fan:
            even = True
            for i in range(prim.firstElement + 2, prim.firstElement + prim.numElements):
                if even:
                    faces.append(((indices[i], indices[i - 1], indices[0]), dmat))
                else:
                    faces.append(((indices[0], indices[i - 1], indices[i]), dmat))
                even = not even
        else: # Default to Triangle Lists (prim.type & Primitive.Triangles)
            for i in range(prim.firstElement + 2, prim.firstElement + prim.numElements, 3):
                faces.append(((indices[i], indices[i - 1], indices[i - 2]), dmat))

    me.vertices.add(len(dmesh.verts))
    me.vertices.foreach_set("co", unpack_list(dmesh.verts))
    me.vertices.foreach_set("normal", unpack_list(dmesh.normals))

    me.polygons.add(len(faces))
    me.loops.add(len(faces) * 3)

    me.uv_textures.new()
    uvs = me.uv_layers[0]

    for i, ((verts, dmat), poly) in enumerate(zip(faces, me.polygons)):
        poly.loop_total = 3
        poly.loop_start = i * 3

        if dmat:
            poly.material_index = material_indices[dmat]

        for j, index in zip(poly.loop_indices, verts):
            me.loops[j].vertex_index = index
            uv = dmesh.tverts[index]
            uvs.data[j].uv = (uv.x, 1 - uv.y)

    me.validate()
    me.update()

    return me

def action_get_or_new(ob):
  if not ob.animation_data:
    ob.animation_data_create()

  if ob.animation_data.action:
    return ob.animation_data.action

  action = bpy.data.actions.new(ob.name + "Action")
  ob.animation_data.action = action

  return action

def ob_curves_array(ob, data_path, array_count):
  action = action_get_or_new(ob)
  curves = [None] * array_count

  for curve in action.fcurves:
    if curve.data_path != data_path or curve.array_index < 0 or curve.array_index >= array_count:
      continue

    if curves[curve.array_index]:
      pass # TODO: warn if more than one curve for an array slot

    curves[curve.array_index] = curve

  for index, curve in enumerate(curves):
    if curve is None:
      curves[index] = action.fcurves.new(data_path, index)

  return curves

def ob_location_curves(ob):
  return ob_curves_array(ob, "location", 3)

def ob_scale_curves(ob):
  return ob_curves_array(ob, "scale", 3)

def ob_rotation_curves(ob):
  if ob.rotation_mode == "QUATERNION":
    data_path = "rotation_quaternion"
    array_count = 4
  elif ob.rotation_mode == "XYZ":
    data_path = "rotation_euler"
    array_count = 3
  else:
    assert false, "unhandled rotation mode '{}' on '{}'".format(ob.rotation_mode, ob.name)

  return ob.rotation_mode, ob_curves_array(ob, data_path, array_count)

def get_node_head(i, node, shape):
    return node.mat.to_translation()

def get_node_tail(i, node, shape):
    # ischildfound = False
    # childbone = None
    # childbonelist = []
    # for j, other in enumerate(shape.nodes):
    #     if other.parent == i:
    #         ischildfound = True
    #         childbone = other
    #         childbonelist.append(other)
    #
    # if ischildfound:
    #     tmp_head = Vector((0, 0, 0))
    #     for other in childbonelist:
    #         tmp_head[0] += other.head[0]
    #         tmp_head[1] += other.head[1]
    #         tmp_head[2] += other.head[2]
    #     tmp_head[0] /= len(childbonelist)
    #     tmp_head[1] /= len(childbonelist)
    #     tmp_head[2] /= len(childbonelist)
    #     return tmp_head
    # elif node.parent != -1:
    #     parent = shape.nodes[node.parent]
    #
    #     tmp_len = 0.0
    #     tmp_len += (node.head[0] - parent.head[0]) ** 2
    #     tmp_len += (node.head[1] - parent.head[1]) ** 2
    #     tmp_len += (node.head[2] - parent.head[2]) ** 2
    #     tmp_len = tmp_len ** 0.5 * 0.5
    #
    #     return Vector((
    #         node.head[0] + tmp_len * node.mat[0][0],
    #         node.head[1] + tmp_len * node.mat[1][0],
    #         node.head[2] + tmp_len * node.mat[2][0]))
    # else:
    return node.head + Vector((0, 0, 0.25))

def file_base_name(filepath):
    return os.path.basename(filepath).rsplit(".", 1)[0]

def load_new(operator, context, filepath,
             hacky_new_bone_connect=True):
    shape = DtsShape()

    with open(filepath, "rb") as fd:
        shape.load(fd)

    root_arm = bpy.data.armatures.new(file_base_name(filepath))
    root_ob = bpy.data.objects.new(root_arm.name, root_arm)

    context.scene.objects.link(root_ob)
    context.scene.objects.active = root_ob

    root_ob.show_x_ray = True

    # Preprocess our bones with magic spice?
    for i, node in enumerate(shape.nodes):
        node.mat = shape.default_rotations[i].to_matrix()
        node.mat = Matrix.Translation(shape.default_translations[i]) * node.mat.to_4x4()

        if node.parent != -1:
            node.mat = shape.nodes[node.parent].mat * node.mat

    for i, node in enumerate(shape.nodes):
        node.head = get_node_head(i, node, shape)

    for i, node in enumerate(shape.nodes):
        node.tail = get_node_tail(i, node, shape)
        node.has_any_users = False

    for obj in shape.objects:
        if shape.names[obj.name] not in blockhead_nodes:
            continue

        node = obj.node

        while node != -1:
            shape.nodes[node].has_any_users = True
            node = shape.nodes[node].parent

    bpy.ops.object.mode_set(mode="EDIT")

    bones_indexed = []

    for i, node in enumerate(shape.nodes):
        if not node.has_any_users:
            bones_indexed.append(True)
            continue

        bone = root_arm.edit_bones.new(shape.names[node.name])

        if hacky_new_bone_connect:
            bone.use_connect = True

        if node.parent != -1:
            parent_bone = bones_indexed[node.parent]

            bone.parent = parent_bone
            bone.head = node.head
            bone.tail = node.tail

            vp = bone.parent.tail - bone.parent.head
            vc = bone.tail - bone.head
            vc.normalize()
            vp.normalize()

            if vp.dot(vc) > -0.8:
                bone.roll = bone.parent.roll
            else:
                bone.roll = -bone.parent.roll
        else:
            bone.head = node.head
            bone.tail = node.tail
            bone.roll = math.radians(90)

        bones_indexed.append(bone)

    bpy.ops.object.mode_set(mode="OBJECT")

    materials = {}
    color_source = get_rgb_colors()

    for dmat in shape.materials:
        materials[dmat] = import_material(color_source, dmat, filepath)

    # Now assign IFL material properties where needed
    for ifl in shape.iflmaterials:
        mat = materials[shape.materials[ifl.slot]]
        assert mat["ifl"] == True
        mat["iflName"] = shape.names[ifl.name]
        mat["iflFirstFrame"] = ifl.firstFrame
        mat["iflNumFrames"] = ifl.numFrames
        mat["iflTime"] = ifl.time

    detail_by_index = {}

    for lod in shape.detail_levels:
        detail_by_index[lod.objectDetail] = lod

    for obj in shape.objects:
        if shape.names[obj.name] not in blockhead_nodes:
            continue

        for index in range(obj.numMeshes):
            mesh = shape.meshes[obj.firstMesh + index]

            if mesh.type == Mesh.NullType:
                continue

            if mesh.type != Mesh.StandardType:
                print("{} is a {} mesh, unsupported, but trying".format(
                    shape.names[obj.name], mesh.type))
                # continue

            bmesh = create_bmesh(mesh, materials, shape)
            bobj = bpy.data.objects.new(shape.names[obj.name], bmesh)
            context.scene.objects.link(bobj)

            if obj.node != -1:
                # bobj.location = bones_indexed[obj.node].head
                # bobj.matrix_world = shape.nodes[obj.node].mat
                bobj.parent = root_ob
                bobj.parent_bone = bones_indexed[obj.node].name
                bobj.parent_type = "BONE"
                bobj.matrix_world = shape.nodes[obj.node].mat

            if shape.names[obj.name] not in blockhead_nodes:
                bobj.hide = True

            lod_name = shape.names[detail_by_index[index].name]

            if lod_name not in bpy.data.groups:
                bpy.data.groups.new(lod_name)

            bpy.data.groups[lod_name].objects.link(bobj)

    return {"FINISHED"}

def load(operator, context, filepath,
         hide_default_player=False,
         import_node_order=False,
         import_sequences=True,
         debug_report=False,
         hacky_new_bone_import=False,
         hacky_new_bone_connect=True):
    if hacky_new_bone_import:
        return load_new(operator, context, filepath, hacky_new_bone_connect)
    shape = DtsShape()

    with open(filepath, "rb") as fd:
        shape.load(fd)

    if debug_report:
        write_debug_report(filepath + ".txt", shape)
        with open(filepath + ".pass.dts", "wb") as fd:
            shape.save(fd)

    # Create a Blender material for each DTS material
    materials = {}
    color_source = get_rgb_colors()

    for dmat in shape.materials:
        materials[dmat] = import_material(color_source, dmat, filepath)

    # Now assign IFL material properties where needed
    for ifl in shape.iflmaterials:
        mat = materials[shape.materials[ifl.slot]]
        assert mat["ifl"] == True
        mat["iflName"] = shape.names[ifl.name]
        mat["iflFirstFrame"] = ifl.firstFrame
        mat["iflNumFrames"] = ifl.numFrames
        mat["iflTime"] = ifl.time

    # First load all the nodes into armatures
    lod_by_mesh = {}

    for lod in shape.detail_levels:
        lod_by_mesh[lod.objectDetail] = lod

    if import_node_order:
        if "NodeOrder" in bpy.data.texts:
            order_buf = bpy.data.texts["NodeOrder"]
        else:
            order_buf = bpy.data.texts.new("NodeOrder")

        order_buf.from_string("\n".join(shape.names[node.name] for node in shape.nodes))

    node_obs = []
    node_obs_val = {}

    for i, node in enumerate(shape.nodes):
        ob = bpy.data.objects.new(shape.names[node.name], None)
        ob.empty_draw_type = "SINGLE_ARROW"
        ob.empty_draw_size = 0.5

        if node.parent != -1:
            ob.parent = node_obs[node.parent]

        ob.location = shape.default_translations[i]
        ob.rotation_mode = "QUATERNION"
        ob.rotation_quaternion = shape.default_rotations[i]

        context.scene.objects.link(ob)
        node_obs.append(ob)
        node_obs_val[node] = ob

    # Try animation?
    if import_sequences:
        globalToolIndex = 10
        fps = context.scene.render.fps

        sequences_text = []

        for seq in shape.sequences:
            name = shape.names[seq.nameIndex]
            print("Importing sequence", name)

            flags = []

            if seq.flags & Sequence.Cyclic:
                flags.append("cyclic")

            if seq.flags & Sequence.Blend:
                flags.append("blend {}".format(seq.priority))

            if flags:
                sequences_text.append(name + ": " + ", ".join(flags))

            nodesRotation = tuple(map(lambda p: p[0], filter(lambda p: p[1], zip(shape.nodes, seq.rotationMatters))))
            nodesTranslation = tuple(map(lambda p: p[0], filter(lambda p: p[1], zip(shape.nodes, seq.translationMatters))))
            nodesScale = tuple(map(lambda p: p[0], filter(lambda p: p[1], zip(shape.nodes, seq.scaleMatters))))

            step = 1

            for mattersIndex, node in enumerate(nodesTranslation):
                ob = node_obs_val[node]
                curves = ob_location_curves(ob)

                for frameIndex in range(seq.numKeyframes):
                    vec = shape.node_translations[seq.baseTranslation + mattersIndex * seq.numKeyframes + frameIndex]

                    for curve in curves:
                        curve.keyframe_points.add(1)
                        key = curve.keyframe_points[-1]
                        key.interpolation = "LINEAR"
                        key.co = (
                            globalToolIndex + frameIndex * step,
                            vec[curve.array_index])

            for mattersIndex, node in enumerate(nodesRotation):
                ob = node_obs_val[node]
                mode, curves = ob_rotation_curves(ob)

                for frameIndex in range(seq.numKeyframes):
                    rot = shape.node_rotations[seq.baseRotation + mattersIndex * seq.numKeyframes + frameIndex]
                    if mode != "QUATERNION":
                        rot = rot.to_euler(mode)

                    for curve in curves:
                        curve.keyframe_points.add(1)
                        key = curve.keyframe_points[-1]
                        key.interpolation = "LINEAR"
                        key.co = (
                            globalToolIndex + frameIndex * step,
                            rot[curve.array_index])

            for mattersIndex, node in enumerate(nodesScale):
                ob = node_obs_val[node]
                curves = ob_scale_curves(ob)

                for frameIndex in range(seq.numKeyframes):
                    index = seq.baseScale + mattersIndex * seq.numKeyframes + frameIndex
                    vec = shape.node_translations[seq.baseTranslation + mattersIndex * seq.numKeyframes + frameIndex]

                    if seq.UniformScale:
                        s = shape.node_uniform_scales[index]
                        vec = (s, s, s)
                    elif seq.AlignedScale:
                        vec = shape.node_aligned_scales[index]
                    elif seq.ArbitraryScale:
                        print("Warning: Arbitrary scale animation not implemented")
                        break
                    else:
                        print("Warning: Invalid scale flags found in sequence")
                        break

                    for curve in curves:
                        curve.keyframe_points.add(1)
                        key = curve.keyframe_points[-1]
                        key.interpolation = "LINEAR"
                        key.co = (
                            globalToolIndex + frameIndex * step,
                            vec[curve.array_index])

            context.scene.timeline_markers.new(name + ":start", globalToolIndex)
            context.scene.timeline_markers.new(name + ":end", globalToolIndex + seq.numKeyframes * step)
            globalToolIndex += seq.numKeyframes * step + 30

        if "Sequences" in bpy.data.texts:
            sequences_buf = bpy.data.texts["Sequences"]
        else:
            sequences_buf = bpy.data.texts.new("Sequences")

        sequences_buf.from_string("\n".join(sequences_text))

    # Then put objects in the armatures
    for obj in shape.objects:
        for meshIndex in range(obj.numMeshes):
            mesh = shape.meshes[obj.firstMesh + meshIndex]

            if mesh.type == Mesh.NullType:
                continue

            if mesh.type != Mesh.StandardType:
                print("{} is a {} mesh, unsupported, but trying".format(
                    shape.names[obj.name], mesh.type))
                # continue

            bmesh = create_bmesh(mesh, materials, shape)
            bobj = bpy.data.objects.new(name=shape.names[obj.name], object_data=bmesh)
            context.scene.objects.link(bobj)

            if obj.node != -1:
                bobj.parent = node_obs[obj.node]

            if hide_default_player and shape.names[obj.name] not in blockhead_nodes:
                bobj.hide = True

            lod_name = shape.names[lod_by_mesh[meshIndex].name]

            if lod_name not in bpy.data.groups:
                bpy.data.groups.new(lod_name)

            bpy.data.groups[lod_name].objects.link(bobj)

    # Import a bounds mesh
    me = bpy.data.meshes.new("Mesh")
    me.vertices.add(8)
    me.vertices[0].co = (shape.bounds.min.x, shape.bounds.min.y, shape.bounds.min.z)
    me.vertices[1].co = (shape.bounds.max.x, shape.bounds.min.y, shape.bounds.min.z)
    me.vertices[2].co = (shape.bounds.max.x, shape.bounds.max.y, shape.bounds.min.z)
    me.vertices[3].co = (shape.bounds.min.x, shape.bounds.max.y, shape.bounds.min.z)
    me.vertices[4].co = (shape.bounds.min.x, shape.bounds.min.y, shape.bounds.max.z)
    me.vertices[5].co = (shape.bounds.max.x, shape.bounds.min.y, shape.bounds.max.z)
    me.vertices[6].co = (shape.bounds.max.x, shape.bounds.max.y, shape.bounds.max.z)
    me.vertices[7].co = (shape.bounds.min.x, shape.bounds.max.y, shape.bounds.max.z)
    me.validate()
    me.update()
    ob = bpy.data.objects.new("bounds", me)
    ob.draw_type = "BOUNDS"
    context.scene.objects.link(ob)

    return {"FINISHED"}
