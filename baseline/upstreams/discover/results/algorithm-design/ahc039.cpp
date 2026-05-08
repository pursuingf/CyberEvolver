/*
 * Heuristic solver for “Mackerel vs Sardine’’ (improved version).
 *
 * – Build a large pool of promising axis‑aligned rectangles.
 * – Greedy initial solution.
 * – Simulated annealing with a rich set of moves.
 * – Clean‑up and final greedy addition.
 *
 * The program follows the algorithm described in the editorial above.
 *
 * Compile with: g++ -std=c++20 -O2 -pipe -static -s -o solver solver.cpp
 */

#pragma GCC optimize("O3")
#include <bits/stdc++.h>
using namespace std;
using ll = long long;

constexpr int MAXC = 100000;          // coordinate bound
constexpr int MAX_VERT = 1000;        // max vertices of output polygon
constexpr ll  MAX_PERIM = 400000LL;   // max perimeter
constexpr int KMAX = 900;             // max rectangles kept (soft)
constexpr int POOL_TARGET = 600000;   // target size of rectangle pool
constexpr int EXPAND_ATTEMPTS = 30;   // trials for expand/shrink moves
constexpr int SLIDE_MAX = 1000;       // max slide distance
constexpr double SAFETY = 0.001;      // safety margin for TL
constexpr double INIT_T = 300.0;      // start temperature
constexpr double FINAL_T = 0.005;     // final temperature

/*--------------------------- fast RNG ---------------------------*/
struct FastRNG {
    uint64_t x;
    FastRNG() {
        x = chrono::steady_clock::now().time_since_epoch().count()
            ^ (uint64_t)random_device{}();
        if (!x) x = 88172645463325252ULL;
    }
    uint64_t next() {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        return x;
    }
    int nextInt(int lo, int hi) {            // inclusive
        return lo + int(next() % uint64_t(hi - lo + 1));
    }
    double nextDouble() {                    // [0,1)
        return (next() & ((1ULL << 53) - 1)) / double(1ULL << 53);
    }
} rng;

/*--------------------------- data ------------------------------*/
struct Fish { int x, y, w; };                 // w = +1 (mackerel) or -1 (sardine)
vector<Fish> fish;

/* coordinate compression */
vector<int> xs, ys;
int Nx = 0, Ny = 0;
vector<int> pref;                            // (Nx+1)*(Ny+1) prefix sums
inline size_t prefIdx(int i, int j) { return (size_t)i * (Ny + 1) + j; }

/* fast index tables */
vector<int> lowerXIdx, upperXIdx, lowerYIdx, upperYIdx;

/* inclusive rectangle weight query O(1) */
inline int weightRect(int L, int R, int B, int T) {
    int iL = lowerXIdx[L];
    int iR = upperXIdx[R];
    int iB = lowerYIdx[B];
    int iT = upperYIdx[T];
    if (iL > iR || iB > iT) return 0;
    int a = pref[prefIdx(iR + 1, iT + 1)];
    int b = pref[prefIdx(iL    , iT + 1)];
    int c = pref[prefIdx(iR + 1, iB    )];
    int d = pref[prefIdx(iL    , iB    )];
    return a - b - c + d;
}

/*--------------------------- rectangle ------------------------*/
struct SimpleRect { int L, R, B, T; };          // inclusive, L<R, B<T

inline bool rectTouches(const SimpleRect& a, const SimpleRect& b) {
    bool overlap = (a.L < b.R && b.L < a.R && a.B < b.T && b.B < a.T);
    if (overlap) return true;
    if ((a.R == b.L || b.R == a.L) && max(a.B, b.B) < min(a.T, b.T)) return true;
    if ((a.T == b.B || b.T == a.B) && max(a.L, b.L) < min(a.R, b.R)) return true;
    return false;
}

/* current shape */
vector<SimpleRect> curRects;

/* weight of uncovered part of R w.r.t. a set of rectangles */
int uncoveredWeight(const SimpleRect& R, const vector<SimpleRect>& vec) {
    vector<SimpleRect> pieces = {R};
    for (const auto& U : vec) {
        vector<SimpleRect> nxt;
        nxt.reserve(pieces.size() * 2);
        for (auto& p : pieces) {
            SimpleRect I{ max(p.L, U.L), min(p.R, U.R), max(p.B, U.B), min(p.T, U.T) };
            if (I.L > I.R || I.B > I.T) {         // no overlap
                nxt.push_back(p);
                continue;
            }
            // split p into up to four sub‑rectangles (the remainder)
            if (p.L <= I.L - 1) nxt.push_back({p.L, I.L - 1, p.B, p.T});
            if (I.R + 1 <= p.R) nxt.push_back({I.R + 1, p.R, p.B, p.T});
            if (p.B <= I.B - 1) nxt.push_back({I.L, I.R, p.B, I.B - 1});
            if (I.T + 1 <= p.T) nxt.push_back({I.L, I.R, I.T + 1, p.T});
        }
        pieces.swap(nxt);
        if (pieces.empty()) break;
    }
    long long sum = 0;
    for (auto& p : pieces) sum += weightRect(p.L, p.R, p.B, p.T);
    return int(sum);
}

