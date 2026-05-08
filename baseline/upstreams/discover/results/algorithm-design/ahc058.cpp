#pragma GCC optimize("O3,unroll-loops")
#include <bits/stdc++.h>
using namespace std;

constexpr int MAX_N = 10;
constexpr int MAX_L = 4;
constexpr int MAX_T = 500;
constexpr int EARLY_STEPS = 250;          // steps for beam search
constexpr int BEAM_SIZE   = 600;          // beam width
constexpr int TOP_K_COUNT = 8;            // number of top IDs to consider in beam

int N, L, T;
long long K;
vector<long long> A;
unsigned long long C[MAX_L][MAX_N];
unsigned long long bin2[MAX_T + 1];
unsigned long long bin3[MAX_T + 1];
unsigned long long bin4[MAX_T + 1];

struct State {
    __int128 apple{};
    __int128 B[MAX_L][MAX_N];
    int P[MAX_L][MAX_N];
    State() {
        apple = 0;
        for (int i = 0; i < MAX_L; ++i)
            for (int j = 0; j < MAX_N; ++j) {
                B[i][j] = 1;
                P[i][j] = 0;
            }
    }
};

inline void production(State &s) {
    __int128 add = 0;
    for (int j = 0; j < N; ++j) {
        long long p0 = s.P[0][j];
        if (p0) add += (__int128)A[j] * s.B[0][j] * p0;
    }
    s.apple += add;
    for (int lvl = 1; lvl < L; ++lvl) {
        for (int j = 0; j < N; ++j) {
            long long p = s.P[lvl][j];
            if (p && s.B[lvl][j])
                s.B[lvl - 1][j] += s.B[lvl][j] * (__int128)p;
        }
    }
}

/* marginal gain for upgrading (lvl,id) assuming the upgrade happens now and then
   nothing else */
inline __int128 marginal_gain(const State &s, int steps, int lvl, int id) {
    const __int128 B0 = s.B[0][id];
    const __int128 B1 = s.B[1][id];
    const __int128 B2 = s.B[2][id];
    const __int128 B3 = s.B[3][id];
    const long long p0 = s.P[0][id];
    const long long p1 = s.P[1][id];
    const long long p2 = s.P[2][id];
    const long long p3 = s.P[3][id];
    const long long a = A[id];

    if (lvl == 0) {
        __int128 sum = (__int128)steps * B0;
        if (p1) {
            sum += B1 * (__int128)p1 * (unsigned long long)bin2[steps];
            if (p2) {
                sum += B2 * (__int128)p1 * (__int128)p2 *
                       (unsigned long long)bin3[steps];
                if (p3) {
                    sum += B3 * (__int128)p1 * (__int128)p2 *
                           (__int128)p3 *
                           (unsigned long long)bin4[steps];
                }
            }
        }
        return (__int128)a * sum;
    }
    if (lvl == 1) {
        if (!p0) return 0;
        __int128 t = B1 * (unsigned long long)bin2[steps];
        if (p2) {
            t += B2 * (__int128)p2 * (unsigned long long)bin3[steps];
            if (p3) t += B3 * (__int128)p3 * (unsigned long long)bin4[steps];
        }
        return (__int128)a * (__int128)p0 * t;
    }
    if (lvl == 2) {
        if (!p0 || !p1) return 0;
        __int128 t = B2 * (__int128)p1 * (unsigned long long)bin3[steps];
        if (p3) t += B3 * (__int128)p3 * (unsigned long long)bin4[steps];
        return (__int128)a * (__int128)p0 * t;
    }
    // lvl == 3
    if (!p0 || !p1 || !p2) return 0;
    __int128 t = B3 * (__int128)p1 * (__int128)p2 *
                 (unsigned long long)bin4[steps];
    return (__int128)a * (__int128)p0 * t;
}

/* net gain = future apples from upgrade (including this turn's immediate effect for lvl0) - cost */
inline __int128 net_gain(const State &s, int steps, int lvl, int id) {
    __int128 gain = marginal_gain(s, steps, lvl, id);
    if (lvl == 0) gain += (__int128)A[id] * s.B[0][id];
    return gain;
}

