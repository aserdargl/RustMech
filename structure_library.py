"""Unified 3D Structure Library imported from Gitterburg: TPMS, Truss Lattice, and Porous structures.

Provides analytical and procedural scalar priority fields where lower values
belong to the solid phase, enabling exact solid-fraction targeting for both
marching-cubes reference surfaces and voxel HEX8 finite-element meshes.
"""
from __future__ import annotations

from functools import lru_cache
from itertools import permutations, product
from typing import Callable

import numpy as np
from scipy.fft import fftfreq, fftn, ifftn
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import distance_transform_edt, gaussian_filter
from scipy.spatial import cKDTree
from rust_mesh import graph_field, interpolate_field, tpms_field

Node = tuple[float, float, float]
Edge = tuple[Node, Node]

STRUCTURE_FAMILIES: dict[str, str] = {
    "tpms": "TPMS",
    "lattice": "Truss Lattice",
    "porous": "Porous",
}

TPMS_MORPHOLOGIES: dict[str, str] = {
    "sheet": "Sheet (Shell)",
    "network_a": "Network A (Solid)",
    "network_b": "Network B (Inverted Solid)",
}

# ── 1. TPMS Functions & Equations (from Gitterburg core/libraries.py) ─────────
SCHWARZ_PRIMITIVE_NAME = "S. Primitive"
SCHWARZ_DIAMOND_SURFACE_NAME = "S. Diamond Surface"
SCHWARZ_DIAMOND_NAME = "S. Diamond"

TPMS_ITEMS: tuple[str, ...] = (
    "Gyroid",
    SCHWARZ_PRIMITIVE_NAME,
    SCHWARZ_DIAMOND_SURFACE_NAME,
    SCHWARZ_DIAMOND_NAME,
    "Neovius",
    "Lidinoid",
    "IWP",
    "FRD",
    "PMY",
    "Fischer Koch S",
    "Fischer Koch Y",
    "Split P",
    "Tubular P",
    "Tubular G",
    "Schwarz CLP",
    "Lonsdaleite",
    "Octo",
    "Schwarz H",
    "KP Minimal",
)

TPMS_ALIASES: dict[str, str] = {
    "Schwarz P": SCHWARZ_PRIMITIVE_NAME,
    "Primitive": SCHWARZ_PRIMITIVE_NAME,
    "Schwarz D": SCHWARZ_DIAMOND_SURFACE_NAME,
    "Diamond": SCHWARZ_DIAMOND_NAME,
    "Schwarz D (Diamond)": SCHWARZ_DIAMOND_SURFACE_NAME,
}

