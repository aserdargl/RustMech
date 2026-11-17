"""OpenCFD blockMesh/snappyHexMesh fluid meshes in an owned case directory."""
from pathlib import Path
import math
import os
import re
import shlex
import subprocess

import numpy as np
import pyvista as pv


def block_mesh_dictionary(specimen, flow):
    from flow_analysis import _header
    width,height = flow.cross_section
    h = flow.cell_size_mm
    z0,z1 = -flow.inlet_length_mm,flow.section_length(specimen)+flow.outlet_length_mm
    nz = math.ceil((z1-z0)/h)
    centre = specimen.size/2
    arcs = []
    if flow.channel_shape == "rectangular":
        ring = [(centre-width/2,centre-height/2),(centre+width/2,centre-height/2),
                (centre+width/2,centre+height/2),(centre-width/2,centre+height/2)]
        points = [(x,y,z) for z in (z0,z1) for x,y in ring]
        blocks = [((0,1,2,3,4,5,6,7),(math.ceil(width/h),math.ceil(height/h),nz))]
        inlet,outlet = [(3,2,1,0)],[(4,5,6,7)]
        walls = [(i,(i+1)%4,(i+1)%4+4,i+4) for i in range(4)]
    else:
        radius = width/2
        inner,outer = radius/2,radius/math.sqrt(2)
        ring = [(centre+sx*r,centre+sy*r) for r in (inner,outer)
                for sx,sy in ((-1,-1),(1,-1),(1,1),(-1,1))]
        points = [(x,y,z) for z in (z0,z1) for x,y in ring]
        nt,nr = max(2,math.ceil(2*inner/h)),max(1,math.ceil((radius-inner)/h))
        blocks = [((0,1,2,3,8,9,10,11),(nt,nt,nz))]
        inlet,outlet,walls = [(3,2,1,0)],[(8,9,10,11)],[]
        midpoints = [(centre,centre-radius),(centre+radius,centre),
                     (centre,centre+radius),(centre-radius,centre)]
        for i in range(4):
            j = (i+1)%4
            face = (i+4,j+4,j,i)
            blocks.append(((*face,*(p+8 for p in face)),(nt,nr,nz)))
            inlet.append(tuple(reversed(face)))
            outlet.append(tuple(p+8 for p in face))
            walls.append((i+4,j+4,j+12,i+12))
            for offset,z in ((0,z0),(8,z1)):
                arcs.append((i+4+offset,j+4+offset,(*midpoints[i],z)))
    def vec(p):
        return "("+" ".join(f"{v*.001:.16g}" for v in p)+")"
    def face(f):
        return "("+" ".join(map(str,f))+")"
    text = _header("dictionary","blockMeshDict")+"convertToMeters 1;\nvertices (\n"+"\n".join(vec(p) for p in points)+"\n);\nblocks (\n"
    for vertices, counts in blocks:
        text += f"hex {face(vertices)} {face(counts)} simpleGrading (1 1 1)\n"
    text += ");\nedges (\n"+"\n".join(f"arc {a} {b} {vec(p)}" for a,b,p in arcs)+"\n);\nboundary (\n"
    for name,faces in (("inlet",inlet),("outlet",outlet),("walls",walls)):
        text += f"{name} {{ type {'wall' if name=='walls' else 'patch'}; faces ("+" ".join(face(f) for f in faces)+"); }\n"
    return text+");\nmergePatchPairs ();\n"


