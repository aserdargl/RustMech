#pragma once
#include <array>
#include <cmath>
#include <limits>
#include <unordered_map>
#include <vector>

namespace rustmech {
// Reuse only exact canonical coordinates. If a later insertion can enter a
// cached point's rounded query box, invalidate it before insertion. Nonexact
// merges and ambiguous queries always retain Netgen's original tree behavior.
class WeldCache {
    using Point=netgen::Point<3>;
    using Key=std::array<double,3>;
    struct Hash {
        size_t operator()(const Key& key) const {
            size_t value=0;
            for(double coordinate:key)
                value^=std::hash<double>{}(coordinate)+0x9e3779b9+(value<<6)+(value>>2);
            return value;
        }
    };
    double tolerance, guard;
    std::unordered_map<Key,int,Hash> exact;
    std::vector<unsigned char> valid;
    netgen::Array<int> candidates;
    static Key MakeKey(const Point& p) { return {p[0],p[1],p[2]}; }
public:
    WeldCache(double tol,double magnitude,size_t reserve=0)
        : tolerance(tol),guard(std::max(1.,magnitude+tol)*std::numeric_limits<float>::epsilon()*8) {
        exact.reserve(reserve);
    }
    int Find(const Point& p) const {
        auto it=exact.find(MakeKey(p));
        return it!=exact.end() && valid[it->second] ? it->second : -1;
    }
    template<class Points>
    void BeforeInsert(const Point& p,netgen::Point3dTree<>& tree,const Points& points) {
        netgen::Vec<3> radius(tolerance+2*guard,tolerance+2*guard,tolerance+2*guard);
        tree.GetIntersecting(p-radius,p+radius,candidates);
        for(int id:candidates) {
            if(id>=static_cast<int>(valid.size()) || !valid[id]) continue;
            bool inside=true;
            for(int d=0;d<3;++d) {
                float coordinate=static_cast<float>(p[d]);
                if(coordinate<static_cast<float>(points[id][d]-tolerance) ||
                   coordinate>static_cast<float>(points[id][d]+tolerance)) { inside=false;break; }
            }
            if(inside) valid[id]=0;
        }
    }
    void Inserted(int id) {
        valid.resize(static_cast<size_t>(id)+1,0);
        valid[id]=1;
    }
    void Remember(const Point& p,int id,const Point& canonical) {
        if(valid[id] && p[0]==canonical[0] && p[1]==canonical[1] && p[2]==canonical[2])
            exact.emplace(MakeKey(p),id);
    }
};
}
