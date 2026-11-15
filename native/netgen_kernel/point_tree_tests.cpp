#include <mystdlib.h>
#include <myadt.hpp>
#include <linalg.hpp>
#include <gprim.hpp>
#include <meshing.hpp>
#include "rustmech_point_tree.hpp"
#include "rustmech_weld_cache.hpp"
#include "rustmech_boundary_grid.hpp"
#include <random>
#include <stdexcept>
#include <iostream>

int main() {
    std::mt19937 generator(321);
    std::uniform_real_distribution<double> random(-10.,10.);
    size_t queries=0;
    for(int count:{1,7,64,65,200,1000}) for(int shape=0;shape<3;++shape) {
        netgen::Array<netgen::Point<3>> points(count);
        for(auto& p:points) p=netgen::Point<3>(random(generator),shape==1 ? 0. : random(generator),shape==2 ? 0. : random(generator));
        if(count>1) points[1]=points[0];
        rustmech::PointTree tree(points);
        for(int q=0;q<200;++q) {
            netgen::Point<3> p(random(generator),random(generator),random(generator));
            double radius=std::abs(random(generator));
            netgen::Vec<3> offset(radius,radius,radius);
            auto lo=p-offset,hi=p+offset;
            double minimum=1e50;
            bool within=false;
            for(const auto& candidate:points) {
                double distance=netgen::Dist2(p,candidate);
                within=within || distance<radius*radius;
                if(candidate[0]>=lo[0] && candidate[0]<=hi[0] && candidate[1]>=lo[1] && candidate[1]<=hi[1] && candidate[2]>=lo[2] && candidate[2]<=hi[2])
                    minimum=std::min(minimum,distance);
            }
            if(tree.HasWithin(p,radius*radius)!=within || tree.MinimumInBox(p,lo,hi)!=minimum)
                throw std::runtime_error("Point index differs from exhaustive search");
            ++queries;
        }
        auto p=points[0];
        if(tree.HasWithin(p,0.) || !tree.HasWithin(p,std::numeric_limits<double>::min()) || tree.MinimumInBox(p,p,p)!=0.)
            throw std::runtime_error("Strict radius/inclusive box boundary mismatch");
    }
    netgen::Array<netgen::Point<3>> empty;
    rustmech::PointTree tree(empty);
    netgen::Point<3> zero(0.,0.,0.);
    if(tree.HasWithin(zero,1.) || tree.MinimumInBox(zero,zero,zero)!=1e50)
        throw std::runtime_error("Empty point index mismatch");
    // Exercise the compact boundary tree's splits, removals and slot reuse.
    netgen::Point<2> low(-20.,-20.), high(20.,20.);
    netgen::BoxTree<2,int> boxes(low,high);
    rustmech::BoundaryGrid grid(netgen::Box<2>(low,high),.5);
    std::vector<netgen::Box<2>> bounds(1000);
    std::vector<bool> active(1000,true);
    for(int i=0;i<1000;++i) {
        bounds[i].Set(netgen::Point<2>(random(generator),random(generator)));
        bounds[i].Add(netgen::Point<2>(random(generator),random(generator)));
        boxes.Insert(bounds[i],i);
        grid.Insert(bounds[i],i);
    }
    for(int pass=0;pass<3;++pass) {
        if(pass==1) for(int i=0;i<1000;i+=3) { boxes.DeleteElement(i);grid.DeleteElement(i);active[i]=false; }
        if(pass==2) for(int i=0;i<1000;i+=3) {
            bounds[i].Set(netgen::Point<2>(random(generator),random(generator)));
            bounds[i].Add(netgen::Point<2>(random(generator),random(generator)));
            boxes.Insert(bounds[i],i);active[i]=true;
            grid.Insert(bounds[i],i);
        }
        for(int q=0;q<1000;++q) {
            netgen::Box<2> query;
            query.Set(netgen::Point<2>(random(generator),random(generator)));
            query.Add(netgen::Point<2>(random(generator),random(generator)));
            bool expected=false,found=false;
            for(int i=0;i<1000;++i) expected=expected || (active[i] && query.Intersect(bounds[i]));
            boxes.GetFirstIntersecting(query.PMin(),query.PMax(),[&](int i) {
                if(!active[i]) throw std::runtime_error("Deleted boundary slot was returned");
                found=query.Intersect(bounds[i]);return found;
            });
            if(expected!=found) throw std::runtime_error("Boundary index differs from exhaustive search");
            found=false;
            grid.GetFirstIntersecting(query.PMin(),query.PMax(),[&](int i) {
                if(!active[i]) throw std::runtime_error("Deleted grid slot was returned");
                found=query.Intersect(bounds[i]);return found;
            });
            if(expected!=found) throw std::runtime_error("Boundary grid differs from exhaustive search");
            ++queries;
        }
    }
    // Short edge bins, exact endpoints, empty queries and recycled IDs.
    rustmech::BoundaryGrid short_grid(netgen::Box<2>(low,high),.05);
    std::vector<netgen::Box<2>> short_boxes(200);
    for(int i=0;i<200;++i) {
        netgen::Point<2> p((i%20)*.2,(i/20)*.2);
        short_boxes[i].Set(p);short_boxes[i].Add(p+netgen::Vec<2>(.01,.01));
        short_grid.Insert(short_boxes[i],i);
    }
    for(int pass=0;pass<2;++pass) {
        if(pass==1) for(int i=0;i<200;i+=3) short_grid.DeleteElement(i);
        for(int i=0;i<200;++i) {
            bool hit=false;
            auto p=short_boxes[i].PMax();
            short_grid.GetFirstIntersecting(p,p,[&](int id) {hit=short_boxes[id].IsIn(p);return hit;});
            if(hit!=(pass==0 || i%3!=0)) throw std::runtime_error("Short boundary edge lookup mismatch");
            ++queries;
        }
        bool hit=false;
        netgen::Point<2> far(15.,15.);
        short_grid.GetFirstIntersecting(far,far,[&](int id) {hit=short_boxes[id].IsIn(far);return hit;});
        if(hit) throw std::runtime_error("Empty boundary query mismatch");
    }
    // Compare exact reuse to the original float-coordinate tree, including
    // close/ambiguous points, rounded box boundaries and repeated coordinates.
    for(double scale:{1e-3,1.,1e6}) {
        double tol=scale*1e-5;
        netgen::Point<3> low3(-20*scale,-20*scale,-20*scale),high3(20*scale,20*scale,20*scale);
        netgen::Point3dTree<> original(low3,high3), accelerated(low3,high3);
        netgen::Array<netgen::Point<3>> first,second;
        first.Append(zero);second.Append(zero);
        rustmech::WeldCache cache(tol,20*scale);
        netgen::Array<int> found;
        std::vector<netgen::Point<3>> input;
        for(int i=0;i<10000;++i) {
            netgen::Point<3> p;
            if(i>20 && i%5!=0) {
                p=input[generator()%input.size()];
                if(i%7==0) p[generator()%3]+=tol*(i%3==0 ? 1.000001 : .75);
            } else p=netgen::Point<3>(random(generator)*scale,random(generator)*scale,random(generator)*scale);
            input.push_back(p);
            netgen::Vec<3> radius(tol,tol,tol);
            original.GetIntersecting(p-radius,p+radius,found);
            int expected;
            if(found.Size()) expected=found[0];
            else {expected=first.Size();first.Append(p);original.Insert(p,expected);}
            int actual=cache.Find(p);
            if(actual<0) {
                accelerated.GetIntersecting(p-radius,p+radius,found);
                if(found.Size()) actual=found[0];
                else {
                    cache.BeforeInsert(p,accelerated,second);
                    actual=second.Size();second.Append(p);accelerated.Insert(p,actual);cache.Inserted(actual);
                }
                cache.Remember(p,actual,second[actual]);
            }
            if(actual!=expected) throw std::runtime_error("Exact weld reuse differs from the original tree");
            ++queries;
        }
    }
    // Adjacency filtering, deleted elements and row ordering match an
    // independent scan of the fixture's corner IDs.
    netgen::Mesh mesh;
    for(int i=0;i<9;++i) mesh.AddPoint(netgen::Point<3>(i%3,(i/3)%3,i/6));
    for(int k=0;k<20;++k) {
        netgen::Element element(netgen::TET);
        for(int j=0;j<4;++j) element[j]=netgen::PointIndex::FromNr0((k*3+j*2)%9);
        element.SetIndex(netgen::VolumeRegionIndex::FromNr1(1+k%2));
        auto ei=mesh.AddVolumeElement(element);
        if(k%4==0) mesh.VolumeElements()[ei].Delete();
    }
    for(int masked=0;masked<2;++masked) for(int domain=0;domain<3;++domain) {
        std::optional<netgen::TBitArray<netgen::PointIndex>> mask;
        if(masked) {
            mask.emplace(mesh.GetNP());mask->Clear();
            for(int i=0;i<9;i+=2) mask->SetBit(netgen::PointIndex::FromNr0(i));
        }
        auto table=mesh.CreatePoint2ElementTable(mask,domain);
        for(int i=0;i<9;++i) {
            std::vector<int> expected;
            if(!masked || i%2==0) for(int k=0;k<20;++k) {
                if(k%4==0 || (domain && domain!=1+k%2)) continue;
                for(int j=0;j<4;++j) if((k*3+j*2)%9==i) expected.push_back(k);
            }
            auto row=table[netgen::PointIndex::FromNr0(i)];
            if(row.Size()!=expected.size()) throw std::runtime_error("Adjacency row size mismatch");
            for(size_t j=0;j<expected.size();++j)
                if(row[j].Nr0()!=expected[j]) throw std::runtime_error("Adjacency ordering mismatch");
            ++queries;
        }
    }
    std::cout << queries << " spatial/weld/adjacency queries matched reference searches\n";
}
