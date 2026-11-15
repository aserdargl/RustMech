"""Versioned virtual-test profiles and their specimen/fixture geometry.

Profiles describe nominal geometry, not certification of a physical test. A
porous gauge section is explicitly reported as an architected research coupon.
Coordinates and loads are mm/N/MPa; tension is +Z, flexure/compression are -Z.
"""
from dataclasses import dataclass, asdict, replace
import numpy as np
import pyvista as pv


@dataclass(frozen=True)
class TestProfile:
    label: str
    standard: str
    kind: str
    dimensions: tuple[float, float, float]
    gauge_length: float = 0.
    gauge_width: float = 0.
    span: float = 0.
    support_radius: float = 0.
    nose_radius: float = 0.
    source: str = ''


PROFILES = {
    'iso13314_cube': TestProfile('ISO 13314:2011 · Cellular metal compression',
        'ISO 13314:2011', 'compression', (30., 30., 30.),
        source='https://www.iso.org/standard/53669.html'),
    'astm_e8_subsize': TestProfile('ASTM E8/E8M-25 · Flat subsize tension',
        'ASTM E8/E8M-25', 'tension', (10., 3., 102.), 25., 6.,
        source='https://store.astm.org/standards/e8_e8m'),
    'iso178_preferred': TestProfile('ISO 178:2019 · Three-point flexure',
        'ISO 178:2019', 'flexure', (80., 10., 4.), span=64.,
        support_radius=5., nose_radius=5.,
        source='https://www.iso.org/standard/70513.html'),
}
ARCHITECTURES = {'solid': 'Solid specimen', 'porous_centre': 'Porous centre / solid ends',
                 'porous_all': 'Porous throughout'}


def profile(settings):
    return PROFILES.get(settings.test_profile)


def kind(settings):
    p = profile(settings)
    return p.kind if p else 'compression'


def load_sign(settings):
    return 1. if kind(settings) == 'tension' else -1.


def apply_profile(settings, identifier):
    """Apply defaults once on selection; subsequent analysis edits are retained."""
    if identifier == 'custom':
        return replace(settings, test_profile='custom')
    p = PROFILES[identifier]
    return replace(settings, test_profile=identifier, size=p.dimensions[2],
                   imported_geometry_path='', imported_geometry_name='',
                   specimen_architecture='porous_all' if p.kind == 'compression' else 'solid',
                   core_fraction=100., mesh_resolution=.5, local_mesh_size=.25,
                   cell_size=3., solid_fraction=30., self_contact_mode='disabled',
                   tetra_strategy='auto', compression=1. if p.kind == 'flexure' else .6,
                   bottom_platen_contact='bonded' if p.kind=='tension' else 'frictionless',
                   top_platen_contact='bonded' if p.kind=='tension' else 'frictionless')


def nominal_volume(settings):
    p = profile(settings)
    return float(np.prod(p.dimensions)) if p else settings.size**3


def profile_report(settings):
    p = profile(settings)
    if not p:
        return {'profile': 'custom', 'label': 'Özel deney / Custom experiment'}
    deviations = []
    if settings.specimen_architecture != 'solid' and p.kind != 'compression':
        deviations.append('Architected porous specimen: material-standard applicability is not established.')
    if p.kind == 'compression':
        if settings.solid_fraction > 50 or settings.specimen_architecture == 'solid':
            deviations.append('ISO 13314 requires porosity of at least 50%.')
        deviations.append('Mean pore size must be measured independently; cell period is not mean pore size.')
    report={**asdict(p), 'profile': settings.test_profile,
            'fixture_model': ('Bonded end-face grips; idealized axial loading.' if p.kind=='tension' else
                              'Rigid cylinders with contact; left axial pin and lateral guides at both supports.' if p.kind=='flexure' else
                              'Rigid parallel platens with contact.'),
            'architecture': settings.specimen_architecture, 'core_fraction_percent': settings.core_fraction,
            'deviations': deviations, 'compliance': 'Nominal virtual-test profile; conformity not certified.',
            'limitations': ['Material scope, dimensional tolerances, test speed and physical instrumentation require verification.',
                           'Static load steps are not a calibrated physical strain rate.',
                           'No calibrated fracture model: rupture properties are not predicted.']}
    if settings.solver_backend=='kratos' and p.kind=='flexure':
        report['fixture_model']='Idealized pin/roller support lines and a uniform-displacement loading line.'
        report['limitations'].append('Kratos flexure uses HEX8 line constraints; roller indentation, lift-off and contact migration are not modeled.')
    return report