/* exclusive contribution of rectangle idx */
int exclusiveWeightIdx(int idx) {
    const SimpleRect& R = curRects[idx];
    vector<SimpleRect> others;
    others.reserve(curRects.size() - 1);
    for (int i = 0; i < (int)curRects.size(); ++i)
        if (i != idx) others.push_back(curRects[i]);
    return uncoveredWeight(R, others);
}

/* deltas */
inline int deltaAdd(const SimpleRect& R) { return uncoveredWeight(R, curRects); }
inline int deltaRemove(int idx) { return -exclusiveWeightIdx(idx); }

int deltaReplace(int idx, const SimpleRect& newR) {
    int exclOld = exclusiveWeightIdx(idx);
    vector<SimpleRect> others;
    others.reserve(curRects.size() - 1);
    for (int i = 0; i < (int)curRects.size(); ++i)
        if (i != idx) others.push_back(curRects[i]);
    int gainNew = uncoveredWeight(newR, others);
    return gainNew - exclOld;
}
int deltaAddAfterRemoval(int idxRem, const SimpleRect& newR) {
    vector<SimpleRect> others;
    others.reserve(curRects.size() - 1);
    for (int i = 0; i < (int)curRects.size(); ++i)
        if (i != idxRem) others.push_back(curRects[i]);
    return uncoveredWeight(newR, others);
}

/*--------------------- candidate generators -------------------*/
vector<int> guideX, guideY;

/* random rectangle (edges on guide lines) */
SimpleRect randomRect() {
    SimpleRect r;
    r.L = guideX[rng.nextInt(0, (int)guideX.size() - 1)];
    r.R = guideX[rng.nextInt(0, (int)guideX.size() - 1)];
    if (r.L > r.R) swap(r.L, r.R);
    if (r.L == r.R) { if (r.R < MAXC) ++r.R; else --r.L; }
    r.B = guideY[rng.nextInt(0, (int)guideY.size() - 1)];
    r.T = guideY[rng.nextInt(0, (int)guideY.size() - 1)];
    if (r.B > r.T) swap(r.B, r.T);
    if (r.B == r.T) { if (r.T < MAXC) ++r.T; else --r.B; }
    return r;
}

/* Kadane on a random x‑range */
SimpleRect kadaneRect() {
    int L = guideX[rng.nextInt(0, (int)guideX.size() - 1)];
    int R = guideX[rng.nextInt(0, (int)guideX.size() - 1)];
    if (L > R) swap(L, R);
    if (L == R) { if (R < MAXC) ++R; else --L; }
    int iL = lowerXIdx[L];
    int iR = upperXIdx[R];
    if (iL > iR) return randomRect();
    int curSum = 0, best = INT_MIN;
    int curStart = 0, bestS = 0, bestE = -1;
    for (int j = 0; j < Ny; ++j) {
        int w = pref[prefIdx(iR + 1, j + 1)] - pref[prefIdx(iL, j + 1)]
              - pref[prefIdx(iR + 1, j)] + pref[prefIdx(iL, j)];
        if (curSum <= 0) { curSum = w; curStart = j; }
        else curSum += w;
        if (curSum > best) {
            best = curSum;
            bestS = curStart;
            bestE = j;
        }
    }
    if (best <= 0) return randomRect();
    int B = ys[bestS];
    int T = ys[bestE];
    if (B == T) { if (T < MAXC) ++T; else --B; }
    return SimpleRect{L, R, B, T};
}

/* Kadane on a random y‑range (column Kadane) */
SimpleRect kadaneRectYRange() {
    int B = guideY[rng.nextInt(0, (int)guideY.size() - 1)];
    int T = guideY[rng.nextInt(0, (int)guideY.size() - 1)];
    if (B > T) swap(B, T);
    if (B == T) { if (T < MAXC) ++T; else --B; }
    static vector<int> colWeight;
    if ((int)colWeight.size() < Nx) colWeight.assign(Nx, 0);
    else fill(colWeight.begin(), colWeight.begin() + Nx, 0);
    for (const auto& f : fish) {
        if (f.y < B || f.y > T) continue;
        int ix = lowerXIdx[f.x];
        colWeight[ix] += f.w;
    }
    int curSum = 0, best = INT_MIN;
    int curStart = 0, bestL = 0, bestR = -1;
    for (int i = 0; i < Nx; ++i) {
        int w = colWeight[i];
        if (curSum <= 0) { curSum = w; curStart = i; }
        else curSum += w;
        if (curSum > best) {
            best = curSum;
            bestL = curStart;
            bestR = i;
        }
    }
    if (best <= 0) return randomRect();
    int L = xs[bestL];
    int R = xs[bestR];
    if (L == R) { if (R < MAXC) ++R; else --L; }
    return SimpleRect{L, R, B, T};
}

