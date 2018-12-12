
#
# This script is licensed as public domain.
#

from .utils import PathType, GetFilepath, CheckFilepath, \
                   FloatToString, Vector3ToString, Vector4ToString, \
                   WriteXmlFile, SDBMHash

from xml.etree import ElementTree as ET
from mathutils import Vector, Quaternion, Matrix
import bpy
import os
import logging
import math

jsonNodetreeAvailable = False
log = logging.getLogger("ExportLogger")

jsonNodetreeAvailable = "addon_jsonnodetree" in bpy.context.user_preferences.addons.keys()
if jsonNodetreeAvailable:
    from addon_jsonnodetree import JSONNodetree
    from addon_jsonnodetree import JSONNodetreeUtils

 
#-------------------------
# Scene and nodes classes
#-------------------------

# Options for scene and nodes export
class SOptions:
    def __init__(self):
        self.doIndividualPrefab = False
        self.doCollectivePrefab = False
        self.doScenePrefab = False
        self.noPhysics = False
        self.individualPhysics = False
        self.individualPrefab_onlyRootObject = True
        self.globalPhysics = False
        self.mergeObjects = False
        self.shape = None
        self.shapeItems = None
        self.trasfObjects = False
        self.exportUserdata = True
        self.globalOrigin = False
        self.orientation = Quaternion((1.0, 0.0, 0.0, 0.0))
        self.wiredAsEmpty = False
        self.exportGroupsAsObject = True
        self.objectsPath = "Objects"


class UrhoSceneMaterial:
    def __init__(self):
        # Material name
        self.name = None
        # List\Tuple of textures
        self.texturesList = None

    def Load(self, uExportData, uGeometry):
        self.name = uGeometry.uMaterialName
        for uMaterial in uExportData.materials:
            if uMaterial.name == self.name:
                self.texturesList = uMaterial.getTextures()
                break


class UrhoSceneModel:
    def __init__(self):
        # Model name
        self.name = None
        # Blender object name
        self.objectName = None
        # Parent Blender object name
        self.parentObjectName = None
        # Model type
        self.type = None
        # List of UrhoSceneMaterial
        self.materialsList = []
        # Model bounding box
        self.boundingBox = None
        # Model position
        self.position = Vector()
        # Model rotation
        self.rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        # Model scale
        self.scale = Vector((1.0, 1.0, 1.0))
    def Load(self, uExportData, uModel, objectName, sOptions):
        self.name = uModel.name

        self.blenderObjectName = objectName
        if objectName:
            object = bpy.data.objects[objectName]

            transObject = object
            if object.parent and object.parent.type=="ARMATURE":
                print("FOUND PARENT Armature!")
                transObject = object.parent

            # Get the local matrix (relative to parent)
            objMatrix = transObject.matrix_local
            # Reorient (normally only root objects need to be re-oriented but 
            # here we need to undo the previous rotation done by DecomposeMesh)
            if sOptions.orientation:
                om = sOptions.orientation.to_matrix().to_4x4()
                objMatrix = om * objMatrix * om.inverted()

            # Get pos/rot/scale
            pos = objMatrix.to_translation()
            rot = objMatrix.to_quaternion()
            scale = objMatrix.to_scale()

            self.position = Vector((pos.x, pos.z, pos.y))
            self.rotation = Quaternion((rot.w, -rot.x, -rot.z, -rot.y))
            self.scale = Vector((scale.x, scale.z, scale.y))

            # Get parent object
            parentObject = transObject.parent
            if parentObject :
                self.parentObjectName = parentObject.name

        if len(uModel.bones) > 0 or len(uModel.morphs) > 0:
            self.type = "AnimatedModel"
        else:
            self.type = "StaticModel"

        for uGeometry in uModel.geometries:
            uSceneMaterial = UrhoSceneMaterial()
            uSceneMaterial.Load(uExportData, uGeometry)
            self.materialsList.append(uSceneMaterial)

        self.boundingBox = uModel.boundingBox

# Get the object quaternion rotation, convert if it uses other rotation modes
def GetQuatenion(obj):
    # Quaternion mode
    if obj.rotation_mode == 'QUATERNION':
        return obj.rotation_quaternion
    # Axis Angle mode
    if obj.rotation_mode == 'AXIS_ANGLE':
        rot = obj.rotation_axis_angle
        return Quaternion(Vector((rot[1], rot[2], rot[3])), rot[0])
    # Euler mode
    return obj.rotation_euler.to_quaternion()

