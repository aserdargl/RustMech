# RustMech Netgen core

This is a separate STL meshing build of Netgen 6.2.2608, pinned to
`96e5682f6ea43ba77ba3bb2ae4bb4bd1791f506e`. It does not replace the Python
environment's Netgen/OCCT DLLs. The installed pair has private names and an
array-only C ABI; the regular Netgen package remains the fallback.

`Build-NetgenKernel.ps1 -Check -Install` obtains pinned Netgen and zlib sources,
applies `patch_netgen.py`, builds the DLLs and verifies 37,054 spatial/weld/adjacency queries
against exhaustive search. Patches reproduce from a clean pinned checkout and
are idempotent. The source directories and intermediate build directories are
excluded from version control. `LICENSE.Netgen` and `LICENSE.zlib` accompany
the source/build instructions.

The patches preserve the input triangles, local refinement samples, all mesh
sizes, floating-point contraction setting and optimization passes:

- Immutable balanced point indices replace exhaustive inner-chart searches
  and repeatedly allocated chart-distance trees. Final distance comparisons
  still use Netgen's original `Dist2`, strict radius and inclusive box tests.
- Repeated exact canonical STL coordinates reuse their original node ID.
  New canonical insertions invalidate any potentially ambiguous cached query
  using the original tree's float-coordinate/float-box comparisons. Inexact
  merges continue through the original tree. Randomized reference tests cover
  duplicate, near, ambiguous and rounded-boundary coordinates at three scales.
- The outer-chart distance rejection runs before the expensive intersection
  predicate, with the same acceptance condition. Immutable per-point proximity
  results are reused within one outer-chart expansion; sizing changes between
  charts invalidate these results through chart-generation stamps.
- Boundary intersections address stable segment slots directly rather than
  recovering each candidate through an additional edge-key hash lookup.
  Removed slots are recycled; projection/normal calculations are performed
  only for newly inserted edges. A conservative sparse 2D grid finds candidate
  edges; the original intersection predicate decides acceptance. Long edges and
  large queries use exact bounded fallbacks. The original generic BoxTree is
  unchanged.
- Single-thread volume adjacency tables use plain counters and ascending
  element traversal. Their entries and ordering match the original sorted
  tables, including masks, deleted elements and domain filters; the multi-thread
  path remains the upstream implementation.
- The five original meshing stages are executed in order and report progress.
  Cancellation/error cleanup releases the global mesh reference before reuse.

`netgen_kernel.py` checks the build revision and both DLL checksums before use.
NumPy owns all returned coordinate/connectivity buffers. Existing Rust TET10
assembly and bounded linear-mesh reuse operate above this core.

The MSVC profile-guided experiment did not improve the controlled comparison
and is not enabled or installed. Its experimental build configuration is
archived under `benchmarks/netgen_kernel/pgo_experiment`.

The original algorithms are in [Netgen's pinned source](https://github.com/NGSolve/netgen/tree/v6.2.2608/libsrc/stlgeom).
