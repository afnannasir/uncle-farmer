/* The map searches of atlaslib.MapModel, in C.
 *
 * Every function here is a line-for-line copy of a Python method in unclefarmer/atlaslib.py, and
 * returns exactly what that method returns: the same costs, the same paths, the same cells. The
 * searches pop their heaps in the same order as Python's heapq (entries compare as the same
 * tuples, (cost, cell) or (f, guess, state), and no two entries are equal), and every cost is the
 * same sum of doubles in the same order, so even ties between equal-cost paths are broken the
 * same way. tests/test_native.py checks that against the Python code on real maps.
 *
 * The grids are the model's flat byte strings (walk, clear, gate, portal, oneway, directional),
 * indexed y * W + x, with a void border round the map. ctypes calls these with the GIL released,
 * so the bot's other threads keep running while a long search runs; nothing here keeps state
 * between calls.
 *
 * Built by build_native.py into _routing.dll beside this file. The DLL carries a hash of this
 * source, and unclefarmer/native/__init__.py only loads a DLL built from the source beside it.
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

#ifndef UF_SOURCE_HASH
#define UF_SOURCE_HASH "unknown"
#endif

#define UF_ABI 3

/* return codes shared by every search */
#define UF_NOMEM (-1)
#define UF_SMALL (-2)          /* the caller's output buffer is too small */

EXPORT int uf_abi(void) { return UF_ABI; }

EXPORT const char *uf_source_hash(void) { return UF_SOURCE_HASH; }

/* ------------------------------------------------------------------ heaps
 * Binary min-heaps that order their entries exactly as Python compares the tuples heapq holds (the
 * order things come off a heap depends only on that, not on the heap's shape). A 4-ary heap was
 * measured no faster on the big maps (dijkstra 27.6 against 23.2 ms on 1041). */
#define ARITY 2

typedef struct { double d; int32_t c; } CellEntry;                 /* (d, c) */
typedef struct { double f; double h; int32_t st; } StateEntry;     /* (f, guess, state) */

typedef struct { CellEntry *a; size_t n, cap; } CellHeap;
typedef struct { StateEntry *a; size_t n, cap; } StateHeap;

static int cell_less(const CellEntry *x, const CellEntry *y)
{
    if (x->d != y->d) return x->d < y->d;
    return x->c < y->c;
}

static int state_less(const StateEntry *x, const StateEntry *y)
{
    if (x->f != y->f) return x->f < y->f;
    if (x->h != y->h) return x->h < y->h;
    return x->st < y->st;
}

static int cell_push(CellHeap *hp, double d, int32_t c)
{
    if (hp->n == hp->cap) {
        size_t cap = hp->cap ? hp->cap * 2 : 1024;
        CellEntry *a = (CellEntry *)realloc(hp->a, cap * sizeof *a);
        if (!a) return 0;
        hp->a = a;
        hp->cap = cap;
    }
    size_t i = hp->n++;
    CellEntry e = {d, c};
    while (i) {
        size_t p = (i - 1) / ARITY;
        if (!cell_less(&e, &hp->a[p])) break;
        hp->a[i] = hp->a[p];
        i = p;
    }
    hp->a[i] = e;
    return 1;
}

static CellEntry cell_pop(CellHeap *hp)
{
    CellEntry top = hp->a[0];
    CellEntry last = hp->a[--hp->n];
    size_t i = 0, n = hp->n;
    for (;;) {
        size_t l = ARITY * i + 1;
        if (l >= n) break;
        size_t m = l, end = l + ARITY < n ? l + ARITY : n;
        for (size_t k = l + 1; k < end; k++)
            if (cell_less(&hp->a[k], &hp->a[m])) m = k;
        if (!cell_less(&hp->a[m], &last)) break;
        hp->a[i] = hp->a[m];
        i = m;
    }
    if (n) hp->a[i] = last;
    return top;
}

