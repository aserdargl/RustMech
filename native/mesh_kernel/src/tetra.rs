//! Straight-sided TET10 assembly. Each undirected edge owns one shared midpoint.
use crate::guarded;
use std::collections::HashMap;
use std::{ptr, slice};

const EDGES: [(usize, usize); 6] = [(0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3)];

/// # Safety
/// points has point_count*3 doubles, faces has count*3 i64s, output has count*3
/// writable doubles, all disjoint. Matches the STL writer's f64 arithmetic.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_triangle_normals(
    points: *const f64,
    point_count: usize,
    faces: *const i64,
    count: usize,
    output: *mut f64,
) -> i32 {
    let result = guarded(|| {
        if points.is_null()
            || faces.is_null()
            || output.is_null()
            || point_count == 0
            || point_count > 50_000_000
            || count > 50_000_000
        {
            return Err("Invalid triangle normal buffers".into());
        }
        let points = unsafe { slice::from_raw_parts(points, point_count * 3) };
        let faces = unsafe { slice::from_raw_parts(faces, count * 3) };
        let output = unsafe { slice::from_raw_parts_mut(output, count * 3) };
        if points.iter().any(|v| !v.is_finite())
            || faces.iter().any(|&v| v < 0 || v as usize >= point_count)
        {
            return Err("Invalid triangle coordinates or indices".into());
        }
        for (face, normal) in faces.chunks_exact(3).zip(output.chunks_exact_mut(3)) {
            let [p, q, r] = [
                face[0] as usize * 3,
                face[1] as usize * 3,
                face[2] as usize * 3,
            ];
            let a: [f64; 3] = std::array::from_fn(|d| points[r + d] - points[q + d]);
            let b: [f64; 3] = std::array::from_fn(|d| points[p + d] - points[q + d]);
            normal[0] = a[1] * b[2] - a[2] * b[1];
            normal[1] = a[2] * b[0] - a[0] * b[2];
            normal[2] = a[0] * b[1] - a[1] * b[0];
            let length =
                (normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]).sqrt();
            if length > 0. {
                for value in normal {
                    *value /= length;
                }
            }
        }
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

pub struct TetPlan {
    points: usize,
    edges: Vec<(usize, usize)>,
    cells: Vec<i64>,
}

impl TetPlan {
    fn new(corners: &[i64], points: usize) -> Result<Self, String> {
        if points == 0 || points > u32::MAX as usize || corners.len() % 4 != 0 {
            return Err("Invalid linear tetrahedron dimensions".into());
        }
        let mut edges = Vec::new();
        let mut cells = Vec::with_capacity(corners.len() / 4 * 10);
        let mut lookup = HashMap::with_capacity(corners.len() / 2);
        for tet in corners.chunks_exact(4) {
            if tet.iter().any(|&v| v < 0 || v as usize >= points)
                || EDGES.iter().any(|&(a, b)| tet[a] == tet[b])
            {
                return Err("Invalid tetrahedron corner indices".into());
            }
            cells.extend_from_slice(tet);
            for (a, b) in EDGES {
                let (a, b) = (tet[a].min(tet[b]) as usize, tet[a].max(tet[b]) as usize);
                let key = ((a as u64) << 32) | b as u64;
                let midpoint = *lookup.entry(key).or_insert_with(|| {
                    let index = (points + edges.len()) as i64;
                    edges.push((a, b));
                    index
                });
                cells.push(midpoint);
            }
        }
        Ok(Self {
            points,
            edges,
            cells,
        })
    }
}

/// # Safety
/// corners contains count*4 i64s, counts contains two writable usize values.
/// Free the resulting handle with rustmech_tet_plan_free.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_tet_plan_new(
    corners: *const i64,
    count: usize,
    point_count: usize,
    counts: *mut usize,
) -> *mut TetPlan {
    guarded(|| {
        if corners.is_null() || counts.is_null() || count == 0 || count > 1_100_000 {
            return Err("Invalid tetrahedron input buffer".into());
        }
        let plan = Box::new(TetPlan::new(
            unsafe { slice::from_raw_parts(corners, count * 4) },
            point_count,
        )?);
        unsafe {
            *counts = plan.points + plan.edges.len();
            *counts.add(1) = count;
        }
        Ok(Box::into_raw(plan))
    })
    .unwrap_or(ptr::null_mut())
}

/// # Safety
/// plan is live. points contains plan.points*3 doubles, output has length*3
/// writable doubles and cells has cell_length writable i64s, all disjoint.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_tet_plan_write(
    plan: *const TetPlan,
    points: *const f64,
    output: *mut f64,
    length: usize,
    cells: *mut i64,
    cell_length: usize,
) -> i32 {
    let result = guarded(|| {
        if plan.is_null() || points.is_null() || output.is_null() || cells.is_null() {
            return Err("Null tetrahedron output buffer".into());
        }
        let plan = unsafe { &*plan };
        if length != plan.points + plan.edges.len() || cell_length != plan.cells.len() {
            return Err("Tetrahedron output length mismatch".into());
        }
        let points = unsafe { slice::from_raw_parts(points, plan.points * 3) };
        if points.iter().any(|v| !v.is_finite()) {
            return Err("Tetrahedron coordinates must be finite".into());
        }
        let output = unsafe { slice::from_raw_parts_mut(output, length * 3) };
        output[..points.len()].copy_from_slice(points);
        for (edge, &(a, b)) in plan.edges.iter().enumerate() {
            for d in 0..3 {
                output[3 * (plan.points + edge) + d] = (points[3 * a + d] + points[3 * b + d]) / 2.;
            }
        }
        unsafe { slice::from_raw_parts_mut(cells, cell_length) }.copy_from_slice(&plan.cells);
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

/// # Safety
/// plan is null or a live handle returned by rustmech_tet_plan_new.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_tet_plan_free(plan: *mut TetPlan) {
    if !plan.is_null() {
        drop(unsafe { Box::from_raw(plan) });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn neighbours_share_midpoints_in_both_edge_directions() {
        let plan = TetPlan::new(&[0, 1, 2, 3, 2, 1, 0, 4], 5).unwrap();
        assert_eq!(plan.edges.len(), 9);
        assert_eq!(plan.cells[4], plan.cells[15]);
        assert_eq!(plan.cells[5], plan.cells[14]);
        assert_eq!(plan.cells[6], plan.cells[16]);
        assert!(TetPlan::new(&[0, 1, 2, 2], 5).is_err());
        assert!(TetPlan::new(&[0, 1, 2, 5], 5).is_err());
        assert!(TetPlan::new(&[0, 1, 2, -1], 5).is_err());
    }
}
