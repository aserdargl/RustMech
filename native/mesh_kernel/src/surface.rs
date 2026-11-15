//! Exact point merging and largest triangle component without VTK graph passes.
use crate::guarded;
use std::collections::HashMap;
use std::{ptr, slice};

pub struct SurfacePlan {
    order: Vec<i32>,
    triangles: Vec<i64>,
}

fn find(parents: &mut [u32], mut node: usize) -> usize {
    while parents[node] as usize != node {
        parents[node] = parents[parents[node] as usize];
        node = parents[node] as usize;
    }
    node
}

fn join(parents: &mut [u32], sizes: &mut [u32], a: usize, b: usize) {
    let (mut a, mut b) = (find(parents, a), find(parents, b));
    if a == b {
        return;
    }
    if sizes[a] < sizes[b] {
        std::mem::swap(&mut a, &mut b);
    }
    parents[b] = a as u32;
    sizes[a] += sizes[b];
}

impl SurfacePlan {
    fn new(points: &[f64], triangles: &[i32]) -> Result<Self, String> {
        let point_count = points.len() / 3;
        if points.iter().any(|v| !v.is_finite())
            || triangles
                .iter()
                .any(|&v| v < 0 || v as usize >= point_count)
        {
            return Err("Invalid triangle surface coordinates or indices".into());
        }
        let mut canonical = vec![u32::MAX; point_count];
        let mut lookup = HashMap::with_capacity(point_count);
        let (mut order, mut parents, mut sizes) = (Vec::new(), Vec::new(), Vec::new());
        for triangle in triangles.chunks_exact(3) {
            let mut cell = [0; 3];
            for (slot, &raw) in cell.iter_mut().zip(triangle) {
                let raw = raw as usize;
                if canonical[raw] == u32::MAX {
                    let key: [u64; 3] = std::array::from_fn(|d| {
                        let value = points[3 * raw + d];
                        if value == 0.0 { 0 } else { value.to_bits() }
                    });
                    let next = order.len() as u32;
                    let id = *lookup.entry(key).or_insert_with(|| {
                        order.push(raw as i32);
                        parents.push(next);
                        sizes.push(1);
                        next
                    });
                    canonical[raw] = id;
                }
                *slot = canonical[raw] as usize;
            }
            if cell[0] == cell[1] || cell[1] == cell[2] || cell[2] == cell[0] {
                return Err("Degenerate triangles require VTK cleanup".into());
            }
            join(&mut parents, &mut sizes, cell[0], cell[1]);
            join(&mut parents, &mut sizes, cell[0], cell[2]);
        }
        drop(lookup);
        let mut counts = vec![0_usize; order.len()];
        for triangle in triangles.chunks_exact(3) {
            counts[find(&mut parents, canonical[triangle[0] as usize] as usize)] += 1;
        }
        let (mut largest, mut largest_size) = (0, 0);
        for triangle in triangles.chunks_exact(3) {
            let root = find(&mut parents, canonical[triangle[0] as usize] as usize);
            if counts[root] > largest_size {
                largest = root;
                largest_size = counts[root];
            }
        }
        let mut compact = vec![-1_i64; order.len()];
        let mut final_order = Vec::new();
        let mut final_triangles = Vec::with_capacity(largest_size * 3);
        for triangle in triangles.chunks_exact(3) {
            if find(&mut parents, canonical[triangle[0] as usize] as usize) != largest {
                continue;
            }
            for &raw in triangle {
                let canonical = canonical[raw as usize] as usize;
                if compact[canonical] < 0 {
                    compact[canonical] = final_order.len() as i64;
                    final_order.push(order[canonical]);
                }
                final_triangles.push(compact[canonical]);
            }
        }
        Ok(Self {
            order: final_order,
            triangles: final_triangles,
        })
    }
}

/// # Safety
/// points contains point_count*3 doubles, triangles contains triangle_count*3
/// i32s, counts contains two writable usize values. Release the returned handle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_surface_plan_new(
    points: *const f64,
    point_count: usize,
    triangles: *const i32,
    triangle_count: usize,
    counts: *mut usize,
) -> *mut SurfacePlan {
    guarded(|| {
        if points.is_null()
            || triangles.is_null()
            || counts.is_null()
            || point_count == 0
            || point_count > 50_000_000
            || triangle_count == 0
            || triangle_count > 50_000_000
        {
            return Err("Invalid triangle surface buffers".into());
        }
        let points = unsafe { slice::from_raw_parts(points, point_count * 3) };
        let triangles = unsafe { slice::from_raw_parts(triangles, triangle_count * 3) };
        let plan = Box::new(SurfacePlan::new(points, triangles)?);
        unsafe {
            *counts = plan.order.len();
            *counts.add(1) = plan.triangles.len() / 3;
        }
        Ok(Box::into_raw(plan))
    })
    .unwrap_or(ptr::null_mut())
}

/// # Safety
/// plan is a live handle, order and triangles are disjoint writable buffers
/// matching the plan's point count and three times its triangle count.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_surface_plan_write(
    plan: *const SurfacePlan,
    order: *mut i32,
    order_length: usize,
    triangles: *mut i64,
    triangles_length: usize,
) -> i32 {
    let result = guarded(|| {
        if plan.is_null() || order.is_null() || triangles.is_null() {
            return Err("Null triangle surface output".into());
        }
        let plan = unsafe { &*plan };
        if order_length != plan.order.len() || triangles_length != plan.triangles.len() {
            return Err("Triangle surface buffer length mismatch".into());
        }
        unsafe { slice::from_raw_parts_mut(order, order_length) }.copy_from_slice(&plan.order);
        unsafe { slice::from_raw_parts_mut(triangles, triangles_length) }
            .copy_from_slice(&plan.triangles);
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

/// # Safety
/// plan is null or a live handle returned by rustmech_surface_plan_new. Do not
/// access it after this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_surface_plan_free(plan: *mut SurfacePlan) {
    if !plan.is_null() {
        drop(unsafe { Box::from_raw(plan) });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn merge_duplicate_points_and_retain_first_component_on_tie() {
        let points = [
            0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0., 0., 1., 1., 0., 5., 0., 0., 6., 0., 0., 5.,
            1., 0.,
        ];
        let plan = SurfacePlan::new(&points, &[0, 1, 2, 3, 2, 4, 5, 6, 7]).unwrap();
        assert_eq!(plan.order, [0, 1, 2, 4]);
        assert_eq!(plan.triangles, [0, 1, 2, 0, 2, 3]);
    }
}
