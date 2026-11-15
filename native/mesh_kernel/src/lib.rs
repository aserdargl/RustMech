//! Deterministic voxel selection and VTK HEX8/HEX20 assembly.
//! No Python ABI or third-party dependencies; callers own all output arrays.

use std::cell::RefCell;
use std::ffi::{CString, c_char};
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::{ptr, slice};

mod accelerate;
mod surface;
mod tetra;
#[allow(unused_parens)]
mod tpms_generated;

const MAX_AXIS: usize = 150;
const SELECTED: u32 = u32::MAX;
const CORNERS: [[usize; 3]; 8] = [
    [0, 0, 0],
    [1, 0, 0],
    [1, 1, 0],
    [0, 1, 0],
    [0, 0, 1],
    [1, 0, 1],
    [1, 1, 1],
    [0, 1, 1],
];
const EDGES: [(usize, usize); 12] = [
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 0),
    (4, 5),
    (5, 6),
    (6, 7),
    (7, 4),
    (0, 4),
    (1, 5),
    (2, 6),
    (3, 7),
];

thread_local! {
    static LAST_ERROR: RefCell<CString> = RefCell::new(CString::default());
}

fn guarded<T>(operation: impl FnOnce() -> Result<T, String>) -> Option<T> {
    let error = match catch_unwind(AssertUnwindSafe(operation)) {
        Ok(Ok(value)) => return Some(value),
        Ok(Err(error)) => error,
        Err(_) => "Rust mesh kernel panicked".to_string(),
    };
    LAST_ERROR.with(|slot| *slot.borrow_mut() = CString::new(error).unwrap_or_default());
    None
}

fn dimensions(nx: usize, ny: usize, nz: usize, length: usize) -> Result<(), String> {
    if [nx, ny, nz].iter().any(|&n| n == 0 || n > MAX_AXIS) {
        return Err(format!("Voxel dimensions must be between 1 and {MAX_AXIS}"));
    }
    if nx * ny * nz != length {
        return Err("Voxel buffer length does not match its dimensions".into());
    }
    Ok(())
}

fn select(values: &[f64], shape: [usize; 3], count: usize) -> Result<(Vec<u32>, u32, u32), String> {
    if count > values.len() || values.iter().any(|v| !v.is_finite()) {
        return Err("Voxel values must be finite and count must not exceed their length".into());
    }
    let mut labels = vec![0; values.len()];
    if count == 0 {
        return Ok((labels, 0, 0));
    }
    // Partition values, then resolve threshold ties in C grid order. This is
    // identical to taking count items from a stable argsort, without sorting all.
    let threshold = {
        let mut scratch = values.to_vec();
        let (_, pivot, _) =
            scratch.select_nth_unstable_by(count - 1, |a, b| a.partial_cmp(b).unwrap());
        *pivot
    };
    let below = values.iter().filter(|&&value| value < threshold).count();
    let mut ties_left = count - below;
    for (label, &value) in labels.iter_mut().zip(values) {
        if value < threshold {
            *label = SELECTED;
        } else if value == threshold && ties_left > 0 {
            *label = SELECTED;
            ties_left -= 1;
        }
    }
    let [nx, ny, nz] = shape;
    let plane = ny * nz;
    let mut components = 0;
    let (mut largest, mut largest_size) = (0, 0);
    let mut queue = Vec::new();
    for seed in 0..labels.len() {
        if labels[seed] != SELECTED {
            continue;
        }
        components += 1;
        labels[seed] = components;
        queue.clear();
        queue.push(seed as u32);
        let mut head = 0;
        while head < queue.len() {
            let index = queue[head] as usize;
            head += 1;
            let x = index / plane;
            let y = (index / nz) % ny;
            let z = index % nz;
            let mut visit = |neighbour| {
                if labels[neighbour] == SELECTED {
                    labels[neighbour] = components;
                    queue.push(neighbour as u32);
                }
            };
            if x > 0 {
                visit(index - plane);
            }
            if x + 1 < nx {
                visit(index + plane);
            }
            if y > 0 {
                visit(index - nz);
            }
            if y + 1 < ny {
                visit(index + nz);
            }
            if z > 0 {
                visit(index - 1);
            }
            if z + 1 < nz {
                visit(index + 1);
            }
        }
        // Equal-size components retain the first one in C grid order (SciPy).
        if queue.len() > largest_size {
            largest = components;
            largest_size = queue.len();
        }
    }
    Ok((labels, components, largest))
}

