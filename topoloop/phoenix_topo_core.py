"""
phoenix_topo_core.py  --  Phoenix Topo Loop Preview, topology core.

Pure Python (no Maya imports) so the loop / ring logic can be unit-tested
outside Maya. The Maya controller feeds it raw mesh arrays; the VP2 draw
plugin reads the shared DRAW slot below.

Definitions (what the preview shows)
------------------------------------
EDGE mode, seed edge E
  loop  (blue)     : the edge loop through E. At each end vertex the walk
                     continues only through an unambiguous vertex:
                       * interior vertex, valence 4, one closed manifold fan
                         -> continue to the edge opposite in the fan
                       * border vertex, valence 3 (2 border edges + 1 inner)
                         while walking a border edge -> continue along border
                     Anything else (poles, corners, non-manifold, lamina)
                     stops the walk.
  ring  (lavender) : the edge ring through E. Crosses each adjacent face to
                     the opposite edge; only quads have an opposite edge, so
                     triangles / n-gons / borders / non-manifold edges stop.
                     A dashed lavender spine links ring-edge midpoints.

VERTEX mode, seed vertex V   (every direction is shown, none chosen)
  The edges around V are grouped into directions (PreviewPaths.groups):
    interior valence 4, one manifold fan -> 2 directions of 2 opposite edges
    border valence 3                     -> border pair + the inner edge
    anything else (poles, corners, ...)  -> one direction per edge, in fan
                                            order (colours alternate)
  Each direction is the loop walk leaving V through its edges.

FACE mode, seed face F       (quads only; both directions shown)
  groups[0], groups[1] : the two face loops (strips of quads) through F,
          each with its faces, the edges it crosses (rungs) and its chain.
  Triangles / n-gons have no opposite edges -> no loops for them.

Ambiguous or invalid topology always terminates the path; nothing is
guessed.
"""
from array import array

__all__ = [
    "DRAW", "UNLOAD_HOOKS", "VERTEX", "EDGE", "FACE", "E", "F",
    "TopologyError", "TopologyBase", "Topology", "CachedTopology",
    "PreviewPaths", "compute_preview",
]

# --------------------------------------------------------------------------
# Shared state between the controller (writer) and the VP2 plugin (reader).
# The payload is an opaque object holding MPointArrays / colours.
# --------------------------------------------------------------------------
VERSION = "1.2.0"          # must match phoenix_topo_preview.py and phoenixTopoPreview.py
PAYLOAD_FORMAT = 2        # 2 = DrawPayload.layers
DRAW = {"payload": None}
UNLOAD_HOOKS = []

VERTEX, EDGE, FACE = "vertex", "edge", "face"
E, F = 0, 1                      # chain element kinds
DEFAULT_MAX_STEPS = 20000


class TopologyError(Exception):
    pass


def _izeros(n, fill=0):
    return array("i", [fill]) * n


