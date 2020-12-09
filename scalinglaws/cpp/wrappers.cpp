#include <torch/extension.h>
#include <torch/csrc/autograd/variable.h>
#include <pybind11/pybind11.h>
#include "common.h"

namespace py = pybind11;
using namespace pybind11::literals;
using namespace std::string_literals;

PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
    m.def("solve_policy", &solve_policy, "pi"_a, "q"_a, "lambda_n"_a, py::call_guard<py::gil_scoped_release>());

    py::class_<Solution>(m, "Solution", py::module_local())
        .def_property_readonly("policy", [](Solution s) { return s.policy.t; })
        .def_property_readonly("alpha_min", [](Solution s) { return s.alpha_min.t; })
        .def_property_readonly("alpha_star", [](Solution s) { return s.alpha_star.t; })
        .def_property_readonly("error", [](Solution s) { return s.error.t; });
}