pub struct HexPlan {
    node_ids: Vec<i32>,
    origins: Vec<u32>,
    offsets: Vec<(usize, usize)>,
    shape: [usize; 3],
    cell_shape: [usize; 3],
    scale: usize,
    point_count: usize,
    bottom: Vec<i32>,
    top: Vec<i32>,
}

impl HexPlan {
    fn new(solid: &[u8], shape: [usize; 3], quadratic: bool) -> Self {
        let scale = if quadratic { 2 } else { 1 };
        let lattice = shape.map(|n| scale * n + 1);
        let [nx, ny, nz] = shape;
        // Only four parity classes exist in a HEX20 mesh: corners and the
        // three edge directions. Face/body centres are never allocated.
        let even_plane = (ny + 1) * (2 * nz + 1) + ny * (nz + 1);
        let odd_plane = (ny + 1) * (nz + 1);
        let pair = even_plane + odd_plane;
        let key = |p: [usize; 3]| -> (usize, usize) {
            if !quadratic {
                return (0, (p[0] * lattice[1] + p[1]) * lattice[2] + p[2]);
            }
            if p[0] % 2 == 1 {
                (2, (p[1] / 2) * (nz + 1) + p[2] / 2)
            } else if p[1] % 2 == 1 {
                (1, (p[0] / 2) * pair + p[2] / 2)
            } else {
                (0, (p[0] / 2) * pair + (p[1] / 2) * (3 * nz + 2) + p[2])
            }
        };
        let mut offsets: Vec<_> = CORNERS.iter().map(|p| key(p.map(|v| scale * v))).collect();
        if quadratic {
            offsets.extend(
                EDGES
                    .iter()
                    .map(|&(a, b)| key(std::array::from_fn(|d| CORNERS[a][d] + CORNERS[b][d]))),
            );
        }
        let cell_count = solid.iter().filter(|&&v| v != 0).count();
        let mut origins = Vec::with_capacity(cell_count);
        // Empty masks do not allocate a potentially large lattice.
        let mut node_ids = if cell_count == 0 {
            Vec::new()
        } else {
            vec![
                -1;
                if quadratic {
                    nx * pair + even_plane
                } else {
                    lattice.iter().product()
                }
            ]
        };
        for (index, &occupied) in solid.iter().enumerate() {
            if occupied == 0 {
                continue;
            }
            let point = [
                index / (shape[1] * shape[2]),
                (index / shape[2]) % shape[1],
                index % shape[2],
            ];
            let [x, y, z] = point;
            let bases = if quadratic {
                [
                    x * pair + y * (3 * nz + 2) + 2 * z,
                    x * pair + y * (3 * nz + 2) + (2 * nz + 1) + z,
                    x * pair + even_plane + y * (nz + 1) + z,
                ]
            } else {
                [(x * lattice[1] + y) * lattice[2] + z, 0, 0]
            };
            origins.push(index as u32);
            for &(family, offset) in &offsets {
                node_ids[bases[family] + offset] = -2;
            }
        }
        let mut point_count = 0;
        let (mut bottom, mut top) = (Vec::new(), Vec::new());
        if cell_count != 0 {
            let mut key = 0;
            for x in 0..lattice[0] {
                let y_step = if quadratic && x % 2 == 1 { 2 } else { 1 };
                for y in (0..lattice[1]).step_by(y_step) {
                    let z_step = if quadratic && (x % 2 == 1 || y % 2 == 1) {
                        2
                    } else {
                        1
                    };
                    for z in (0..lattice[2]).step_by(z_step) {
                        if node_ids[key] == -2 {
                            node_ids[key] = point_count as i32;
                            point_count += 1;
                            if z == 0 {
                                bottom.push(point_count as i32);
                            }
                            if z + 1 == lattice[2] {
                                top.push(point_count as i32);
                            }
                        }
                        key += 1;
                    }
                }
            }
        }
        Self {
            node_ids,
            origins,
            offsets,
            shape: lattice,
            cell_shape: shape,
            scale,
            point_count,
            bottom,
            top,
        }
    }