def validate_profile(settings):
    if settings.test_profile == 'custom':
        return []
    p = profile(settings)
    if not p:
        return ['Unknown standard test profile.']
    errors = []
    if settings.imported_geometry_path:
        errors.append('Standard profiles generate their own specimen. Use Özel deney for imported geometry.')
    if settings.specimen_architecture not in ARCHITECTURES:
        errors.append('Select a supported specimen architecture.')
    if p.kind != 'compression' and settings.specimen_architecture == 'porous_all':
        errors.append('Tension and flexure profiles preserve solid fixture regions; select a porous centre.')
    if isinstance(settings.core_fraction, bool) or not isinstance(settings.core_fraction, (float, int)) or not np.isfinite(settings.core_fraction) or not 10 <= settings.core_fraction <= 100:
        errors.append('Porous centre length must be between 10 and 100% of the available centre region.')
    if settings.size != p.dimensions[2]:
        errors.append('The standard profile fixes specimen height; reselect the profile.')
    if settings.tetra_strategy == 'periodic':
        errors.append('Standard specimens require Auto or Netgen meshing.')
    if p.kind == 'compression' and (settings.specimen_architecture != 'porous_all' or settings.solid_fraction > 50):
        errors.append('ISO 13314 profile requires a porous specimen throughout with solid fraction ≤50%.')
    return errors