static int state_push(StateHeap *hp, double f, double h, int32_t st)
{
    if (hp->n == hp->cap) {
        size_t cap = hp->cap ? hp->cap * 2 : 1024;
        StateEntry *a = (StateEntry *)realloc(hp->a, cap * sizeof *a);
        if (!a) return 0;
        hp->a = a;
        hp->cap = cap;
    }
    size_t i = hp->n++;
    StateEntry e = {f, h, st};
    while (i) {
        size_t p = (i - 1) / ARITY;
        if (!state_less(&e, &hp->a[p])) break;
        hp->a[i] = hp->a[p];
        i = p;
    }
    hp->a[i] = e;
    return 1;
}

static StateEntry state_pop(StateHeap *hp)
{
    StateEntry top = hp->a[0];
    StateEntry last = hp->a[--hp->n];
    size_t i = 0, n = hp->n;
    for (;;) {
        size_t l = ARITY * i + 1;
        if (l >= n) break;
        size_t m = l, end = l + ARITY < n ? l + ARITY : n;
        for (size_t k = l + 1; k < end; k++)
            if (state_less(&hp->a[k], &hp->a[m])) m = k;
        if (!state_less(&hp->a[m], &last)) break;
        hp->a[i] = hp->a[m];
        i = m;
    }
    if (n) hp->a[i] = last;
    return top;
}

/* ------------------------------------------------------------ queues
 * Every step costs 1 + a clearance penalty (+ TURN in the route search), and with the game's
 * numbers (penalties 4, 1.5, 0.5, TURN 12) every cost is a whole number of half-steps. So the
 * searches keep their open entries in buckets by cost in half-steps (Dial's algorithm) instead of
 * one big heap: a pop costs a few instructions instead of a walk down a heap of thousands. Inside
 * a bucket the entries come out in the order Python's heapq gives equal costs - by cell for
 * dijkstra, by (guess, state) for the route search - so the searches settle the same cells in the
 * same order as before. Costs that are not whole half-steps (if PEN or TURN ever change) use the
 * heaps above. */

#define NB_CELL 16             /* > the largest step in half-steps: 2 * (1 + 4.0) = 10 */
#define NB_STATE 128           /* > the largest rise of f in half-steps: 2 * (17 + 1 + 24) = 84 */
#define UF_RETRY (-3)          /* the bucket route search met a cost it can't bucket: use the heap */

typedef struct { int32_t *a; size_t n, cap; } I32Vec;
typedef struct { uint64_t *a; size_t n, cap; } U64Vec;

static int i32_push(I32Vec *v, int32_t x)
{
    if (v->n == v->cap) {
        size_t cap = v->cap ? v->cap * 2 : 256;
        int32_t *a = (int32_t *)realloc(v->a, cap * sizeof *a);
        if (!a) return 0;
        v->a = a;
        v->cap = cap;
    }
    v->a[v->n++] = x;
    return 1;
}

static int u64_push(U64Vec *v, uint64_t x)
{
    if (v->n == v->cap) {
        size_t cap = v->cap ? v->cap * 2 : 256;
        uint64_t *a = (uint64_t *)realloc(v->a, cap * sizeof *a);
        if (!a) return 0;
        v->a = a;
        v->cap = cap;
    }
    v->a[v->n++] = x;
    return 1;
}

static int cmp_i32(const void *x, const void *y)
{
    int32_t a = *(const int32_t *)x, b = *(const int32_t *)y;
    return (a > b) - (a < b);
}

/* min-heaps of uint64 kept in a U64Vec */
static void u64_sift_down(uint64_t *a, size_t n, size_t i)
{
    uint64_t x = a[i];
    for (;;) {
        size_t l = 2 * i + 1;
        if (l >= n) break;
        size_t m = (l + 1 < n && a[l + 1] < a[l]) ? l + 1 : l;
        if (a[m] >= x) break;
        a[i] = a[m];
        i = m;
    }
    a[i] = x;
}

static int u64_heap_push(U64Vec *v, uint64_t x)
{
    if (!u64_push(v, x)) return 0;
    size_t i = v->n - 1;
    while (i) {
        size_t p = (i - 1) / 2;
        if (v->a[p] <= x) break;
        v->a[i] = v->a[p];
        i = p;
    }
    v->a[i] = x;
    return 1;
}

static uint64_t u64_heap_pop(U64Vec *v)
{
    uint64_t top = v->a[0];
    v->a[0] = v->a[--v->n];
    if (v->n) u64_sift_down(v->a, v->n, 0);
    return top;
}

