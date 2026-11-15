"""Exact nodal support/loading lines for the Kratos three-point beam model."""
import numpy as np


def line_fixture_nodes(mesh, profile):
    if not np.all(mesh.grid.celltypes==12):
        raise ValueError('Kratos three-point flexure currently requires HEX8 with aligned support/loading lines.')
    points=np.asarray(mesh.grid.points)
    ids=np.asarray(mesh.grid.point_data['ansys_node_num'])
    length,width,height=profile.dimensions
    tolerance=max(length*1e-9,1e-10)
    lines={}
    for name,x,z in (('left',(length-profile.span)/2,0.),
                     ('right',(length+profile.span)/2,0.),('loading',length/2,height)):
        mask=np.isclose(points[:,0],x,rtol=0,atol=tolerance)&np.isclose(points[:,2],z,rtol=0,atol=tolerance)
        selected=points[mask]
        if len(selected)<2 or not np.isclose(selected[:,1].min(),0.,atol=tolerance) or not np.isclose(selected[:,1].max(),width,atol=tolerance):
            raise ValueError(f'The {name} fixture line is missing from the solid mesh. Regenerate the standard HEX8 specimen.')
        lines[name]=list(map(int,ids[mask]))
    return lines