def sample_shape(settings, axes):
    """Sample a fixed envelope with pores restricted to the selected centre."""
    from mesh_engine import geometry_threshold
    from structure_library import evaluate_priority_field
    p = profile(settings)
    dims = np.array(p.dimensions)
    result = np.empty(tuple(map(len, axes)), np.float32)
    # Procedural pore generators require coordinates inside their reference
    # domain. A flexural coupon is longer in X than in Z, unlike a cube.
    field_settings=replace(settings,size=float(dims.max())) if settings.structure_family=='porous' else settings
    threshold = geometry_threshold(field_settings) if settings.specimen_architecture != 'solid' else 0.
    y, z = axes[1][None, :, None], axes[2][None, None, :]
    # Bounded slabs keep large pore fields below the native sampler limit.
    slab = max(1, 1_000_000 // (len(axes[1])*len(axes[2])))
    for start in range(0, len(axes[0]), slab):
        x = axes[0][start:start+slab, None, None]
        envelope = np.maximum(np.maximum(np.maximum(-x, x-dims[0]),
            np.maximum(-y, y-dims[1])), np.maximum(-z, z-dims[2])) / dims.min()
        if p.kind == 'tension':
            # 32 mm parallel section, R6 shoulders, 6 mm gauge / 10 mm grips.
            s = np.maximum(np.abs(z-dims[2]/2)-16., 0.)
            half_width = np.where(s < np.sqrt(20.), 3.+6.-np.sqrt(np.maximum(36.-s*s, 0.)), 5.)
            envelope = np.maximum(envelope, (np.abs(x-5.)-half_width)/dims.min())
        field = envelope
        if settings.specimen_architecture != 'solid':
            pore = evaluate_priority_field(field_settings, x, y, z)-threshold
            if settings.specimen_architecture == 'porous_centre':
                axis = z if p.kind == 'tension' else x
                centre = dims[2]/2 if p.kind == 'tension' else dims[0]/2
                available = p.gauge_length if p.kind == 'tension' else p.span*.75
                in_core = np.abs(axis-centre) < available*settings.core_fraction/200.
                # Retain a solid contact band beneath the loading nose.
                if p.kind == 'flexure':
                    in_core = in_core & (np.abs(x-centre) > p.nose_radius)
                pore = np.where(in_core, pore, -1.)
            field = np.maximum(envelope, pore)
        result[start:start+slab] = field
    return result


def make_standard_surface(settings):
    from skimage.measure import marching_cubes
    from mesh_engine import surface_from_marching_cubes
    dims = np.array(profile(settings).dimensions)
    step = min(dims.min()/16, settings.cell_size/20) if settings.specimen_architecture != 'solid' else dims.min()/16
    divisions = np.ceil(dims/step).astype(int)
    if np.prod(divisions+3) > 256**3:
        raise ValueError('Standard specimen surface exceeds the sampling limit. Increase the pore scale.')
    spacing = dims/divisions
    axes = [np.arange(-1, n+2)*h for n, h in zip(divisions, spacing)]
    field = sample_shape(settings, axes)
    # Clip at exact bounds after closing the ghost layer, as for custom cubes.
    field[[0,-1],:,:]=1; field[:,[0,-1],:]=1; field[:,:,[0,-1]]=1
    vertices, faces, _, _ = marching_cubes(field, 0., spacing=tuple(spacing), allow_degenerate=False)
    vertices -= spacing
    vertices = np.clip(vertices, 0., dims)
    a,b,c = vertices[faces].transpose(1,0,2)
    faces = faces[np.linalg.norm(np.cross(b-a,c-a),axis=1)>1e-12]
    return surface_from_marching_cubes(vertices, faces)


def make_standard_mesh(settings, surface=None, on_progress=None):
    from model import FEMesh, MAX_FE_ELEMENTS
    from rust_mesh import select_voxels
    p = profile(settings)
    if settings.mesh_type.startswith('TET'):
        from tetrahedral_mesh import generate_tetrahedral_mesh
        return generate_tetrahedral_mesh(settings, surface if surface is not None else make_standard_surface(settings), on_progress=on_progress)
    dims = np.array(p.dimensions)
    divisions = np.ceil(dims/settings.mesh_resolution).astype(int)
    edges=[np.linspace(0.,length,n+1) for length,n in zip(dims,divisions)]
    if p.kind=='flexure':
        # Keep all three tangent lines on mesh nodes at every refinement size.
        stations=[0.,(dims[0]-p.span)/2,dims[0]/2,(dims[0]+p.span)/2,dims[0]]
        edges[0]=np.concatenate([np.linspace(a,b,int(np.ceil((b-a)/settings.mesh_resolution))+1)[:-1]
                                 for a,b in zip(stations[:-1],stations[1:])]+[np.array([dims[0]])])
        divisions[0]=len(edges[0])-1
    if np.prod(divisions) > MAX_FE_ELEMENTS*4:
        raise ValueError('Standard specimen FE sampling exceeds the limit. Increase mesh size.')
    spacing = np.array([np.diff(edge).max() for edge in edges])
    axes = [(edge[:-1]+edge[1:])/2 for edge in edges]
    values = sample_shape(settings, axes).astype(float)
    count = int(np.count_nonzero(values<0))
    if not 1 <= count <= MAX_FE_ELEMENTS:
        raise ValueError('Standard specimen exceeds the element limit or has no solid cells.')
    if max(divisions) <= 150:
        retained, removed, components = select_voxels(values, count)
    else:
        from scipy.ndimage import label, generate_binary_structure
        labels, components=label(values<0,generate_binary_structure(3,1))
        counts=np.bincount(labels.ravel());counts[0]=0
        retained=labels==np.argmax(counts)
        removed=(values<0)&~retained
    grid = rectangular_hex_grid(retained, settings.mesh_type=='HEX20')
    excluded = rectangular_hex_grid(removed, settings.mesh_type=='HEX20')
    for dimension,edge in enumerate(edges):
        grid.points[:,dimension]=np.interp(grid.points[:,dimension],np.arange(len(edge)),edge)
        if excluded.n_points:
            excluded.points[:,dimension]=np.interp(excluded.points[:,dimension],np.arange(len(edge)),edge)
    ids = grid.point_data['ansys_node_num']
    z = grid.points[:,2]
    bottom = ids[np.isclose(z,0)]
    top = ids[np.isclose(z,dims[2])]
    return FEMesh(grid, float(spacing.max()), None, 100*grid.volume/nominal_volume(settings),
        1, bottom, top, excluded, int(components), count)


def rectangular_hex_grid(mask, quadratic=False):
    """Rectangular coupons may exceed the custom cube kernel's 150-axis limit."""
    locations=np.argwhere(mask)
    if not len(locations):return pv.UnstructuredGrid()
    corners=np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]])
    points,inverse=np.unique((locations[:,None,:]+corners).reshape(-1,3),axis=0,return_inverse=True)
    points=points.astype(float)
    cells=inverse.reshape(-1,8)
    width=8
    cell_type=pv.CellType.HEXAHEDRON
    if quadratic:
        from model import HEX_EDGES
        edges=cells[:,np.array(HEX_EDGES)].reshape(-1,2)
        edges.sort(axis=1)
        unique,indices=np.unique(edges,axis=0,return_inverse=True)
        mids=(points[unique[:,0]]+points[unique[:,1]])/2
        cells=np.column_stack((cells,indices.reshape(-1,12)+len(points)))
        points=np.vstack((points,mids));width=20;cell_type=pv.CellType.QUADRATIC_HEXAHEDRON
    grid=pv.UnstructuredGrid(np.column_stack((np.full(len(cells),width),cells)).ravel(),
        np.full(len(cells),cell_type,np.uint8),points)
    grid.point_data['ansys_node_num']=np.arange(1,grid.n_points+1)
    grid.cell_data['ansys_elem_num']=np.arange(1,grid.n_cells+1)
    return grid