/* thin vertical strip (width 1) */
SimpleRect thinRectV() {
    int L = rng.nextInt(0, MAXC - 1);
    int R = L + 1;
    int B = guideY[rng.nextInt(0, (int)guideY.size() - 1)];
    int T = guideY[rng.nextInt(0, (int)guideY.size() - 1)];
    if (B > T) swap(B, T);
    if (B == T) { if (T < MAXC) ++T; else --B; }
    return SimpleRect{L, R, B, T};
}

/* thin horizontal strip (height 1) */
SimpleRect thinRectH() {
    int B = rng.nextInt(0, MAXC - 1);
    int T = B + 1;
    int L = guideX[rng.nextInt(0, (int)guideX.size() - 1)];
    int R = guideX[rng.nextInt(0, (int)guideX.size() - 1)];
    if (L > R) swap(L, R);
    if (L == R) { if (R < MAXC) ++R; else --L; }
    return SimpleRect{L, R, B, T};
}

/* improve rectangle by greedy side moves */
void improveRect(SimpleRect& r, int steps = 6) {
    for (int it = 0; it < steps; ++it) {
        int side = rng.nextInt(0, 3);               // 0:L 1:R 2:B 3:T
        SimpleRect cand = r;
        if (side == 0) {                            // L
            int nl = guideX[rng.nextInt(0, (int)guideX.size() - 1)];
            if (nl < cand.L) {
                cand.L = max(0, nl);
                if (cand.L >= cand.R) continue;
            } else continue;
        } else if (side == 1) {                     // R
            int nr = guideX[rng.nextInt(0, (int)guideX.size() - 1)];
            if (nr > cand.R) {
                cand.R = min(MAXC, nr);
                if (cand.L >= cand.R) continue;
            } else continue;
        } else if (side == 2) {                     // B
            int nb = guideY[rng.nextInt(0, (int)guideY.size() - 1)];
            if (nb < cand.B) {
                cand.B = max(0, nb);
                if (cand.B >= cand.T) continue;
            } else continue;
        } else {                                    // T
            int nt = guideY[rng.nextInt(0, (int)guideY.size() - 1)];
            if (nt > cand.T) {
                cand.T = min(MAXC, nt);
                if (cand.B >= cand.T) continue;
            } else continue;
        }
        int delta = weightRect(cand.L, cand.R, cand.B, cand.T) -
                    weightRect(r.L, r.R, r.B, r.T);
        if (delta > 0) r = cand;
    }
}

/* choose random candidate (mostly from pool) */
SimpleRect genCandidate(const vector<SimpleRect>& pool) {
    if (!pool.empty() && rng.nextDouble() < 0.94)
        return pool[rng.nextInt(0, (int)pool.size() - 1)];
    return randomRect();
}

