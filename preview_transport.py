"""Bulk preview transfer through one parent-owned shared memory buffer.

The receiver copies into ordinary NumPy-owned arrays before closing the shared
buffer. VTK/solver grids therefore never depend on a worker's memory lifetime.
"""

from dataclasses import fields

import numpy as np
import pyvista as pv
from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy

from model import FEMesh


def pack_preview(key, surface, mesh):
    arrays = []
    size = 0

    def array(value):
        nonlocal size
        value = np.ascontiguousarray(value)
        if value.dtype.hasobject or value.size == 0:
            return {"inline": value}
        size = (size + 63) & ~63
        descriptor = dict(offset=size, shape=value.shape, dtype=value.dtype.str)
        arrays.append((size, value))
        size += value.nbytes
        return descriptor

    def cells(value):
        if value.GetNumberOfCells() == 0:
            return None
        width = value.IsHomogeneous()
        return dict(
            width=width,
            connectivity=array(vtk_to_numpy(value.GetConnectivityArray())),
            offsets=None if width > 0 else array(vtk_to_numpy(value.GetOffsetsArray())),
        )

    def attributes(value):
        return dict(
            arrays={name: array(value[name]) for name in value},
            scalars=value.active_scalars_name,
            vectors=value.active_vectors_name,
            normals=value.VTKObject.GetNormals().GetName()
            if value.VTKObject.GetNormals()
            else None,
        )

    def grid(value):
        result = dict(
            points=array(value.points),
            point_data=attributes(value.point_data),
            cell_data=attributes(value.cell_data),
            field_data={
                name: array(value.field_data[name]) for name in value.field_data
            },
        )
        if isinstance(value, pv.PolyData):
            result.update(
                kind="poly",
                cells=[
                    cells(value.GetVerts()),
                    cells(value.GetLines()),
                    cells(value.GetPolys()),
                    cells(value.GetStrips()),
                ],
            )
        else:
            result.update(
                kind="unstructured",
                cells=cells(value.GetCells()),
                types=array(value.celltypes),
            )
        return result

    mesh_data = None
    if mesh is not None:
        mesh_data = {}
        for field in fields(FEMesh):
            value = getattr(mesh, field.name)
            mesh_data[field.name] = (
                grid(value)
                if field.name in ("grid", "removed_grid")
                else array(value)
                if isinstance(value, np.ndarray)
                else value
            )
    payload = dict(
        key=key, surface=grid(surface) if surface is not None else None, mesh=mesh_data
    )
    return payload, arrays, size


def write_arrays(segment, arrays):
    for offset, array in arrays:
        segment.buf[offset : offset + array.nbytes] = memoryview(array).cast("B")


def unpack_preview(payload, segment, cached):
    from mesh_engine import Preview

    def array(descriptor):
        if "inline" in descriptor:
            return descriptor["inline"]
        # Deliberately detach from the transport: cancellation, cache eviction,
        # shallow grid copies and long-running solvers remain independent.
        return np.ndarray(
            descriptor["shape"],
            dtype=np.dtype(descriptor["dtype"]),
            buffer=segment.buf,
            offset=descriptor["offset"],
        ).copy()

    def cells(descriptor):
        if descriptor is None:
            return pv.CellArray()
        connectivity = array(descriptor["connectivity"])
        if descriptor["width"] > 0:
            return pv.CellArray.from_regular_cells(
                connectivity.reshape(-1, descriptor["width"])
            )
        return pv.CellArray.from_arrays(array(descriptor["offsets"]), connectivity)

    def attributes(target, descriptor):
        for name, values in descriptor["arrays"].items():
            target[name] = array(values)
        target.VTKObject.SetActiveScalars(descriptor["scalars"])
        target.VTKObject.SetActiveVectors(descriptor["vectors"])
        target.VTKObject.SetActiveNormals(descriptor["normals"])

    def grid(descriptor):
        if descriptor["kind"] == "poly":
            result = pv.PolyData()
            result.points = array(descriptor["points"])
            for setter, data in zip(
                (result.SetVerts, result.SetLines, result.SetPolys, result.SetStrips),
                descriptor["cells"],
            ):
                setter(cells(data))
        else:
            result = pv.UnstructuredGrid()
            result.points = array(descriptor["points"])
            result.SetCells(
                numpy_to_vtk(array(descriptor["types"]), deep=False),
                cells(descriptor["cells"]),
            )
        attributes(result.point_data, descriptor["point_data"])
        attributes(result.cell_data, descriptor["cell_data"])
        for name, values in descriptor["field_data"].items():
            result.field_data[name] = array(values)
        return result

    surface = (
        grid(payload["surface"]) if payload["surface"] is not None else cached.surface
    )
    if payload["mesh"] is None:
        mesh = cached.mesh
    else:
        data = {}
        for name, value in payload["mesh"].items():
            data[name] = (
                grid(value)
                if name in ("grid", "removed_grid")
                else array(value)
                if isinstance(value, dict)
                else value
            )
        mesh = FEMesh(**data)
    return Preview(surface, mesh, payload["key"])