def fixture_surfaces(settings, displacement=0.):
    """Return fixed and moving fixture surfaces, also used for STL exports."""
    p = profile(settings)
    if not p or p.kind == 'compression':
        from nonlinear_compression import make_compression_platens
        return make_compression_platens(settings.size, displacement)
    w,t,length = p.dimensions
    if p.kind == 'tension':
        fixed = pv.Box(bounds=(-1,w+1,-1,t+1,-5,20))
        moving = pv.Box(bounds=(-1,w+1,-1,t+1,length-20+displacement,length+5+displacement))
        return fixed, moving
    fixed = pv.Cylinder(center=((w-p.span)/2,t/2,-p.support_radius),direction=(0,1,0),
        radius=p.support_radius,height=t+4,resolution=96).triangulate()
    other = fixed.copy()
    other.translate((p.span,0,0),inplace=True)
    moving = pv.Cylinder(center=(w/2,t/2,length+p.nose_radius+displacement),direction=(0,1,0),
        radius=p.nose_radius,height=t+4,resolution=96).triangulate()
    return fixed.merge(other), moving


def export_standard_graphs(history, settings, folder, mesh, images, result=None):
    """Keep actuator travel distinct from virtual gauge extension / beam deflection."""
    import csv
    from model import loading_target_reached
    p = profile(settings)
    travel, force = history[:,1],history[:,2]
    measured = travel.copy()
    if result is None or mesh is None:
        raise ValueError('Tension/flexure postprocessing requires nodal displacement results and the specimen mesh.')
    points = mesh.grid.points
    ids = mesh.grid.point_data['ansys_node_num']
    if p.kind == 'tension':
        # Two virtual extensometer planes; interpolate between mesh Z layers.
        levels = np.unique(points[:,2])
        means=[]
        for i in range(result.nsets):
            u=result.displacement(i,ids)[:,2]
            plane_means=np.array([np.mean(u[np.isclose(points[:,2],z,atol=1e-8,rtol=0)]) for z in levels])
            means.append(np.diff(np.interp([p.dimensions[2]/2-p.gauge_length/2,p.dimensions[2]/2+p.gauge_length/2], levels,plane_means))[0])
        measured=np.r_[0.,means]
        strain=measured/p.gauge_length
        stress=force/(p.gauge_width*p.dimensions[1])
        labels=('Gauge_extension_mm','Nominal_tensile_stress_MPa','Engineering_strain')
    else:
        # Interpolate the neutral-axis displacement at midspan and supports.
        targets=np.array([[(p.dimensions[0]-p.span)/2,p.dimensions[1]/2,p.dimensions[2]/2],
                          [p.dimensions[0]/2,p.dimensions[1]/2,p.dimensions[2]/2],
                          [(p.dimensions[0]+p.span)/2,p.dimensions[1]/2,p.dimensions[2]/2]])
        from scipy.spatial import Delaunay
        triangulation=Delaunay(points)
        simplex=triangulation.find_simplex(targets)
        if np.any(simplex<0):raise ValueError('Beam measurement points fall outside the specimen.')
        transform=triangulation.transform[simplex]
        barycentric=np.einsum('ijk,ik->ij',transform[:,:3],targets-transform[:,3])
        weights=np.column_stack((barycentric,1-barycentric.sum(axis=1)))
        vertices=triangulation.simplices[simplex]
        values=[]
        for i in range(result.nsets):
            u=result.displacement(i,ids)[:,2]
            uz=(u[vertices]*weights).sum(axis=1)
            if not np.all(np.isfinite(uz)):
                raise ValueError('Cannot interpolate beam deflection inside the specimen mesh.')
            values.append(-uz[1]+(uz[0]+uz[2])/2)
        measured=np.r_[0.,values]
        b,h=p.dimensions[1:]
        strain=6*h*measured/p.span**2
        stress=3*force*p.span/(2*b*h*h)
        labels=('Midspan_deflection_mm','Nominal_flexural_stress_MPa','Nominal_flexural_strain')
    work=np.r_[0.,np.cumsum((force[1:]+force[:-1])*np.diff(travel)/2000)]
    with (folder/'force_displacement.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream)
        writer.writerow(['Time','Actuator_travel_mm','Force_N','Fixed_RFZ_N','Force_Imbalance_N','External_work_J',labels[0],labels[1],labels[2]])
        writer.writerows(np.column_stack((history,work,measured,stress,strain)))
    with (folder/'stress_strain.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow([labels[2],labels[1]]);writer.writerows(zip(strain,stress))
    if np.all(np.diff(strain)>=0) and strain[-1]>0:
        samples=np.linspace(0,strain[-1],100)
        with (folder/'standardized_stress_strain.csv').open('w',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream);writer.writerow([labels[2],labels[1]])
            writer.writerows(zip(samples,np.interp(samples,strain,stress)))
    if images:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        for x,y,xlabel,ylabel,name in ((measured,force,labels[0],'Force_N','force_displacement.png'),(strain,stress,labels[2],labels[1],'stress_strain.png')):
            fig,ax=plt.subplots();ax.plot(x,y);ax.set(xlabel=xlabel,ylabel=ylabel);ax.grid(alpha=.25)
            fig.tight_layout();fig.savefig(folder/name,dpi=160);plt.close(fig)
    return {'maximum_force_N':float(force.max()),'maximum_nominal_stress_MPa':float(stress.max()),
        'external_work_J':float(work[-1]),'reached_actuator_travel_mm':float(travel[-1]),
        'final_measured_deformation_mm':float(measured[-1]),'loading_mode':settings.loading_mode,
        'completed':loading_target_reached(settings,travel[-1],force[-1],history[-1,0]),
        'interpretation':'Nominal section properties; porous local stresses require FE fields. Flexure formulas assume small-deflection beam behaviour.',
        'test_profile':profile_report(settings)}