/* twice x as an integer when x is a whole number of half-steps (0 or more), else -1 */
static int64_t half_steps(double x)
{
    double h = x * 2.0;
    if (!(h >= 0.0) || h > 9.0e15) return -1;
    int64_t k = (int64_t)h;
    return (double)k == h ? k : -1;
}

/* true when every dijkstra step lands 1 to NB_CELL - 1 buckets ahead */
static int cell_buckets_ok(const double *pen)
{
    for (int i = 0; i < 16; i++) {
        int64_t s = half_steps(1.0 + pen[i]);
        if (s < 1 || s >= NB_CELL) return 0;
    }
    return 1;
}

/* ------------------------------------------------------------ dijkstra
 * MapModel.dijkstra: wall-avoiding shortest paths from src, stopping once every target is settled
 * or past limit. Reads and writes dist and prev only where mark says it has (mark == 2 * stamp:
 * reached, 2 * stamp + 1: settled; see uf_astar_rect), and wmark == stamp marks the targets still
 * wanted. stats[0] counts the cells settled, stats[1] the pushes. Returns the cells settled, or
 * UF_NOMEM.
 *
 * With buckets, a bucket is complete when its turn comes - every step lands in a later one - so
 * it is sorted by cell once and settled in that order: Python's (cost, cell) order exactly. */
static int dijkstra_run(int32_t W, int32_t n, const uint8_t *walk, const uint8_t *clear,
                        const uint8_t *gate, const uint8_t *direc, const double *pen,
                        int32_t src, const int32_t *targets, int32_t ntargets,
                        int32_t has_limit, double limit, double *dist, int32_t *prev,
                        uint32_t *mark, uint32_t *wmark, uint32_t stamp, int64_t *stats)
{
    const int32_t offs[4] = {1, -1, W, -W};
    const uint8_t bits[4] = {4, 1, 8, 2};
    const uint32_t seen = 2 * stamp, done = 2 * stamp + 1;
    const int buckets = cell_buckets_ok(pen);
    CellHeap heap = {0};
    I32Vec b[NB_CELL];
    I32Vec cur = {0};
    size_t at = 0;                  /* the next entry of cur, the bucket being settled */
    int64_t k = 0, pending = 0, pops = 0, pushes = 0;
    int32_t left = 0, never = 0;
    int ok;
    memset(b, 0, sizeof b);
    for (int32_t i = 0; i < ntargets; i++) {
        int32_t t = targets[i];
        if (t < 0 || t >= n) never = 1;                 /* in Python's set, never settled */
        else if (wmark[t] != stamp) { wmark[t] = stamp; left++; }
    }
    dist[src] = 0.0;
    prev[src] = -1;
    mark[src] = seen;
    ok = buckets ? i32_push(&cur, src) : cell_push(&heap, 0.0, src);
    while (ok) {
        int32_t c;
        double d;
        if (buckets) {
            if (at == cur.n) {                         /* this bucket is settled: the next one */
                if (!pending) break;
                cur.n = 0;
                at = 0;
                do k++; while (!b[k % NB_CELL].n);
                I32Vec next = b[k % NB_CELL];
                b[k % NB_CELL] = cur;                  /* its emptied array is reused */
                cur = next;
                pending -= (int64_t)cur.n;
                qsort(cur.a, cur.n, sizeof(int32_t), cmp_i32);
            }
            c = cur.a[at++];
            if (mark[c] == done) continue;
            d = dist[c];
        } else {
            if (!heap.n) break;
            CellEntry e = cell_pop(&heap);
            c = e.c;
            d = e.d;
            if (mark[c] == done) continue;
        }
        if (has_limit && d > limit) break;
        mark[c] = done;
        pops++;
        if (wmark[c] == stamp) { wmark[c] = stamp - 1; left--; }
        if (ntargets && !left && !never) break;
        uint8_t gc = gate[c];
        for (int j = 0; j < 4 && ok; j++) {
            int32_t nb = c + offs[j];
            if (nb < 0 || nb >= n || !walk[nb] || (gc & bits[j])) continue;
            uint32_t mk = mark[nb];
            if (mk == done) continue;
            if (direc && direc[nb]) continue;
            double nd = d + 1.0 + pen[clear[nb]];
            if (mk != seen || nd < dist[nb]) {
                dist[nb] = nd;
                prev[nb] = c;
                mark[nb] = seen;
                pushes++;
                if (buckets) {
                    ok = i32_push(&b[half_steps(nd) % NB_CELL], nb);
                    pending++;
                } else ok = cell_push(&heap, nd, nb);
            }
        }
    }
    free(heap.a);
    free(cur.a);
    for (int i = 0; i < NB_CELL; i++) free(b[i].a);
    if (stats) { stats[0] = pops; stats[1] = pushes; }
    return ok ? (int)pops : UF_NOMEM;
}