    fn write(&self, spacing: f64, points: &mut [f64], cells: &mut [i64]) {
        if self.origins.is_empty() {
            return;
        }
        let step = spacing / self.scale as f64;
        let quadratic = self.scale == 2;
        let mut key = 0;
        for x in 0..self.shape[0] {
            let y_step = if quadratic && x % 2 == 1 { 2 } else { 1 };
            for y in (0..self.shape[1]).step_by(y_step) {
                let z_step = if quadratic && (x % 2 == 1 || y % 2 == 1) {
                    2
                } else {
                    1
                };
                for z in (0..self.shape[2]).step_by(z_step) {
                    let id = self.node_ids[key];
                    key += 1;
                    if id >= 0 {
                        let base = 3 * id as usize;
                        points[base] = x as f64 * step;
                        points[base + 1] = y as f64 * step;
                        points[base + 2] = z as f64 * step;
                    }
                }
            }
        }
        let width = self.offsets.len();
        let [_, ny, nz] = self.cell_shape;
        let even_plane = (ny + 1) * (2 * nz + 1) + ny * (nz + 1);
        let pair = even_plane + (ny + 1) * (nz + 1);
        for (&index, cell) in self.origins.iter().zip(cells.chunks_exact_mut(width)) {
            let index = index as usize;
            let x = index / (ny * nz);
            let y = (index / nz) % ny;
            let z = index % nz;
            let bases = if quadratic {
                [
                    x * pair + y * (3 * nz + 2) + 2 * z,
                    x * pair + y * (3 * nz + 2) + (2 * nz + 1) + z,
                    x * pair + even_plane + y * (nz + 1) + z,
                ]
            } else {
                [(x * self.shape[1] + y) * self.shape[2] + z, 0, 0]
            };
            for (slot, &(family, offset)) in cell.iter_mut().zip(&self.offsets) {
                *slot = self.node_ids[bases[family] + offset] as i64;
            }
        }
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn rustmech_core_abi_version() -> u32 {
    4
}

#[unsafe(no_mangle)]
pub extern "C" fn rustmech_core_last_error() -> *const c_char {
    LAST_ERROR.with(|slot| slot.borrow().as_ptr())
}

/// # Safety
/// Input/output pointers must address non-overlapping buffers of `length`
/// elements; components must address one writable u32.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_select_voxels(
    values: *const f64,
    length: usize,
    nx: usize,
    ny: usize,
    nz: usize,
    count: usize,
    retained: *mut u8,
    removed: *mut u8,
    components: *mut u32,
) -> i32 {
    let result = guarded(|| {
        dimensions(nx, ny, nz, length)?;
        if values.is_null() || retained.is_null() || removed.is_null() || components.is_null() {
            return Err("Null voxel buffer".into());
        }
        let values = unsafe { slice::from_raw_parts(values, length) };
        let (labels, total, largest) = select(values, [nx, ny, nz], count)?;
        let retained = unsafe { slice::from_raw_parts_mut(retained, length) };
        let removed = unsafe { slice::from_raw_parts_mut(removed, length) };
        for ((&label, keep), discard) in labels.iter().zip(retained).zip(removed) {
            *keep = u8::from(label != 0 && label == largest);
            *discard = u8::from(label != 0 && label != largest);
        }
        unsafe {
            *components = total;
        }
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

/// # Safety
/// solid must address length bytes and counts four writable usize values.
/// The returned handle must be released exactly once with rustmech_hex_plan_free.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_hex_plan_new(
    solid: *const u8,
    length: usize,
    nx: usize,
    ny: usize,
    nz: usize,
    quadratic: u8,
    counts: *mut usize,
) -> *mut HexPlan {
    guarded(|| {
        dimensions(nx, ny, nz, length)?;
        if solid.is_null() || counts.is_null() || quadratic > 1 {
            return Err("Invalid hex plan arguments".into());
        }
        let solid = unsafe { slice::from_raw_parts(solid, length) };
        let plan = Box::new(HexPlan::new(solid, [nx, ny, nz], quadratic != 0));
        unsafe {
            *counts = plan.point_count;
            *counts.add(1) = plan.origins.len();
            *counts.add(2) = plan.bottom.len();
            *counts.add(3) = plan.top.len();
        }
        Ok(Box::into_raw(plan))
    })
    .unwrap_or(ptr::null_mut())
}

/// # Safety
/// plan must be a live handle. points and cells must address non-overlapping
/// writable buffers with the supplied element counts and matching scalar types.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_hex_plan_write(
    plan: *const HexPlan,
    spacing: f64,
    points: *mut f64,
    points_length: usize,
    cells: *mut i64,
    cells_length: usize,
) -> i32 {
    let result = guarded(|| {
        if plan.is_null()
            || points.is_null()
            || cells.is_null()
            || !spacing.is_finite()
            || spacing <= 0.0
        {
            return Err("Invalid hex output arguments".into());
        }
        let plan = unsafe { &*plan };
        if points_length != plan.point_count * 3
            || cells_length != plan.origins.len() * plan.offsets.len()
        {
            return Err("Hex output buffer lengths do not match the plan".into());
        }
        plan.write(
            spacing,
            unsafe { slice::from_raw_parts_mut(points, points_length) },
            unsafe { slice::from_raw_parts_mut(cells, cells_length) },
        );
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

/// # Safety
/// plan must be null or a live handle returned by rustmech_hex_plan_new; it must
/// never be used again after this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_hex_plan_free(plan: *mut HexPlan) {
    if !plan.is_null() {
        drop(unsafe { Box::from_raw(plan) });
    }
}

/// # Safety
/// plan is a live handle; bottom and top point to disjoint writable i32
/// buffers whose lengths match the plan's contact counts.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_hex_contacts(
    plan: *const HexPlan,
    bottom: *mut i32,
    bottom_length: usize,
    top: *mut i32,
    top_length: usize,
) -> i32 {
    let result = guarded(|| {
        if plan.is_null() || bottom.is_null() || top.is_null() {
            return Err("Null hex contact buffer".into());
        }
        let plan = unsafe { &*plan };
        if bottom_length != plan.bottom.len() || top_length != plan.top.len() {
            return Err("Hex contact buffer length mismatch".into());
        }
        unsafe { slice::from_raw_parts_mut(bottom, bottom_length) }.copy_from_slice(&plan.bottom);
        unsafe { slice::from_raw_parts_mut(top, top_length) }.copy_from_slice(&plan.top);
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ties_and_component_ties_keep_first_grid_index() {
        let (labels, components, largest) = select(&[0.0, 2.0, 0.0, 2.0], [1, 1, 4], 2).unwrap();
        assert_eq!(labels, [1, 0, 2, 0]);
        assert_eq!((components, largest), (2, 1));
        let (labels, components, _) = select(&[-0.0, 0.0, 0.0, 0.0], [1, 1, 4], 2).unwrap();
        assert_eq!(labels, [1, 1, 0, 0]);
        assert_eq!(components, 1);
    }

    #[test]
    fn diagonal_voxels_do_not_connect_or_wrap() {
        let values = [2.0, 0.0, 0.0, 2.0, 2.0, 2.0, 2.0, 0.0];
        let (_, components, _) = select(&values, [2, 2, 2], 3).unwrap();
        assert_eq!(components, 3);
    }

    #[test]
    fn shared_quadratic_edge_nodes_and_no_face_centres() {
        let plan = HexPlan::new(&[1, 1], [2, 1, 1], true);
        assert_eq!(plan.point_count, 32);
        let mut points = vec![0.0; 3 * plan.point_count];
        let mut cells = vec![0; 40];
        plan.write(0.5, &mut points, &mut cells);
        assert!(points.iter().all(|&v| (0.0..=1.0).contains(&v)));
        let shared = cells[..20]
            .iter()
            .filter(|id| cells[20..].contains(id))
            .count();
        assert_eq!(shared, 8);
    }

    #[test]
    fn empty_plan_and_invalid_values() {
        let plan = HexPlan::new(&[0; 8], [2, 2, 2], false);
        assert_eq!(plan.point_count, 0);
        assert!(plan.node_ids.is_empty());
        plan.write(0.1, &mut [], &mut []);
        assert!(select(&[f64::NAN], [1, 1, 1], 1).is_err());
        assert!(select(&[1.0], [1, 1, 1], 2).is_err());
        assert!(dimensions(usize::MAX, 1, 1, 1).is_err());
    }

    #[test]
    fn quadratic_lattice_contains_only_corners_and_edges() {
        let [nx, ny, nz] = [3, 4, 5];
        let plan = HexPlan::new(&vec![1; nx * ny * nz], [nx, ny, nz], true);
        let expected = (nx + 1) * (ny + 1) * (nz + 1)
            + nx * (ny + 1) * (nz + 1)
            + (nx + 1) * ny * (nz + 1)
            + (nx + 1) * (ny + 1) * nz;
        assert_eq!(plan.node_ids.len(), expected);
        assert_eq!(plan.point_count, expected);
        assert_eq!(
            plan.bottom.len(),
            (nx + 1) * (ny + 1) + nx * (ny + 1) + (nx + 1) * ny
        );
        assert_eq!(plan.bottom.len(), plan.top.len());
    }
}