TPMS_FUNCTIONS: dict[str, Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]] = {
    "Gyroid": lambda x, y, z: (
        np.sin(x) * np.cos(y) + np.sin(y) * np.cos(z) + np.sin(z) * np.cos(x)
    ),
    SCHWARZ_PRIMITIVE_NAME: lambda x, y, z: np.cos(x) + np.cos(y) + np.cos(z),
    SCHWARZ_DIAMOND_SURFACE_NAME: lambda x, y, z: (
        np.cos(x) * np.cos(y) * np.cos(z) - np.sin(x) * np.sin(y) * np.sin(z)
    ),
    SCHWARZ_DIAMOND_NAME: lambda x, y, z: (
        np.sin(x) * np.sin(y) * np.sin(z)
        + np.sin(x) * np.cos(y) * np.cos(z)
        + np.cos(x) * np.sin(y) * np.cos(z)
        + np.cos(x) * np.cos(y) * np.sin(z)
    ),
    "Neovius": lambda x, y, z: (
        3.0 * (np.cos(x) + np.cos(y) + np.cos(z))
        + 4.0 * np.cos(x) * np.cos(y) * np.cos(z)
    ),
    "Lidinoid": lambda x, y, z: (
        np.sin(x) * np.sin(2.0 * y) * np.cos(z)
        + np.sin(2.0 * x) * np.cos(y) * np.sin(z)
        + np.cos(x) * np.sin(y) * np.sin(2.0 * z)
        - np.cos(2.0 * x) * np.cos(2.0 * y)
        - np.cos(2.0 * x) * np.cos(2.0 * z)
        - np.cos(2.0 * y) * np.cos(2.0 * z)
        + 0.3
    ),
    "IWP": lambda x, y, z: (
        2.0 * (np.cos(x) * np.cos(y) + np.cos(y) * np.cos(z) + np.cos(z) * np.cos(x))
        - (np.cos(2.0 * x) + np.cos(2.0 * y) + np.cos(2.0 * z))
    ),
    "FRD": lambda x, y, z: (
        4.0 * np.cos(x) * np.cos(y) * np.cos(z)
        - (
            np.cos(2.0 * x) * np.cos(2.0 * y)
            + np.cos(2.0 * y) * np.cos(2.0 * z)
            + np.cos(2.0 * z) * np.cos(2.0 * x)
        )
    ),
    "PMY": lambda x, y, z: (
        2.0 * np.cos(x) * np.cos(y) * np.cos(z)
        + np.sin(2.0 * x) * np.sin(y)
        + np.sin(2.0 * y) * np.sin(z)
        + np.sin(2.0 * z) * np.sin(x)
    ),
    "Fischer Koch S": lambda x, y, z: (
        np.cos(2.0 * x) * np.sin(y) * np.cos(z)
        + np.cos(x) * np.cos(2.0 * y) * np.sin(z)
        + np.sin(x) * np.cos(y) * np.cos(2.0 * z)
    ),
    "Fischer Koch Y": lambda x, y, z: (
        np.cos(x) * np.cos(y) * np.cos(z)
        + np.sin(x) * np.sin(y) * np.sin(z)
        + np.sin(2.0 * x) * np.sin(y)
        + np.cos(x) * np.sin(2.0 * y)
        + np.sin(x) * np.sin(2.0 * z)
        + np.sin(2.0 * x) * np.cos(z)
        + np.sin(2.0 * y) * np.sin(z)
        + np.cos(y) * np.sin(2.0 * z)
    ),
    "Split P": lambda x, y, z: (
        1.1
        * (
            np.sin(2.0 * x) * np.cos(y) * np.sin(z)
            + np.sin(2.0 * y) * np.cos(z) * np.sin(x)
            + np.sin(2.0 * z) * np.cos(x) * np.sin(y)
        )
        - 0.2
        * (
            np.cos(2.0 * x) * np.cos(2.0 * y)
            + np.cos(2.0 * y) * np.cos(2.0 * z)
            + np.cos(2.0 * z) * np.cos(2.0 * x)
        )
        - 0.4 * (np.cos(2.0 * x) + np.cos(2.0 * y) + np.cos(2.0 * z))
    ),
    "Tubular P": lambda x, y, z: (
        np.cos(x)
        + np.cos(y)
        + np.cos(z)
        - 0.5 * (np.cos(x) * np.cos(y) + np.cos(y) * np.cos(z) + np.cos(z) * np.cos(x))
    ),
    "Tubular G": lambda x, y, z: (
        np.sin(x) * np.cos(y)
        + np.sin(y) * np.cos(z)
        + np.sin(z) * np.cos(x)
        - 0.2 * (np.cos(2.0 * x) + np.cos(2.0 * y) + np.cos(2.0 * z))
    ),
    "Schwarz CLP": lambda x, y, z: (
        np.sin(x) * np.cos(y)
        + np.sin(y) * np.cos(z)
        + np.sin(z) * np.cos(x)
        + 0.3
        * (
            np.cos(2.0 * x) * np.cos(2.0 * y)
            + np.cos(2.0 * y) * np.cos(2.0 * z)
            + np.cos(2.0 * z) * np.cos(2.0 * x)
        )
    ),
    "Lonsdaleite": lambda x, y, z: (
        np.cos(x) * np.cos(y)
        + np.cos(y) * np.cos(z)
        + np.cos(z) * np.cos(x)
        + 0.5 * (np.cos(x) + np.cos(y) + np.cos(z))
    ),
    "Octo": lambda x, y, z: (
        4.0 * (np.cos(x) * np.cos(y) + np.cos(y) * np.cos(z) + np.cos(z) * np.cos(x))
        - 3.0 * np.cos(x) * np.cos(y) * np.cos(z)
    ),
    "Schwarz H": lambda x, y, z: (
        np.cos(x)
        + np.cos(y)
        + np.cos(z)
        + np.cos(x) * np.cos(y)
        + np.cos(y) * np.cos(z)
        + np.cos(z) * np.cos(x)
    ),
    "KP Minimal": lambda x, y, z: (
        0.5 * (np.cos(x) + np.cos(y) + np.cos(z))
        + np.cos(x) * np.cos(y) * np.cos(z)
    ),
}