/* The whole search out: dist (n doubles) and prev (n int32), INFINITY / -2 for a cell it never
 * touched - a cell missing from Python's dicts. */
EXPORT int uf_dijkstra(int32_t W, int32_t n, const uint8_t *walk, const uint8_t *clear,
                       const uint8_t *gate, const uint8_t *direc, const double *pen,
                       int32_t src, const int32_t *targets, int32_t ntargets,
                       int32_t has_limit, double limit, double *dist, int32_t *prev,
                       int64_t *stats)
{
    uint32_t *mark = (uint32_t *)calloc((size_t)n, sizeof(uint32_t));
    uint32_t *wmark = (uint32_t *)calloc((size_t)n, sizeof(uint32_t));
    int r = UF_NOMEM;
    if (mark && wmark) {
        r = dijkstra_run(W, n, walk, clear, gate, direc, pen, src, targets, ntargets, has_limit,
                         limit, dist, prev, mark, wmark, 1, stats);
        for (int32_t i = 0; i < n; i++)
            if (!mark[i]) { dist[i] = INFINITY; prev[i] = -2; }
    }
    free(mark);
    free(wmark);
    return r;
}

/* Only the targets' distances out (out[i], INFINITY when not reached), on the caller's kept
 * workspace: a search that stops after a few hundred cells costs those, not the whole map. */
EXPORT int uf_dijkstra_to(int32_t W, int32_t n, const uint8_t *walk, const uint8_t *clear,
                          const uint8_t *gate, const uint8_t *direc, const double *pen,
                          int32_t src, const int32_t *targets, int32_t ntargets,
                          int32_t has_limit, double limit, double *dist, int32_t *prev,
                          uint32_t *mark, uint32_t *wmark, uint32_t stamp, double *out,
                          int64_t *stats)
{
    const uint32_t seen = 2 * stamp, done = 2 * stamp + 1;
    int r = dijkstra_run(W, n, walk, clear, gate, direc, pen, src, targets, ntargets, has_limit,
                         limit, dist, prev, mark, wmark, stamp, stats);
    for (int32_t i = 0; i < ntargets; i++) {
        int32_t t = targets[i];
        out[i] = (t >= 0 && t < n && (mark[t] == seen || mark[t] == done)) ? dist[t] : INFINITY;
    }
    return r;
}

/* ------------------------------------------------------------ astar_rect
 * MapModel.astar_rect + path_rect: the cheapest (cell, heading) route from src to the nearest
 * target, paying TURN for every turn, never stepping on an exit cell except a target or the exit
 * the route starts in (own rectangle; ox1 > ox2 for none). The guess is _astar_guess over the
 * targets' bounding box. The path's cells go into out (cap int32); returns the path length, 0 when
 * no target can be reached, UF_NOMEM, UF_SMALL, or UF_RETRY (with heap 0 only: a cost the buckets
 * can't hold - search again with heap 1 and a new stamp). *reached_cell gets the target reached.
 * stats[0] counts the states settled, stats[1] the pushes.
 *
 * The caller's workspace is kept between searches, so none of it is cleared (or faulted into
 * memory) per search: dist and prev (4n each) are only read where mark (4n) says this search wrote
 * them - mark == 2 * stamp: reached, 2 * stamp + 1: settled - and wmark (n) == stamp marks the
 * targets. The caller gives every search a new stamp (1 .. 2^31 - 2) and zeroes the marks before
 * the stamps start over. A short route costs what it looks at, not the size of the map.
 *
 * With buckets (heap 0) the open states sit in buckets by f in half-steps. A step can leave f as
 * it was (one step nearer, one less to guess), so the bucket being settled is a heap of (guess,
 * state) - both in one uint64 - and later buckets are plain arrays, made a heap when their turn
 * comes. The guess never falls by more than a step costs, so f never falls; a step that would
 * returns UF_RETRY rather than settle a state out of order. */

