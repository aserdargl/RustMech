// Private Netgen build: array-only C ABI, independent of Python and OCCT.
#include <mystdlib.h>
#include <myadt.hpp>
#include <linalg.hpp>
#include <gprim.hpp>
#include <meshing.hpp>
#include <stlgeom.hpp>
#include <nginterface_v2.hpp>
#include <vector>
#include <limits>

namespace {
thread_local std::string last_error;
using Progress = int(*)(int);
thread_local Progress progress=nullptr;
struct GlobalReset { ~GlobalReset() { netgen::SetGlobalMesh(nullptr); } };
struct Output {
    std::vector<double> points;
    std::vector<int64_t> cells;
};
}

extern "C" __declspec(dllexport) uint32_t rustmech_netgen_abi() { return 1; }
extern "C" __declspec(dllexport) const char* rustmech_netgen_revision() { return "96e5682f-rustmech-atlas-6"; }
extern "C" __declspec(dllexport) void rustmech_netgen_set_progress(Progress callback) { progress=callback; }
extern "C" __declspec(dllexport) const char* rustmech_netgen_error() { return last_error.c_str(); }
extern "C" __declspec(dllexport) void* rustmech_netgen_generate(
    const double* points, size_t np, const int64_t* faces, size_t nf,
    const double* normals, const double* samples, size_t ns,
    double maxh, double localh, size_t* counts) {
    try {
        last_error.clear();
        if (!points || !faces || !normals || !counts || np == 0 || nf == 0 ||
            nf > std::numeric_limits<int>::max() || np > std::numeric_limits<int>::max() ||
            ns > std::numeric_limits<int>::max() || (ns && !samples) ||
            !std::isfinite(maxh) || !std::isfinite(localh) || maxh <= 0 || localh <= 0)
            throw std::invalid_argument("Invalid Netgen input arrays or sizes");
        ngcore::printmessage_importance = 0;
        GlobalReset reset;
        netgen::Array<netgen::STLReadTriangle> input(nf);
        for (size_t i=0; i<nf; ++i) {
            netgen::Point<3> vertices[3];
            for (int j=0; j<3; ++j) {
                int64_t id=faces[3*i+j];
                if (id<0 || static_cast<size_t>(id)>=np) throw std::invalid_argument("Invalid boundary index");
                vertices[j]=netgen::Point<3>(points[3*id],points[3*id+1],points[3*id+2]);
                for(int d=0;d<3;++d) if(!std::isfinite(points[3*id+d])) throw std::invalid_argument("Non-finite boundary");
            }
            for(int d=0;d<3;++d) if(!std::isfinite(normals[3*i+d])) throw std::invalid_argument("Non-finite facet normal");
            input[i]=netgen::STLReadTriangle(vertices,netgen::Vec<3>(normals[3*i],normals[3*i+1],normals[3*i+2]));
        }
        auto geometry=std::make_shared<netgen::STLGeometry>();
        geometry->InitSTLGeometry(input);
        auto mesh=std::make_shared<netgen::Mesh>();
        mesh->SetGeometry(geometry);
        netgen::SetGlobalMesh(mesh);
        netgen::MeshingParameters parameters;
        parameters.maxh=maxh;
        for(size_t i=0;i<ns;++i) {
            for(int d=0;d<3;++d) if(!std::isfinite(samples[3*i+d])) throw std::invalid_argument("Non-finite refinement sample");
            parameters.meshsize_points.Append({netgen::Point<3>(samples[3*i],samples[3*i+1],samples[3*i+2]),localh,1});
        }
        ngcore::NgProfiler::Reset();
        netgen::STLParameters stlparameters;
        static ngcore::Timer generation_timer("RustMech::GenerateMesh");
        {
        ngcore::RegionTimer timer(generation_timer);
        for(int stage=2;stage<=6;++stage) {
            if(progress && progress(stage)) throw std::runtime_error("Netgen generation cancelled by the progress callback");
            parameters.perfstepsstart=stage==2 ? 0 : stage;
            parameters.perfstepsend=stage;
            if(netgen::STLMeshingDummy(geometry.get(),mesh,parameters,stlparameters)!=0)
                throw std::runtime_error("Netgen meshing stage failed");
            if(stage>=3 && !geometry->surfacemeshed) throw std::runtime_error("Netgen surface generation failed");
            if(stage>=5 && !geometry->volumemeshed) throw std::runtime_error("Netgen volume generation failed");
        }
        }
        if(mesh->GetNE()==0)
            throw std::runtime_error("Netgen mesh has no solid elements.");
        auto output=std::make_unique<Output>();
        output->points.reserve(static_cast<size_t>(mesh->GetNP())*3);
        output->cells.reserve(static_cast<size_t>(mesh->GetNE())*4);
        for(auto& point:mesh->Points()) for(int d=0;d<3;++d) output->points.push_back(point[d]);
        for(const auto& cell:mesh->VolumeElements()) {
            if(cell.GetType()!=netgen::TET) throw std::runtime_error("Expected linear tetrahedra");
            for(int j:{1,0,2,3}) output->cells.push_back(cell[j].Nr0());
        }
        counts[0]=mesh->GetNP(); counts[1]=mesh->GetNE();
        return output.release();
    } catch(const std::exception& exc) { last_error=exc.what(); return nullptr; }
      catch(...) { last_error="Unknown Netgen failure"; return nullptr; }
}
extern "C" __declspec(dllexport) int rustmech_netgen_write(void* handle,double* points,size_t np,int64_t* cells,size_t ne) {
    if(!handle || !points || !cells) return -1;
    auto& out=*static_cast<Output*>(handle);
    if(out.points.size()!=np*3 || out.cells.size()!=ne*4) return -1;
    std::copy(out.points.begin(),out.points.end(),points);
    std::copy(out.cells.begin(),out.cells.end(),cells);
    return 0;
}
extern "C" __declspec(dllexport) void rustmech_netgen_free(void* handle) { delete static_cast<Output*>(handle); }
extern "C" __declspec(dllexport) double rustmech_netgen_timer(const char* name) { return ngcore::NgProfiler::GetTime(name); }