TPMS_EQUATIONS: dict[str, str] = {
    "Gyroid": "sin(X)*cos(Y) + sin(Y)*cos(Z) + sin(Z)*cos(X)",
    SCHWARZ_PRIMITIVE_NAME: "cos(X) + cos(Y) + cos(Z)",
    SCHWARZ_DIAMOND_SURFACE_NAME: "cos(X)*cos(Y)*cos(Z) - sin(X)*sin(Y)*sin(Z)",
    SCHWARZ_DIAMOND_NAME: "sin(X)*sin(Y)*sin(Z) + sin(X)*cos(Y)*cos(Z) + cos(X)*sin(Y)*cos(Z) + cos(X)*cos(Y)*sin(Z)",
    "Neovius": "3*(cos(X) + cos(Y) + cos(Z)) + 4*cos(X)*cos(Y)*cos(Z)",
    "Lidinoid": "sin(X)*sin(2*Y)*cos(Z) + sin(2*X)*cos(Y)*sin(Z) + cos(X)*sin(Y)*sin(2*Z) - cos(2*X)*cos(2*Y) - cos(2*X)*cos(2*Z) - cos(2*Y)*cos(2*Z) + 0.3",
    "IWP": "2*(cos(X)*cos(Y) + cos(Y)*cos(Z) + cos(Z)*cos(X)) - (cos(2*X) + cos(2*Y) + cos(2*Z))",
    "FRD": "4*cos(X)*cos(Y)*cos(Z) - (cos(2*X)*cos(2*Y) + cos(2*Y)*cos(2*Z) + cos(2*Z)*cos(2*X))",
    "PMY": "2*cos(X)*cos(Y)*cos(Z) + sin(2*X)*sin(Y) + sin(2*Y)*sin(Z) + sin(2*Z)*sin(X)",
    "Fischer Koch S": "cos(2*X)*sin(Y)*cos(Z) + cos(X)*cos(2*Y)*sin(Z) + sin(X)*cos(Y)*cos(2*Z)",
    "Fischer Koch Y": "sin(X)*sin(Y)*sin(Z) + cos(X)*cos(Y)*cos(Z) + sin(2*X)*sin(Y) + cos(X)*sin(2*Y) + sin(X)*sin(2*Z) + sin(2*X)*cos(Z) + sin(2*Y)*sin(Z) + cos(Y)*sin(2*Z)",
    "Split P": "-0.2*(cos(2*X)*cos(2*Y) + cos(2*X)*cos(2*Z) + cos(2*Y)*cos(2*Z)) - 0.4*(cos(2*X) + cos(2*Y) + cos(2*Z)) + 1.1*(sin(X)*sin(2*Y)*cos(Z) + sin(2*X)*cos(Y)*sin(Z) + cos(X)*sin(Y)*sin(2*Z))",
    "Tubular P": "cos(X) + cos(Y) + cos(Z) - 0.5*(cos(X)*cos(Y) + cos(Y)*cos(Z) + cos(Z)*cos(X))",
    "Tubular G": "sin(X)*cos(Y) + sin(Y)*cos(Z) + sin(Z)*cos(X) - 0.2*(cos(2*X) + cos(2*Y) + cos(2*Z))",
    "Schwarz CLP": "sin(X)*cos(Y) + sin(Y)*cos(Z) + sin(Z)*cos(X) + 0.3*(cos(2*X)*cos(2*Y) + cos(2*Y)*cos(2*Z) + cos(2*Z)*cos(2*X))",
    "Lonsdaleite": "cos(X)*cos(Y) + cos(Y)*cos(Z) + cos(Z)*cos(X) + 0.5*(cos(X) + cos(Y) + cos(Z))",
    "Octo": "4*(cos(X)*cos(Y) + cos(Y)*cos(Z) + cos(Z)*cos(X)) - 3*cos(X)*cos(Y)*cos(Z)",
    "Schwarz H": "cos(X) + cos(Y) + cos(Z) + cos(X)*cos(Y) + cos(Y)*cos(Z) + cos(Z)*cos(X)",
    "KP Minimal": "0.5*(cos(X) + cos(Y) + cos(Z)) + cos(X)*cos(Y)*cos(Z)",
}


# ── 2. Truss Lattice Functions (from Gitterburg core/libraries.py & research_lattice_*) ──
CORNERS: tuple[Node, ...] = tuple(product((0.0, 1.0), repeat=3))
CUBE_EDGES: tuple[Edge, ...] = tuple(
    (start, end)
    for index, start in enumerate(CORNERS)
    for end in CORNERS[index + 1 :]
    if sum(a != b for a, b in zip(start, end)) == 1
)


def _face_centers() -> tuple[Node, ...]:
    return (
        (0.0, 0.5, 0.5),
        (1.0, 0.5, 0.5),
        (0.5, 0.0, 0.5),
        (0.5, 1.0, 0.5),
        (0.5, 0.5, 0.0),
        (0.5, 0.5, 1.0),
    )


def _shared_face(center: Node, corner: Node) -> bool:
    return any(value in (0.0, 1.0) and corner[i] == value for i, value in enumerate(center))


def _periodic_distance(a: Node, b: Node) -> float:
    delta = tuple(min(abs(x - y), 1.0 - abs(x - y)) for x, y in zip(a, b))
    return sum(value * value for value in delta) ** 0.5


def _distance(a: Node, b: Node) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _periodic_nearest_edges(nodes: tuple[Node, ...], target: float) -> tuple[Edge, ...]:
    tolerance = 1e-6
    edges = []
    for index, start in enumerate(nodes):
        for end in nodes[index + 1 :]:
            if abs(_periodic_distance(start, end) - target) <= tolerance:
                unwrapped_end = tuple(
                    s + (e - s - round(e - s)) for s, e in zip(start, end)
                )
                edges.append((start, unwrapped_end))
    return tuple(edges)