def snappy_dictionary(specimen, flow):
    from flow_analysis import _header
    # Off-centre, irrational fractions avoid background and refinement faces.
    width,height = flow.cross_section
    keep = np.array([specimen.size/2+.0314159*width,specimen.size/2+.0271828*height,-.6180339*flow.inlet_length_mm])*.001
    point = "("+" ".join(f"{v:.16g}" for v in keep)+")"
    geometry = "" if flow.empty_pipe else "insert.stl { type triSurfaceMesh; name insert; }"
    refinement = "" if flow.empty_pipe else f"insert {{ level ({flow.surface_refinement} {flow.surface_refinement}); patchInfo {{ type wall; }} }}"
    return _header("dictionary","snappyHexMeshDict")+f"""
castellatedMesh {'false' if flow.empty_pipe else 'true'};
snap {'false' if flow.empty_pipe else 'true'};
addLayers {'true' if flow.boundary_layers else 'false'};
geometry {{ {geometry} }}
castellatedMeshControls {{
 maxLocalCells {flow.max_fitted_cells}; maxGlobalCells {flow.max_fitted_cells};
 minRefinementCells 0; nCellsBetweenLevels 3; features ();
 refinementSurfaces {{ {refinement} }}
 resolveFeatureAngle 30; refinementRegions {{ }}
 locationInMesh {point}; allowFreeStandingZoneFaces false;
}}
snapControls {{ nSmoothPatch 3; tolerance 1; nSolveIter 50; nRelaxIter 8;
 nFeatureSnapIter 10; implicitFeatureSnap true; explicitFeatureSnap false; multiRegionFeatureSnap false; }}
addLayersControls {{
 relativeSizes true; layers {{ "(walls|insert)" {{ nSurfaceLayers {flow.boundary_layers}; }} }}
 expansionRatio 1.2; finalLayerThickness 0.3; minThickness 0.05;
 nGrow 0; featureAngle 60; nRelaxIter 5; nSmoothSurfaceNormals 1;
 nSmoothNormals 3; nSmoothThickness 10; maxFaceThicknessRatio 0.5;
 maxThicknessToMedialRatio 0.3; minMedialAxisAngle 90;
 nBufferCellsNoExtrude 0; nLayerIter 50;
}}
meshQualityControls {{
 maxNonOrtho 65; maxBoundarySkewness 20; maxInternalSkewness 4;
 maxConcave 80; minVol 1e-30; minTetQuality 1e-15; minArea -1;
 minTwist 0.02; minDeterminant 0.001; minFaceWeight 0.05;
 minVolRatio 0.01; minTriangleTwist -1; nSmoothScale 4; errorReduction 0.75;
}}
mergeTolerance 1e-8;
writeFlags (layerSets layerFields);
"""


def _internal_mesh(block, name=""):
    if isinstance(block,pv.MultiBlock):
        for key in block.keys():
            found = _internal_mesh(block[key],name+"/"+key)
            if found is not None:
                return found
    elif isinstance(block,pv.UnstructuredGrid) and "internalMesh" in name:
        return block
    return None


def _patch_area(block, patch, name=""):
    if isinstance(block,pv.MultiBlock):
        return sum(_patch_area(block[key],patch,name+"/"+key) for key in block.keys())
    if isinstance(block,pv.DataSet) and patch in name.split("/") and block.n_cells:
        return float(np.sum(block.compute_cell_sizes(length=False,volume=False).cell_data["Area"]))
    return 0.


def placed_insert_surface(specimen, flow):
    """Preserve the finite specimen and the same axial crop as Cartesian CFD."""
    from mesh_engine import make_surface
    from flow_analysis import specimen_transform
    surface = make_surface(specimen).copy(deep=True)
    rotation,centre = specimen_transform(specimen,flow)
    surface.points = (surface.points-specimen.size/2) @ rotation.T+centre
    length = flow.section_length(specimen)
    tolerance = max(specimen.size*1e-10,np.finfo(float).eps)
    if surface.bounds[4]<0:
        surface = surface.clip_closed_surface(normal=(0,0,1),origin=(0,0,0),tolerance=tolerance)
    if surface.bounds[5]>length:
        surface = surface.clip_closed_surface(normal=(0,0,-1),origin=(0,0,length),tolerance=tolerance)
    if not surface.n_cells or surface.n_open_edges or not surface.is_manifold:
        raise ValueError("The placed/clipped specimen surface is not a closed manifold solid.")
    surface.points *= .001
    return surface