class TopologyBase(object):
    """Path algorithms. Subclasses provide five local adjacency queries:

        face_verts(f) face_edges(f) edge_verts(e) edge_faces(e) vert_edges(v)

    plus num_vertices / num_edges / num_faces. Nothing here ever iterates
    the whole mesh, so cost is proportional to the length of the path.
    """
    num_vertices = num_edges = num_faces = 0

    def face_verts(self, f):
        raise NotImplementedError

    def face_edges(self, f):
        raise NotImplementedError

    def edge_verts(self, e):
        raise NotImplementedError

    def edge_faces(self, e):
        raise NotImplementedError

    def vert_edges(self, v):
        raise NotImplementedError

    def valid(self, mode, index):
        n = {VERTEX: self.num_vertices, EDGE: self.num_edges,
             FACE: self.num_faces}.get(mode, 0)
        return 0 <= index < n

    # ---------------------------------------------------- local predicates
    def is_quad(self, f):
        fe = self.face_edges(f)
        fv = self.face_verts(f)
        return len(fe) == 4 and len(set(fe)) == 4 and len(set(fv)) == 4

    def quad_opposite(self, f, e):
        """Edge of quad f sharing no vertex with e, or -1."""
        if not self.is_quad(f):
            return -1
        fe = self.face_edges(f)
        if e not in fe:
            return -1
        a, b = self.edge_verts(e)
        found = -1
        for x in fe:
            if x == e:
                continue
            c, d = self.edge_verts(x)
            if c != a and c != b and d != a and d != b:
                if found != -1:
                    return -1
                found = x
        return found

    def other_face(self, e, f):
        fs = self.edge_faces(e)
        if len(fs) != 2 or fs[0] == fs[1]:
            return -1
        if fs[0] == f:
            return fs[1]
        if fs[1] == f:
            return fs[0]
        return -1

    def _face_edge_at_vertex(self, f, v, exclude):
        found = -1
        for x in self.face_edges(f):
            if x == exclude:
                continue
            a, b = self.edge_verts(x)
            if a == v or b == v:
                if found != -1:
                    return -1          # vertex used twice by face: degenerate
                found = x
        return found

    def fan(self, v, e):
        """Walk the face fan around v starting at incident edge e.

        Returns (ordered_edges, closed) or (None, False) when the fan is
        not a single manifold fan.
        """
        fs = self.edge_faces(e)
        if len(fs) not in (1, 2) or (len(fs) == 2 and fs[0] == fs[1]):
            return None, False
        limit = len(self.vert_edges(v)) + 1
        order = [e]
        seen = {e}
        f = fs[0]
        cur = e
        while True:
            nxt = self._face_edge_at_vertex(f, v, cur)
            if nxt < 0:
                return None, False
            if nxt == e:
                return order, True
            if nxt in seen:
                return None, False
            order.append(nxt)
            seen.add(nxt)
            if len(order) > limit:
                return None, False
            nfs = self.edge_faces(nxt)
            if len(nfs) == 1:
                return order, False
            if len(nfs) != 2:
                return None, False
            f = self.other_face(nxt, f)
            if f < 0:
                return None, False
            cur = nxt

    # --------------------------------------------------------------- loops
    def loop_next(self, e, v):
        """Edge that continues the loop of e through vertex v, or -1."""
        nfaces = len(self.edge_faces(e))
        deg = len(self.vert_edges(v))
        if nfaces == 2:
            if deg != 4:
                return -1
            order, closed = self.fan(v, e)
            if order is None or not closed or len(order) != 4:
                return -1
            return order[2]
        if nfaces == 1:
            if deg != 3:
                return -1
            order, closed = self.fan(v, e)
            if order is None or closed or len(order) != 3:
                return -1
            if len(self.edge_faces(order[2])) != 1:
                return -1
            return order[2]
        return -1

    def _walk_loop(self, e, v, seed, visited, max_steps):
        out = []
        cur, vert = e, v
        while len(out) < max_steps:
            n = self.loop_next(cur, vert)
            if n < 0:
                return out, False
            if n == seed:
                return out, True
            if n in visited:
                return out, False
            a, b = self.edge_verts(n)
            if a == b:
                return out, False
            visited.add(n)
            out.append(n)
            if a == vert:
                vert = b
            elif b == vert:
                vert = a
            else:
                return out, False
            cur = n
        return out, False

    def edge_loop(self, seed, max_steps=DEFAULT_MAX_STEPS):
        """Ordered edge loop through seed -> (edges, closed)."""
        a, b = self.edge_verts(seed)
        if a < 0 or b < 0:
            return [seed], False
        visited = {seed}
        fwd, closed = self._walk_loop(seed, b, seed, visited, max_steps)
        if closed:
            return [seed] + fwd, True
        bwd, _ = self._walk_loop(seed, a, seed, visited, max_steps)
        return list(reversed(bwd)) + [seed] + fwd, False

    def half_loop(self, e, v, max_steps=DEFAULT_MAX_STEPS):
        """Loop leaving vertex v through edge e (e included)."""
        a, b = self.edge_verts(e)
        if a == v:
            far = b
        elif b == v:
            far = a
        else:
            return []
        rest, _ = self._walk_loop(e, far, e, {e}, max_steps)
        return [e] + rest

    def vertex_directions(self, v):
        """Incident edges of v grouped into loop directions (see docstring)."""
        inc = list(self.vert_edges(v))
        if not inc:
            return []
        start = inc[0]
        for e in inc:
            if len(self.edge_faces(e)) == 1:
                start = e                   # open fans must start at a border
                break
        order, closed = self.fan(v, start)
        if order is None or len(order) != len(inc):
            return [[e] for e in inc]       # non-manifold: no reliable pairing
        n = len(order)
        if closed and n == 4:
            return [[order[0], order[2]], [order[1], order[3]]]
        if not closed and n == 3:
            return [[order[0], order[2]], [order[1]]]
        return [[e] for e in order]

    # --------------------------------------------------------------- rings
    def _walk_ring(self, seed, f, visited, max_steps):
        out = []
        e = seed
        steps = 0
        while steps < max_steps:
            opp = self.quad_opposite(f, e)
            if opp < 0:
                return out, False
            out.append((F, f))
            if opp == seed:
                return out, True
            if opp in visited:
                return out, False
            visited.add(opp)
            out.append((E, opp))
            nf = self.other_face(opp, f)
            if nf < 0:
                return out, False
            e, f = opp, nf
            steps += 1
        return out, False

    def edge_ring(self, seed, max_steps=DEFAULT_MAX_STEPS):
        """Ordered chain [(E|F, id), ...] through seed -> (chain, closed).

        Chain alternates edges and the quads between them; F items are the
        face loop, E items the ring edges.
        """
        fs = self.edge_faces(seed)
        if len(fs) == 0 or len(fs) > 2 or (len(fs) == 2 and fs[0] == fs[1]):
            return [(E, seed)], False
        visited = {seed}
        side1, closed = self._walk_ring(seed, fs[0], visited, max_steps)
        if closed:
            return [(E, seed)] + side1, True
        side2 = []
        if len(fs) == 2:
            side2, _ = self._walk_ring(seed, fs[1], visited, max_steps)
        return list(reversed(side2)) + [(E, seed)] + side1, False