/*----------------- polygon from union of rectangles -----------*/
vector<pair<int,int>> buildPolygonFromRects(const vector<SimpleRect>& rects) {
    vector<int> xs_, ys_;
    xs_.reserve(rects.size()*2);
    ys_.reserve(rects.size()*2);
    for (auto &r: rects) {
        xs_.push_back(r.L);
        xs_.push_back(r.R);
        ys_.push_back(r.B);
        ys_.push_back(r.T);
    }
    sort(xs_.begin(), xs_.end()); xs_.erase(unique(xs_.begin(), xs_.end()), xs_.end());
    sort(ys_.begin(), ys_.end()); ys_.erase(unique(ys_.begin(), ys_.end()), ys_.end());
    if (xs_.size() < 2 || ys_.size() < 2) return {};

    int nx = (int)xs_.size() - 1;
    int ny = (int)ys_.size() - 1;
    vector<char> inside((size_t)nx * ny, 0);

    for (auto &r : rects) {
        int iL = lower_bound(xs_.begin(), xs_.end(), r.L) - xs_.begin();
        int iR = lower_bound(xs_.begin(), xs_.end(), r.R) - xs_.begin(); // exclusive
        int jB = lower_bound(ys_.begin(), ys_.end(), r.B) - ys_.begin();
        int jT = lower_bound(ys_.begin(), ys_.end(), r.T) - ys_.begin(); // exclusive
        for (int i = iL; i < iR; ++i)
            for (int j = jB; j < jT; ++j)
                inside[(size_t)i * ny + j] = 1;
    }

    auto pack = [&](int x,int y)->ll { return ((ll)x<<20) | y; };
    auto unpack = [&](ll v)->pair<int,int> { return {int(v>>20), int(v & ((1<<20)-1))}; };

    unordered_map<ll, vector<ll>> adj;
    adj.reserve((size_t)nx * ny * 4);
    for (int i=0;i<nx;++i) for (int j=0;j<ny;++j) if (inside[(size_t)i*ny+j]) {
        // left side
        if (i==0 || !inside[(size_t)(i-1)*ny+j]) {
            int x = xs_[i];
            ll a = pack(x, ys_[j]), b = pack(x, ys_[j+1]);
            adj[a].push_back(b); adj[b].push_back(a);
        }
        // right side
        if (i==nx-1 || !inside[(size_t)(i+1)*ny+j]) {
            int x = xs_[i+1];
            ll a = pack(x, ys_[j]), b = pack(x, ys_[j+1]);
            adj[a].push_back(b); adj[b].push_back(a);
        }
        // bottom side
        if (j==0 || !inside[(size_t)i*ny+(j-1)]) {
            int y = ys_[j];
            ll a = pack(xs_[i], y), b = pack(xs_[i+1], y);
            adj[a].push_back(b); adj[b].push_back(a);
        }
        // top side
        if (j==ny-1 || !inside[(size_t)i*ny+(j+1)]) {
            int y = ys_[j+1];
            ll a = pack(xs_[i], y), b = pack(xs_[i+1], y);
            adj[a].push_back(b); adj[b].push_back(a);
        }
    }
    if (adj.empty()) return {};

    // every vertex must have degree 2 for a simple polygon
    for (auto &kv : adj) {
        auto &v = kv.second;
        sort(v.begin(), v.end());
        v.erase(unique(v.begin(), v.end()), v.end());
        if ((int)v.size() != 2) return {};
    }

    ll start = adj.begin()->first;
    for (auto &kv : adj) if (kv.first < start) start = kv.first;

    vector<pair<int,int>> poly;
    ll cur = start, prev = -1;
    do {
        poly.emplace_back(unpack(cur));
        const auto &nbr = adj[cur];
        ll nxt = -1;
        for (ll nb : nbr) if (nb != prev) { nxt = nb; break; }
        if (nxt == -1) return {};
        prev = cur; cur = nxt;
    } while (cur != start);
    if ((int)poly.size() != (int)adj.size()) return {};

    return poly;
}

/*----------------- perimeter ----------------*/
inline ll perimeter(const vector<pair<int,int>>& poly) {
    ll per = 0;
    int m = poly.size();
    for (int i=0;i<m;++i) {
        per += llabs(poly[i].first - poly[(i+1)%m].first);
        per += llabs(poly[i].second - poly[(i+1)%m].second);
    }
    return per;
}

