"""
Unit tests for phoenix_topo_core (runs with plain Python 3, no Maya).

    python test_phoenix_topo_core.py
"""
import random
import sys
import time
import unittest

import phoenix_topo_core as core
from phoenix_topo_core import (Topology, CachedTopology, compute_preview,
                               VERTEX, EDGE, FACE, E, F)

# When True every test runs through CachedTopology (the lazy path the Maya
# controller uses) wrapped around the CSR reference implementation.
LAZY = False


# ---------------------------------------------------------------- builders
def build(faces, num_vertices=None, rotate_face_edges=False, explicit=False,
          shuffle_edge_ids=False):
    """Build a Topology like Maya would: edge k of a face joins verts k,k+1."""
    edge_ids = {}
    edge_list = []
    counts, fv, fe = [], [], []
    for poly in faces:
        n = len(poly)
        counts.append(n)
        fv.extend(poly)
        ids = []
        for k in range(n):
            a, b = poly[k], poly[(k + 1) % n]
            key = frozenset((a, b))
            if key not in edge_ids:
                edge_ids[key] = len(edge_list)
                edge_list.append((a, b))
            ids.append(edge_ids[key])
        if rotate_face_edges:
            ids = ids[1:] + ids[:1]
        fe.extend(ids)
    ne = len(edge_list)
    if shuffle_edge_ids:
        perm = list(range(ne))
        random.Random(7).shuffle(perm)
        fe = [perm[e] for e in fe]
        new = [None] * ne
        for old, (a, b) in enumerate(edge_list):
            new[perm[old]] = (a, b)
        edge_list = new
        edge_ids = {frozenset(ab): i for i, ab in enumerate(edge_list)}
    nv = num_vertices if num_vertices is not None else (max(fv) + 1 if fv else 0)
    ev = None
    if explicit:
        ev = [x for ab in edge_list for x in ab]
    topo = Topology(nv, ne, counts, fv, fe, ev)
    if LAZY:
        ref = topo
        topo = CachedTopology(ref.num_vertices, ref.num_edges, ref.num_faces,
                              ref.face_verts, ref.face_edges, ref.edge_verts,
                              ref.edge_faces, ref.vert_edges)
        topo.conflicts, topo.unassigned = ref.conflicts, ref.unassigned
    topo._ids = edge_ids
    return topo


def eid(topo, a, b):
    return topo._ids[frozenset((a, b))]


def grid(nx, ny):
    """nx * ny quads, vertex (i, j) = j*(nx+1)+i."""
    w = nx + 1
    faces = []
    for j in range(ny):
        for i in range(nx):
            v = j * w + i
            faces.append([v, v + 1, v + 1 + w, v + w])
    return faces


def cylinder(nu, nv):
    """nu around (closed), nv rows (open ends)."""
    faces = []
    for j in range(nv):
        for i in range(nu):
            a = j * nu + i
            b = j * nu + (i + 1) % nu
            faces.append([a, b, b + nu, a + nu])
    return faces


def torus(nu, nv):
    faces = []
    for j in range(nv):
        for i in range(nu):
            a = j * nu + i
            b = j * nu + (i + 1) % nu
            c = ((j + 1) % nv) * nu + (i + 1) % nu
            d = ((j + 1) % nv) * nu + i
            faces.append([a, b, c, d])
    return faces


def cube():
    return [[0, 1, 3, 2], [2, 3, 5, 4], [4, 5, 7, 6],
            [6, 7, 1, 0], [1, 7, 5, 3], [6, 0, 2, 4]]


def uv_sphere(nu, nrings):
    """Quads in the middle, triangle fans at both poles."""
    top = 0
    rings = []
    idx = 1
    for _ in range(nrings):
        rings.append(list(range(idx, idx + nu)))
        idx += nu
    bottom = idx
    faces = []
    for i in range(nu):
        faces.append([top, rings[0][(i + 1) % nu], rings[0][i]])
    for r in range(nrings - 1):
        for i in range(nu):
            a, b = rings[r][i], rings[r][(i + 1) % nu]
            c, d = rings[r + 1][(i + 1) % nu], rings[r + 1][i]
            faces.append([a, b, c, d])
    for i in range(nu):
        faces.append([bottom, rings[-1][i], rings[-1][(i + 1) % nu]])
    return faces, top, bottom, rings


