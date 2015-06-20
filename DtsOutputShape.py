from struct import pack, unpack, calcsize
from array import array

from .DtsTypes import *

def read_struct(fd, spec):
	return unpack(spec, fd.read(calcsize(spec)))

def read_multi(fd, count, spec):
	return read_struct(fd, str(count) + spec)

class DtsOutputStream(object):
	def __init__(self, fd, dtsVersion=24, exporterVersion=0):
		self.sequence = 0
		self.buffer32 = []
		self.buffer16 = []
		self.buffer8 = []

	def guard(self):
		self.write32(self.sequence)
		self.write16(self.sequence)
		self.write8(self.sequence)
		self.sequence = self.sequence + 1

	def read32(self):
		if self.tell32 >= len(self.buffer32):
			raise EOFError()

		data = self.buffer32[self.tell32]
		self.tell32 += 1
		return data

	def read16(self):
		if self.tell16 >= len(self.buffer16):
			raise EOFError()

		data = self.buffer16[self.tell16]
		self.tell16 += 1
		return data

	def read8(self):
		if self.tell8 >= len(self.buffer8):
			raise EOFError()

		data = self.buffer8[self.tell8]
		self.tell8 += 1
		return data

	def read_float(self):
		return unpack("f", pack("i", self.read32()))[0]

	def read_point(self):
		return Point(self.read_float(), self.read_float(), self.read_float())

	def read_point2d(self):
		return Point2D(self.read_float(), self.read_float())

	def read_box(self):
		return Box(self.read_point(), self.read_point())

	def read_quat(self):
		return Quaternion(
			self.read16() / 32767.0,
			self.read16() / 32767.0,
			self.read16() / 32767.0,
			self.read16() / 32767.0)

class DtsOutputShape(object):
	def __init__(self, stream):
		self.dtsVersion = stream.dtsVersion
		self.exporterVersion = stream.exporterVersion
		self.sequences = stream.sequences
		self.materials = stream.materials

		# Header
		n_node = stream.read32()
		n_object = stream.read32()
		n_decal = stream.read32()
		n_subshape = stream.read32()
		n_ifl = stream.read32()

		if stream.dtsVersion < 22:
			n_noderotation = stream.read32()
			n_noderotation -= n_node
			n_nodetranslation = n_noderotation
			n_nodescaleuniform = 0
			n_nodescalealigned = 0
			n_nodescalearbitrary = 0
		else:
			n_noderotation = stream.read32()
			n_nodetranslation = stream.read32()
			n_nodescaleuniform = stream.read32()
			n_nodescalealigned = stream.read32()
			n_nodescalearbitrary = stream.read32()

		if stream.dtsVersion > 23:
			n_groundframe = stream.read32()
		else:
			n_groundframe = 0

		n_objectstate = stream.read32()
		n_decalstate = stream.read32()
		n_trigger = stream.read32()
		n_detaillevel = stream.read32()
		n_mesh = stream.read32()

		if stream.dtsVersion < 23:
			n_skin = stream.read32()
		else:
			n_skin = 0

		n_name = stream.read32()
		self.smallest_size = stream.read_float()
		self.smallest_detail_level = stream.read32()
		stream.guard()

		# Misc geometry properties
		self.radius = stream.read_float()
		self.radius_tube = stream.read_float()
		self.center = stream.read_point()
		self.bounds = stream.read_box()
		stream.guard()

		# Primary data
		self.nodes = [Node.read(stream) for i in range(n_node)]
		stream.guard()
		self.objects = [Object.read(stream) for i in range(n_object)]
		stream.guard()
		self.decals = [Decal.read(stream) for i in range(n_decal)]
		stream.guard()
		self.iflmaterials = [IflMaterial.read(stream) for i in range(n_ifl)]
		stream.guard()

		# Subshapes
		self.subshapes = [Subshape(0, 0, 0, 0, 0, 0) for i in range(n_subshape)]
		for i in range(n_subshape):
			self.subshapes[i].firstNode = stream.read32()
		for i in range(n_subshape):
			self.subshapes[i].firstObject = stream.read32()
		for i in range(n_subshape):
			self.subshapes[i].firstDecal = stream.read32()
		stream.guard()
		for i in range(n_subshape):
			self.subshapes[i].numNodes = stream.read32()
		for i in range(n_subshape):
			self.subshapes[i].numObjects = stream.read32()
		for i in range(n_subshape):
			self.subshapes[i].numDecals = stream.read32()
		stream.guard()

		# MeshIndexList (obsolete data)
		if stream.dtsVersion < 16:
			for i in range(stream.read32()):
				stream.read32()

		# Default translations and rotations
		self.default_rotations = [None] * n_node
		self.default_translations = [None] * n_node

		for i in range(n_node):
			self.default_rotations[i] = stream.read_quat()
			self.default_translations[i] = stream.read_point()

		# Animation translations and rotations
		self.node_translations = [stream.read_point() for i in range(n_nodetranslation)]
		self.node_rotations = [stream.read_quat() for i in range(n_noderotation)]
		stream.guard()

		# Default scales
		if stream.dtsVersion > 21:
			self.nodescalesuniform = [stream.read_point() for i in range(n_nodescaleuniform)]
			self.nodescalesaligned = [stream.read_point() for i in range(n_nodescalealigned)]
			self.nodescalesarbitrary = [stream.read_point() for i in range(n_nodescalearbitrary)]
			stream.guard()
		else:
			self.nodescalesuniform = [None] * n_nodescaleuniform
			self.nodescalesaligned = [None] * n_nodescalealigned
			self.nodescalesarbitrary = [None] * n_nodescalearbitrary

		# Ground transformations
		if stream.dtsVersion > 23:
			self.ground_translations = [stream.read_point() for i in range(n_groundframe)]
			self.ground_rotations = [stream.read_quat() for i in range(n_groundframe)]
			stream.guard()
		else:
			self.ground_translations = [None] * n_groundframe
			self.ground_rotations = [None] * n_groundframe

		# Object states
		self.objectstates = [ObjectState.read(stream) for i in range(n_objectstate)]
		stream.guard()

		# Decal states
		self.decalstates = [stream.read32() for i in range(n_decalstate)]
		stream.guard()

		# Triggers
		self.triggers = [Trigger.read(stream) for i in range(n_trigger)]
		stream.guard()

		# Detail levels
		self.detaillevels = [DetailLevel.read(stream) for i in range(n_detaillevel)]
		stream.guard()

		# Meshes
		self.meshes = [Mesh.read(stream) for i in range(n_mesh)]
		stream.guard()

		# Names
		self.names = [None] * n_name

		for i in range(n_name):
			buffer = bytearray()

			while True:
				byte = stream.read8()

				if byte == 0:
					break
				else:
					buffer.append(byte)

			self.names[i] = "".join(map(chr, buffer))

		stream.guard()

		self.alphaIn = [None] * n_detaillevel
		self.alphaOut = [None] * n_detaillevel

		if stream.dtsVersion >= 26:
			for i in range(n_detaillevel):
				self.alphaIn[i] = stream.read32()
			for i in range(n_detaillevel):
				self.alphaOut[i] = stream.read32()