/* ---------------- Greedy ---------------- */
pair<vector<pair<int, int>>, State> greedy_plan(const State &init,
                                                int steps_total,
                                                int bias_id) {
    State cur = init;
    vector<pair<int, int>> plan;
    plan.reserve(steps_total);
    for (int turn = 0; turn < steps_total; ++turn) {
        int remain = steps_total - turn - 1;
        int chosen_i = -1, chosen_j = -1;
        if (bias_id >= 0) {
            __int128 best_main_net = -1;
            int best_main_i = -1;
            for (int lvl = 0; lvl < L; ++lvl) {
                __int128 cost = (__int128)C[lvl][bias_id] *
                                (cur.P[lvl][bias_id] + 1);
                if (cur.apple < cost) continue;
                __int128 net = net_gain(cur, remain, lvl, bias_id) - cost;
                if (net > best_main_net) {
                    best_main_net = net;
                    best_main_i = lvl;
                }
            }
            if (best_main_i != -1 && best_main_net > 0) {
                chosen_i = best_main_i;
                chosen_j = bias_id;
            }
        }
        if (chosen_i == -1) {
            __int128 best_gain = 0;
            int best_i = -1, best_j = -1;
            for (int i = 0; i < L; ++i) {
                for (int j = 0; j < N; ++j) {
                    __int128 cost = (__int128)C[i][j] *
                                    (cur.P[i][j] + 1);
                    if (cur.apple < cost) continue;
                    __int128 net = net_gain(cur, remain, i, j) - cost;
                    if (net > best_gain) {
                        best_gain = net;
                        best_i = i;
                        best_j = j;
                    }
                }
            }
            if (best_i != -1) {
                chosen_i = best_i;
                chosen_j = best_j;
            }
        }
        if (chosen_i != -1) {
            __int128 cost = (__int128)C[chosen_i][chosen_j] *
                            (cur.P[chosen_i][chosen_j] + 1);
            cur.apple -= cost;
            ++cur.P[chosen_i][chosen_j];
        }
        plan.emplace_back(chosen_i, chosen_j);
        production(cur);
    }
    return {plan, cur};
}

/* compute full prefix states for a given plan */
void compute_prefix(const vector<pair<int, int>> &plan,
                    vector<State> &pref) {
    pref.assign(T + 1, State());
    pref[0].apple = K;
    for (int t = 0; t < T; ++t) {
        State cur = pref[t];
        int lvl = plan[t].first, id = plan[t].second;
        if (lvl != -1) {
            __int128 cost = (__int128)C[lvl][id] *
                            (cur.P[lvl][id] + 1);
            cur.apple -= cost;
            ++cur.P[lvl][id];
        }
        production(cur);
        pref[t + 1] = cur;
    }
}

/* evaluate a candidate plan from certain index using cached prefix */
pair<bool, __int128> evaluate_candidate(const vector<pair<int, int>> &cand,
                                        int first,
                                        const vector<State> &pref) {
    State cur = pref[first];
    for (int t = first; t < (int)cand.size(); ++t) {
        int lvl = cand[t].first, id = cand[t].second;
        if (lvl != -1) {
            __int128 cost = (__int128)C[lvl][id] *
                            (cur.P[lvl][id] + 1);
            if (cur.apple < cost) return {false, 0};
            cur.apple -= cost;
            ++cur.P[lvl][id];
        }
        production(cur);
    }
    return {true, cur.apple};
}

/* recompute prefix from start index after modifications */
void recompute_prefix_from(const vector<pair<int, int>> &plan,
                           vector<State> &pref,
                           int start) {
    State cur = pref[start];
    for (int t = start; t < T; ++t) {
        int lvl = plan[t].first, id = plan[t].second;
        if (lvl != -1) {
            __int128 cost = (__int128)C[lvl][id] *
                            (cur.P[lvl][id] + 1);
            cur.apple -= cost;
            ++cur.P[lvl][id];
        }
        production(cur);
        pref[t + 1] = cur;
    }
}

/* generate a random legal action for a given state */
pair<int, int> random_legal_action(const State &s,
                                   mt19937_64 &rng) {
    static vector<pair<int, int>> legal;
    legal.clear();
    legal.emplace_back(-1, -1);
    for (int i = 0; i < L; ++i)
        for (int j = 0; j < N; ++j) {
            __int128 cost = (__int128)C[i][j] *
                            (s.P[i][j] + 1);
            if (s.apple >= cost) legal.emplace_back(i, j);
        }
    uniform_int_distribution<size_t> dist(0, legal.size() - 1);
    return legal[dist(rng)];
}

/* beam search structures */
struct BeamNode {
    State st;
    int parent_depth;
    int parent_idx;
    int act_i;
    int act_j;
};