# ------------------------------------------------------------------- tests
class TestBuild(unittest.TestCase):
    def test_derived_matches_explicit(self):
        faces = grid(5, 4)
        a = build(faces)
        b = build(faces, explicit=True)
        self.assertEqual(a.conflicts, 0)
        self.assertEqual(a.unassigned, 0)
        for e in range(a.num_edges):
            self.assertEqual(set(a.edge_verts(e)), set(b.edge_verts(e)))

    def test_misordered_face_edges_detected(self):
        t = build(grid(4, 4), rotate_face_edges=True)
        self.assertGreater(t.conflicts, 0)

    def test_explicit_edges_with_shuffled_ids(self):
        t = build(grid(6, 6), explicit=True, shuffle_edge_ids=True)
        e = eid(t, 7 * 3 + 0, 7 * 3 + 1)
        edges, closed = t.edge_loop(e)
        self.assertEqual(len(edges), 6)
        self.assertFalse(closed)

    def test_bad_input(self):
        with self.assertRaises(core.TopologyError):
            Topology(3, 3, [3], [0, 1, 5], [0, 1, 2])


class TestGrid(unittest.TestCase):
    def setUp(self):
        self.nx, self.ny = 6, 5
        self.t = build(grid(self.nx, self.ny))
        self.w = self.nx + 1

    def v(self, i, j):
        return j * self.w + i

    def test_interior_edge_loop_spans_grid(self):
        e = eid(self.t, self.v(2, 2), self.v(3, 2))
        edges, closed = self.t.edge_loop(e)
        self.assertFalse(closed)
        self.assertEqual(len(edges), self.nx)
        for x in edges:
            a, b = self.t.edge_verts(x)
            self.assertEqual(a // self.w, 2)
            self.assertEqual(b // self.w, 2)

    def test_border_loop_stops_at_corners(self):
        e = eid(self.t, self.v(2, 0), self.v(3, 0))
        edges, closed = self.t.edge_loop(e)
        self.assertFalse(closed)
        self.assertEqual(len(edges), self.nx)       # whole bottom border only

    def test_ring_spans_grid(self):
        e = eid(self.t, self.v(2, 2), self.v(3, 2))   # horizontal edge
        chain, closed = self.t.edge_ring(e)
        self.assertFalse(closed)
        ring = [i for k, i in chain if k == E]
        faces = [i for k, i in chain if k == F]
        self.assertEqual(len(ring), self.ny + 1)
        self.assertEqual(len(faces), self.ny)
        kinds = [k for k, _ in chain]
        for a, b in zip(kinds, kinds[1:]):
            self.assertNotEqual(a, b)                  # strictly alternating

    def test_vertex_interior_two_loops(self):
        p = compute_preview(self.t, VERTEX, self.v(3, 2))
        self.assertEqual(len(p.loop_edges), self.nx + self.ny)
        self.assertEqual(len(p.ring_chains), 4)

    def test_vertex_corner(self):
        p = compute_preview(self.t, VERTEX, self.v(0, 0))
        self.assertEqual(len(p.loop_edges), self.nx + self.ny)

    def test_face_two_strips(self):
        f = 2 * self.nx + 2
        p = compute_preview(self.t, FACE, f)
        self.assertEqual(len(p.loop_chains), 2)
        self.assertEqual(len(p.loop_faces), self.nx + self.ny - 1)
        self.assertIn(f, p.loop_faces)
        self.assertEqual(len(p.ring_edges), (self.nx + 1) + (self.ny + 1))


class TestDirections(unittest.TestCase):
    def setUp(self):
        self.n = 6
        self.t = build(grid(self.n, self.n))
        self.w = self.n + 1

    def test_interior_vertex_two_straight_directions(self):
        v = 3 * self.w + 3
        p = compute_preview(self.t, VERTEX, v)
        self.assertEqual(len(p.groups), 2)
        rows = set()
        for g in p.groups:
            self.assertEqual(len(g["edges"]), self.n)          # full straight line
            verts = set()
            for e in g["edges"]:
                verts.update(self.t.edge_verts(e))
            same_row = len({x // self.w for x in verts}) == 1
            same_col = len({x % self.w for x in verts}) == 1
            self.assertTrue(same_row or same_col)
            rows.add(same_row)
        self.assertEqual(rows, {True, False})                  # one horizontal, one vertical

    def test_border_vertex_directions(self):
        v = 3                                                   # bottom border, valence 3
        p = compute_preview(self.t, VERTEX, v)
        self.assertEqual(len(p.groups), 2)
        self.assertEqual(sorted(len(g["edges"]) for g in p.groups), [self.n, self.n])

    def test_corner_vertex(self):
        p = compute_preview(self.t, VERTEX, 0)
        self.assertEqual(len(p.groups), 2)

    def test_pole_one_direction_per_spoke(self):
        faces, top, bottom, rings = uv_sphere(8, 5)
        t = build(faces)
        p = compute_preview(t, VERTEX, top)
        self.assertEqual(len(p.groups), 8)
        firsts = [g["edges"] for g in p.groups]
        self.assertEqual(len(set().union(*firsts)), 8 * 6)     # spoke + 5 meridian edges each

    def test_face_two_groups(self):
        f = 2 * self.n + 2
        p = compute_preview(self.t, FACE, f)
        self.assertEqual(len(p.groups), 2)
        self.assertEqual(sorted(len(g["faces"]) for g in p.groups), [self.n, self.n])
        self.assertEqual(p.groups[0]["faces"] & p.groups[1]["faces"], {f})
        for g in p.groups:
            self.assertEqual(len(g["rungs"]), self.n + 1)


class TestClosed(unittest.TestCase):
    def test_cylinder_loop_closed_and_ring_open(self):
        nu, nv = 8, 4
        t = build(cylinder(nu, nv))
        e = eid(t, 2 * nu + 0, 2 * nu + 1)
        edges, closed = t.edge_loop(e)
        self.assertTrue(closed)
        self.assertEqual(len(edges), nu)
        chain, rclosed = t.edge_ring(e)
        self.assertFalse(rclosed)
        self.assertEqual(len([1 for k, _ in chain if k == E]), nv + 1)
        # vertical edge: open loop, closed ring around the cylinder
        ev = eid(t, nu + 3, 2 * nu + 3)
        edges, closed = t.edge_loop(ev)
        self.assertFalse(closed)
        self.assertEqual(len(edges), nv)
        chain, rclosed = t.edge_ring(ev)
        self.assertTrue(rclosed)
        self.assertEqual(len([1 for k, _ in chain if k == E]), nu)
        self.assertEqual(len([1 for k, _ in chain if k == F]), nu)

    def test_torus_all_closed(self):
        nu, nv = 10, 6
        t = build(torus(nu, nv))
        for e in range(t.num_edges):
            edges, closed = t.edge_loop(e)
            self.assertTrue(closed)
            self.assertIn(len(edges), (nu, nv))
            chain, rclosed = t.edge_ring(e)
            self.assertTrue(rclosed)
        p = compute_preview(t, FACE, 0)
        self.assertEqual(len(p.loop_chains), 2)
        self.assertTrue(all(c for _, c in p.loop_chains))
        self.assertEqual(len(p.loop_faces), nu + nv - 1)

    def test_cube(self):
        t = build(cube())
        for e in range(t.num_edges):
            edges, closed = t.edge_loop(e)
            self.assertEqual(edges, [e])               # valence-3 corners stop
            chain, rclosed = t.edge_ring(e)
            self.assertTrue(rclosed)
            self.assertEqual(len([1 for k, _ in chain if k == E]), 4)
        p = compute_preview(t, FACE, 0)
        self.assertEqual(len(p.loop_faces), 6)        # two belts of 4 = 6 faces
        p = compute_preview(t, VERTEX, 0)
        self.assertEqual(len(p.loop_edges), 3)


class TestPolesTrisNgons(unittest.TestCase):
    def test_sphere_poles(self):
        nu, nr = 8, 5
        faces, top, bottom, rings = uv_sphere(nu, nr)
        t = build(faces)
        # meridian edge in the quad region: loop stops at both poles
        e = eid(t, rings[1][0], rings[2][0])
        edges, closed = t.edge_loop(e)
        self.assertFalse(closed)
        verts = set()
        for x in edges:
            verts.update(t.edge_verts(x))
        self.assertIn(rings[0][0], verts)
        self.assertIn(top, verts)                     # walks up the spoke...
        self.assertIn(bottom, verts)                  # ...and stops at each pole
        self.assertEqual(len(edges), nr + 1)
        # latitude edge: closed loop
        e = eid(t, rings[2][0], rings[2][1])
        edges, closed = t.edge_loop(e)
        self.assertTrue(closed)
        self.assertEqual(len(edges), nu)
        # ring of latitude edge stops at triangle caps
        chain, rclosed = t.edge_ring(e)
        self.assertFalse(rclosed)
        self.assertEqual(len([1 for k, _ in chain if k == E]), nr)
        # pole vertex: nu half loops radiating, ring of each spoke blocked by tris
        p = compute_preview(t, VERTEX, top)
        self.assertEqual(len(t.vert_edges(top)), nu)
        spokes = [x for x in p.loop_edges if top in t.edge_verts(x)]
        self.assertEqual(len(spokes), nu)
        self.assertEqual(p.ring_chains, [])
        # triangle face: nothing
        self.assertTrue(compute_preview(t, FACE, 0).is_empty())

    def test_triangle_mesh(self):
        faces = []
        for q in grid(4, 4):
            a, b, c, d = q
            faces += [[a, b, c], [a, c, d]]
        t = build(faces)
        for e in range(t.num_edges):
            chain, closed = t.edge_ring(e)
            self.assertEqual(chain, [(E, e)])
        self.assertTrue(compute_preview(t, FACE, 3).is_empty())

    def test_ngon_stops_ring_and_face_loop(self):
        # 3x1 strip of quads where the middle is replaced by a pentagon
        faces = [[0, 1, 5, 4], [1, 2, 8, 6, 5], [2, 3, 7, 8]]
        t = build(faces)
        e = eid(t, 0, 4)
        chain, closed = t.edge_ring(e)
        self.assertEqual([i for k, i in chain if k == E], [e, eid(t, 1, 5)])
        self.assertTrue(compute_preview(t, FACE, 1).is_empty())
        p = compute_preview(t, FACE, 0)
        self.assertNotIn(1, p.loop_faces)

    def test_non_manifold_edge(self):
        # three quads sharing edge 0-1
        faces = [[0, 1, 2, 3], [1, 0, 4, 5], [0, 1, 6, 7]]
        t = build(faces)
        e = eid(t, 0, 1)
        self.assertEqual(len(t.edge_faces(e)), 3)
        edges, _ = t.edge_loop(e)
        self.assertEqual(edges, [e])
        chain, _ = t.edge_ring(e)
        self.assertEqual(chain, [(E, e)])
        # ring from a side edge stops at the non-manifold edge
        chain, _ = t.edge_ring(eid(t, 1, 2))
        self.assertEqual([i for k, i in chain if k == E], [eid(t, 1, 2), eid(t, 3, 0)])

    def test_bowtie_vertex(self):
        # two fans touching at vertex 4 (valence 4, but not one closed fan)
        faces = [[0, 1, 4], [1, 2, 4], [4, 5, 6], [4, 6, 7]]
        t = build(faces)
        e = eid(t, 1, 4)
        self.assertEqual(t.loop_next(e, 4), -1)

    def test_mobius_terminates(self):
        n = 9
        top = list(range(n))
        bot = list(range(n, 2 * n))
        faces = []
        for i in range(n - 1):
            faces.append([top[i], top[i + 1], bot[i + 1], bot[i]])
        faces.append([top[n - 1], bot[0], top[0], bot[n - 1]])   # twisted join
        t = build(faces)
        for e in range(t.num_edges):
            t.edge_loop(e)
            t.edge_ring(e)
            compute_preview(t, EDGE, e)
        for f in range(t.num_faces):
            compute_preview(t, FACE, f)
        for v in range(t.num_vertices):
            compute_preview(t, VERTEX, v)

    def test_isolated_and_invalid(self):
        t = build([[0, 1, 2, 3]], num_vertices=6)      # vertices 4,5 unused
        self.assertTrue(compute_preview(t, VERTEX, 5).is_empty())
        self.assertTrue(compute_preview(t, EDGE, 99).is_empty())
        self.assertTrue(compute_preview(t, FACE, -1).is_empty())

    def test_max_steps(self):
        t = build(grid(200, 2))
        e = eid(t, 201 + 50, 201 + 51)
        edges, _ = t.edge_loop(e, max_steps=10)
        self.assertLessEqual(len(edges), 21)


class TestFuzz(unittest.TestCase):
    def test_random_meshes_never_crash(self):
        rnd = random.Random(1)
        for _ in range(60):
            nv = rnd.randint(4, 30)
            faces = []
            for _ in range(rnd.randint(1, 40)):
                k = min(nv, rnd.choice((3, 4, 4, 4, 5, 6)))
                poly = rnd.sample(range(nv), k)
                faces.append(poly)
            t = build(faces, num_vertices=nv)
            for e in range(t.num_edges):
                compute_preview(t, EDGE, e)
            for v in range(nv):
                compute_preview(t, VERTEX, v)
            for f in range(t.num_faces):
                compute_preview(t, FACE, f)


def benchmark(n=1000):
    faces = grid(n, n)
    counts = [4] * len(faces)
    fv = [v for f in faces for v in f]
    tmp = build(faces[:1])  # warm import
    del tmp
    t0 = time.time()
    t = build(faces)
    t1 = time.time()
    mid = (n // 2) * (n + 1) + n // 2
    p = compute_preview(t, VERTEX, mid)
    t2 = time.time()
    print("benchmark: %d faces / %d edges  build %.2fs  vertex preview %.1fms (%d loop edges, %d ring edges)"
          % (t.num_faces, t.num_edges, t1 - t0, (t2 - t1) * 1000,
             len(p.loop_edges), len(p.ring_edges)))
    del counts, fv


def benchmark_lazy(n=1000):
    """Lazy queries on an n*n grid defined only by arithmetic (no full build),
    standing in for MFnMesh lookups. Shows cost scales with path length."""
    w = n + 1
    H = n * w

    def face_verts(f):
        j, i = divmod(f, n)
        v = j * w + i
        return (v, v + 1, v + 1 + w, v + w)

    def face_edges(f):
        j, i = divmod(f, n)
        return (j * n + i, H + j * w + i + 1, (j + 1) * n + i, H + j * w + i)

    def edge_verts(e):
        if e < H:
            j, i = divmod(e, n)
            return (j * w + i, j * w + i + 1)
        j, i = divmod(e - H, w)
        return (j * w + i, (j + 1) * w + i)

    def edge_faces(e):
        out = []
        if e < H:
            j, i = divmod(e, n)
            if j > 0:
                out.append((j - 1) * n + i)
            if j < n:
                out.append(j * n + i)
        else:
            j, i = divmod(e - H, w)
            if i > 0:
                out.append(j * n + i - 1)
            if i < n:
                out.append(j * n + i)
        return out

    def vert_edges(v):
        j, i = divmod(v, w)
        out = []
        if i > 0:
            out.append(j * n + i - 1)
        if i < n:
            out.append(j * n + i)
        if j > 0:
            out.append(H + (j - 1) * w + i)
        if j < n:
            out.append(H + j * w + i)
        return out

    t = CachedTopology(w * w, H + n * w, n * n, face_verts, face_edges,
                       edge_verts, edge_faces, vert_edges)
    mid = (n // 2) * w + n // 2
    for mode, idx in ((VERTEX, mid), (EDGE, mid), (FACE, (n // 2) * n + n // 2)):
        t0 = time.time()
        p = compute_preview(t, mode, idx)
        dt = (time.time() - t0) * 1000
        print("lazy %-6s on %d faces: %6.1fms  queries=%d  loop=%d ring=%d faces=%d"
              % (mode, n * n, dt, t.queries, len(p.loop_edges),
                 len(p.ring_edges), len(p.loop_faces)))
    t0 = time.time()
    compute_preview(t, VERTEX, mid)
    print("lazy vertex again (warm cache): %.1fms" % ((time.time() - t0) * 1000))


if __name__ == "__main__":
    if "--bench" in sys.argv:
        sys.argv.remove("--bench")
        benchmark()
        benchmark_lazy()
    ok = True
    for lazy in (False, True):
        LAZY = lazy
        print("\n===== %s topology =====" % ("CachedTopology (lazy)" if lazy else "CSR Topology"))
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        res = unittest.TextTestRunner(verbosity=1).run(suite)
        ok = ok and res.wasSuccessful()
    sys.exit(0 if ok else 1)