@lru_cache(maxsize=16)
def bcc_z_edges() -> tuple[Edge, ...]:
    center = (0.5, 0.5, 0.5)
    vertical = tuple(edge for edge in CUBE_EDGES if edge[0][:2] == edge[1][:2])
    return tuple((center, corner) for corner in CORNERS) + vertical


@lru_cache(maxsize=16)
def fcc_z_edges() -> tuple[Edge, ...]:
    faces = _face_centers()
    face_edges = tuple(
        (center, corner)
        for center in faces
        for corner in CORNERS
        if _shared_face(center, corner)
    )
    vertical = tuple(edge for edge in CUBE_EDGES if edge[0][:2] == edge[1][:2])
    return face_edges + vertical


@lru_cache(maxsize=16)
def diamond_edges() -> tuple[Edge, ...]:
    basis = (
        (0.0, 0.0, 0.0),
        (0.0, 0.5, 0.5),
        (0.5, 0.0, 0.5),
        (0.5, 0.5, 0.0),
        (0.25, 0.25, 0.25),
        (0.25, 0.75, 0.75),
        (0.75, 0.25, 0.75),
        (0.75, 0.75, 0.25),
    )
    return _periodic_nearest_edges(basis, 3.0**0.5 / 4.0)


@lru_cache(maxsize=16)
def kelvin_edges() -> tuple[Edge, ...]:
    raw = set()
    for values in set(permutations((0.0, 1.0, 2.0))):
        for signs in product((-1.0, 1.0), repeat=3):
            raw.add(tuple(0.5 + signs[i] * values[i] / 4.0 for i in range(3)))
    return _periodic_nearest_edges(tuple(sorted(raw)), 2.0**0.5 / 4.0)


@lru_cache(maxsize=16)
def rhombic_dodecahedron_edges() -> tuple[Edge, ...]:
    inner = tuple(product((0.25, 0.75), repeat=3))
    outer = _face_centers()
    return tuple((a, b) for a in inner for b in outer if _distance(a, b) < 0.44)


@lru_cache(maxsize=16)
def tetrahedral_edges() -> tuple[Edge, ...]:
    return tuple(
        (center, corner)
        for center in _face_centers()
        for corner in CORNERS
        if _shared_face(center, corner)
    )


@lru_cache(maxsize=16)
def x_braced_cubic_edges() -> tuple[Edge, ...]:
    diagonals = tuple(
        (a, b)
        for index, a in enumerate(CORNERS)
        for b in CORNERS[index + 1 :]
        if sum(x != y for x, y in zip(a, b)) >= 2
    )
    return CUBE_EDGES + diagonals


@lru_cache(maxsize=16)
def pyramidal_edges() -> tuple[Edge, ...]:
    center = (0.5, 0.5, 0.5)
    return tuple((center, corner) for corner in CORNERS) + CUBE_EDGES


@lru_cache(maxsize=16)
def kagome_type_edges() -> tuple[Edge, ...]:
    layer = ((0.0, 0.0), (1.0, 0.0), (0.5, 0.5), (0.0, 1.0), (1.0, 1.0))
    layers = tuple(tuple((x, y, z) for x, y in layer) for z in (0.0, 1.0))
    planar = tuple(
        edge for nodes in layers for edge in _periodic_nearest_edges(nodes, 0.5**0.5)
    )
    vertical = tuple(((x, y, 0.0), (x, y, 1.0)) for x, y in layer)
    return planar + vertical


_PERIOD = 2.0 * np.pi


def _pw(v: np.ndarray) -> np.ndarray:
    return np.mod(v + np.pi, 2.0 * np.pi) - np.pi


