"""Apply small, pinned-source Netgen patches without changing quality settings."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent / "upstream" / "libsrc" / "stlgeom"


def apply():
    path = ROOT.parent / "meshing" / "meshclass.cpp"
    text = path.read_text(encoding="utf-8")
    if "RUSTMECH_SERIAL_ADJACENCY" not in text:
        start = text.index(
            "Table<ElementIndex, PointIndex> Mesh :: CreatePoint2ElementTable"
        )
        anchor = '    static Timer timer("Mesh::CreatePoint2VolumeElementTable"); RegionTimer rt(timer);'
        location = text.index(anchor, start) + len(anchor)
        text = (
            text[:location]
            + """
    // RUSTMECH_SERIAL_ADJACENCY: the sequential path needs neither atomic
    // counters nor a row sort: element IDs arrive in ascending array order.
    if (ngcore::TaskManager::GetNumThreads() == 1)
      {
        Array<int,PointIndex> counts(GetNP());
        counts = 0;
        auto visit = [&](auto add)
          {
            for (auto ei : volelements.Range())
              {
                const auto & el = (*this)[ei];
                if (el.IsDeleted() || (domain && el.GetIndex().Nr1() != domain)) continue;
                for (auto pi : el.PNums())
                  if (!points || (*points)[pi]) add(pi, ei);
              }
          };
        visit([&](PointIndex pi, ElementIndex ei) { counts[pi]++; });
        Table<ElementIndex,PointIndex> result(counts);
        counts = 0;
        visit([&](PointIndex pi, ElementIndex ei) { result[pi][counts[pi]++] = ei; });
        return result;
      }
"""
            + text[location:]
        )
        path.write_text(text, encoding="utf-8", newline="\n")

    path = ROOT / "stltopology.cpp"
    text = path.read_text(encoding="utf-8")
    if "RUSTMECH_EXACT_WELD" not in text:
        text = text.replace(
            '#include "stlgeom.hpp"',
            '#include "stlgeom.hpp"\n#include "rustmech_weld_cache.hpp"\n// RUSTMECH_EXACT_WELD',
        )
        anchor = '  PrintMessageCR(5,"identify points ...");'
        assert text.count(anchor) == 1
        text = text.replace(
            anchor,
            anchor
            + """
  double magnitude=0;
  for(int d=0;d<3;++d)
      magnitude=std::max(magnitude,std::max(std::abs(boundingbox.PMin()[d]),std::abs(boundingbox.PMax()[d])));
  rustmech::WeldCache exact_cache(pointtol,magnitude,readtrigs.Size()/2);
""",
        )
        start = text.index(
            "          pointtree->GetIntersecting (pmin, pmax, pintersect);",
            text.index("void STLTopology :: InitSTLGeometry"),
        )
        end = text.index("          if (Dist(p, points[foundpos])", start)
        text = (
            text[:start]
            + """          int foundpos=exact_cache.Find(p);
          if(foundpos<0) {
              pointtree->GetIntersecting(pmin,pmax,pintersect);
              if(pintersect.Size()>1) PrintError("too many close points");
              if(pintersect.Size()) foundpos=pintersect[0];
              if(foundpos<0) {
                  exact_cache.BeforeInsert(p,*pointtree,points);
                  foundpos=AddPoint(p);
                  pointtree->Insert(p,foundpos);
                  exact_cache.Inserted(foundpos);
              }
              exact_cache.Remember(p,foundpos,points[foundpos]);
          }
"""
            + text[end:]
        )
        path.write_text(text, encoding="utf-8", newline="\n")

    path = ROOT / "stlgeomchart.cpp"
    text = path.read_text(encoding="utf-8")
    if "RUSTMECH_FAST_ATLAS" not in text:
        text = text.replace(
            '#include "stlgeom.hpp"',
            '#include "stlgeom.hpp"\n#include "rustmech_point_tree.hpp"\n// RUSTMECH_FAST_ATLAS',
        )
        anchor = "      // NgProfiler::StopTimer (timer2);"
        tree = """      std::unique_ptr<rustmech::PointTree> inner_tree;
      if (innerchartpts.Size() > 64)
        inner_tree = std::make_unique<rustmech::PointTree>(innerchartpts);

"""
        assert text.count(anchor) == 1
        text = text.replace(anchor, tree + anchor)
        start = text.index(
            "                      // outer chart is only small environment of"
        )
        end = text.index(
            "                      // NgProfiler::StartTimer (timer5b);", start
        )
        near = text[start:end]
        old = """                                for (int l = 0; l < innerchartpts.Size(); l++)"""
        assert near.count(old) == 1
        near = near.replace(
            old,
            """                                if (inner_tree)
                                  accepted = inner_tree->HasWithin(pt, 4*h2);
                                else