# Hierarchical sorting (based on a post by Hyperboreus at SO)
class Node:
    def __init__(self, name):
        self.name = name
        self.children = []
        self.parent = None

    def to_list(self):
        names = [self.name]
        for child in self.children:
            names.extend(child.to_list())
        return names
            
class Tree:
    def __init__(self):
    	self.nodes = {}

    def push(self, item):
        name, parent = item
        if name not in self.nodes:
        	self.nodes[name] = Node(name)
        if parent:
            if parent not in self.nodes:
                self.nodes[parent] = Node(parent)
            if parent != name:
                self.nodes[name].parent = self.nodes[parent]
                self.nodes[parent].children.append(self.nodes[name])

    def to_list(self):
        names = []
        for node in self.nodes.values():
            if not node.parent:
                names.extend(node.to_list())
        return names

class UrhoScene:
    def __init__(self, blenderScene):
        # Blender scene name
        self.blenderSceneName = blenderScene.name
        # List of UrhoSceneModel
        self.modelsList = []
        # List of all files
        self.files = {}

    # name must be unique in its type
    def AddFile(self, pathType, name, fileUrhoPath):
        if not name:
            log.critical("Name null type:{:s} path:{:s}".format(pathType, fileUrhoPath) )
            return False
        if name in self.files:
            log.critical("Already added type:{:s} name:{:s}".format(pathType, name) )
            return False
        self.files[pathType+name] = fileUrhoPath
        return True

    def FindFile(self, pathType, name):
        if name is None:
            return ""
        try:
            return self.files[pathType+name]
        except KeyError:
            return ""

    def Load(self, uExportData, objectName, sOptions):
        for uModel in uExportData.models:
            uSceneModel = UrhoSceneModel()
            uSceneModel.Load(uExportData, uModel, objectName, sOptions)
            self.modelsList.append(uSceneModel)

    def SortModels(self):
        # Sort by parent-child relation
        names_tree = Tree()
        for model in self.modelsList:
            ##names_tree.push((model.objectName, model.parentObjectName))
            names_tree.push((model.name, model.parentObjectName))
        # Rearrange the model list in the new order
        orderedModelsList = []
        for name in names_tree.to_list():
            for model in self.modelsList:
                ##if model.objectName == name:
                if model.name == name:
                    orderedModelsList.append(model)
                    # No need to reverse the list, we break straightway
                    self.modelsList.remove(model)
                    break
        self.modelsList = orderedModelsList

#------------------------
# Export materials
#------------------------

def UrhoWriteMaterial(uScene, uMaterial, filepath, fOptions):

    materialElem = ET.Element('material')

    #comment = ET.Comment("Material {:s} created from Blender".format(uMaterial.name))
    #materialElem.append(comment)

    # Technique
    techniquFile = GetFilepath(PathType.TECHNIQUES, uMaterial.techniqueName, fOptions)
    techniqueElem = ET.SubElement(materialElem, "technique")
    techniqueElem.set("name", techniquFile[1])

    # Textures
    if uMaterial.diffuseTexName:
        diffuseElem = ET.SubElement(materialElem, "texture")
        diffuseElem.set("unit", "diffuse")
        diffuseElem.set("name", uScene.FindFile(PathType.TEXTURES, uMaterial.diffuseTexName))

    if uMaterial.normalTexName:
        normalElem = ET.SubElement(materialElem, "texture")
        normalElem.set("unit", "normal")
        normalElem.set("name", uScene.FindFile(PathType.TEXTURES, uMaterial.normalTexName))

    if uMaterial.specularTexName:
        specularElem = ET.SubElement(materialElem, "texture")
        specularElem.set("unit", "specular")
        specularElem.set("name", uScene.FindFile(PathType.TEXTURES, uMaterial.specularTexName))

    if uMaterial.emissiveTexName:
        emissiveElem = ET.SubElement(materialElem, "texture")
        emissiveElem.set("unit", "emissive")
        emissiveElem.set("name", uScene.FindFile(PathType.TEXTURES, uMaterial.emissiveTexName))

    # PS defines
    if uMaterial.psdefines != "":
        psdefineElem = ET.SubElement(materialElem, "shader")
        psdefineElem.set("psdefines", uMaterial.psdefines.lstrip())

    # VS defines
    if uMaterial.vsdefines != "":
        vsdefineElem = ET.SubElement(materialElem, "shader")
        vsdefineElem.set("vsdefines", uMaterial.vsdefines.lstrip())

    # Parameters
    if uMaterial.diffuseColor:
        diffuseColorElem = ET.SubElement(materialElem, "parameter")
        diffuseColorElem.set("name", "MatDiffColor")
        diffuseColorElem.set("value", Vector4ToString(uMaterial.diffuseColor) )

    if uMaterial.specularColor:
        specularElem = ET.SubElement(materialElem, "parameter")
        specularElem.set("name", "MatSpecColor")
        specularElem.set("value", Vector4ToString(uMaterial.specularColor) )

    if uMaterial.emissiveColor:
        emissiveElem = ET.SubElement(materialElem, "parameter")
        emissiveElem.set("name", "MatEmissiveColor")
        emissiveElem.set("value", Vector3ToString(uMaterial.emissiveColor) )

    if uMaterial.twoSided:
        cullElem = ET.SubElement(materialElem, "cull")
        cullElem.set("value", "none")
        shadowCullElem = ET.SubElement(materialElem, "shadowcull")
        shadowCullElem.set("value", "none")

    WriteXmlFile(materialElem, filepath, fOptions)