static double astar_guess(int32_t c, int d, int32_t W, double turn,
                          int32_t bx1, int32_t bx2, int32_t by1, int32_t by2)
{
    int32_t x = c % W, y = c / W;
    int32_t togo = 0;
    int hx = -1, hy = -1;            /* headings 0 east, 1 west, 2 south, 3 north */
    if (x < bx1) { togo = bx1 - x; hx = 0; }
    else if (x > bx2) { togo = x - bx2; hx = 1; }
    if (y < by1) { togo = togo + by1 - y; hy = 2; }
    else if (y > by2) { togo = togo + y - by2; hy = 3; }
    int ways = (hx >= 0) + (hy >= 0);
    if (!ways) return 0.0;
    int k = (d == hx || d == hy) ? ways - 1 : ways;
    return (double)togo + turn * (double)k;
}

typedef struct {
    int heap_mode;
    StateHeap heap;            /* heap_mode: the plain heap */
    U64Vec cur;                /* buckets: the bucket being settled, a heap of (guess, state) */
    U64Vec b[NB_STATE];        /* buckets: the later ones, by f in half-steps */
    int64_t k, pending;
} StateQueue;

/* 1 pushed, 0 out of memory, UF_RETRY a cost the buckets can't hold */
static int sq_push(StateQueue *q, double f, double g, int32_t st)
{
    if (q->heap_mode) return state_push(&q->heap, f, g, st);
    int64_t f2 = half_steps(f), g2 = half_steps(g);
    if (f2 < 0 || g2 < 0 || g2 > 0xFFFFFFFFLL || f2 < q->k || f2 - q->k >= NB_STATE) return UF_RETRY;
    uint64_t key = ((uint64_t)g2 << 32) | (uint32_t)st;
    if (f2 == q->k) return u64_heap_push(&q->cur, key);
    q->pending++;
    return u64_push(&q->b[f2 % NB_STATE], key);
}

/* the next state, or -1 when none is left */
static int32_t sq_pop(StateQueue *q)
{
    if (q->heap_mode) return q->heap.n ? state_pop(&q->heap).st : -1;
    if (!q->cur.n) {
        if (!q->pending) return -1;
        do q->k++; while (!q->b[q->k % NB_STATE].n);
        U64Vec next = q->b[q->k % NB_STATE];
        q->b[q->k % NB_STATE] = q->cur;            /* its empty array is reused */
        q->cur = next;
        q->pending -= (int64_t)next.n;
        for (size_t i = next.n / 2; i-- > 0;) u64_sift_down(q->cur.a, q->cur.n, i);
    }
    return (int32_t)(uint32_t)u64_heap_pop(&q->cur);
}

static void sq_free(StateQueue *q)
{
    free(q->heap.a);
    free(q->cur.a);
    for (int i = 0; i < NB_STATE; i++) free(q->b[i].a);
}