class Topology(TopologyBase):
    """Compact CSR adjacency for a polygon mesh.

    face_counts / face_vertices : Maya MFnMesh.getVertices() layout.
    face_edges                  : edge ids per face, same layout.
    edge_vertices               : optional flat [a0,b0,a1,b1,...]. If omitted
                                  it is derived assuming face edge k joins
                                  face vertex k and k+1; inconsistencies are
                                  counted in .conflicts / .unassigned so the
                                  caller can fall back to explicit data.
    """

    def __init__(self, num_vertices, num_edges, face_counts, face_vertices,
                 face_edges, edge_vertices=None):
        nv = int(num_vertices)
        ne = int(num_edges)
        counts = array("i", face_counts)
        fv = array("i", face_vertices)
        fe = array("i", face_edges)
        nf = len(counts)

        foff = _izeros(nf + 1)
        total = 0
        for f in range(nf):
            foff[f] = total
            total += counts[f]
        foff[nf] = total
        if len(fv) != total or len(fe) != total:
            raise TopologyError("face arrays do not match face counts")
        if total:
            if min(fv) < 0 or max(fv) >= nv:
                raise TopologyError("face vertex index out of range")
            if min(fe) < 0 or max(fe) >= ne:
                raise TopologyError("face edge index out of range")

        conflicts = 0
        unassigned = 0
        if edge_vertices is None:
            ev = _izeros(2 * ne, -1)
            for f in range(nf):
                s = foff[f]
                n = foff[f + 1] - s
                if n < 2:
                    continue
                last = s + n - 1
                for i in range(s, s + n):
                    e2 = 2 * fe[i]
                    a = fv[i]
                    b = fv[i + 1] if i < last else fv[s]
                    ea = ev[e2]
                    if ea == -1:
                        ev[e2] = a
                        ev[e2 + 1] = b
                    else:
                        eb = ev[e2 + 1]
                        if not ((ea == a and eb == b) or (ea == b and eb == a)):
                            conflicts += 1
            for e in range(ne):
                if ev[2 * e] == -1:
                    unassigned += 1
        else:
            ev = array("i", edge_vertices)
            if len(ev) != 2 * ne:
                raise TopologyError("edge_vertices must have 2 * num_edges items")

        # edge -> faces
        efoff = _izeros(ne + 1)
        for e in fe:
            efoff[e + 1] += 1
        for e in range(ne):
            efoff[e + 1] += efoff[e]
        ef = _izeros(total)
        cur = array("i", efoff)
        for f in range(nf):
            for i in range(foff[f], foff[f + 1]):
                e = fe[i]
                ef[cur[e]] = f
                cur[e] += 1

        # vertex -> edges
        veoff = _izeros(nv + 1)
        for e in range(ne):
            a = ev[2 * e]
            b = ev[2 * e + 1]
            if a >= 0:
                veoff[a + 1] += 1
            if b >= 0 and b != a:
                veoff[b + 1] += 1
        for v in range(nv):
            veoff[v + 1] += veoff[v]
        ve = _izeros(veoff[nv])
        cur = array("i", veoff)
        for e in range(ne):
            a = ev[2 * e]
            b = ev[2 * e + 1]
            if a >= 0:
                ve[cur[a]] = e
                cur[a] += 1
            if b >= 0 and b != a:
                ve[cur[b]] = e
                cur[b] += 1

        self.num_vertices = nv
        self.num_edges = ne
        self.num_faces = nf
        self._foff, self._fv, self._fe = foff, fv, fe
        self._ev = ev
        self._efoff, self._ef = efoff, ef
        self._veoff, self._ve = veoff, ve
        self.conflicts = conflicts
        self.unassigned = unassigned

    # ---------------------------------------------------------- accessors
    def face_verts(self, f):
        return self._fv[self._foff[f]:self._foff[f + 1]]

    def face_edges(self, f):
        return self._fe[self._foff[f]:self._foff[f + 1]]

    def edge_verts(self, e):
        return self._ev[2 * e], self._ev[2 * e + 1]

    def edge_faces(self, e):
        return self._ef[self._efoff[e]:self._efoff[e + 1]]

    def vert_edges(self, v):
        return self._ve[self._veoff[v]:self._veoff[v + 1]]