def _bcc_strut_distance(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    xc, yc, zc = x - np.pi, y - np.pi, z - np.pi

    def _d2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        aw, bw = _pw(a), _pw(b)
        return 2.0 * (aw * aw - aw * bw + bw * bw) / 3.0

    return np.sqrt(
        np.minimum(
            np.minimum(_d2(xc - yc, xc - zc), _d2(xc + yc, xc + zc)),
            np.minimum(_d2(xc + yc, xc - zc), _d2(xc - yc, xc + zc)),
        )
    )


def _fcc_strut_distance(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    def _d2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return _pw(a) ** 2 / 2.0 + _pw(b) ** 2

    return np.sqrt(
        np.minimum(
            np.minimum(
                np.minimum(_d2(x - y, z), _d2(x + y, z)),
                np.minimum(_d2(x - z, y), _d2(x + z, y)),
            ),
            np.minimum(_d2(y - z, x), _d2(y + z, x)),
        )
    )


def _cubic_strut_distance(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    def _d2(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return _pw(a) ** 2 + _pw(b) ** 2

    return np.sqrt(np.minimum(np.minimum(_d2(y, z), _d2(x, z)), _d2(x, y)))


def _periodic_graph_distance(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    edges: tuple[Edge, ...],
) -> np.ndarray:
    native = graph_field(x, y, z, edges)
    if native is not None:
        return native
    points = tuple(np.mod(axis, _PERIOD) / _PERIOD for axis in (x, y, z))
    minimum = np.full(np.broadcast_shapes(x.shape, y.shape, z.shape), np.inf)
    for start, end in edges:
        midpoint = tuple((start[i] + end[i]) * 0.5 for i in range(3))
        shift = tuple(np.round(points[i] - midpoint[i]) for i in range(3))
        shifted_start = tuple(start[i] + shift[i] for i in range(3))
        shifted_end = tuple(end[i] + shift[i] for i in range(3))
        direction = tuple(shifted_end[i] - shifted_start[i] for i in range(3))
        length_sq = sum(v * v for v in direction)
        proj = sum((points[i] - shifted_start[i]) * direction[i] for i in range(3))
        param = np.clip(proj / length_sq, 0.0, 1.0)
        dist_sq = sum(
            (points[i] - shifted_start[i] - param * direction[i]) ** 2
            for i in range(3)
        )
        minimum = np.minimum(minimum, dist_sq)
    return np.sqrt(minimum) * _PERIOD


def _graph_field(edges_factory: Callable[[], tuple[Edge, ...]]):
    def evaluate(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
        return _periodic_graph_distance(x, y, z, edges_factory())

    return evaluate


LATTICE_ITEMS: tuple[str, ...] = (
    "BCC",
    "FCC",
    "Octet Truss",
    "Cubic",
    "BCC-Z",
    "FCC-Z",
    "Diamond Truss",
    "Kelvin Cell",
    "Rhombic Dodecahedron",
    "Tetrahedral Truss",
    "X-Braced Cubic",
    "Pyramidal Lattice",
    "Kagome-type Lattice",
)

LATTICE_FUNCTIONS: dict[str, Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]] = {
    "BCC": _bcc_strut_distance,
    "FCC": _fcc_strut_distance,
    "Octet Truss": lambda x, y, z: np.minimum(
        _bcc_strut_distance(x, y, z), _fcc_strut_distance(x, y, z)
    ),
    "Cubic": _cubic_strut_distance,
    "BCC-Z": _graph_field(bcc_z_edges),
    "FCC-Z": _graph_field(fcc_z_edges),
    "Diamond Truss": _graph_field(diamond_edges),
    "Kelvin Cell": _graph_field(kelvin_edges),
    "Rhombic Dodecahedron": _graph_field(rhombic_dodecahedron_edges),
    "Tetrahedral Truss": _graph_field(tetrahedral_edges),
    "X-Braced Cubic": _graph_field(x_braced_cubic_edges),
    "Pyramidal Lattice": _graph_field(pyramidal_edges),
    "Kagome-type Lattice": _graph_field(kagome_type_edges),
}


# ── 3. Porous Structure Generators (from Gitterburg core/porous.py & services/advanced_porous_*) ──
POROUS_ITEMS: tuple[str, ...] = (
    "Voronoi Cell",
    "Advanced Voronoi",
    "Stochastic Foam",
    "Gaussian Random Field",
    "Reaction-Diffusion",
    "Spinodal / Cahn-Hilliard",
    "Wang Tile",
    "Stochastic Reconstruction",
)

ALL_STRUCTURES_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "tpms": TPMS_ITEMS,
    "lattice": LATTICE_ITEMS,
    "porous": POROUS_ITEMS,
}


def resolve_family_and_name(family: str, name: str) -> tuple[str, str]:
    fam = (family or "tpms").strip().lower()
    if fam not in STRUCTURE_FAMILIES:
        fam = "tpms"
    clean_name = (name or "").strip()
    if fam == "tpms":
        clean_name = TPMS_ALIASES.get(clean_name, clean_name)
    valid_items = ALL_STRUCTURES_BY_FAMILY[fam]
    if clean_name not in valid_items:
        # Check if the name belongs to another family automatically
        for candidate_fam, items in ALL_STRUCTURES_BY_FAMILY.items():
            mapped = TPMS_ALIASES.get(clean_name, clean_name) if candidate_fam == "tpms" else clean_name
            if mapped in items:
                return candidate_fam, mapped
        return fam, valid_items[0]
    return fam, clean_name


def _normalize_scalar_field(field: np.ndarray) -> np.ndarray:
    arr = np.asarray(field, dtype=np.float32)
    arr = arr - float(np.mean(arr))
    std = float(np.std(arr))
    if std > 1e-8:
        arr = arr / std
    return arr


@lru_cache(maxsize=16)
def _voronoi_seeds(
    size: float,
    cell_size: float,
    random_seed: int,
    advanced: bool = False,
) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(int(random_seed))
    cells_per_axis = max(1.0, float(size) / max(float(cell_size), 1e-3))
    seed_count = int(np.clip(round(6.0 * (cells_per_axis**3)), 6, 600))
    seeds = rng.random((seed_count, 3), dtype=np.float32) * float(size)
    weights = rng.uniform(0.0, 0.35 * (float(cell_size) ** 2), size=seed_count).astype(np.float32)
    if advanced:
        # 2 Lloyd centroidal relaxation iterations on a coarse sample grid
        axis = np.linspace(0.0, float(size), 24, dtype=np.float32)
        gx, gy, gz = np.meshgrid(axis, axis, axis, indexing="ij")
        sample = np.column_stack((gx.ravel(), gy.ravel(), gz.ravel()))
        for _ in range(2):
            d2 = (
                np.square(sample[:, 0, None] - seeds[None, :, 0])
                + np.square(sample[:, 1, None] - seeds[None, :, 1])
                + np.square(sample[:, 2, None] - seeds[None, :, 2])
                - weights[None, :]
            )
            labels = np.argmin(d2, axis=1)
            for idx in range(seed_count):
                region = sample[labels == idx]
                if len(region):
                    seeds[idx] = region.mean(axis=0)
    return seeds, weights


def _evaluate_voronoi(
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
    size: float,
    cell_size: float,
    random_seed: int,
    advanced: bool = False,
) -> np.ndarray:
    gx, gy, gz = np.broadcast_arrays(x, y, z)
    shape = gx.shape
    points = np.column_stack((gx.ravel(), gy.ravel(), gz.ravel())).astype(np.float32, copy=False)
    seeds, weights = _voronoi_seeds(float(size), float(cell_size), int(random_seed), advanced=advanced)
    if not advanced:
        tree = cKDTree(seeds)
        distances, _ = tree.query(points, k=2)
        distances = np.asarray(distances, dtype=np.float32)
        gap = distances[:, 1] - distances[:, 0]
        return gap.reshape(shape)
    # Power distance gap for Advanced Voronoi
    chunk_size = 65536
    gaps = np.empty(len(points), dtype=np.float32)
    for start in range(0, len(points), chunk_size):
        end = min(start + chunk_size, len(points))
        pts = points[start:end]
        d2 = (
            np.square(pts[:, 0, None] - seeds[None, :, 0])
            + np.square(pts[:, 1, None] - seeds[None, :, 1])
            + np.square(pts[:, 2, None] - seeds[None, :, 2])
            - weights[None, :]
        )
        nearest = np.partition(d2, 1, axis=1)[:, :2]
        gaps[start:end] = np.sqrt(np.maximum(nearest[:, 1] - nearest[:, 0], 0.0))
    return gaps.reshape(shape)


@lru_cache(maxsize=16)
def _cached_porous_reference_grid(
    porous_name: str,
    size: float,
    cell_size: float,
    random_seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a canonical 3D scalar field on [0, size]^3 so surface and FE mesh match."""
    cells_per_axis = np.clip(float(size) / max(float(cell_size), 1e-3), 1.0, 12.0)
    res = int(np.clip(round(cells_per_axis * 16), 36, 72))
    axis = np.linspace(0.0, float(size), res, dtype=np.float32)
    sigma_voxels = max(1.2, (float(cell_size) / float(size)) * res * 0.22)
    rng = np.random.default_rng(int(random_seed))
    shape = (res, res, res)

    if porous_name == "Stochastic Foam":
        noise = rng.standard_normal(shape, dtype=np.float32)
        field = gaussian_filter(noise, sigma=sigma_voxels, mode="reflect")
        return axis, _normalize_scalar_field(field)

    if porous_name == "Gaussian Random Field":
        noise = rng.standard_normal(shape, dtype=np.float32)
        field = gaussian_filter(noise, sigma=sigma_voxels * 1.35, mode="wrap")
        return axis, _normalize_scalar_field(field)

    if porous_name == "Reaction-Diffusion":
        u = np.ones(shape, dtype=np.float32) + 0.04 * rng.standard_normal(shape, dtype=np.float32)
        v = np.zeros(shape, dtype=np.float32)
        seed_patches = max(6, int(round(cells_per_axis**3 * 2.5)))
        span = max(2, res // 10)
        margin = span + 1
        for _ in range(seed_patches):
            cx, cy, cz = (int(rng.integers(margin, max(margin + 1, res - margin))) for _ in range(3))
            rx, ry, rz = (int(rng.integers(1, span + 1)) for _ in range(3))
            sl = (
                slice(max(cx - rx, 0), min(cx + rx + 1, res)),
                slice(max(cy - ry, 0), min(cy + ry + 1, res)),
                slice(max(cz - rz, 0), min(cz + rz + 1, res)),
            )
            loc = v[sl].shape
            v[sl] = np.maximum(v[sl], 0.5 + 0.04 * rng.random(loc, dtype=np.float32))
            u[sl] = np.minimum(u[sl], 0.5 + 0.25 * rng.random(loc, dtype=np.float32))
        du, dv, feed, kill, dt = 0.16, 0.08, 0.035, 0.060, 1.0
        for _ in range(80):
            lap_u = (
                np.roll(u, 1, 0) + np.roll(u, -1, 0)
                + np.roll(u, 1, 1) + np.roll(u, -1, 1)
                + np.roll(u, 1, 2) + np.roll(u, -1, 2)
                - 6.0 * u
            )
            lap_v = (
                np.roll(v, 1, 0) + np.roll(v, -1, 0)
                + np.roll(v, 1, 1) + np.roll(v, -1, 1)
                + np.roll(v, 1, 2) + np.roll(v, -1, 2)
                - 6.0 * v
            )
            uvv = u * v * v
            u += (du * lap_u - uvv + feed * (1.0 - u)) * dt
            v += (dv * lap_v + uvv - (feed + kill) * v) * dt
            np.clip(u, 0.0, 1.0, out=u)
            np.clip(v, 0.0, 1.0, out=v)
        field = _normalize_scalar_field(v - 0.35 * u)
        return axis, field

    if porous_name == "Spinodal / Cahn-Hilliard":
        phi = 0.05 * rng.standard_normal(shape)
        phi = phi - float(np.mean(phi))
        k2 = np.zeros(shape, dtype=np.float64)
        for ax_idx in range(3):
            reshape = [1, 1, 1]
            reshape[ax_idx] = res
            wn = 2.0 * np.pi * fftfreq(res, d=max(1.0 / cells_per_axis, 0.25))
            k2 += wn.reshape(reshape) ** 2
        mobility, epsilon, dt, stab = 1.0, 0.85, 0.015, 2.0
        for _ in range(45):
            spec = fftn(phi)
            bulk = fftn(phi**3 - phi)
            scale = dt * mobility * k2
            s_val = max(stab, 0.5 * float(np.max(np.abs(3.0 * phi**2 - 1.0))))
            num = spec - scale * (bulk - s_val * spec)
            den = 1.0 + scale * (s_val + (epsilon**2) * k2)
            phi = ifftn(num / den).real
        return axis, _normalize_scalar_field(phi.astype(np.float32))

    if porous_name == "Wang Tile":
        tile_count = max(2, int(round(cells_per_axis)))
        tile_size = max(10, int(round(res / tile_count)))
        total_res = tile_count * tile_size
        axis_wt = np.linspace(0.0, float(size), total_res, dtype=np.float32)
        band = max(1, tile_size // 4)
        edge_slices = [
            (slice(0, 1), slice(None), slice(None)),
            (slice(tile_size - 1, tile_size), slice(None), slice(None)),
            (slice(None), slice(0, 1), slice(None)),
            (slice(None), slice(tile_size - 1, tile_size), slice(None)),
            (slice(None), slice(None), slice(0, 1)),
            (slice(None), slice(None), slice(tile_size - 1, tile_size)),
        ]
        core_sl = (slice(band, tile_size - band),) * 3
        tiles = []
        for t_id in range(12):
            edges = tuple(int(v) for v in rng.integers(0, 2, size=6))
            tf = rng.random((tile_size, tile_size, tile_size), dtype=np.float32)
            tf = gaussian_filter(tf, sigma=max(tile_size / 6.0, 1.0), mode="reflect")
            tf[core_sl] += float(rng.uniform(-0.15, 0.15))
            for e_idx, sl in enumerate(edge_slices):
                tf[sl] += 0.25 if edges[e_idx] else -0.25
            tiles.append((t_id, edges, tf))
        vol = np.zeros((total_res, total_res, total_res), dtype=np.float32)
        placed = [[[None for _ in range(tile_count)] for _ in range(tile_count)] for _ in range(tile_count)]
        for ix in range(tile_count):
            for iy in range(tile_count):
                for iz in range(tile_count):
                    xn = placed[ix - 1][iy][iz] if ix > 0 else None
                    yn = placed[ix][iy - 1][iz] if iy > 0 else None
                    zn = placed[ix][iy][iz - 1] if iz > 0 else None
                    cands = [
                        t
                        for t in tiles
                        if (xn is None or xn[1][1] == t[1][0])
                        and (yn is None or yn[1][3] == t[1][2])
                        and (zn is None or zn[1][5] == t[1][4])
                    ] or tiles
                    chosen = cands[int(rng.integers(0, len(cands)))]
                    placed[ix][iy][iz] = chosen
                    vol[
                        ix * tile_size : (ix + 1) * tile_size,
                        iy * tile_size : (iy + 1) * tile_size,
                        iz * tile_size : (iz + 1) * tile_size,
                    ] = chosen[2]
        field = gaussian_filter(vol, sigma=max(tile_size / 7.5, 1.0), mode="reflect")
        return axis_wt, _normalize_scalar_field(field)

    # Default / Stochastic Reconstruction
    binary = rng.random(shape) >= 0.5
    void_dist = distance_transform_edt(~binary)
    solid_dist = distance_transform_edt(binary)
    signed = solid_dist - void_dist
    field = gaussian_filter(signed.astype(np.float32), sigma=sigma_voxels, mode="reflect")
    return axis, _normalize_scalar_field(field)


def evaluate_priority_field(
    settings,
    x: np.ndarray,
    y: np.ndarray,
    z: np.ndarray,
) -> np.ndarray:
    """Evaluate 3D priority field at coordinates (x, y, z) in mm.

    Lower returned values correspond to the solid phase first when sorting or
    thresholding by solid fraction.
    """
    family, name = resolve_family_and_name(
        getattr(settings, "structure_family", "tpms"),
        getattr(settings, "structure_name", "Gyroid"),
    )
    morphology = getattr(settings, "tpms_morphology", "sheet") or "sheet"
    cell_size = float(getattr(settings, "cell_size", 3.0))
    seed = int(getattr(settings, "random_seed", 42))

    if family == "tpms":
        k = 2.0 * np.pi / cell_size
        # Keep axis inputs sparse while evaluating trig functions. Broadcasting
        # them first repeats each sine/cosine on every point of the 3D volume.
        kx, ky, kz = k * x, k * y, k * z
        func = TPMS_FUNCTIONS.get(name, TPMS_FUNCTIONS["Gyroid"])
        raw = tpms_field(TPMS_ITEMS.index(name), x, y, z, k)
        if raw is None:
            raw = np.asarray(func(kx, ky, kz), dtype=np.float64)
        if morphology == "network_a":
            return raw
        if morphology == "network_b":
            return -raw
        return np.abs(raw)

    if family == "lattice":
        k = 2.0 * np.pi / cell_size
        kx, ky, kz = k * x, k * y, k * z
        func = LATTICE_FUNCTIONS.get(name, LATTICE_FUNCTIONS["BCC"])
        dist = np.asarray(func(kx, ky, kz), dtype=np.float64)
        if morphology == "network_b":
            return -dist
        return dist

    # Crop the reference field to the physical specimen. Using only its height
    # would clamp samples along longer axes at the edge of the reference grid.
    size = max(getattr(settings, "dimensions", (getattr(settings, "size", 6.0),)))
    if name in ("Voronoi Cell", "Advanced Voronoi"):
        gap = _evaluate_voronoi(
            x, y, z, size, cell_size, seed, advanced=(name == "Advanced Voronoi")
        )
        return -gap.astype(np.float64) if morphology == "network_b" else gap.astype(np.float64)

    axis, ref_field = _cached_porous_reference_grid(name, size, cell_size, seed)
    sampled = interpolate_field(x, y, z, axis, ref_field, size)
    if sampled is not None:
        if morphology == "sheet":
            return np.abs(sampled)
        if morphology == "network_b":
            return sampled
        return -sampled
    gx, gy, gz = np.broadcast_arrays(x, y, z)
    pts = np.column_stack(
        (
            np.clip(gx.ravel(), 0.0, float(size)),
            np.clip(gy.ravel(), 0.0, float(size)),
            np.clip(gz.ravel(), 0.0, float(size)),
        )
    )
    interpolator = RegularGridInterpolator(
        (axis, axis, axis), ref_field, method="linear", bounds_error=False, fill_value=None
    )
    sampled = interpolator(pts).reshape(gx.shape).astype(np.float64)
    if morphology == "sheet":
        return np.abs(sampled)
    if morphology == "network_b":
        return sampled
    return -sampled


def describe_structure(settings) -> str:
    if getattr(settings, 'imported_geometry_path', ''):
        return f"Imported geometry: {settings.imported_geometry_name}; selected support and compression end faces; coordinates in mm"
    family, name = resolve_family_and_name(
        getattr(settings, "structure_family", "tpms"),
        getattr(settings, "structure_name", "Gyroid"),
    )
    morphology = getattr(settings, "tpms_morphology", "sheet") or "sheet"
    morph_label = TPMS_MORPHOLOGIES.get(morphology, "Sheet")
    if family == "tpms":
        eq = TPMS_EQUATIONS.get(name, "")
        return f"TPMS · {name} ({morph_label}) | {eq}"
    if family == "lattice":
        return f"Truss Lattice · {name} (Periodic Strut Distance Field)"
    return f"Porous · {name} (Seed={getattr(settings, 'random_seed', 42)}, {morph_label})"