EXPORT int uf_astar_rect(int32_t W, int32_t n, const uint8_t *walk, const uint8_t *clear,
                         const uint8_t *gate, const uint8_t *direc, const uint8_t *portal,
                         const double *pen, double turn, int32_t src,
                         const int32_t *targets, int32_t ntargets,
                         int32_t bx1, int32_t bx2, int32_t by1, int32_t by2,
                         int32_t ox1, int32_t oy1, int32_t ox2, int32_t oy2,
                         double *dist, int32_t *prev, uint32_t *mark, uint32_t *wmark,
                         uint32_t stamp, int32_t heap, int32_t *out, int32_t cap,
                         int32_t *reached_cell, int64_t *stats)
{
    const int32_t offs[4] = {1, -1, W, -W};
    const uint8_t bits[4] = {4, 1, 8, 2};
    const uint32_t seen = 2 * stamp, done = 2 * stamp + 1;
    StateQueue *q = (StateQueue *)calloc(1, sizeof(StateQueue));
    int64_t pops = 0, pushes = 0;
    int32_t found = -1;
    int r = 1, result = 0;
    double g0[4];
    *reached_cell = -1;
    if (!q) return UF_NOMEM;
    q->heap_mode = heap != 0;
    for (int32_t i = 0; i < ntargets; i++)
        if (targets[i] >= 0 && targets[i] < n) wmark[targets[i]] = stamp;
    q->k = -1;
    for (int d = 0; d < 4; d++) {
        g0[d] = astar_guess(src, d, W, turn, bx1, bx2, by1, by2);
        int64_t f2 = half_steps(g0[d]);
        if (f2 < 0) q->k = -2;                          /* not in half-steps: no buckets */
        else if (q->k != -2 && (q->k < 0 || f2 < q->k)) q->k = f2;   /* start at the cheapest */
    }
    if (q->k < 0 && !q->heap_mode) { r = UF_RETRY; goto out_free; }
    for (int d = 0; d < 4; d++) {
        int32_t st = src * 4 + d;
        dist[st] = 0.0;
        prev[st] = -1;
        mark[st] = seen;
        if ((r = sq_push(q, g0[d], g0[d], st)) != 1) goto out_free;
    }
    for (;;) {
        int32_t st = sq_pop(q);
        if (st < 0) break;
        if (mark[st] == done) continue;
        mark[st] = done;
        pops++;
        int32_t c = st / 4;
        int d = st % 4;
        if (wmark[c] == stamp) { found = st; break; }
        if (portal[c] && c != src) {
            int32_t x = c % W, y = c / W;
            if (!(x >= ox1 && x <= ox2 && y >= oy1 && y <= oy2)) continue;  /* walk ends on an exit */
        }
        double cost = dist[st];
        for (int nd = 0; nd < 4; nd++) {
            int32_t nb = c + offs[nd];
            if (nb < 0 || nb >= n || !walk[nb] || (gate[c] & bits[nd])) continue;
            if (portal[nb] && wmark[nb] != stamp) {
                int32_t x = nb % W, y = nb / W;
                if (!(x >= ox1 && x <= ox2 && y >= oy1 && y <= oy2)) continue;
            }
            if (direc && direc[nb]) continue;
            int32_t ns = nb * 4 + nd;
            uint32_t mk = mark[ns];
            if (mk == done) continue;
            double nc = cost + 1.0 + pen[clear[nb]] + (nd != d ? turn : 0.0);
            if (mk != seen || nc < dist[ns]) {
                double g = astar_guess(nb, nd, W, turn, bx1, bx2, by1, by2);
                dist[ns] = nc;
                prev[ns] = st;
                mark[ns] = seen;
                pushes++;
                if ((r = sq_push(q, nc + g, g, ns)) != 1) goto out_free;
            }
        }
    }
    if (found >= 0) {
        int32_t len = 0;
        for (int32_t st = found; st != -1; st = prev[st]) len++;
        if (len > cap) { result = UF_SMALL; goto out_free; }
        int32_t i = len;
        for (int32_t st = found; st != -1; st = prev[st]) out[--i] = st / 4;
        *reached_cell = found / 4;
        result = len;
    }
out_free:
    sq_free(q);
    free(q);
    if (stats) { stats[0] = pops; stats[1] = pushes; }
    if (r == 0) return UF_NOMEM;
    if (r == UF_RETRY) return UF_RETRY;
    return result;
}

/* ------------------------------------------------------------ bfs
 * MapModel.steps_arr (one source, gates obeyed), shortloop's covered() (many sources, gate NULL)
 * and two_way's spreads. Layered BFS: dist (n int32) gets the steps, -1 where not reached, and
 * cells at limit are not expanded (limit < 0: no limit). reverse: step c -> nb only if nb could
 * step back onto c (two_way's backward spread). Returns the cells reached, or UF_NOMEM. */