def UrhoWriteMaterialsList(uScene, uModel, filepath):

    # Search for the model in the UrhoScene
    for uSceneModel in uScene.modelsList:
        if uSceneModel.name == uModel.name:
            break
    else:
        return

    # Get the model materials and their corresponding file paths
    content = ""
    for uSceneMaterial in uSceneModel.materialsList:
        file = uScene.FindFile(PathType.MATERIALS, uSceneMaterial.name)
        # If the file is missing add a placeholder to preserve the order
        if not file:
            file = "null"
        content += file + "\n"

    try:
        file = open(filepath, "w")
    except Exception as e:
        log.error("Cannot open file {:s} {:s}".format(filepath, e))
        return
    file.write(content)
    file.close()


#------------------------
# Export scene and nodes
#------------------------

def CreateNodeTreeXML(xmlroot,nodetreeID,nodeID):
    nodetree = JSONNodetreeUtils.getNodetreeById(nodetreeID)
    # get all changed values
    # TODO: this might be a bit fragile. Maybe using an property-event to set a dirty-flag!?
    exportNodeTree = JSONNodetree.exportNodes(nodetree,True)

    # a node is in urho3d a component
    for node in exportNodeTree["nodes"]:
        bodyElem = ET.SubElement(xmlroot, "component")
        bodyElem.set("type", node["name"])
        nodeID += 1
        bodyElem.set("id", "{:d}".format(nodeID))

        # node-properties are the component-attributes
        for prop in node["props"]:
            modelElem = ET.SubElement(bodyElem, "attribute")
            modelElem.set("name", prop["name"])
            value = prop["value"]
            if prop["type"].startswith("vector"):
                value = value.replace("(","")
                value = value.replace(")","")
                value = value.replace(","," ")

            modelElem.set("value", value)

    return nodeID



## Where was the scene to generate the node again, since all was already done in scene-handling?

# Generate individual prefabs XML
# def IndividualPrefabXml(uScene, uSceneModel, sOptions):



#     # Set first node ID
#     nodeID = 0x1000000

#     # Get model file relative path
#     modelFile = uScene.FindFile(PathType.MODELS, uSceneModel.name)

#     # Gather materials
#     materials = ""
#     for uSceneMaterial in uSceneModel.materialsList:
#         file = uScene.FindFile(PathType.MATERIALS, uSceneMaterial.name)
#         materials += ";" + file

#     # Generate xml prefab content
#     rootNodeElem = ET.Element('node')
#     rootNodeElem.set("id", "{:d}".format(nodeID))
#     modelNameElem = ET.SubElement(rootNodeElem, "attribute")
#     modelNameElem.set("name", "Name")
#     modelNameElem.set("value", uSceneModel.name)

#     obj = bpy.data.objects[uSceneModel.name]

#     # the default-behaviour
#     typeElem = ET.SubElement(rootNodeElem, "component")
#     typeElem.set("type", uSceneModel.type)
#     typeElem.set("id", "{:d}".format(nodeID))

#     modelElem = ET.SubElement(typeElem, "attribute")
#     modelElem.set("name", "Model")
#     modelElem.set("value", "Model;" + modelFile)

#     materialElem = ET.SubElement(typeElem, "attribute")
#     materialElem.set("name", "Material")
#     materialElem.set("value", "Material" + materials)