inline __int128 bound_future(const State &s, int steps) {
    __int128 b = s.apple;
    if (steps > 0) {
        for (int lvl = 0; lvl < L; ++lvl)
            for (int id = 0; id < N; ++id)
                b += marginal_gain(s, steps, lvl, id);
    }
    return b;
}

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    if (!(cin >> N >> L >> T >> K)) return 0;
    A.resize(N);
    for (int i = 0; i < N; ++i) cin >> A[i];
    for (int i = 0; i < L; ++i)
        for (int j = 0; j < N; ++j) cin >> C[i][j];

    for (int i = 0; i <= T; ++i) {
        bin2[i] = (unsigned long long)i * (i + 1) / 2ULL;
        if (i >= 2)
            bin3[i] = (unsigned long long)i * (i + 1) * (i - 1) / 6ULL;
        else
            bin3[i] = 0ULL;
        if (i >= 2)
            bin4[i] = (unsigned long long)(i + 2) * (i + 1) * i * (i - 1) / 24ULL;
        else
            bin4[i] = 0ULL;
    }

    mt19937_64 rng(chrono::steady_clock::now().time_since_epoch().count());

    State init;
    init.apple = K;

    // Determine top IDs for focused beam search
    vector<pair<long long,int>> sorted_ids;
    for (int i = 0; i < N; ++i) sorted_ids.emplace_back(A[i], i);
    sort(sorted_ids.rbegin(), sorted_ids.rend());
    const int TOP_K = min(N, TOP_K_COUNT);
    vector<int> top_ids;
    for (int i = 0; i < TOP_K; ++i) top_ids.push_back(sorted_ids[i].second);

    vector<pair<int, int>> best_plan;
    __int128 best_val = -1;

    auto try_update = [&](const vector<pair<int, int>> &pl, const State &st) {
        if (st.apple > best_val) {
            best_val = st.apple;
            best_plan = pl;
        }
    };

    auto start_total = chrono::high_resolution_clock::now();

    /* Baseline greedy */
    {
        auto res = greedy_plan(init, T, -1);
        try_update(res.first, res.second);
    }

    /* Greedy focused on top IDs (now TOP_K) */
    for (int idx = 0; idx < TOP_K; ++idx) {
        int id = sorted_ids[idx].second;
        auto res = greedy_plan(init, T, id);
        try_update(res.first, res.second);
    }

    /* Random bias runs */
    const int RAND_RUNS = 15;
    for (int trial = 0; trial < RAND_RUNS; ++trial) {
        int bias = int(rng() % (N + 1)) - 1; // -1 .. N-1
        auto res = greedy_plan(init, T, bias);
        try_update(res.first, res.second);
    }

    /* Split at mid and thirds */
    vector<int> splits = {T / 2, T / 3, (2 * T) / 3};
    for (int split : splits) {
        for (int id1 = 0; id1 < N; ++id1) {
            auto seg1 = greedy_plan(init, split, id1);
            State mid_state = seg1.second;
            for (int id2 = 0; id2 < N; ++id2) {
                auto seg2 = greedy_plan(mid_state, T - split, id2);
                if (seg2.second.apple > best_val) {
                    vector<pair<int, int>> total;
                    total.reserve(T);
                    total.insert(total.end(), seg1.first.begin(), seg1.first.end());
                    total.insert(total.end(), seg2.first.begin(), seg2.first.end());
                    try_update(total, seg2.second);
                }
            }
        }
    }

    /* Beam search for early steps (restricted to top IDs) */
    vector<vector<BeamNode>> layers(EARLY_STEPS + 1);
    layers[0].push_back({init, -1, -1, -1, -1});
    for (int step = 0; step < EARLY_STEPS; ++step) {
        vector<BeamNode> cand;
        cand.reserve(layers[step].size() * (TOP_K * L + 1));
        for (int idx = 0; idx < (int)layers[step].size(); ++idx) {
            const BeamNode &bn = layers[step][idx];
            // Do nothing
            {
                State ns = bn.st;
                production(ns);
                cand.push_back({ns, step, idx, -1, -1});
            }
            // Upgrades (restricted to top IDs)
            for (int lvl = 0; lvl < L; ++lvl) {
                for (int ti = 0; ti < TOP_K; ++ti) {
                    int id = top_ids[ti];
                    __int128 cost = (__int128)C[lvl][id] *
                                    (bn.st.P[lvl][id] + 1);
                    if (bn.st.apple >= cost) {
                        State ns = bn.st;
                        ns.apple -= cost;
                        ++ns.P[lvl][id];
                        production(ns);
                        cand.push_back({ns, step, idx, lvl, id});
                    }
                }
            }
        }
        vector<pair<__int128, int>> scores;
        scores.reserve(cand.size());
        int remaining = T - (step + 1);
        for (int i = 0; i < (int)cand.size(); ++i) {
            __int128 bnd = bound_future(cand[i].st, remaining);
            scores.emplace_back(bnd, i);
        }
        sort(scores.begin(), scores.end(),
             [&](const auto &a, const auto &b) { return a.first > b.first; });
        int keep = min((int)scores.size(), BEAM_SIZE);
        layers[step + 1].clear();
        layers[step + 1].reserve(keep);
        for (int k = 0; k < keep; ++k) {
            layers[step + 1].push_back(cand[scores[k].second]);
        }
    }

    // Evaluate each beam node with greedy suffix
    int rest_steps = T - EARLY_STEPS;
    for (int idx = 0; idx < (int)layers[EARLY_STEPS].size(); ++idx) {
        const BeamNode &bn = layers[EARLY_STEPS][idx];
        // reconstruct prefix actions
        vector<pair<int, int>> prefix;
        int cur_depth = EARLY_STEPS;
        int cur_idx = idx;
        while (cur_depth > 0) {
            const BeamNode &node = layers[cur_depth][cur_idx];
            prefix.push_back({node.act_i, node.act_j});
            cur_idx = node.parent_idx;
            cur_depth = node.parent_depth;
        }
        reverse(prefix.begin(), prefix.end());
        // suffix greedy
        auto suffix_res = greedy_plan(bn.st, rest_steps, -1);
        vector<pair<int, int>> full_plan;
        full_plan.reserve(T);
        full_plan.insert(full_plan.end(), prefix.begin(), prefix.end());
        full_plan.insert(full_plan.end(),
                         suffix_res.first.begin(),
                         suffix_res.first.end());
        try_update(full_plan, suffix_res.second);
    }

    /* Simulated annealing refinement */
    vector<State> pref;
    compute_prefix(best_plan, pref);
    __int128 cur_val = best_val;
    vector<pair<int, int>> cur_plan = best_plan;

    auto after_initial = chrono::high_resolution_clock::now();
    double elapsed_initial = chrono::duration<double>(after_initial - start_total).count();
    const double TIME_LIMIT = max(0.0, 1.98 - elapsed_initial);

    const long double MAX_TEMP = 1e14L;
    const long double MIN_TEMP = 1e6L;
    uniform_real_distribution<long double> unif01(0.0L, 1.0L);
    auto start_sa = chrono::high_resolution_clock::now();

    while (true) {
        double elapsed = chrono::duration<double>(chrono::high_resolution_clock::now() - start_sa).count();
        if (elapsed > TIME_LIMIT) break;
        long double progress = elapsed / TIME_LIMIT;
        long double temperature = MAX_TEMP * (1.0L - progress) + MIN_TEMP * progress;
        int op = int(rng() % 100);
        vector<pair<int, int>> cand = cur_plan;
        int earliest = T;

        if (op < 30) { // random changes
            int changes = 1 + int(rng() % 5);
            for (int c = 0; c < changes; ++c) {
                int pos = int(rng() % T);
                cand[pos] = random_legal_action(pref[pos], rng);
                earliest = min(earliest, pos);
            }
        } else if (op < 60) { // swap two
            int i = int(rng() % T);
            int j = int(rng() % T);
            if (i != j) {
                if (i > j) swap(i, j);
                swap(cand[i], cand[j]);
                earliest = min(earliest, i);
            }
        } else if (op < 80) { // move
            int i = int(rng() % T);
            int j = int(rng() % T);
            if (i != j) {
                if (i > j) swap(i, j);
                auto act = cand[i];
                cand.erase(cand.begin() + i);
                if (j > i) --j;
                cand.insert(cand.begin() + j, act);
                earliest = min(earliest, min(i, j));
            }
        } else if (op < 95) { // replace segment
            int s = int(rng() % T);
            int maxlen = min(10, T - s);
            int len = 1 + int(rng() % maxlen);
            if (rng() % 2 == 0) {
                // random segment
                State curst = pref[s];
                vector<pair<int, int>> newseg;
                newseg.reserve(len);
                for (int k = 0; k < len; ++k) {
                    auto act = random_legal_action(curst, rng);
                    if (act.first != -1) {
                        __int128 cost = (__int128)C[act.first][act.second] *
                                        (curst.P[act.first][act.second] + 1);
                        curst.apple -= cost;
                        ++curst.P[act.first][act.second];
                    }
                    newseg.emplace_back(act);
                    production(curst);
                }
                cand.erase(cand.begin() + s, cand.begin() + s + len);
                cand.insert(cand.begin() + s, newseg.begin(), newseg.end());
            } else {
                // greedy segment
                int bias = int(rng() % (N + 1)) - 1;
                State curst = pref[s];
                auto seg = greedy_plan(curst, len, bias);
                cand.erase(cand.begin() + s, cand.begin() + s + len);
                cand.insert(cand.begin() + s,
                            seg.first.begin(),
                            seg.first.end());
            }
            earliest = min(earliest, s);
        } else if (op < 98) { // replace suffix with greedy
            int s = int(rng() % T);
            State curst = pref[s];
            int bias = int(rng() % (N + 1)) - 1;
            auto suff = greedy_plan(curst, T - s, bias);
            cand.erase(cand.begin() + s, cand.end());
            cand.insert(cand.end(),
                        suff.first.begin(),
                        suff.first.end());
            earliest = s;
        } else { // replace single with best greedy
            int pos = int(rng() % T);
            State curst = pref[pos];
            int remain = T - pos - 1;
            int best_i = -1, best_j = -1;
            __int128 best_net = 0;
            for (int lvl = 0; lvl < L; ++lvl) {
                for (int id = 0; id < N; ++id) {
                    __int128 cost = (__int128)C[lvl][id] *
                                    (curst.P[lvl][id] + 1);
                    if (curst.apple < cost) continue;
                    __int128 net = net_gain(curst, remain, lvl, id) - cost;
                    if (net > best_net) {
                        best_net = net;
                        best_i = lvl;
                        best_j = id;
                    }
                }
            }
            cand[pos] = {best_i, best_j};
            earliest = min(earliest, pos);
        }

        auto eval = evaluate_candidate(cand, earliest, pref);
        if (!eval.first) continue;
        __int128 new_val = eval.second;
        __int128 delta = new_val - cur_val;
        bool accept = false;
        if (delta > 0) accept = true;
        else {
            long double prob = expl((long double)delta / temperature);
            if (unif01(rng) < prob) accept = true;
        }
        if (accept) {
            cur_plan.swap(cand);
            cur_val = new_val;
            recompute_prefix_from(cur_plan, pref, earliest);
            if (new_val > best_val) {
                best_val = new_val;
                best_plan = cur_plan;
            }
        }
    }

    /* Final local improvement */
    compute_prefix(best_plan, pref);
    bool improved = true;
    while (improved) {
        improved = false;
        for (int t = 0; t < T; ++t) {
            State &curst = pref[t];
            int remain = T - t - 1;
            int cur_i = best_plan[t].first;
            int cur_j = best_plan[t].second;
            __int128 cur_net = -1;
            if (cur_i != -1) {
                __int128 cost = (__int128)C[cur_i][cur_j] *
                                (curst.P[cur_i][cur_j] + 1);
                if (curst.apple >= cost) {
                    __int128 net = net_gain(curst, remain, cur_i, cur_j) - cost;
                    cur_net = net;
                }
            }
            __int128 best_net = max(cur_net, (__int128)0);
            int best_i = (best_net == 0 && cur_net < 0) ? -1 : cur_i;
            int best_j = (best_i == -1) ? -1 : cur_j;
            for (int i = 0; i < L; ++i) {
                for (int j = 0; j < N; ++j) {
                    __int128 cost = (__int128)C[i][j] *
                                    (curst.P[i][j] + 1);
                    if (curst.apple < cost) continue;
                    __int128 net = net_gain(curst, remain, i, j) - cost;
                    if (net > best_net) {
                        best_net = net;
                        best_i = i;
                        best_j = j;
                    }
                }
            }
            if (best_i == cur_i && best_j == cur_j) continue;
            best_plan[t] = {best_i, best_j};
            auto [ok, finalApple] = evaluate_candidate(best_plan, t, pref);
            if (ok && finalApple > pref[T].apple) {
                recompute_prefix_from(best_plan, pref, t);
                improved = true;
                break;
            } else {
                best_plan[t] = {cur_i, cur_j};
            }
        }
    }

    /* Output */
    for (auto &p : best_plan) {
        if (p.first == -1) cout << -1 << '\n';
        else cout << p.first << ' ' << p.second << '\n';
    }
    return 0;
}