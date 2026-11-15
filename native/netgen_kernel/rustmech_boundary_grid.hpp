#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <unordered_map>
#include <vector>

namespace rustmech {
// Conservative 2D broad phase for the short edges of a sampled STL boundary.
// Every actual intersection still goes through Netgen's unchanged predicate.
class BoundaryGrid {
    using Point=netgen::Point<2>;
    struct Range { int x0=0,x1=-1,y0=0,y1=-1; bool active=false,large=false; };
    Point origin;
    double width;
    std::unordered_map<uint64_t,std::vector<int>> bins;
    std::vector<Range> records;
    std::vector<int> oversized;
    std::vector<uint32_t> seen;
    uint32_t stamp=0;
    static uint64_t Key(int x,int y) { return (uint64_t(uint32_t(x))<<32)|uint32_t(y); }
    Range Cells(const Point& lo,const Point& hi) const {
        return {int(std::floor((lo[0]-origin[0])/width)),int(std::floor((hi[0]-origin[0])/width)),
                int(std::floor((lo[1]-origin[1])/width)),int(std::floor((hi[1]-origin[1])/width)),true,false};
    }
    static int64_t Count(const Range& r) { return int64_t(r.x1-r.x0+1)*(r.y1-r.y0+1); }
    static void Erase(std::vector<int>& values,int id) {
        auto pos=std::find(values.begin(),values.end(),id);
        if(pos!=values.end()) { *pos=values.back();values.pop_back(); }
    }
public:
    BoundaryGrid(const netgen::Box<2>& bounds,double cell_width) : origin(bounds.PMin()),width(cell_width) {
        if(!std::isfinite(width) || width<=0) width=1;
    }
    void Insert(const netgen::Box<2>& bounds,int id) {
        if(id>=int(records.size())) { records.resize(size_t(id)+1);seen.resize(size_t(id)+1); }
        auto r=Cells(bounds.PMin(),bounds.PMax());
        r.large=Count(r)>64;
        records[id]=r;
        if(r.large) { oversized.push_back(id);return; }
        for(int x=r.x0;x<=r.x1;++x) for(int y=r.y0;y<=r.y1;++y) bins[Key(x,y)].push_back(id);
    }
    void DeleteElement(int id) {
        auto& r=records[id];
        if(r.large) Erase(oversized,id);
        else for(int x=r.x0;x<=r.x1;++x) for(int y=r.y0;y<=r.y1;++y) {
            auto pos=bins.find(Key(x,y));
            if(pos!=bins.end()) { Erase(pos->second,id);if(pos->second.empty()) bins.erase(pos); }
        }
        r.active=false;
    }
    template<class Function>
    void GetFirstIntersecting(const Point& lo,const Point& hi,Function callback) {
        auto r=Cells(lo,hi);
        if(++stamp==0) {std::fill(seen.begin(),seen.end(),0);stamp=1;}
        auto visit=[&](int id) {
            if(seen[id]==stamp) return false;
            seen[id]=stamp;
            return callback(id);
        };
        // Long edges/queries stay bounded; sparse grid enumeration is used for
        // ordinary voxel-sized segments only. These fallbacks are exact too.
        if(Count(r)>1024) {
            for(int id=0;id<int(records.size());++id) if(records[id].active && visit(id)) return;
            return;
        }
        for(int id:oversized) if(visit(id)) return;
        for(int x=r.x0;x<=r.x1;++x) for(int y=r.y0;y<=r.y1;++y) {
            auto pos=bins.find(Key(x,y));
            if(pos!=bins.end()) for(int id:pos->second) if(visit(id)) return;
        }
    }
};
}