EXPORT int uf_bfs(int32_t W, int32_t n, const uint8_t *walk, const uint8_t *gate,
                  int32_t reverse, const int32_t *srcs, int32_t nsrc, int32_t limit,
                  int32_t *dist)
{
    const int32_t offs[4] = {1, -1, W, -W};
    const uint8_t out_bits[4] = {4, 1, 8, 2};    /* the step c -> c + off */
    const uint8_t back_bits[4] = {1, 4, 2, 8};   /* the step c + off -> c */
    int32_t *q = (int32_t *)malloc((size_t)n * sizeof(int32_t));
    int32_t head = 0, tail = 0;
    if (!q) return UF_NOMEM;
    for (int32_t i = 0; i < n; i++) dist[i] = -1;
    for (int32_t i = 0; i < nsrc; i++) {
        int32_t s = srcs[i];
        if (s >= 0 && s < n && dist[s] < 0) { dist[s] = 0; q[tail++] = s; }
    }
    while (head < tail) {
        int32_t c = q[head++];
        int32_t dc = dist[c];
        if (limit >= 0 && dc >= limit) continue;
        for (int k = 0; k < 4; k++) {
            int32_t nb = c + offs[k];
            if (nb < 0 || nb >= n || !walk[nb] || dist[nb] >= 0) continue;
            if (gate) {
                if (reverse ? (gate[nb] & back_bits[k]) : (gate[c] & out_bits[k])) continue;
            }
            dist[nb] = dc + 1;
            q[tail++] = nb;
        }
    }
    free(q);
    return tail;
}

/* ------------------------------------------------------------ components
 * MapModel._components: 4-connected regions of walkable cells, numbered 1, 2, ... in the order of
 * their first cell. comp (n int32) gets each cell's region (0 = not walkable); sizes gets each
 * region's size from index 1 (cap entries). Returns the number of regions, UF_NOMEM or UF_SMALL. */
EXPORT int uf_components(int32_t W, int32_t n, const uint8_t *walk, int32_t *comp,
                         int32_t *sizes, int32_t cap)
{
    const int32_t offs[4] = {1, -1, W, -W};
    int32_t *stack = (int32_t *)malloc((size_t)n * sizeof(int32_t));
    int32_t cid = 0;
    if (!stack) return UF_NOMEM;
    memset(comp, 0, (size_t)n * sizeof(int32_t));
    for (int32_t s = 0; s < n; s++) {
        if (!walk[s] || comp[s]) continue;
        if (++cid >= cap) { free(stack); return UF_SMALL; }
        comp[s] = cid;
        int32_t top = 0, size = 0;
        stack[top++] = s;
        while (top) {
            int32_t c = stack[--top];
            size++;
            for (int k = 0; k < 4; k++) {
                int32_t nb = c + offs[k];
                if (nb >= 0 && nb < n && walk[nb] && !comp[nb]) {
                    comp[nb] = cid;
                    stack[top++] = nb;
                }
            }
        }
        sizes[cid] = size;
    }
    free(stack);
    return cid;
}

/* ------------------------------------------------------------ lines
 * MapModel.ok, gate_blocks, line_ok, diag_ok and diag_hops. */

typedef struct {
    int32_t W, n, w, h;
    const uint8_t *walk, *clear, *gate, *portal, *direc;
} Grid;

static int grid_ok(const Grid *g, int32_t x, int32_t y)
{
    return x >= 1 && x <= g->w && y >= 1 && y <= g->h && g->walk[y * g->W + x];
}

static uint8_t grid_gate(const Grid *g, int32_t x, int32_t y)
{
    int32_t c = y * g->W + x;
    return (x >= 0 && x < g->W && c >= 0 && c < g->n) ? g->gate[c] : 0;
}

static int gate_blocks(const Grid *g, int32_t ax, int32_t ay, int32_t bx, int32_t by)
{
    uint8_t gt = grid_gate(g, ax, ay);
    if (!gt) return 0;
    int32_t dx = bx - ax, dy = by - ay;
    uint8_t bit = dx > 0 ? 4 : dx < 0 ? 1 : dy > 0 ? 8 : 2;
    return (gt & bit) != 0;
}

/* one step a -> b of diag_ok's walk to q: open ground, no ledge, no gate, no exit before q */
static int diag_step_ok(const Grid *g, int32_t ax, int32_t ay, int32_t bx, int32_t by,
                        int32_t qx, int32_t qy)
{
    if (!grid_ok(g, bx, by) || g->direc[by * g->W + bx] || gate_blocks(g, ax, ay, bx, by))
        return 0;
    if (!(bx == qx && by == qy) && g->portal[by * g->W + bx]) return 0;
    return 1;
}

