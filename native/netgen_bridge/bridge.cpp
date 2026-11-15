// The only C++ glue: construct Netgen's registered STLGeometry from NumPy.
// Netgen owns topology/meshing; no private object layout or pointer casts.
#include <mystdlib.h>
#include <myadt.hpp>
#include <linalg.hpp>
#include <gprim.hpp>
#include <meshing.hpp>
#include <stlgeom.hpp>
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>

namespace py = pybind11;

// This value-type constructor is declared in the public header but is not
// exported by the Windows wheel. Keep its plain member initialization local.
netgen::STLReadTriangle::STLReadTriangle(const netgen::Point<3>* vertices,
                                       const netgen::Vec<3>& n) : normal(n) {
    for (int j = 0; j < 3; ++j) pts[j] = vertices[j];
}

PYBIND11_MODULE(_rustmech_netgen, module) {
    module.def("restrict_h", [](netgen::MeshingParameters& parameters,
                                py::array_t<double, py::array::c_style> points,
                                double maxh) {
        if (points.ndim() != 2 || points.shape(1) != 3 || !std::isfinite(maxh) || maxh <= 0)
            throw py::value_error("Invalid refinement points or size");
        auto p = points.unchecked<2>();
        py::gil_scoped_release release;
        for (py::ssize_t i = 0; i < points.shape(0); ++i) {
            if (!std::isfinite(p(i, 0)) || !std::isfinite(p(i, 1)) || !std::isfinite(p(i, 2)))
                throw std::invalid_argument("Refinement coordinates must be finite");
            parameters.meshsize_points.Append({netgen::Point<3>(p(i, 0), p(i, 1), p(i, 2)), maxh, 1});
        }
    });
    module.def("geometry_from_arrays", [](
        py::array_t<double, py::array::c_style> points,
        py::array_t<int64_t, py::array::c_style> triangles,
        py::array_t<double, py::array::c_style> normals) {
        if (points.ndim() != 2 || points.shape(0) == 0 || points.shape(1) != 3 ||
            triangles.ndim() != 2 || triangles.shape(1) != 3 ||
            triangles.shape(0) == 0 ||
            normals.ndim() != 2 || normals.shape(1) != 3 ||
            normals.shape(0) != triangles.shape(0) ||
            triangles.shape(0) > INT_MAX)
            throw py::value_error("Invalid Netgen boundary arrays");
        auto p = points.unchecked<2>();
        auto t = triangles.unchecked<2>();
        auto n = normals.unchecked<2>();
        py::gil_scoped_release release;
        netgen::Array<netgen::STLReadTriangle> input(triangles.shape(0));
        for (py::ssize_t i = 0; i < triangles.shape(0); ++i) {
            netgen::Point<3> vertices[3];
            for (int j = 0; j < 3; ++j) {
                auto index = t(i, j);
                if (index < 0 || index >= points.shape(0))
                    throw std::invalid_argument("Boundary index out of range");
                if (!std::isfinite(p(index, 0)) || !std::isfinite(p(index, 1)) || !std::isfinite(p(index, 2)))
                    throw std::invalid_argument("Boundary coordinates must be finite");
                vertices[j] = netgen::Point<3>(p(index, 0), p(index, 1), p(index, 2));
            }
            if (!std::isfinite(n(i, 0)) || !std::isfinite(n(i, 1)) || !std::isfinite(n(i, 2)))
                throw std::invalid_argument("Boundary normals must be finite");
            input[i] = netgen::STLReadTriangle(vertices, netgen::Vec<3>(n(i, 0), n(i, 1), n(i, 2)));
        }
        auto geometry = std::make_shared<netgen::STLGeometry>();
        geometry->InitSTLGeometry(input);
        return geometry;
    });
}
