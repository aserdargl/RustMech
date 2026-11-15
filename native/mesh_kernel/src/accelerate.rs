//! Fused geometry evaluation, result indexing, and buffered ANSYS serialization.
use crate::{guarded, tpms_generated};
use std::collections::HashMap;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::{slice, thread};

fn fill(output: &mut [f64], function: impl Fn(usize) -> f64 + Sync) {
    let available = thread::available_parallelism()
        .map_or(1, usize::from)
        .min(8);
    let threads = std::env::var("RUSTMECH_THREADS")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(available)
        .clamp(1, 64);
    if output.len() < 131_072 || threads == 1 {
        for (index, value) in output.iter_mut().enumerate() {
            *value = function(index);
        }
    } else {
        let chunk_size = output.len().div_ceil(threads);
        thread::scope(|scope| {
            for (part, chunk) in output.chunks_mut(chunk_size).enumerate() {
                let function = &function;
                scope.spawn(move || {
                    for (offset, value) in chunk.iter_mut().enumerate() {
                        *value = function(part * chunk_size + offset);
                    }
                });
            }
        });
    }
}

fn shape(nx: usize, ny: usize, nz: usize, length: usize) -> Result<(), String> {
    if [nx, ny, nz].iter().any(|&n| n == 0 || n > 4096)
        || nx.checked_mul(ny).and_then(|v| v.checked_mul(nz)) != Some(length)
        || length > 12_000_000
    {
        return Err("Invalid geometry sampling dimensions".into());
    }
    Ok(())
}

fn tpms_rows<const KIND: u32>(tables: &[f64], nx: usize, ny: usize, nz: usize, output: &mut [f64]) {
    let evaluate = |row: usize, values: &mut [f64]| {
        let x: [f64; 4] = tables[4 * (row / ny)..4 * (row / ny) + 4]
            .try_into()
            .unwrap();
        let yi = nx + row % ny;
        let y: [f64; 4] = tables[4 * yi..4 * yi + 4].try_into().unwrap();
        for (value, z) in values
            .iter_mut()
            .zip(tables[4 * (nx + ny)..].chunks_exact(4))
        {
            *value = tpms_generated::evaluate::<KIND>([x, y, z.try_into().unwrap()]);
        }
    };
    let available = thread::available_parallelism()
        .map_or(1, usize::from)
        .min(8);
    let threads = std::env::var("RUSTMECH_THREADS")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(available)
        .clamp(1, 64);
    if output.len() < 131_072 || threads == 1 {
        for (row, values) in output.chunks_exact_mut(nz).enumerate() {
            evaluate(row, values);
        }
    } else {
        let rows_per_part = (nx * ny).div_ceil(threads);
        thread::scope(|scope| {
            for (part, rows) in output.chunks_mut(rows_per_part * nz).enumerate() {
                let evaluate = &evaluate;
                scope.spawn(move || {
                    for (row, values) in rows.chunks_exact_mut(nz).enumerate() {
                        evaluate(part * rows_per_part + row, values);
                    }
                });
            }
        });
    }
}