#     if jsonNodetreeAvailable and hasattr(obj,"nodetreeId") and obj.nodetreeId!=-1:
#         # bypass nodeID and receive the new value
#         nodeID = CreateNodeTreeXML(rootNodeElem,obj.nodetreeId,nodeID)
#     else:
#         if not sOptions.noPhysics:
#             #Use model's bounding box to compute CollisionShape's size and offset
#             physicsSettings = [sOptions.shape] #tData.physicsSettings = [sOptions.shape, obj.game.physics_type, obj.game.mass, obj.game.radius, obj.game.velocity_min, obj.game.velocity_max, obj.game.collision_group, obj.game.collision_mask, obj.game.use_ghost] **************************************
#             shapeType = physicsSettings[0]
#             bbox = uSceneModel.boundingBox
#             #Size
#             x = bbox.max[0] - bbox.min[0]
#             y = bbox.max[1] - bbox.min[1]
#             z = bbox.max[2] - bbox.min[2]
#             shapeSize = Vector((x, y, z))
#             #Offset
#             offsetX = bbox.max[0] - x / 2
#             offsetY = bbox.max[1] - y / 2
#             offsetZ = bbox.max[2] - z / 2
#             shapeOffset = Vector((offsetX, offsetY, offsetZ))

#             bodyElem = ET.SubElement(rootNodeElem, "component")
#             bodyElem.set("type", "RigidBody")
#             bodyElem.set("id", "{:d}".format(nodeID+1))

#             collisionLayerElem = ET.SubElement(bodyElem, "attribute")
#             collisionLayerElem.set("name", "Collision Layer")
#             collisionLayerElem.set("value", "2")

#             gravityElem = ET.SubElement(bodyElem, "attribute")
#             gravityElem.set("name", "Use Gravity")
#             gravityElem.set("value", "false")

#             shapeElem = ET.SubElement(rootNodeElem, "component")
#             shapeElem.set("type", "CollisionShape")
#             shapeElem.set("id", "{:d}".format(nodeID+2))

#             shapeTypeElem = ET.SubElement(shapeElem, "attribute")
#             shapeTypeElem.set("name", "Shape Type")
#             shapeTypeElem.set("value", shapeType)

#             if shapeType == "TriangleMesh":
#                 physicsModelElem = ET.SubElement(shapeElem, "attribute")
#                 physicsModelElem.set("name", "Model")
#                 physicsModelElem.set("value", "Model;" + modelFile)

#             else:
#                 shapeSizeElem = ET.SubElement(shapeElem, "attribute")
#                 shapeSizeElem.set("name", "Size")
#                 shapeSizeElem.set("value", Vector3ToString(shapeSize))

#                 shapeOffsetElem = ET.SubElement(shapeElem, "attribute")
#                 shapeOffsetElem.set("name", "Offset Position")
#                 shapeOffsetElem.set("value", Vector3ToString(shapeOffset))

#     return rootNodeElem



def AddGroupInstanceComponent(a,m,groupFilename,modelNode):

    attribID = m
    a["{:d}".format(m)] = ET.SubElement(a[modelNode], "component")
    a["{:d}".format(m)].set("type", "GroupInstance")
    a["{:d}".format(m)].set("id", str(m))
    
    m += 1

    a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(attribID)], "attribute")
    a["{:d}".format(m)].set("name", "groupFilename")
    a["{:d}".format(m)].set("value", groupFilename)
    
    return m

## add userdata-attributes 
def ExportUserdata(a,m,obj,modelNode):
    attribID = m
    a["{:d}".format(m)] = ET.SubElement(a[modelNode], "attribute")
    a["{:d}".format(m)].set("name", "Variables")
    m += 1

    tags = []

    for ud in obj.user_data:
        if ud.key.lower() != "tag":
            a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(attribID)], "variant")
            a["{:d}".format(m)].set("hash", str(SDBMHash(ud.key)))
            a["{:d}".format(m)].set("type", "String")
            a["{:d}".format(m)].set("value", ud.value)
            m += 1
        else:
            tags.extend(ud.value.split(","))

    if tags:
        tagsID = m
        a["{:d}".format(tagsID)] = ET.SubElement(a[modelNode], "attribute")
        a["{:d}".format(tagsID)].set("name", "Tags")
        m += 1
        for tag in tags:
            a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(tagsID)], "string")
            a["{:d}".format(m)].set("value", tag.strip())
            m += 1                    
    return m

# look up userdata in the specific object with the given key. return None if not present
def GetUserData(obj,key):
    for kv in obj.user_data:
        if kv.key==key:
            return kv
    return None

# find tag with value 
def HasTag(obj,tagName):
    for kv in obj.user_data:
        if kv.key=="tag" and kv.value==tagName:
            return True
    return False