class CachedTopology(TopologyBase):
    """Lazy topology: answers each query through a callable (e.g. Maya
    MFnMesh / iterator lookups) and memoises the result. Used inside Maya
    so a dense mesh is never scanned as a whole; only the components a
    path actually touches are ever queried.
    """

    def __init__(self, num_vertices, num_edges, num_faces, face_verts,
                 face_edges, edge_verts, edge_faces, vert_edges,
                 max_cache=4000000):
        self.num_vertices = int(num_vertices)
        self.num_edges = int(num_edges)
        self.num_faces = int(num_faces)
        self._q = (face_verts, face_edges, edge_verts, edge_faces, vert_edges)
        self._c = ({}, {}, {}, {}, {})
        self._max = max_cache
        self.queries = 0

    def _get(self, slot, key):
        cache = self._c[slot]
        r = cache.get(key)
        if r is None:
            if len(cache) > self._max:
                cache.clear()
            r = tuple(self._q[slot](key))
            cache[key] = r
            self.queries += 1
        return r

    def face_verts(self, f):
        return self._get(0, f)

    def face_edges(self, f):
        return self._get(1, f)

    def edge_verts(self, e):
        return self._get(2, e)

    def edge_faces(self, e):
        return self._get(3, e)

    def vert_edges(self, v):
        return self._get(4, v)

    def cache_size(self):
        return sum(len(c) for c in self._c)


class PreviewPaths(object):
    __slots__ = ("mode", "seed", "loop_edges", "loop_chains", "ring_edges",
                 "ring_chains", "loop_faces", "groups")

    def __init__(self, mode, seed):
        self.mode = mode
        self.seed = seed
        self.loop_edges = set()     # drawn as edges, loop colour
        self.loop_chains = []       # [(chain, closed)] face-loop spines, loop colour
        self.ring_edges = set()     # drawn as edges, ring colour
        self.ring_chains = []       # [(chain, closed)] ring spines, ring colour
        self.loop_faces = set()     # faces in face loops (info / tests)
        # per-direction groups (vertex / face mode), drawn in alternating
        # primary / secondary colour:
        #   vertex: {"edges": set}
        #   face  : {"faces": set, "rungs": set, "chain": (chain, closed)}
        self.groups = []

    def is_empty(self):
        return not (self.loop_edges or self.loop_chains or self.ring_edges)


def _chain_ids(chain, kind):
    return [i for k, i in chain if k == kind]


def compute_preview(topo, mode, index, max_steps=DEFAULT_MAX_STEPS):
    """Return PreviewPaths for a seed component (see module docstring)."""
    p = PreviewPaths(mode, index)
    if not topo.valid(mode, index):
        return p

    if mode == EDGE:
        edges, _ = topo.edge_loop(index, max_steps)
        p.loop_edges.update(edges)
        chain, closed = topo.edge_ring(index, max_steps)
        p.ring_edges.update(_chain_ids(chain, E))
        if len(chain) > 1:
            p.ring_chains.append((chain, closed))

    elif mode == VERTEX:
        for direction in topo.vertex_directions(index):
            edges = set()
            for e in direction:
                edges.update(topo.half_loop(e, index, max_steps))
            p.groups.append({"edges": edges})
        seen_rings = set()
        for e in topo.vert_edges(index):
            p.loop_edges.update(topo.half_loop(e, index, max_steps))
            chain, closed = topo.edge_ring(e, max_steps)
            ids = _chain_ids(chain, E)
            key = frozenset(ids)
            if key in seen_rings:
                continue
            seen_rings.add(key)
            p.ring_edges.update(ids)
            if len(chain) > 1:
                p.ring_chains.append((chain, closed))

    elif mode == FACE:
        if topo.is_quad(index):
            fe = list(topo.face_edges(index))
            e0 = fe[0]
            a, b = topo.edge_verts(e0)
            e1 = -1
            for x in fe[1:]:
                c, d = topo.edge_verts(x)
                if c in (a, b) or d in (a, b):
                    e1 = x
                    break
            for d in (e0, e1):
                if d < 0:
                    continue
                chain, closed = topo.edge_ring(d, max_steps)
                faces = _chain_ids(chain, F)
                if not faces:
                    continue
                p.loop_chains.append((chain, closed))
                p.loop_faces.update(faces)
                rungs = _chain_ids(chain, E)
                p.ring_edges.update(rungs)
                p.groups.append({"faces": set(faces), "rungs": set(rungs),
                                 "chain": (chain, closed)})
    return p