/// # Safety
/// tables contains 4*(nx+ny+nz) doubles; output contains length writable doubles.
/// All buffers must be valid, aligned, and non-overlapping for this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_tpms(
    kind: u32,
    tables: *const f64,
    nx: usize,
    ny: usize,
    nz: usize,
    output: *mut f64,
    length: usize,
) -> i32 {
    let result = guarded(|| {
        shape(nx, ny, nz, length)?;
        if kind >= 19 || tables.is_null() || output.is_null() {
            return Err("Invalid TPMS arguments".into());
        }
        let tables = unsafe { slice::from_raw_parts(tables, 4 * (nx + ny + nz)) };
        let output = unsafe { slice::from_raw_parts_mut(output, length) };
        // Dispatch once, then specialize the formula outside the inner voxel
        // loop. X/Y tables are reused per row instead of dividing per sample.
        macro_rules! run {
            ($kind:expr) => {
                tpms_rows::<$kind>(tables, nx, ny, nz, output)
            };
        }
        match kind {
            0 => run!(0),
            1 => run!(1),
            2 => run!(2),
            3 => run!(3),
            4 => run!(4),
            5 => run!(5),
            6 => run!(6),
            7 => run!(7),
            8 => run!(8),
            9 => run!(9),
            10 => run!(10),
            11 => run!(11),
            12 => run!(12),
            13 => run!(13),
            14 => run!(14),
            15 => run!(15),
            16 => run!(16),
            17 => run!(17),
            18 => run!(18),
            _ => unreachable!(),
        }
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

/// # Safety
/// axes contains nx+ny+nz wrapped coordinates, edges contains edge_count*6
/// doubles, and output contains length writable doubles; buffers cannot overlap.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_graph_field(
    axes: *const f64,
    nx: usize,
    ny: usize,
    nz: usize,
    edges: *const f64,
    edge_count: usize,
    output: *mut f64,
    length: usize,
) -> i32 {
    let result = guarded(|| {
        shape(nx, ny, nz, length)?;
        if axes.is_null()
            || edges.is_null()
            || output.is_null()
            || edge_count == 0
            || edge_count > 10000
        {
            return Err("Invalid lattice graph arguments".into());
        }
        let axes = unsafe { slice::from_raw_parts(axes, nx + ny + nz) };
        let edges = unsafe { slice::from_raw_parts(edges, edge_count * 6) };
        if axes.iter().chain(edges).any(|v| !v.is_finite()) {
            return Err("Lattice coordinates must be finite".into());
        }
        let output = unsafe { slice::from_raw_parts_mut(output, length) };
        fill(output, |index| {
            let point = [
                axes[index / (ny * nz)],
                axes[nx + (index / nz) % ny],
                axes[nx + ny + index % nz],
            ];
            let mut minimum = f64::INFINITY;
            for edge in edges.chunks_exact(6) {
                let shifted: [f64; 3] = std::array::from_fn(|d| {
                    (point[d] - (edge[d] + edge[d + 3]) * 0.5).round_ties_even()
                });
                let start: [f64; 3] = std::array::from_fn(|d| edge[d] + shifted[d]);
                let direction: [f64; 3] =
                    std::array::from_fn(|d| (edge[d + 3] + shifted[d]) - start[d]);
                let length_sq = direction.iter().fold(0.0, |sum, &v| sum + v * v);
                let projection =
                    (0..3).fold(0.0, |sum, d| sum + (point[d] - start[d]) * direction[d]);
                let param = (projection / length_sq).clamp(0.0, 1.0);
                let distance = (0..3).fold(0.0, |sum, d| {
                    let delta = point[d] - start[d] - param * direction[d];
                    sum + delta * delta
                });
                minimum = minimum.min(distance);
            }
            minimum.sqrt() * (2.0 * std::f64::consts::PI)
        });
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

/// # Safety
/// indices and weights contain nx+ny+nz entries, reference contains resolution^3
/// doubles, output contains length writable doubles. Buffers must not overlap.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_interpolate(
    indices: *const i64,
    weights: *const f64,
    nx: usize,
    ny: usize,
    nz: usize,
    reference: *const f64,
    resolution: usize,
    output: *mut f64,
    length: usize,
) -> i32 {
    let result = guarded(|| {
        shape(nx, ny, nz, length)?;
        if indices.is_null()
            || weights.is_null()
            || reference.is_null()
            || output.is_null()
            || !(2..=512).contains(&resolution)
        {
            return Err("Invalid porous interpolation arguments".into());
        }
        let indices = unsafe { slice::from_raw_parts(indices, nx + ny + nz) };
        let weights = unsafe { slice::from_raw_parts(weights, nx + ny + nz) };
        if indices
            .iter()
            .any(|&i| i < 0 || i as usize + 1 >= resolution)
        {
            return Err("Interpolation index out of range".into());
        }
        let reference = unsafe { slice::from_raw_parts(reference, resolution.pow(3)) };
        let output = unsafe { slice::from_raw_parts_mut(output, length) };
        fill(output, |index| {
            let positions = [
                index / (ny * nz),
                nx + (index / nz) % ny,
                nx + ny + index % nz,
            ];
            let base = positions.map(|p| indices[p] as usize);
            let t = positions.map(|p| weights[p]);
            let mut value = 0.0;
            for corner in 0..8 {
                let offset = [(corner >> 2) & 1, (corner >> 1) & 1, corner & 1];
                let weight = (0..3).fold(1.0, |v, d| {
                    v * if offset[d] == 0 { 1.0 - t[d] } else { t[d] }
                });
                let key = ((base[0] + offset[0]) * resolution + base[1] + offset[1]) * resolution
                    + base[2]
                    + offset[2];
                value += reference[key] * weight;
            }
            value
        });
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

fn indices(source: &[i64], targets: &[i64]) -> Result<Vec<i64>, String> {
    if source.windows(2).all(|pair| pair[0] < pair[1]) {
        let consecutive = source
            .windows(2)
            .all(|pair| pair[0].checked_add(1) == Some(pair[1]));
        return targets
            .iter()
            .map(|target| {
                let position = if consecutive && !source.is_empty() {
                    target
                        .checked_sub(source[0])
                        .filter(|&i| i >= 0 && (i as usize) < source.len())
                        .map(|i| i as usize)
                } else {
                    source.binary_search(target).ok()
                };
                position
                    .map(|p| p as i64)
                    .ok_or_else(|| "Result file is missing FE node IDs.".into())
            })
            .collect();
    }
    let mut lookup = HashMap::with_capacity(source.len());
    for (index, &id) in source.iter().enumerate() {
        if lookup.insert(id, index).is_some() {
            return Err("Invalid or duplicate result node IDs.".into());
        }
    }
    targets
        .iter()
        .map(|id| {
            lookup
                .get(id)
                .map(|&i| i as i64)
                .ok_or_else(|| "Result file is missing FE node IDs.".into())
        })
        .collect()
}

/// # Safety
/// source contains source_length i64s; targets and output contain target_length
/// i64s; input and output buffers must not overlap.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_map_indices(
    source: *const i64,
    source_length: usize,
    targets: *const i64,
    target_length: usize,
    output: *mut i64,
) -> i32 {
    let result = guarded(|| {
        if source.is_null() || targets.is_null() || output.is_null() {
            return Err("Null result ID buffer".into());
        }
        let source = unsafe { slice::from_raw_parts(source, source_length) };
        let targets = unsafe { slice::from_raw_parts(targets, target_length) };
        let output = unsafe { slice::from_raw_parts_mut(output, target_length) };
        output.copy_from_slice(&indices(source, targets)?);
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

/// # Safety
/// points contains point_count*3 doubles, connectivity contains count*width
/// i64s, determinants and ratios each contain count writable doubles.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_tet_metrics(
    points: *const f64,
    point_count: usize,
    connectivity: *const i64,
    count: usize,
    width: usize,
    determinants: *mut f64,
    ratios: *mut f64,
) -> i32 {
    let result = guarded(|| {
        if points.is_null()
            || connectivity.is_null()
            || determinants.is_null()
            || ratios.is_null()
            || ![4, 10].contains(&width)
            || count > 2_000_000
            || point_count > 30_000_000
        {
            return Err("Invalid tetrahedral metric buffers".into());
        }
        let points = unsafe { slice::from_raw_parts(points, point_count * 3) };
        let cells = unsafe { slice::from_raw_parts(connectivity, count * width) };
        if cells.iter().any(|&v| v < 0 || v as usize >= point_count) {
            return Err("Invalid tetrahedral node index".into());
        }
        let determinants = unsafe { slice::from_raw_parts_mut(determinants, count) };
        let ratios = unsafe { slice::from_raw_parts_mut(ratios, count) };
        for ((cell, determinant), ratio) in cells.chunks_exact(width).zip(determinants).zip(ratios)
        {
            let p: [[f64; 3]; 4] =
                std::array::from_fn(|i| std::array::from_fn(|d| points[cell[i] as usize * 3 + d]));
            let a: [f64; 3] = std::array::from_fn(|d| p[1][d] - p[0][d]);
            let b: [f64; 3] = std::array::from_fn(|d| p[2][d] - p[0][d]);
            let c: [f64; 3] = std::array::from_fn(|d| p[3][d] - p[0][d]);
            *determinant = a[0] * (b[1] * c[2] - b[2] * c[1])
                + a[1] * (b[2] * c[0] - b[0] * c[2])
                + a[2] * (b[0] * c[1] - b[1] * c[0]);
            let (mut minimum, mut maximum) = (f64::INFINITY, 0.0_f64);
            for i in 0..4 {
                for j in i + 1..4 {
                    let distance = (0..3)
                        .fold(0.0, |sum, d| {
                            let delta = p[i][d] - p[j][d];
                            sum + delta * delta
                        })
                        .sqrt();
                    minimum = minimum.min(distance);
                    maximum = maximum.max(distance);
                }
            }
            *ratio = maximum / minimum;
        }
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

/// # Safety
/// tensors contains count*6 doubles, output contains count writable doubles.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_von_mises(
    tensors: *const f64,
    count: usize,
    output: *mut f64,
) -> i32 {
    let result = guarded(|| {
        if tensors.is_null() || output.is_null() || count > 30_000_000 {
            return Err("Invalid stress tensor buffers".into());
        }
        let tensors = unsafe { slice::from_raw_parts(tensors, count * 6) };
        let output = unsafe { slice::from_raw_parts_mut(output, count) };
        fill(output, |index| {
            let t = &tensors[index * 6..index * 6 + 6];
            let a = t[0] - t[1];
            let b = t[1] - t[2];
            let c = t[2] - t[0];
            (0.5 * (a * a + b * b + c * c) + 3.0 * (t[3] * t[3] + t[4] * t[4] + t[5] * t[5])).sqrt()
        });
        Ok(())
    });
    if result.is_some() { 0 } else { -1 }
}

struct General16(f64);

struct StackText {
    bytes: [u8; 64],
    length: usize,
}

impl std::fmt::Write for StackText {
    fn write_str(&mut self, value: &str) -> std::fmt::Result {
        if self.length + value.len() > self.bytes.len() {
            return Err(std::fmt::Error);
        }
        self.bytes[self.length..self.length + value.len()].copy_from_slice(value.as_bytes());
        self.length += value.len();
        Ok(())
    }
}

impl std::fmt::Display for General16 {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        use std::fmt::Write;
        // Keep Python's .16g rounding, but format entirely on the stack.
        let mut buffer = StackText {
            bytes: [0; 64],
            length: 0,
        };
        write!(&mut buffer, "{:.15e}", self.0)?;
        let scientific =
            std::str::from_utf8(&buffer.bytes[..buffer.length]).map_err(|_| std::fmt::Error)?;
        let (mantissa, exponent) = scientific.split_once('e').ok_or(std::fmt::Error)?;
        let exponent: i32 = exponent.parse().map_err(|_| std::fmt::Error)?;
        let negative = mantissa.starts_with('-');
        let mut digits = [b'0'; 16];
        let mut length = 0;
        for byte in mantissa.bytes().filter(|&c| c != b'.' && c != b'-') {
            digits[length] = byte;
            length += 1;
        }
        while length > 1 && digits[length - 1] == b'0' {
            length -= 1;
        }
        let digits = std::str::from_utf8(&digits[..length]).map_err(|_| std::fmt::Error)?;
        if negative {
            formatter.write_str("-")?;
        }
        if !(-4..16).contains(&exponent) {
            formatter.write_str(&digits[..1])?;
            if digits.len() > 1 {
                formatter.write_str(".")?;
                formatter.write_str(&digits[1..])?;
            }
            return write!(formatter, "e{exponent:+03}");
        }
        const ZEROES: &str = "0000000000000000";
        let decimal = exponent + 1;
        if decimal <= 0 {
            formatter.write_str("0.")?;
            formatter.write_str(&ZEROES[..(-decimal) as usize])?;
            formatter.write_str(digits)
        } else if decimal as usize >= digits.len() {
            formatter.write_str(digits)?;
            formatter.write_str(&ZEROES[..decimal as usize - digits.len()])
        } else {
            formatter.write_str(&digits[..decimal as usize])?;
            formatter.write_str(".")?;
            formatter.write_str(&digits[decimal as usize..])
        }
    }
}

fn general16(value: f64) -> General16 {
    General16(value)
}

/// # Safety
/// path contains path_length UTF-8 bytes; points contains point_count*3 doubles;
/// node_ids contains point_count i64s; connectivity contains cell_count*width
/// i64s; element_ids contains cell_count i64s. Buffers stay live for this call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn rustmech_write_apdl(
    path: *const u8,
    path_length: usize,
    points: *const f64,
    node_ids: *const i64,
    point_count: usize,
    connectivity: *const i64,
    element_ids: *const i64,
    cell_count: usize,
    width: usize,
    element_type: i32,
) -> i32 {
    let result = guarded(|| {
        let valid = matches!(
            (element_type, width),
            (185, 8) | (186, 20) | (285, 4) | (187, 10)
        );
        if !valid
            || path.is_null()
            || points.is_null()
            || node_ids.is_null()
            || connectivity.is_null()
            || element_ids.is_null()
            || point_count > 30_000_000
            || cell_count > 2_000_000
        {
            return Err("Invalid APDL mesh buffers".into());
        }
        let path = std::str::from_utf8(unsafe { slice::from_raw_parts(path, path_length) })
            .map_err(|e| e.to_string())?;
        let points = unsafe { slice::from_raw_parts(points, point_count * 3) };
        let ids = unsafe { slice::from_raw_parts(node_ids, point_count) };
        let cells = unsafe { slice::from_raw_parts(connectivity, cell_count * width) };
        let elements = unsafe { slice::from_raw_parts(element_ids, cell_count) };
        if points.iter().any(|v| !v.is_finite())
            || cells.iter().any(|&v| v < 0 || v as usize >= point_count)
        {
            return Err("Non-finite mesh point or invalid APDL connectivity".into());
        }
        let write_file = || -> std::io::Result<()> {
            let mut writer = BufWriter::with_capacity(1024 * 1024, File::create(path)?);
            writeln!(writer, "/PREP7\nET,1,{element_type}")?;
            if element_type == 185 {
                writeln!(writer, "KEYOPT,1,2,0")?;
            }
            if element_type == 186 {
                writeln!(writer, "KEYOPT,1,2,1")?;
            }
            writeln!(writer, "TYPE,1\nMAT,1")?;
            for (&id, point) in ids.iter().zip(points.chunks_exact(3)) {
                writeln!(
                    writer,
                    "N,{id},{},{},{}",
                    general16(point[0]),
                    general16(point[1]),
                    general16(point[2])
                )?;
            }
            for (&element, cell) in elements.iter().zip(cells.chunks_exact(width)) {
                for (part, chunk) in cell.chunks(8).enumerate() {
                    if part == 0 {
                        write!(writer, "EN,{element}")?;
                    } else {
                        write!(writer, "EMORE")?;
                    }
                    for &index in chunk {
                        write!(writer, ",{}", ids[index as usize])?;
                    }
                    writeln!(writer)?;
                }
            }
            writeln!(writer, "ALLSEL,ALL\nFINISH")?;
            writer.flush()
        };
        write_file().map_err(|error| format!("Cannot write APDL mesh: {error}"))
    });
    if result.is_some() { 0 } else { -1 }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn result_indices_cover_order_duplicates_missing_and_overflow() {
        assert_eq!(indices(&[4, 2, 9], &[9, 4, 9]).unwrap(), [2, 0, 2]);
        assert!(indices(&[2, 2], &[]).is_err());
        assert!(indices(&[1, 2, 3], &[4]).is_err());
        assert_eq!(indices(&[i64::MIN, i64::MAX], &[i64::MAX]).unwrap(), [1]);
    }
    #[test]
    fn python_general_float_format() {
        assert_eq!(general16(0.0).to_string(), "0");
        assert_eq!(general16(-0.0).to_string(), "-0");
        assert_eq!(general16(1e-5).to_string(), "1e-05");
        assert_eq!(general16(1e16).to_string(), "1e+16");
        assert_eq!(
            general16(1.2345678901234567).to_string(),
            "1.234567890123457"
        );
    }
}