/*----------------------- main ------------------------------*/
int main(int argc, char* argv[]) {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    /*----- time handling -----*/
    double TL = 2.0;                         // default 2 s
    if (argc > 1) {
        try { TL = stod(argv[1]); } catch (...) {}
    }
    TL = max(0.2, TL - SAFETY);
    auto now = [](){ return chrono::duration<double>(chrono::steady_clock::now().time_since_epoch()).count(); };
    const double START = now();

    /*----- input -----*/
    int N;
    if (!(cin >> N)) return 0;
    fish.resize(2 * N);
    for (int i = 0; i < N; ++i) { cin >> fish[i].x >> fish[i].y; fish[i].w = 1; }
    for (int i = 0; i < N; ++i) { cin >> fish[N+i].x >> fish[N+i].y; fish[N+i].w = -1; }

    /*----- guide coordinates (fish + borders) -----*/
    guideX.reserve(2 * N + 2);
    guideY.reserve(2 * N + 2);
    for (auto &f : fish) {
        guideX.push_back(f.x);
        guideY.push_back(f.y);
    }
    guideX.push_back(0); guideX.push_back(MAXC);
    guideY.push_back(0); guideY.push_back(MAXC);
    sort(guideX.begin(), guideX.end()); guideX.erase(unique(guideX.begin(), guideX.end()), guideX.end());
    sort(guideY.begin(), guideY.end()); guideY.erase(unique(guideY.begin(), guideY.end()), guideY.end());

    /*----- coordinate compression -----*/
    xs.clear(); ys.clear();
    xs.reserve(2 * N + 2);
    ys.reserve(2 * N + 2);
    for (auto &f : fish) {
        xs.push_back(f.x);
        ys.push_back(f.y);
    }
    xs.push_back(0); xs.push_back(MAXC);
    ys.push_back(0); ys.push_back(MAXC);
    sort(xs.begin(), xs.end()); xs.erase(unique(xs.begin(), xs.end()), xs.end());
    sort(ys.begin(), ys.end()); ys.erase(unique(ys.begin(), ys.end()), ys.end());
    Nx = xs.size(); Ny = ys.size();

    /*----- fast index tables -----*/
    lowerXIdx.assign(MAXC + 1, Nx);
    upperXIdx.assign(MAXC + 1, -1);
    int cur = 0;
    for (int v = 0; v <= MAXC; ++v) {
        while (cur < Nx && xs[cur] < v) ++cur;
        lowerXIdx[v] = cur;
    }
    cur = 0;
    for (int v = 0; v <= MAXC; ++v) {
        while (cur + 1 < Nx && xs[cur + 1] <= v) ++cur;
        upperXIdx[v] = (xs[cur] <= v ? cur : -1);
    }
    lowerYIdx.assign(MAXC + 1, Ny);
    upperYIdx.assign(MAXC + 1, -1);
    cur = 0;
    for (int v = 0; v <= MAXC; ++v) {
        while (cur < Ny && ys[cur] < v) ++cur;
        lowerYIdx[v] = cur;
    }
    cur = 0;
    for (int v = 0; v <= MAXC; ++v) {
        while (cur + 1 < Ny && ys[cur + 1] <= v) ++cur;
        upperYIdx[v] = (ys[cur] <= v ? cur : -1);
    }

    /*----- 2‑D prefix sum -----*/
    pref.assign( (size_t)(Nx + 1) * (Ny + 1), 0 );
    for (auto &f : fish) {
        int ix = lowerXIdx[f.x];
        int iy = lowerYIdx[f.y];
        pref[prefIdx(ix + 1, iy + 1)] += f.w;
    }
    for (int i = 1; i <= Nx; ++i)
        for (int j = 1; j <= Ny; ++j)
            pref[prefIdx(i, j)] += pref[prefIdx(i-1, j)] + pref[prefIdx(i, j-1)] - pref[prefIdx(i-1, j-1)];

    /*----- rectangle pool generation -----*/
    vector<SimpleRect> rectPool;
    rectPool.reserve(POOL_TARGET);
    SimpleRect bestRect{0,0,0,0};
    int bestWeight = INT_MIN;
    const double POOL_END = START + TL * 0.20;   // 20% for pool creation
    while ((int)rectPool.size() < POOL_TARGET && now() < POOL_END) {
        SimpleRect a = kadaneRect(); improveRect(a,5);
        int wa = weightRect(a.L, a.R, a.B, a.T);
        if (wa > 0) {
            rectPool.push_back(a);
            if (wa > bestWeight) { bestWeight = wa; bestRect = a; }
        }
        SimpleRect b = kadaneRectYRange(); improveRect(b,5);
        int wb = weightRect(b.L, b.R, b.B, b.T);
        if (wb > 0) {
            rectPool.push_back(b);
            if (wb > bestWeight) { bestWeight = wb; bestRect = b; }
        }
        SimpleRect c = randomRect(); improveRect(c,4);
        int wc = weightRect(c.L, c.R, c.B, c.T);
        if (wc > 0) {
            rectPool.push_back(c);
            if (wc > bestWeight) { bestWeight = wc; bestRect = c; }
        }
        SimpleRect d = thinRectV(); improveRect(d,3);
        int wd = weightRect(d.L, d.R, d.B, d.T);
        if (wd > 0) {
            rectPool.push_back(d);
            if (wd > bestWeight) { bestWeight = wd; bestRect = d; }
        }
        SimpleRect e = thinRectH(); improveRect(e,3);
        int we = weightRect(e.L, e.R, e.B, e.T);
        if (we > 0) {
            rectPool.push_back(e);
            if (we > bestWeight) { bestWeight = we; bestRect = e; }
        }
    }
    if (bestWeight == INT_MIN) {
        bestRect = {0, MAXC, 0, MAXC};
        bestWeight = weightRect(bestRect.L, bestRect.R, bestRect.B, bestRect.T);
    }

    /*----- initial solution -----*/
    curRects.clear();
    curRects.push_back(bestRect);
    int curScore = bestWeight;
    auto curPoly = buildPolygonFromRects(curRects);
    if (curPoly.empty()) {
        SimpleRect whole{0, MAXC, 0, MAXC};
        curRects = {whole};
        curScore = weightRect(0, MAXC, 0, MAXC);
        curPoly = {{0,0},{MAXC,0},{MAXC,MAXC},{0,MAXC}};
    }
    int bestScore = curScore;
    vector<SimpleRect> bestRects = curRects;
    vector<pair<int,int>> bestPoly = curPoly;

    /*----- greedy addition (up to ~35% TL) -----*/
    sort(rectPool.begin(), rectPool.end(),
         [&](const SimpleRect& a, const SimpleRect& b){
             return weightRect(a.L,a.R,a.B,a.T) > weightRect(b.L,b.R,b.B,b.T);
         });
    const double GREEDY_END = START + TL * 0.35;
    for (const SimpleRect& candOrig : rectPool) {
        if ((int)curRects.size() >= KMAX) break;
        if (now() >= GREEDY_END) break;
        bool touch = false;
        for (auto &r : curRects) if (rectTouches(r, candOrig)) { touch = true; break; }
        if (!touch) continue;
        int delta = deltaAdd(candOrig);
        if (delta <= 0) continue;
        vector<SimpleRect> trial = curRects;
        trial.push_back(candOrig);
        auto poly = buildPolygonFromRects(trial);
        if (poly.empty() || (int)poly.size() > MAX_VERT) continue;
        if (perimeter(poly) > MAX_PERIM) continue;
        curRects.swap(trial);
        curScore += delta;
        curPoly.swap(poly);
        if (curScore > bestScore) {
            bestScore = curScore; bestRects = curRects; bestPoly = curPoly;
        }
    }

    /*----- simulated annealing (dominant phase) -----*/
    const double SA_END = START + TL * 0.96;   // 96% of total time
    const double SA_START = now();
    int stagnation = 0;
    const int STAGNATION_LIMIT = 2500;
    while (now() < SA_END) {
        double prog = (now() - SA_START) / (SA_END - SA_START);
        if (prog > 1.0) prog = 1.0;
        double T = INIT_T * pow(FINAL_T / INIT_T, prog);   // exponential cooling

        int move = rng.nextInt(0, 6);    // 0:add,1:remove,2:replace,3:expand,4:shrink,5:slide,6:rem+add

        if (move == 0 && (int)curRects.size() < KMAX) {               // ADD
            SimpleRect cand = genCandidate(rectPool);
            bool touch = false;
            for (auto &r : curRects) if (rectTouches(r, cand)) { touch = true; break; }
            if (!touch) continue;
            int delta = deltaAdd(cand);
            if (delta <= 0 && rng.nextDouble() >= exp(delta / T)) continue;
            vector<SimpleRect> trial = curRects; trial.push_back(cand);
            auto poly = buildPolygonFromRects(trial);
            if (poly.empty() || (int)poly.size() > MAX_VERT) continue;
            if (perimeter(poly) > MAX_PERIM) continue;
            curRects.swap(trial);
            curScore += delta;
            curPoly.swap(poly);
            if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
        }
        else if (move == 1 && (int)curRects.size() > 1) {               // REMOVE
            int idx = rng.nextInt(0, (int)curRects.size() - 1);
            int delta = deltaRemove(idx);
            if (delta <= 0 && rng.nextDouble() >= exp(delta / T)) continue;
            vector<SimpleRect> trial = curRects;
            trial.erase(trial.begin() + idx);
            auto poly = buildPolygonFromRects(trial);
            if (poly.empty() || (int)poly.size() > MAX_VERT) continue;
            if (perimeter(poly) > MAX_PERIM) continue;
            curRects.swap(trial);
            curScore += delta;
            curPoly.swap(poly);
            if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
        }
        else if (move == 2) {                                          // REPLACE
            int idx = rng.nextInt(0, (int)curRects.size() - 1);
            SimpleRect newR = genCandidate(rectPool);
            bool touch = false;
            for (int i = 0; i < (int)curRects.size(); ++i)
                if (i != idx && rectTouches(curRects[i], newR)) { touch = true; break; }
            if (!touch) continue;
            int delta = deltaReplace(idx, newR);
            if (delta <= 0 && rng.nextDouble() >= exp(delta / T)) continue;
            vector<SimpleRect> trial = curRects;
            trial[idx] = newR;
            auto poly = buildPolygonFromRects(trial);
            if (poly.empty() || (int)poly.size() > MAX_VERT) continue;
            if (perimeter(poly) > MAX_PERIM) continue;
            curRects.swap(trial);
            curScore += delta;
            curPoly.swap(poly);
            if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
        }
        else if (move == 3) {                                          // EXPAND
            int idx = rng.nextInt(0, (int)curRects.size() - 1);
            SimpleRect ns = curRects[idx];
            int side = rng.nextInt(0, 3); // 0:L 1:R 2:B 3:T
            SimpleRect bestR = ns;
            int bestDelta = INT_MIN;
            for (int tt = 0; tt < EXPAND_ATTEMPTS; ++tt) {
                int v = (side <= 1) ? guideX[rng.nextInt(0, (int)guideX.size() - 1)]
                                    : guideY[rng.nextInt(0, (int)guideY.size() - 1)];
                SimpleRect cand = ns;
                if (side == 0) { v = max(0, min(v, cand.R - 1)); cand.L = v; }
                else if (side == 1) { v = max(cand.L + 1, min(v, MAXC)); cand.R = v; }
                else if (side == 2) { v = max(0, min(v, cand.T - 1)); cand.B = v; }
                else { v = max(cand.B + 1, min(v, MAXC)); cand.T = v; }
                if (cand.L >= cand.R || cand.B >= cand.T) continue;
                bool touch = false;
                for (int i = 0; i < (int)curRects.size(); ++i)
                    if (i != idx && rectTouches(curRects[i], cand)) { touch = true; break; }
                if (!touch) continue;
                int delta = deltaReplace(idx, cand);
                if (delta > bestDelta) { bestDelta = delta; bestR = cand; }
            }
            if (bestDelta <= 0 && rng.nextDouble() >= exp(bestDelta / T)) continue;
            vector<SimpleRect> trial = curRects;
            trial[idx] = bestR;
            auto poly = buildPolygonFromRects(trial);
            if (poly.empty() || (int)poly.size() > MAX_VERT) continue;
            if (perimeter(poly) > MAX_PERIM) continue;
            curRects.swap(trial);
            curScore += bestDelta;
            curPoly.swap(poly);
            if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
        }
        else if (move == 4) {                                          // SHRINK
            int idx = rng.nextInt(0, (int)curRects.size() - 1);
            SimpleRect ns = curRects[idx];
            int side = rng.nextInt(0, 3);
            SimpleRect bestR = ns;
            int bestDelta = INT_MIN;
            for (int tt = 0; tt < EXPAND_ATTEMPTS; ++tt) {
                int v = (side <= 1) ? guideX[rng.nextInt(0, (int)guideX.size() - 1)]
                                    : guideY[rng.nextInt(0, (int)guideY.size() - 1)];
                SimpleRect cand = ns;
                if (side == 0) { v = max(0, min(v, cand.R - 1)); cand.L = v; }
                else if (side == 1) { v = max(cand.L + 1, min(v, MAXC)); cand.R = v; }
                else if (side == 2) { v = max(0, min(v, cand.T - 1)); cand.B = v; }
                else { v = max(cand.B + 1, min(v, MAXC)); cand.T = v; }
                if (cand.L >= cand.R || cand.B >= cand.T) continue;
                bool touch = false;
                for (int i = 0; i < (int)curRects.size(); ++i)
                    if (i != idx && rectTouches(curRects[i], cand)) { touch = true; break; }
                if (!touch) continue;
                int delta = deltaReplace(idx, cand);
                if (delta > bestDelta) { bestDelta = delta; bestR = cand; }
            }
            if (bestDelta <= 0 && rng.nextDouble() >= exp(bestDelta / T)) continue;
            vector<SimpleRect> trial = curRects;
            trial[idx] = bestR;
            auto poly = buildPolygonFromRects(trial);
            if (poly.empty() || (int)poly.size() > MAX_VERT) continue;
            if (perimeter(poly) > MAX_PERIM) continue;
            curRects.swap(trial);
            curScore += bestDelta;
            curPoly.swap(poly);
            if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
        }
        else if (move == 5) {                                          // SLIDE
            int idx = rng.nextInt(0, (int)curRects.size() - 1);
            SimpleRect old = curRects[idx];
            SimpleRect cand = old;
            int dx = rng.nextInt(-SLIDE_MAX, SLIDE_MAX);
            int dy = rng.nextInt(-SLIDE_MAX, SLIDE_MAX);
            int w = old.R - old.L;
            int h = old.T - old.B;
            int newL = old.L + dx;
            int newB = old.B + dy;
            newL = max(0, min(newL, MAXC - w));
            int newBclamped = max(0, min(newB, MAXC - h));
            cand.L = newL;
            cand.R = newL + w;
            cand.B = newBclamped;
            cand.T = newBclamped + h;
            if (cand.L >= cand.R || cand.B >= cand.T) continue;
            bool touch = false;
            for (int i = 0; i < (int)curRects.size(); ++i)
                if (i != idx && rectTouches(curRects[i], cand)) { touch = true; break; }
            if (!touch) continue;
            int delta = deltaReplace(idx, cand);
            if (delta <= 0 && rng.nextDouble() >= exp(delta / T)) continue;
            vector<SimpleRect> trial = curRects;
            trial[idx] = cand;
            auto poly = buildPolygonFromRects(trial);
            if (poly.empty() || (int)poly.size() > MAX_VERT) continue;
            if (perimeter(poly) > MAX_PERIM) continue;
            curRects.swap(trial);
            curScore += delta;
            curPoly.swap(poly);
            if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
        }
        else if (move == 6) {                                          // REMOVE + ADD
            if ((int)curRects.size() < 2) continue;
            int idxRem = rng.nextInt(0, (int)curRects.size() - 1);
            SimpleRect cand = genCandidate(rectPool);
            bool touch = false;
            for (int i = 0; i < (int)curRects.size(); ++i)
                if (i != idxRem && rectTouches(curRects[i], cand)) { touch = true; break; }
            if (!touch) continue;
            int delta = deltaRemove(idxRem) + deltaAddAfterRemoval(idxRem, cand);
            if (delta <= 0 && rng.nextDouble() >= exp(delta / T)) continue;
            vector<SimpleRect> trial = curRects;
            trial.erase(trial.begin() + idxRem);
            trial.push_back(cand);
            auto poly = buildPolygonFromRects(trial);
            if (poly.empty() || (int)poly.size() > MAX_VERT) continue;
            if (perimeter(poly) > MAX_PERIM) continue;
            curRects.swap(trial);
            curScore += delta;
            curPoly.swap(poly);
            if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
        }

        /* stagnation handling – restart from best if stuck */
        if (curScore > bestScore) stagnation = 0;
        else ++stagnation;
        if (stagnation >= STAGNATION_LIMIT) {
            curRects = bestRects;
            curScore = bestScore;
            curPoly  = bestPoly;
            stagnation = 0;
        }
    }

    /*----- post‑SA cleanup: delete harmful rectangles -----*/
    bool improved = true;
    const double CLEANUP_END = START + TL * 0.99;
    while (improved && now() < CLEANUP_END) {
        improved = false;
        for (int i = (int)curRects.size() - 1; i >= 0; --i) {
            int delta = deltaRemove(i);
            if (delta > 0) {
                vector<SimpleRect> trial = curRects;
                trial.erase(trial.begin() + i);
                auto poly = buildPolygonFromRects(trial);
                if (!poly.empty() && (int)poly.size() <= MAX_VERT && perimeter(poly) <= MAX_PERIM) {
                    curRects.swap(trial);
                    curScore += delta;
                    curPoly.swap(poly);
                    improved = true;
                    if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
                }
            }
        }
    }

    /*----- hill‑climbing replace phase -----*/
    bool improved2 = true;
    while (improved2 && now() < CLEANUP_END) {
        improved2 = false;
        for (int idx = 0; idx < (int)curRects.size(); ++idx) {
            SimpleRect bestCand = curRects[idx];
            int bestDelta = 0;
            for (int t = 0; t < 30; ++t) {
                const SimpleRect& cand = rectPool[rng.nextInt(0, (int)rectPool.size() - 1)];
                bool touch = false;
                for (int i = 0; i < (int)curRects.size(); ++i)
                    if (i != idx && rectTouches(curRects[i], cand)) { touch = true; break; }
                if (!touch) continue;
                int delta = deltaReplace(idx, cand);
                if (delta > bestDelta) {
                    bestDelta = delta;
                    bestCand = cand;
                }
            }
            if (bestDelta > 0) {
                vector<SimpleRect> trial = curRects;
                trial[idx] = bestCand;
                auto poly = buildPolygonFromRects(trial);
                if (!poly.empty() && (int)poly.size() <= MAX_VERT && perimeter(poly) <= MAX_PERIM) {
                    curRects.swap(trial);
                    curScore += bestDelta;
                    curPoly.swap(poly);
                    improved2 = true;
                    if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
                }
            }
        }
    }

    /*----- final greedy addition of remaining high‑weight rectangles -----*/
    vector<pair<int,SimpleRect>> weighted;
    weighted.reserve(rectPool.size());
    for (auto &r : rectPool) {
        int w = weightRect(r.L, r.R, r.B, r.T);
        if (w > 0) weighted.emplace_back(w, r);
    }
    sort(weighted.begin(), weighted.end(),
         [](const auto& a, const auto& b){ return a.first > b.first; });

    const double FINAL_END = START + TL * 0.998;
    for (auto &pw : weighted) {
        if ((int)curRects.size() >= KMAX) break;
        if (now() > FINAL_END) break;
        const SimpleRect& cand = pw.second;
        bool touch = false;
        for (auto &r : curRects) if (rectTouches(r, cand)) { touch = true; break; }
        if (!touch) continue;
        int delta = deltaAdd(cand);
        if (delta <= 0) continue;
        vector<SimpleRect> trial = curRects;
        trial.push_back(cand);
        auto poly = buildPolygonFromRects(trial);
        if (poly.empty() || (int)poly.size() > MAX_VERT || perimeter(poly) > MAX_PERIM) continue;
        curRects.swap(trial);
        curScore += delta;
        curPoly.swap(poly);
        if (curScore > bestScore) { bestScore = curScore; bestRects = curRects; bestPoly = curPoly; }
    }

    /*----- safety fallback -----*/
    if (bestPoly.empty() || (int)bestPoly.size() > MAX_VERT || perimeter(bestPoly) > MAX_PERIM) {
        SimpleRect r = bestRect;
        bestPoly = {
            {r.L, r.B},
            {r.R, r.B},
            {r.R, r.T},
            {r.L, r.T}
        };
    }

    /*----- output -----*/
    cout << bestPoly.size() << '\n';
    for (auto &p : bestPoly) cout << p.first << ' ' << p.second << '\n';
    return 0;
}