def layer_report(log):
    """Read OpenCFD's final face coverage and per-patch averaged layer table."""
    coverage = list(re.finditer(r"Extruding\s+(\d+)\s+out of\s+(\d+)\s+faces",log))
    report = {"extruded_faces":None,"target_faces":None,"face_coverage_fraction":None,"patches":{}}
    if coverage:
        actual,total = map(int,coverage[-1].groups())
        report.update(extruded_faces=actual,target_faces=total,face_coverage_fraction=actual/total if total else 0.)
    table = r"^\s*(walls|insert)\s+(\d+)\s+(\d+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s*$"
    for match in re.finditer(table,log,re.M):
        name,faces,target,actual,thickness,percent = match.groups()
        report["patches"][name] = {"faces":int(faces),"requested_layers":int(target),
            "average_layers":float(actual),"average_thickness_m":float(thickness),"thickness_percent":float(percent)}
    return report


def _labels(path):
    text = re.sub(r"/\*.*?\*/|//[^\n]*","",Path(path).read_text(),flags=re.S)
    match = re.search(r"\}\s*(\d+)\s*\((.*?)\)\s*$",text,re.S)
    if match:
        count = int(match[1])
        values = np.fromstring(match[2],dtype=np.int64,sep=" ")
    else:
        uniform = re.search(r"\}\s*(\d+)\s*\{\s*(-?\d+)\s*\}\s*$",text,re.S)
        if not uniform:
            raise RuntimeError(f"Cannot read fitted mesh labels: {Path(path).name}")
        count = int(uniform[1])
        values = np.full(count,int(uniform[2]),dtype=np.int64)
    if len(values)!=count:
        raise RuntimeError("Fitted mesh label count mismatch.")
    return values


def verify_surface_refinement(directory, required):
    """Fail rather than accept an under-refined or vanished insert surface."""
    folder = Path(directory)/"constant/polyMesh"
    boundary = (folder/"boundary").read_text()
    match = re.search(r"\binsert\s*\{([^{}]*)\}",boundary,re.S)
    if not match:
        raise RuntimeError("The fitted mesh lost the insert surface.")
    faces = re.search(r"\bnFaces\s+(\d+)",match[1])
    start = re.search(r"\bstartFace\s+(\d+)",match[1])
    if not faces or not start or int(faces[1])<=0:
        raise RuntimeError("The fitted mesh has no insert boundary faces.")
    owners,levels = _labels(folder/"owner"),_labels(folder/"cellLevel")
    first,count = int(start[1]),int(faces[1])
    selected = owners[first:first+count]
    if len(selected)!=count or selected.min()<0 or selected.max()>=len(levels):
        raise RuntimeError("Invalid insert face ownership in fitted mesh.")
    minimum = int(levels[selected].min())
    if minimum<required:
        raise RuntimeError(f"Insert surface refinement reached level {minimum}, below requested {required}.")
    return minimum