"""
            + old,
        )
        text = text[:start] + text[end:]
        anchor = "                      bool isdirtytrig = false;"
        assert text.count(anchor) == 1
        text = text.replace(
            anchor, near + "                      if (!accepted) continue;\n\n" + anchor
        )
        path.write_text(text, encoding="utf-8", newline="\n")

    # Use compact stable segment slots in the boundary tree. Candidate tests
    # no longer hash an edge key to recover a segment they have already found.
    path = ROOT / "stltool.hpp"
    text = path.read_text(encoding="utf-8")
    if "RUSTMECH_BOUNDARY_SLOTS" not in text:
        text = text.replace(
            "  bool smoothedge;", "  bool smoothedge;\n  int tree_id = -1;"
        )
        text = text.replace(
            "  unique_ptr<BoxTree<2,IVec<2>>> searchtree;",
            """  // RUSTMECH_BOUNDARY_SLOTS
  unique_ptr<BoxTree<2,int>> searchtree;
  std::vector<STLBoundarySeg> search_segments;
  std::vector<int> free_segments;""",
        )
        text = text.replace(
            "boundary_ht = ClosedHashTable<IVec<2>,STLBoundarySeg>(); }",
            "boundary_ht = ClosedHashTable<IVec<2>,STLBoundarySeg>(); search_segments.clear(); free_segments.clear(); }",
        )
        path.write_text(text, encoding="utf-8", newline="\n")
        path = ROOT / "stltool.cpp"
        text = path.read_text(encoding="utf-8")
        start = text.index(
            "  IVec<2> segs[3];", text.index("void STLBoundary ::AddTriangle")
        )
        end = text.index("bool STLBoundary :: TestSeg(", start)
        text = (
            text[:start]
            + """  IVec<2> segs[3] = { IVec<2>(t[0],t[1]), IVec<2>(t[1],t[2]), IVec<2>(t[2],t[0]) };
  if(!searchtree) BuildSearchTree();
  for(auto seg : segs) {
      IVec<2> opposite(seg[1],seg[0]);
      if(boundary_ht.Used(opposite)) {
          int slot=boundary_ht[opposite].tree_id;
          searchtree->DeleteElement(slot);
          free_segments.push_back(slot);
          boundary_ht.Delete(opposite);
      } else {
          STLBoundarySeg bseg(seg[0],seg[1],geometry->GetPoints(),chart);
          bseg.SetSmoothEdge(geometry->IsSmoothEdge(seg[0],seg[1]));
          int slot;
          if(free_segments.empty()) {
              slot=static_cast<int>(search_segments.size());
              search_segments.push_back(bseg);
          } else {
              slot=free_segments.back();
              free_segments.pop_back();
              search_segments[slot]=bseg;
          }
          bseg.tree_id=slot;
          boundary_ht[seg]=bseg;
          searchtree->Insert(bseg.BoundingBox(),slot);
      }
  }
}

