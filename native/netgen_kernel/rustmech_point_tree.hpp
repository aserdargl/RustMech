#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <numeric>
#include <vector>

namespace rustmech {
// Immutable, balanced point index. All distance predicates use Netgen's exact
// Dist2 and preserve strict/inclusive comparisons used by the original loops.
class PointTree {
    using Point = netgen::Point<3>;
    struct Node {
        std::array<double,3> lo, hi;
        int begin, end, left=-1, right=-1;
    };
    const netgen::Array<Point>& points;
    std::vector<int> order;
    std::vector<Node> nodes;

    int Build(int begin, int end) {
        Node node;
        node.begin=begin; node.end=end;
        for(int d=0;d<3;++d) node.lo[d]=node.hi[d]=points[order[begin]][d];
        for(int i=begin+1;i<end;++i) for(int d=0;d<3;++d) {
            node.lo[d]=std::min(node.lo[d],points[order[i]][d]);
            node.hi[d]=std::max(node.hi[d],points[order[i]][d]);
        }
        int index=static_cast<int>(nodes.size());
        nodes.push_back(node);
        if(end-begin>12) {
            int axis=0;
            for(int d=1;d<3;++d) if(node.hi[d]-node.lo[d]>node.hi[axis]-node.lo[axis]) axis=d;
            int middle=(begin+end)/2;
            std::nth_element(order.begin()+begin,order.begin()+middle,order.begin()+end,
                [&](int a,int b) { return points[a][axis]<points[b][axis]; });
            int left=Build(begin,middle), right=Build(middle,end);
            nodes[index].left=left; nodes[index].right=right;
        }
        return index;
    }

    static bool Intersects(const Node& node,const Point& lo,const Point& hi) {
        for(int d=0;d<3;++d) if(node.hi[d]<lo[d] || node.lo[d]>hi[d]) return false;
        return true;
    }
    bool Query(int index,const Point& p,const Point& lo,const Point& hi,double& best,bool any) const {
        const auto& node=nodes[index];
        if(!Intersects(node,lo,hi)) return false;
        if(node.left>=0) {
            if(Query(node.left,p,lo,hi,best,any) && any) return true;
            return Query(node.right,p,lo,hi,best,any);
        }
        for(int i=node.begin;i<node.end;++i) {
            const auto& q=points[order[i]];
            bool inside=true;
            for(int d=0;d<3;++d) if(q[d]<lo[d] || q[d]>hi[d]) {inside=false;break;}
            if(!inside) continue;
            double distance=netgen::Dist2(p,q);
            if(distance<best) {
                if(any) return true;
                best=distance;
            }
        }
        return false;
    }
public:
    explicit PointTree(const netgen::Array<Point>& input) : points(input),order(input.Size()) {
        std::iota(order.begin(),order.end(),0);
        nodes.reserve(input.Size()/6+1);
        if(!order.empty()) Build(0,static_cast<int>(order.size()));
    }
    bool HasWithin(const Point& p,double radius_squared) const {
        if(nodes.empty() || radius_squared<=0) return false;
        double radius=std::sqrt(radius_squared), best=radius_squared;
        Point lo,hi;
        for(int d=0;d<3;++d) {
            lo[d]=std::nextafter(p[d]-radius,-std::numeric_limits<double>::infinity());
            hi[d]=std::nextafter(p[d]+radius,std::numeric_limits<double>::infinity());
        }
        return Query(0,p,lo,hi,best,true);
    }
    double MinimumInBox(const Point& p,const Point& lo,const Point& hi) const {
        double best=1e50;
        if(!nodes.empty()) Query(0,p,lo,hi,best,false);
        return best;
    }
};
}