def fit_fluid_case(specimen, flow, directory):
    from analysis_process import report_analysis_step
    from flow_analysis import _write,_wsl,linux_path
    directory = Path(directory).resolve()
    if any(character.isspace() for character in str(directory)):
        raise ValueError("OpenFOAM requires a case folder without whitespace. Use an installation/working folder without spaces.")
    if not flow.empty_pipe:
        report_analysis_step("Preparing the fitted-mesh specimen surface…")
        surface = placed_insert_surface(specimen,flow)
        folder = directory/"constant/triSurface"
        folder.mkdir(parents=True,exist_ok=True)
        surface.save(folder/"insert.stl")
    _write(directory/"system/blockMeshDict",block_mesh_dictionary(specimen,flow))
    _write(directory/"system/snappyHexMeshDict",snappy_dictionary(specimen,flow))
    script = """#!/bin/bash
set -eo pipefail
echo $$ > .runner.pid
trap 'rm -f .runner.pid' EXIT
command -v blockMesh >/dev/null
command -v snappyHexMesh >/dev/null
command -v checkMesh >/dev/null
test ! -f .cancel-request
blockMesh > log.blockMesh 2>&1
test ! -f .cancel-request
snappyHexMesh -overwrite > log.snappyHexMesh 2>&1
test ! -f .cancel-request
checkMesh > log.checkMesh.fitted 2>&1
grep -q 'Mesh OK' log.checkMesh.fitted
"""
    _write(directory/"MeshRun",script)
    report_analysis_step("Building and checking the fitted OpenFOAM fluid mesh…")
    if os.name == "nt":
        command = f"source {shlex.quote(flow.foam_bashrc)} >/dev/null 2>&1 && cd {shlex.quote(linux_path(directory))} && exec setsid --wait bash MeshRun"
        result = _wsl(flow,command,capture_output=True)
    else:
        result = subprocess.run(["bash","MeshRun"],cwd=directory,capture_output=True)
    if result.returncode:
        detail = "\n".join((directory/name).read_text(errors="replace")[-2200:] for name in
                           ("log.blockMesh","log.snappyHexMesh","log.checkMesh.fitted") if (directory/name).exists())
        raise RuntimeError("Fitted mesh failed; check blockMesh/snappyHexMesh/checkMesh availability. No flow solver started.\n"+
                           result.stderr.decode(errors="replace")[-1000:]+detail[-6500:])
    log = (directory/"log.snappyHexMesh").read_text(errors="replace")
    if re.search(r"(?:reached|exceed\w*|stopp\w*).*maxGlobalCells",log,re.I):
        raise RuntimeError("Refinement reached the fitted cell budget; increase the budget or revise the requested mesh.")
    mesh_log = (directory/"log.checkMesh.fitted").read_text(errors="replace")
    count = re.search(r"^\s*cells:\s*(\d+)",mesh_log,re.M)
    if count and int(count[1])>flow.max_fitted_cells:
        raise RuntimeError("Fitted mesh exceeds the requested cell budget.")
    layers = layer_report(log)
    if flow.boundary_layers and not layers["extruded_faces"]:
        raise RuntimeError("No requested wall layers were generated. Adjust the mesh or explicitly select zero layers.")
    minimum_level = verify_surface_refinement(directory,flow.surface_refinement) if not flow.empty_pipe else None
    reader = pv.OpenFOAMReader(directory/"case.foam")
    reader.skip_zero_time = False
    reader.cell_to_point_creation = False
    reader.set_active_time_value(0)
    blocks = reader.read()
    grid = _internal_mesh(blocks)
    if grid is None or not grid.n_cells:
        raise RuntimeError("Cannot read the fitted fluid mesh.")
    volumes = grid.compute_cell_sizes(length=False,area=False).cell_data["Volume"]
    if not np.isfinite(volumes).all() or np.min(volumes)<=0:
        raise RuntimeError("Fitted mesh contains nonpositive or non-finite cells.")
    if grid.n_cells>flow.max_fitted_cells:
        raise RuntimeError("Fitted mesh exceeds the requested cell budget.")
    area = _patch_area(blocks,"inlet")
    if not math.isfinite(area) or area<=0:
        raise RuntimeError("Fitted mesh has no finite, positive inlet patch area.")
    grid.clear_data()
    grid.save(directory/"fluid_mesh.vtu")
    warnings = ["Fitted mesh passed checkMesh. Review layer coverage, y+ and mesh independence before interpreting performance."]
    for name,patch in layers["patches"].items():
        if patch["average_layers"]<patch["requested_layers"]:
            warnings.append(f"{name}: average layers {patch['average_layers']:g} below requested {patch['requested_layers']}.")
    return {"method":"snappyHexMesh","fluid_cells":grid.n_cells,"fluid_volume_m3":float(np.sum(volumes)),
            "inlet_area_m2":area,"effective_diameter_mm":2*math.sqrt(area/math.pi)*1000,
            "surface_refinement_requested":flow.surface_refinement,"boundary_layers_requested":flow.boundary_layers,
            "surface_boundary_min_refinement":minimum_level,
            "mesh_check":"Mesh OK","layer_log":"log.snappyHexMesh","layers":layers,"warnings":warnings}