"""
            + text[end:]
        )
        text = text.replace(
            "make_unique<BoxTree<2,IVec<2>>>", "make_unique<BoxTree<2,int>>"
        )
        text = text.replace(
            "hasIntersection(boundary_ht[i2])", "hasIntersection(search_segments[i2])"
        )
        path.write_text(text, encoding="utf-8", newline="\n")

    path = ROOT.parent / "gprim" / "adtree.hpp"
    text = path.read_text(encoding="utf-8")
    if "RUSTMECH_BOUNDARY_LEAF" in text:
        text = text.replace(
            "  // RUSTMECH_BOUNDARY_LEAF: small 2D edge queries avoid scanning 100 boxes.\n  static constexpr int N = (dim == 2 && std::is_same_v<T,int>) ? 16 : 100;",
            "  static constexpr int N = 100;",
        )
        path.write_text(text, encoding="utf-8", newline="\n")

    path = ROOT / "stltool.hpp"
    text = path.read_text(encoding="utf-8")
    if "RUSTMECH_BOUNDARY_GRID" not in text:
        text = (
            '#include "rustmech_boundary_grid.hpp"\n// RUSTMECH_BOUNDARY_GRID\n' + text
        )
        text = text.replace(
            "unique_ptr<BoxTree<2,int>> searchtree;",
            "unique_ptr<rustmech::BoundaryGrid> searchtree;",
        )
        path.write_text(text, encoding="utf-8", newline="\n")
        path = ROOT / "stltool.cpp"
        text = path.read_text(encoding="utf-8")
        old = "searchtree = make_unique<BoxTree<2,int>> (box2d);"
        assert text.count(old) == 1
        text = text.replace(
            old,
            "double spacing=4*geometry->GetBoundingBox().Diam()/std::sqrt(std::max(1.0,double(geometry->GetNT())));\n  searchtree = make_unique<rustmech::BoundaryGrid>(box2d,spacing);",
        )
        path.write_text(text, encoding="utf-8", newline="\n")

    path = ROOT / "stltool.cpp"
    text = path.read_text(encoding="utf-8")
    updated = text.replace(
        "std::sqrt(double(std::max(size_t(1),geometry->GetNT())))",
        "std::sqrt(std::max(1.0,double(geometry->GetNT())))",
    )
    if updated != text:
        path.write_text(updated, encoding="utf-8", newline="\n")

    path = ROOT / "stltool.cpp"
    text = path.read_text(encoding="utf-8")
    if "RUSTMECH_DIRECT_BOUNDARY" not in text:
        start = text.index("bool STLBoundary :: TestSegChartNV")
        body = text[start:]
        old = """auto hasIntersection = [&] (auto i2) NETGEN_LAMBDA_INLINE
    {
      const STLBoundarySeg & seg = boundary_ht[i2];"""
        assert body.count(old) == 1
        body = body.replace(
            old,
            """// RUSTMECH_DIRECT_BOUNDARY: no second hash lookup for a known segment.
  auto hasIntersection = [&] (const STLBoundarySeg & seg) NETGEN_LAMBDA_INLINE
    {""",
        )
        body = body.replace(
            "has_intersection = hasIntersection(i2);",
            "has_intersection = hasIntersection(boundary_ht[i2]);",
        )
        body = body.replace("if(hasIntersection(i2))", "if(hasIntersection(seg))")
        path.write_text(text[:start] + body, encoding="utf-8", newline="\n")

    text = path.read_text(encoding="utf-8")
    updated = text.replace(
        "hasIntersection(boundary_ht[i2])", "hasIntersection(search_segments[i2])"
    )
    if updated != text:
        path.write_text(updated, encoding="utf-8", newline="\n")

    path = ROOT / "stlgeommesh.cpp"
    text = path.read_text(encoding="utf-8")
    if "RUSTMECH_CHART_DISTANCE" not in text:
        text = text.replace(
            '#include "stlgeom.hpp"',
            '#include "stlgeom.hpp"\n#include "rustmech_point_tree.hpp"\n// RUSTMECH_CHART_DISTANCE',
        )
        start = text.index(
            "      Point3dTree stree(bbox.PMin(), bbox.PMax());",
            text.index("void STLGeometry :: RestrictHChartDistOneChart"),
        )
        end = text.index("      timer3a.Stop();", start)
        text = (
            text[:start] + "      rustmech::PointTree stree(plimes2);\n\n" + text[end:]
        )
        start = text.index(
            "          stree.GetIntersecting (pmin, pmax, foundpts);", start
        )
        end = text.index("          /*", start)
        text = (
            text[:start]
            + "          mindist = stree.MinimumInBox(ap1, pmin, pmax);\n\n"
            + text[end:]
        )
        path.write_text(text, encoding="utf-8", newline="\n")

    path = ROOT / "stlgeomchart.cpp"
    text = path.read_text(encoding="utf-8")
    # A point's distance-to-inner-chart predicate is immutable during one
    # chart's outer expansion. Local sizing changes only after that expansion.
    if "RUSTMECH_PROXIMITY_CACHE" not in text and "RUSTMECH_FAST_ATLAS" in text:
        anchor = "  Array<Point<3>> innerchartpts;"
        assert text.count(anchor) == 1
        text = text.replace(
            anchor,
            anchor
            + """
  // RUSTMECH_PROXIMITY_CACHE
  Array<ChartId,STLPointId> proximity_chart(GetNP());
  Array<unsigned char,STLPointId> proximity_result(GetNP());
  proximity_chart=ChartId::INVALID;
""",
        )
        start = text.index(
            "                                Point<3> pt = GetPoint(ntrig[k]);"
        )
        end = text.index("                                if (accepted) break;", start)
        text = (
            text[:start]
            + """                                auto pi=ntrig[k];
                                if(proximity_chart[pi]==chartnum)
                                  accepted=proximity_result[pi];
                                else
                                  {
                                    Point<3> pt=GetPoint(pi);
                                    double h2=sqr(mesh.GetH(pt));
                                    accepted=false;
                                    if(inner_tree) accepted=inner_tree->HasWithin(pt,4*h2);
                                    else for(int l=0;l<innerchartpts.Size();++l)
                                      if(Dist2(pt,innerchartpts[l])<4*h2) {accepted=true;break;}
                                    proximity_chart[pi]=chartnum;
                                    proximity_result[pi]=accepted;
                                  }
"""
            + text[end:]
        )
        path.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    apply()