def ProcessNodetreeMaterial(obj):
    if obj.materialNodetreeName=="":
        print("No materialnodetree on obj:"+obj.name)
        return None

    materialNT = bpy.data.node_groups[obj.materialNodetreeName]

    if materialNT.id_data.bl_idname!="urho3dmaterials":
        print("Using invalid material-nodetree:"+obj.materialNodetreeName+" on "+obj.name)
        return None

    for node in materialNT.nodes:
        if node.bl_idname=="urho3dmaterials__materialNode":
            return node.prop_Material
    
    print("Using invalid material-nodetree:"+obj.materialNodetreeName+" on "+obj.name)

    return None


# Export scene and nodes
def UrhoExportScene(context, uScene, sOptions, fOptions):
    blenderScene = bpy.data.scenes[uScene.blenderSceneName]
    
    '''
    # Re-order meshes
    orderedModelsList = []
    for obj in blenderScene.objects:
        if obj.type == 'MESH':
            for uSceneModel in uScene.modelsList:
                if uSceneModel.objectName == obj.name:
                    orderedModelsList.append(uSceneModel)
    uScene.modelsList = orderedModelsList
    '''

    a = {}
    k = 0x1000000   # node ID
    compoID = k     # component ID
    m = 0           # internal counter

    # Create scene components
    if sOptions.doScenePrefab:
        sceneRoot = ET.Element('scene')
        sceneRoot.set("id", "1")

        a["{:d}".format(m)] = ET.SubElement(sceneRoot, "component")
        a["{:d}".format(m)].set("type", "Octree")
        a["{:d}".format(m)].set("id", "1")

        a["{:d}".format(m+1)] = ET.SubElement(sceneRoot, "component")
        a["{:d}".format(m+1)].set("type", "DebugRenderer")
        a["{:d}".format(m+1)].set("id", "2")

        m += 2

        if not sOptions.noPhysics:
            a["{:d}".format(m)] = ET.SubElement(sceneRoot, "component")
            a["{:d}".format(m)].set("type", "PhysicsWorld")
            a["{:d}".format(m)].set("id", "4")
            m += 1

        # Create Root node
        root = ET.SubElement(sceneRoot, "node")
    else: 
        # Root node
        root = ET.Element('node') 

    root.set("id", "{:d}".format(k))
    a["{:d}".format(m)] = ET.SubElement(root, "attribute")
    a["{:d}".format(m)].set("name", "Name")
    a["{:d}".format(m)].set("value", uScene.blenderSceneName)

    a["lightnode"] = ET.SubElement(root, "node")

    a["{:d}".format(m+2)] = ET.SubElement(a["lightnode"], "component")
    a["{:d}".format(m+2)].set("type", "Light")
    a["{:d}".format(m+2)].set("id", "3")

    a["{:d}".format(m+3)] = ET.SubElement(a["{:d}".format(m+2)], "attribute")
    a["{:d}".format(m+3)].set("name", "Light Type")
    a["{:d}".format(m+3)].set("value", "Directional")

    a["{:d}".format(m+4)] = ET.SubElement(a["lightnode"], "attribute")
    a["{:d}".format(m+4)].set("name", "Rotation")
    a["{:d}".format(m+4)].set("value", "0.884784 0.399593 0.239756 -0")


    # Create physics stuff for the root node
    if sOptions.globalPhysics:
        a["{:d}".format(m)] = ET.SubElement(root, "component")
        a["{:d}".format(m)].set("type", "RigidBody")
        a["{:d}".format(m)].set("id", "{:d}".format(compoID))

        a["{:d}".format(m+1)] = ET.SubElement(a["{:d}".format(m)] , "attribute")
        a["{:d}".format(m+1)].set("name", "Collision Layer")
        a["{:d}".format(m+1)].set("value", "2")

        a["{:d}".format(m+2)] = ET.SubElement(a["{:d}".format(m)], "attribute")
        a["{:d}".format(m+2)].set("name", "Use Gravity")
        a["{:d}".format(m+2)].set("value", "false")

        a["{:d}".format(m+3)] = ET.SubElement(root, "component")
        a["{:d}".format(m+3)].set("type", "CollisionShape")
        a["{:d}".format(m+3)].set("id", "{:d}".format(compoID+1))
        m += 3

        a["{:d}".format(m+1)] = ET.SubElement(a["{:d}".format(m)], "attribute")
        a["{:d}".format(m+1)].set("name", "Shape Type")
        a["{:d}".format(m+1)].set("value", "TriangleMesh")

        physicsModelFile = GetFilepath(PathType.MODELS, "Physics", fOptions)[1]
        a["{:d}".format(m+2)] = ET.SubElement(a["{:d}".format(m)], "attribute")
        a["{:d}".format(m+2)].set("name", "Model")
        a["{:d}".format(m+2)].set("value", "Model;" + physicsModelFile)
        m += 2
        compoID += 2

    if sOptions.trasfObjects and sOptions.globalOrigin:
        log.warning("To export objects transformations you should use Origin = Local")

    # Sort the models by parent-child relationship
    uScene.SortModels()

    # save the parent objects
    parentObjects = []

    # what object in what groups
    groupObjMapping = {}
    groups=[]
    # Export each decomposed object
    def ObjInGroup(obj):
        return obj.name in groupObjMapping
    def GetGroupName(grpName):
        return "group_"+grpName    

    if (sOptions.exportGroupsAsObject):
        ## create a mapping to determine in which groups the corressponding object is contained
        for group in bpy.data.groups:
            ## ignore internal groups (that might be created,...there are more to be fished out here)
            if group.name=="RigidBodyConstraints" or group.name=="RigidBodyWorld":
                continue
            for grpObj in group.objects:
                print(("obj:%s grp:%s") %(grpObj.name,group.name) )

                if grpObj.name in groupObjMapping:
                    log.critical("Object:{:s} is in multiple groups! Only one group supported! Using grp:%s".format(grpObj.name, groupObjMapping[grpObj.name]) )
                else:
                    groupObjMapping[grpObj.name]=group

        
    for uSceneModel in uScene.modelsList:
        modelNode = uSceneModel.name
        log.info ("Process %s" % modelNode)
        isEmpty = False
        obj = None
        try:
            obj = bpy.data.objects[modelNode]
            isEmpty = (sOptions.wiredAsEmpty and obj.draw_type=="WIRE") or obj.type=="EMPTY"
        except:
            pass

        modelFile = None
        materials = None
        if not isEmpty:
            # Get model file relative path
            modelFile = uScene.FindFile(PathType.MODELS, modelNode)

            # Gather materials
            materials = ""
            if jsonNodetreeAvailable and obj.materialNodetreeName!="":
                materialNT = ProcessNodetreeMaterial(obj)
                if materialNT: # could we get an urho3d-materialfile corresponding to the material-tree?
                    materials = ";"+materialNT
            
            if materials == "": # not processed via materialnodes,yet? use the default way
                for uSceneMaterial in uSceneModel.materialsList:
                    file = uScene.FindFile(PathType.MATERIALS, uSceneMaterial.name)
                    materials += ";" + file
        # elif sOptions.exportGroupsAsObject:
        #     if obj.dupli_type == 'GROUP':
        #         grp = obj.dupli_group
        #         # check if we already have a group__ element in which we store the filename of the group
        #         ud = GetUserData(obj,"group__")
        #         if not ud:
        #             ud = obj.user_data.add()
        #             ud.key="group__"
        #         ud.value = sOptions.objectsPath+"/"+GetGroupName(grp.name)+".xml"
                
        #         if not HasTag(obj,"groupInstance__"):
        #             tag = obj.user_data.add()
        #             tag.key="tag"
        #             tag.value="groupInstance__"
                

        # Generate XML Content
        k += 1
        

        # Parenting: make sure parented objects are child of this in xml as well
        print ( ("PARENT:%s type:%s") % (str(uSceneModel.parentObjectName),str(uSceneModel.type )))
        if not isEmpty and uSceneModel.parentObjectName and (uSceneModel.parentObjectName in a):
            for usm in uScene.modelsList:
                if usm.name == uSceneModel.parentObjectName:
                    a[modelNode] = ET.SubElement(a[usm.name], "node")
                    break
        else:
            if not ObjInGroup(obj):
                print("not in group:"+obj.name)
                a[modelNode] = ET.SubElement(root, "node")
                parentObjects.append({'xml':a[modelNode],'uSceneModel':uSceneModel})
            else:
                print("FOUND GROUP OBJ:%s",obj.name)
                group = groupObjMapping[obj.name]
                groupName = "grp_"+group.name
                
                # get or create node for the group
                if  groupName not in a:
                    a[groupName] = ET.Element('node')
                    groups.append({'xml':a[groupName],'obj':obj,'group':group })
                    # apply group offset
                    offset = group.dupli_offset
                    modelPos = uSceneModel.position
                    ## CAUTION/TODO: this only works for default front-view (I guess)
                    print("POSITION %s : offset %s" % ( modelPos,offset ))
                    newPos = Vector( (modelPos.x - offset.y, modelPos.y - offset.z, modelPos.z - offset.x) )
                    uSceneModel.position = newPos


                
                # create root for the object
                a[modelNode] = ET.SubElement(a[groupName],'node') 

        a[modelNode].set("id", "{:d}".format(k))

        a["{:d}".format(m)] = ET.SubElement(a[modelNode], "attribute")
        a["{:d}".format(m)].set("name", "Name")
        a["{:d}".format(m)].set("value", uSceneModel.name)
        m += 1

        if sOptions.trasfObjects:
            a["{:d}".format(m)] = ET.SubElement(a[modelNode], "attribute")
            a["{:d}".format(m)].set("name", "Position")
            a["{:d}".format(m)].set("value", Vector3ToString(uSceneModel.position))
            m += 1
            a["{:d}".format(m)] = ET.SubElement(a[modelNode], "attribute")
            a["{:d}".format(m)].set("name", "Rotation")
            a["{:d}".format(m)].set("value", Vector4ToString(uSceneModel.rotation))
            m += 1
            a["{:d}".format(m)] = ET.SubElement(a[modelNode], "attribute")
            a["{:d}".format(m)].set("name", "Scale")
            a["{:d}".format(m)].set("value", Vector3ToString(uSceneModel.scale))
            m += 1
        
        if sOptions.exportUserdata and obj and len(obj.user_data)>0:
            m = ExportUserdata(a,m,obj,modelNode)
        
        if sOptions.exportGroupsAsObject and obj.dupli_type == 'GROUP':
            grp = obj.dupli_group
            grpFilename = sOptions.objectsPath+"/"+GetGroupName(grp.name)+".xml"
            m = AddGroupInstanceComponent(a,m,grpFilename,modelNode)


        if not isEmpty:
            compID = m
            a["{:d}".format(compID)] = ET.SubElement(a[modelNode], "component")
            a["{:d}".format(compID)].set("type", uSceneModel.type)
            a["{:d}".format(compID)].set("id", "{:d}".format(compoID))
            m += 1

            a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(compID)], "attribute")
            a["{:d}".format(m)].set("name", "Model")
            a["{:d}".format(m)].set("value", "Model;" + modelFile)
            m += 1

            a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(compID)], "attribute")
            a["{:d}".format(m)].set("name", "Material")
            a["{:d}".format(m)].set("value", "Material" + materials)
            m += 1
            compoID += 1

            finishedNodeTree = False
            try:
                if jsonNodetreeAvailable and hasattr(obj,"nodetreeId") and obj.nodetreeId!=-1:
                    # bypass nodeID and receive the new value
                    compoID = CreateNodeTreeXML(a[modelNode],obj.nodetreeId,compoID)
                    finishedNodeTree = True
            except:
                log.critical("Couldn't export nodetree. skipping nodetree and going on with default behaviour")
                pass

            if not finishedNodeTree:
                # the default-behaviour
                if sOptions.individualPhysics:
                    #Use model's bounding box to compute CollisionShape's size and offset
                    physicsSettings = [sOptions.shape] #tData.physicsSettings = [sOptions.shape, obj.game.physics_type, obj.game.mass, obj.game.radius, obj.game.velocity_min, obj.game.velocity_max, obj.game.collision_group, obj.game.collision_mask, obj.game.use_ghost] **************************************
                    shapeType = physicsSettings[0]
                    if not sOptions.mergeObjects and obj.game.use_collision_bounds:
                        for shapeItems in sOptions.shapeItems:
                            if shapeItems[0] == obj.game.collision_bounds_type:
                                shapeType = shapeItems[1]
                                break
                    bbox = uSceneModel.boundingBox
                    #Size
                    shapeSize = Vector()
                    if bbox.min and bbox.max:
                        shapeSize.x = bbox.max[0] - bbox.min[0]
                        shapeSize.y = bbox.max[1] - bbox.min[1]
                        shapeSize.z = bbox.max[2] - bbox.min[2]
                    #Offset
                    shapeOffset = Vector()
                    if bbox.max:
                        shapeOffset.x = bbox.max[0] - shapeSize.x / 2
                        shapeOffset.y = bbox.max[1] - shapeSize.y / 2
                        shapeOffset.z = bbox.max[2] - shapeSize.z / 2

                    a["{:d}".format(m)] = ET.SubElement(a[modelNode], "component")
                    a["{:d}".format(m)].set("type", "RigidBody")
                    a["{:d}".format(m)].set("id", "{:d}".format(compoID))
                    m += 1

                    a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(m-1)], "attribute")
                    a["{:d}".format(m)].set("name", "Collision Layer")
                    a["{:d}".format(m)].set("value", "2")
                    m += 1

                    a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(m-2)], "attribute")
                    a["{:d}".format(m)].set("name", "Use Gravity")
                    a["{:d}".format(m)].set("value", "false")
                    m += 1

                    a["{:d}".format(m)] = ET.SubElement(a[modelNode], "component")
                    a["{:d}".format(m)].set("type", "CollisionShape")
                    a["{:d}".format(m)].set("id", "{:d}".format(compoID+1))
                    m += 1

                    a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(m-1)] , "attribute")
                    a["{:d}".format(m)].set("name", "Shape Type")
                    a["{:d}".format(m)].set("value", shapeType)
                    m += 1

                    if shapeType == "TriangleMesh":
                        a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(m-2)], "attribute")
                        a["{:d}".format(m)].set("name", "Model")
                        a["{:d}".format(m)].set("value", "Model;" + modelFile)

                    else:
                        a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(m-2)] , "attribute")
                        a["{:d}".format(m)].set("name", "Size")
                        a["{:d}".format(m)].set("value", Vector3ToString(shapeSize))
                        m += 1

                        a["{:d}".format(m)] = ET.SubElement(a["{:d}".format(m-3)] , "attribute")
                        a["{:d}".format(m)].set("name", "Offset Position")
                        a["{:d}".format(m)].set("value", Vector3ToString(shapeOffset))
                        m += 1

                    compoID += 2
        else:
            if jsonNodetreeAvailable and hasattr(obj,"nodetreeId") and obj.nodetreeId!=-1:
                # bypass nodeID and receive the new value
                compoID = CreateNodeTreeXML(a[modelNode],obj.nodetreeId,compoID)

        # Write individual prefabs
        if sOptions.doIndividualPrefab and not sOptions.individualPrefab_onlyRootObject:
            filepath = GetFilepath(PathType.OBJECTS, uSceneModel.name, fOptions)
            if CheckFilepath(filepath[0], fOptions):
                log.info( "Creating prefab {:s}".format(filepath[1]) )
                WriteXmlFile(a[modelNode], filepath[0], fOptions)

        # Merging objects equates to an individual export. And collective equates to individual, so we can skip collective
        if sOptions.mergeObjects and sOptions.doScenePrefab: 
            filepath = GetFilepath(PathType.SCENES, uScene.blenderSceneName, fOptions)
            if CheckFilepath(filepath[0], fOptions):
                log.info( "Creating scene prefab {:s}".format(filepath[1]) )
                WriteXmlFile(sceneRoot, filepath[0], fOptions)

    # Write individual prefabs
    if sOptions.doIndividualPrefab:
        if sOptions.individualPrefab_onlyRootObject:
            for model in parentObjects:
                filepath = GetFilepath(PathType.OBJECTS, model["uSceneModel"].name, fOptions)
                if CheckFilepath(filepath[0], fOptions):
                    log.info( "!!Creating prefab {:s}".format(filepath[1]) )
                    WriteXmlFile(model["xml"], filepath[0], fOptions)
        if (sOptions.exportGroupsAsObject):
            for grp in groups:
                filepath = GetFilepath(PathType.OBJECTS, GetGroupName(grp["group"].name), fOptions)
                if CheckFilepath(filepath[0], fOptions):
                    log.info( "!!Creating group-prefab {:s}".format(filepath[1]) )
                    WriteXmlFile(grp["xml"], filepath[0], fOptions)

    # Write collective and scene prefab files
    if not sOptions.mergeObjects:

        if sOptions.doCollectivePrefab:
            filepath = GetFilepath(PathType.OBJECTS, uScene.blenderSceneName, fOptions)
            if CheckFilepath(filepath[0], fOptions):
                log.info( "Creating collective prefab {:s}".format(filepath[1]) )
                WriteXmlFile(root, filepath[0], fOptions)

        if sOptions.doScenePrefab:
            filepath = GetFilepath(PathType.SCENES, uScene.blenderSceneName, fOptions)
            if CheckFilepath(filepath[0], fOptions):
                log.info( "Creating scene prefab {:s}".format(filepath[1]) )
                WriteXmlFile(sceneRoot, filepath[0], fOptions)