static int diag_ok(const Grid *g, int32_t px, int32_t py, int32_t qx, int32_t qy)
{
    int32_t x = px, y = py;
    int32_t sx = qx > x ? 1 : -1, sy = qy > y ? 1 : -1;
    while (x != qx && y != qy) {
        int32_t hx = x + sx, hy = y, vx = x, vy = y + sy, dx = x + sx, dy = y + sy;
        if (!diag_step_ok(g, x, y, hx, hy, qx, qy) || !diag_step_ok(g, hx, hy, dx, dy, qx, qy)
            || !diag_step_ok(g, x, y, vx, vy, qx, qy) || !diag_step_ok(g, vx, vy, dx, dy, qx, qy))
            return 0;
        x = dx;
        y = dy;
    }
    while (x != qx) {
        if (!diag_step_ok(g, x, y, x + sx, y, qx, qy)) return 0;
        x += sx;
    }
    while (y != qy) {
        if (!diag_step_ok(g, x, y, x, y + sy, qx, qy)) return 0;
        y += sy;
    }
    return 1;
}

EXPORT int uf_diag_ok(int32_t W, int32_t n, int32_t w, int32_t h, const uint8_t *walk,
                      const uint8_t *gate, const uint8_t *portal, const uint8_t *direc,
                      int32_t px, int32_t py, int32_t qx, int32_t qy)
{
    Grid g = {W, n, w, h, walk, NULL, gate, portal, direc};
    return diag_ok(&g, px, py, qx, qy);
}

/* diag_hops: path is npts (x, y) pairs; out gets the index of every hop's end point. Returns
 * the number of hops. */
EXPORT int uf_diag_hops(int32_t W, int32_t n, int32_t w, int32_t h, const uint8_t *walk,
                        const uint8_t *gate, const uint8_t *portal, const uint8_t *direc,
                        const int32_t *path, int32_t npts, int32_t reach, int32_t stride,
                        int32_t *out)
{
    Grid g = {W, n, w, h, walk, NULL, gate, portal, direc};
    int32_t i = 0, k = 0;
    while (i < npts - 1) {
        int32_t j = npts - 1 < i + reach ? npts - 1 : i + reach;
        while (j > i + 1 && !diag_ok(&g, path[2 * i], path[2 * i + 1], path[2 * j], path[2 * j + 1]))
            j -= (j - stride > i + 1) ? stride : 1;
        out[k++] = j;
        i = j;
    }
    return k;
}

/* line_ok: the 4-connected supercover line from a to b stays on walkable ground with at least
 * min_clear clearance, off ledges unless allowed, and through no one-way gate the wrong way. */
EXPORT int uf_line_ok(int32_t W, int32_t n, int32_t w, int32_t h, const uint8_t *walk,
                      const uint8_t *clear, const uint8_t *gate, const uint8_t *direc,
                      int32_t ax, int32_t ay, int32_t bx, int32_t by, int32_t min_clear,
                      int32_t allow_directional)
{
    Grid g = {W, n, w, h, walk, clear, gate, NULL, direc};
    int32_t x = ax, y = ay;
    int32_t dx = bx > ax ? bx - ax : ax - bx, dy = by > ay ? by - ay : ay - by;
    int32_t sx = bx > x ? 1 : -1, sy = by > y ? 1 : -1;
    int32_t ix = 0, iy = 0, px = 0, py = 0, first = 1;
    for (;;) {
        if (!grid_ok(&g, x, y) || (int32_t)(int8_t)clear[y * W + x] < min_clear) return 0;
        if (!allow_directional && direc[y * W + x]) return 0;
        if (!first && gate_blocks(&g, px, py, x, y)) return 0;
        first = 0;
        px = x;
        py = y;
        if (!(ix < dx || iy < dy)) break;
        /* step along whichever axis the true line crosses next */
        if (iy >= dy || (ix < dx && (int64_t)(1 + 2 * ix) * dy < (int64_t)(1 + 2 * iy) * dx)) {
            x += sx;
            ix++;
        } else {
            y += sy;
            iy++;
        }
    }
    return 1;
}
