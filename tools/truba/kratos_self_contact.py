"""Deterministic, topology-aware master/slave selection for native mortar search."""
from itertools import product
import numpy as np


def surface_geometry(points):
    points=np.asarray(points,dtype=float)
    centers=points.mean(axis=1)
    normals=np.cross(points[:,1]-points[:,0],points[:,-1]-points[:,0])
    lengths=np.linalg.norm(normals,axis=1)
    if not np.isfinite(points).all() or np.any(lengths<1e-16):
        raise ValueError('Degenerate or non-finite self-contact facet.')
    return centers,normals/lengths[:,None]


def contact_partition(points,original_ids,node_h,locked=None):
    """Colour facing non-neighbour facets; preserve established contact sides.

    Native contact conditions still compute all forces and tangents. This only
    supplies consistent roles, avoiding flips across a connected closed skin.
    """
    points=np.asarray(points,dtype=float)
    original_ids=np.asarray(original_ids)
    node_h=np.asarray(node_h,dtype=float)
    if not np.isfinite(node_h).all() or np.any(node_h<=0):
        raise ValueError('Self-contact needs finite positive facet mesh sizes.')
    centers,normals=surface_geometry(points)
    padding=.1*float(node_h.max())
    low=points.min(axis=1)-padding
    high=points.max(axis=1)+padding
    cell=max(float(np.median(node_h))*2.,float(np.ptp(centers,axis=0).max())/256.,1e-10)
    buckets={}
    cells=[]
    for i,(a,b) in enumerate(zip(np.floor(low/cell).astype(np.int64),np.floor(high/cell).astype(np.int64))):
        if np.prod(b-a+1)>4096:raise ValueError('Self-contact facet is too distorted; reduce the load step.')
        keys=list(product(*(range(int(x),int(y)+1) for x,y in zip(a,b))))
        cells.append(keys)
        for key in keys:buckets.setdefault(key,[]).append(i)
    graph={}
    pairs=[]
    topology=[set(ids) for ids in original_ids]
    for i,keys in enumerate(cells):
        candidates={j for key in keys for j in buckets[key] if j>i}
        for j in candidates:
            if np.any(low[i]>high[j]) or np.any(low[j]>high[i]):continue
            if topology[i]&topology[j]:continue
            if float(np.dot(normals[i],normals[j]))>=-.125:continue
            delta=centers[j]-centers[i]
            if np.dot(normals[i],delta)<-2*padding or np.dot(normals[j],-delta)<-2*padding:continue
            graph.setdefault(i,[]).append(j)
            graph.setdefault(j,[]).append(i)
            pairs.append((i,j))
    locked={} if locked is None else dict(locked)
    roles=(normals[np.arange(len(normals)),np.abs(normals).argmax(axis=1)]>0).astype(np.int8)
    visited=set()
    for root in sorted(graph):
        if root in visited:continue
        component=[];relative={root:0};queue=[root]
        for current in queue:
            component.append(current);visited.add(current)
            for other in sorted(graph[current]):
                expected=1-relative[current]
                if other in relative:
                    if relative[other]!=expected:raise ValueError('Ambiguous self-contact junction; refine the contact mesh.')
                else:relative[other]=expected;queue.append(other)
        offsets={int(locked[i])^relative[i] for i in component if i in locked}
        if len(offsets)>1:raise ValueError('Self-contact sides would reverse; reduce the load increment or refine the mesh.')
        offset=next(iter(offsets)) if offsets else int(roles[root])
        for i in component:roles[i]=relative[i]^offset;locked[i]=int(roles[i])
    # Empty interfaces still need both roles for native search initialization.
    if len(roles)>1 and not pairs and np.all(roles==roles[0]):roles[-1]=1-roles[0]
    return roles,locked,pairs
