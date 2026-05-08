#![allow(non_snake_case, unused_macros)]

use itertools::Itertools;
use proconio::input;
use rand::prelude::*;
use serde::{Deserialize, Serialize};
use std::ops::RangeBounds;
use svg::node::{
    element::{Group, Rectangle, Title},
    Text,
};

pub trait SetMinMax {
    fn setmin(&mut self, v: Self) -> bool;
    fn setmax(&mut self, v: Self) -> bool;
}
impl<T> SetMinMax for T
where
    T: PartialOrd,
{
    fn setmin(&mut self, v: T) -> bool {
        *self > v && {
            *self = v;
            true
        }
    }
    fn setmax(&mut self, v: T) -> bool {
        *self < v && {
            *self = v;
            true
        }
    }
}

#[macro_export]
macro_rules! mat {
	($($e:expr),*) => { Vec::from(vec![$($e),*]) };
	($($e:expr,)*) => { Vec::from(vec![$($e),*]) };
	($e:expr; $d:expr) => { Vec::from(vec![$e; $d]) };
	($e:expr; $d:expr $(; $ds:expr)+) => { Vec::from(vec![mat![$e $(; $ds)*]; $d]) };
}

#[derive(Clone, Debug)]
pub struct Input {
    pub N: usize,
    pub L: usize,
    pub T: usize,
    pub K: i64,
    pub A: Vec<i64>,
    pub C: Vec<Vec<i64>>,
}

impl std::fmt::Display for Input {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(f, "{} {} {} {}", self.N, self.L, self.T, self.K)?;
        writeln!(f, "{}", self.A.iter().join(" "))?;
        for level in 0..self.L {
            writeln!(f, "{}", self.C[level].iter().join(" "))?;
        }
        Ok(())
    }
}

pub fn parse_input(f: &str) -> Input {
    let f = proconio::source::once::OnceSource::from(f);
    input! {
        from f,
        N: usize, L: usize, T: usize, K: i64,
        A: [i64; N],
        C: [[i64; N]; L],
    }
    Input { N, L, T, K, A, C }
}

pub fn read<T: Copy + PartialOrd + std::fmt::Display + std::str::FromStr, R: RangeBounds<T>>(
    token: Option<&str>,
    range: R,
) -> Result<T, String> {
    if let Some(v) = token {
        if let Ok(v) = v.parse::<T>() {
            if !range.contains(&v) {
                Err(format!("Out of range: {}", v))
            } else {
                Ok(v)
            }
        } else {
            Err(format!("Parse error: {}", v))
        }
    } else {
        Err("Unexpected EOF".to_owned())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum Action {
    Upgrade(usize, usize),
    Nothing,
}

pub struct Output {
    pub actions: Vec<Action>,
}

pub fn parse_output(input: &Input, f: &str) -> Result<Output, String> {
    let mut actions = vec![];
    for line in f.lines() {
        let line = line.trim();
        if line.len() == 0 || line.starts_with('#') {
            continue;
        }
        let mut tokens = line.split_whitespace();
        let first = read(tokens.next(), -1..=input.L as i32 - 1)?;
        if first == -1 {
            actions.push(Action::Nothing);
        } else {
            let level = first as usize;
            let id = read(tokens.next(), 0..input.N)?;
            actions.push(Action::Upgrade(level, id));
        }
        if tokens.next().is_some() {
            return Err(format!("Too many tokens: {}", line));
        }
        if actions.len() == input.T {
            break;
        }
    }
    if actions.len() < input.T {
        return Err(format!(
            "Not enough actions: expected {}, got {}",
            input.T,
            actions.len()
        ));
    }
    Ok(Output { actions })
}

pub struct PartialOutput {
    pub actions: Vec<Action>,
    pub parse_error: Option<String>,
}

pub fn parse_output_partial(input: &Input, f: &str) -> PartialOutput {
    let mut actions = vec![];
    let mut parse_error: Option<String> = None;
    let mut line_num = 0;
    for line in f.lines() {
        let line = line.trim();
        if line.len() == 0 || line.starts_with('#') {
            continue;
        }
        line_num += 1;
        let mut tokens = line.split_whitespace();
        let first = match read(tokens.next(), -1..=input.L as i32 - 1) {
            Ok(v) => v,
            Err(e) => {
                parse_error = Some(format!(
                    "Line {}: Invalid level - {} (valid: -1 to {})",
                    line_num,
                    e,
                    input.L - 1
                ));
                break;
            }
        };
        if first == -1 {
            actions.push(Action::Nothing);
        } else {
            let level = first as usize;
            let id = match read(tokens.next(), 0..input.N) {
                Ok(v) => v,
                Err(e) => {
                    parse_error = Some(format!(
                        "Line {}: Invalid id - {} (valid: 0 to {})",
                        line_num,
                        e,
                        input.N - 1
                    ));
                    break;
                }
            };
            actions.push(Action::Upgrade(level, id));
        }
        if tokens.next().is_some() {
            parse_error = Some(format!("Line {}: Too many tokens", line_num));
            break;
        }
        if actions.len() == input.T {
            break;
        }
    }
    PartialOutput {
        actions,
        parse_error,
    }
}

pub fn gen(seed: u64) -> Input {
    let mut rng = rand_chacha::ChaCha20Rng::seed_from_u64(seed);

    let N = 10;
    let L = 4;
    let T = 500;
    let K = 1;

    // Generate A
    let mut A = vec![0; N];
    A[0] = 1;
    for i in 1..N {
        A[i] = 10.0_f64.powf(rng.gen_range(0.0..=2.0)).round() as i64;
    }
    A.sort();

    let mut C = vec![vec![0; N]; L];
    for level in 0..L {
        for id in 0..N {
            if level == 0 && id == 0 {
                C[level][id] = 1;
            } else {
                let base = (A[id] as f64) * f64::powi(500.0, level as i32);
                let multiplier = 10.0_f64.powf(rng.gen_range(0.0..=2.0));
                C[level][id] = (base * multiplier).round() as i64;
            }
        }
    }

    Input { N, L, T, K, A, C }
}

pub fn compute_score(input: &Input, out: &Output) -> (i64, String) {
    let (mut score, err, _) = compute_score_details(input, &out.actions);
    if err.len() > 0 {
        score = 0;
    }
    (score, err)
}

pub fn compute_score_details(input: &Input, actions: &[Action]) -> (i64, String, ()) {
    let mut B = vec![vec![1i64; input.N]; input.L];
    let mut P = vec![vec![0i64; input.N]; input.L];
    let mut apples = input.K;

    for t in 0..input.T {
        match actions[t] {
            Action::Upgrade(level, id) => {
                let cost = input.C[level][id] * (P[level][id] + 1);
                if apples < cost {
                    return (
                        0,
                        format!(
                            "Not enough apples at turn {}: have {}, need {}",
                            t, apples, cost
                        ),
                        (),
                    );
                }
                apples -= cost;
                P[level][id] += 1;
            }
            Action::Nothing => {}
        }

        for level in 0..input.L {
            for id in 0..input.N {
                if level == 0 {
                    apples += input.A[id] * B[level][id] * P[level][id];
                } else {
                    B[level - 1][id] += B[level][id] * P[level][id];
                }
            }
        }
    }

    if apples <= 0 {
        return (0, format!("Non-positive apples at the end: {}", apples), ());
    }

    let score = (100000.0 * (apples as f64).log2()).round() as i64;
    (score, String::new(), ())
}

pub fn color(mut val: f64) -> String {
    val.setmin(1.0);
    val.setmax(0.0);
    let (r, g, b) = if val < 0.5 {
        let x = val * 2.0;
        (
            200. * (1.0 - x) + 200. * x,
            220. * (1.0 - x) + 235. * x,
            255. * (1.0 - x) + 200. * x,
        )
    } else {
        let x = val * 2.0 - 1.0;
        (
            200. * (1.0 - x) + 255. * x,
            235. * (1.0 - x) + 200. * x,
            200. * (1.0 - x) + 200. * x,
        )
    };
    format!(
        "#{:02x}{:02x}{:02x}",
        r.round() as i32,
        g.round() as i32,
        b.round() as i32
    )
}

pub fn rect(x: usize, y: usize, w: usize, h: usize, fill: &str) -> Rectangle {
    Rectangle::new()
        .set("x", x)
        .set("y", y)
        .set("width", w)
        .set("height", h)
        .set("fill", fill)
}

pub fn group(title: String) -> Group {
    Group::new().add(Title::new().add(Text::new(title)))
}

#[derive(Clone, Serialize, Deserialize)]
struct VisState {
    B: Vec<Vec<i64>>,
    P: Vec<Vec<i64>>,
    apples: i64,
    apples_per_turn: i64,
    action: Option<Action>,
}

pub fn vis_default(input: &Input, out: &Output) -> (i64, String, String) {
    let partial = PartialOutput {
        actions: out.actions.clone(),
        parse_error: None,
    };
    vis_partial(input, &partial)
}

pub fn vis_partial(input: &Input, partial: &PartialOutput) -> (i64, String, String) {
    let actions = &partial.actions;

    let mut states = vec![];
    let mut B = vec![vec![1i64; input.N]; input.L];
    let mut P = vec![vec![0i64; input.N]; input.L];
    let mut apples = input.K;
    let mut sim_error: Option<String> = None;

    states.push(VisState {
        B: B.clone(),
        P: P.clone(),
        apples,
        apples_per_turn: 0,
        action: None,
    });

    for t in 0..input.T {
        if t >= actions.len() {
            break;
        }

        let action = actions[t].clone();
        match &action {
            Action::Upgrade(level, id) => {
                let cost = input.C[*level][*id] * (P[*level][*id] + 1);
                if apples < cost {
                    sim_error = Some(format!(
                        "Turn {}: Not enough apples (have {}, need {})",
                        t, apples, cost
                    ));
                    break;
                }
                apples -= cost;
                P[*level][*id] += 1;
            }
            Action::Nothing => {}
        }

        let mut apples_per_turn: i64 = 0;
        for id in 0..input.N {
            apples_per_turn += input.A[id] * B[0][id] * P[0][id];
        }

        for level in 0..input.L {
            for id in 0..input.N {
                if level == 0 {
                    apples += input.A[id] * B[level][id] * P[level][id];
                } else {
                    B[level - 1][id] += B[level][id] * P[level][id];
                }
            }
        }

        states.push(VisState {
            B: B.clone(),
            P: P.clone(),
            apples,
            apples_per_turn,
            action: Some(action),
        });
    }

    let num_states = states.len();
    let max_turn = if num_states > 0 { num_states - 1 } else { 0 };

    let err = if let Some(ref e) = partial.parse_error {
        e.clone()
    } else if let Some(ref e) = sim_error {
        e.clone()
    } else if actions.len() < input.T {
        format!("Incomplete output: {} / {} actions", actions.len(), input.T)
    } else {
        String::new()
    };

    let final_apples = if num_states > 0 {
        states[num_states - 1].apples
    } else {
        input.K
    };
    let score = if final_apples > 0
        && sim_error.is_none()
        && partial.parse_error.is_none()
        && actions.len() == input.T
    {
        (100000.0 * (final_apples as f64).log2()).round() as i64
    } else {
        0
    };

    let mut html = String::new();
    html.push_str("<html><head><style>");
    html.push_str("body { font-family: monospace; margin: 20px; }");
    html.push_str("table { border-collapse: collapse; margin: 20px 0; table-layout: fixed; }");
    html.push_str(
        "th, td { border: 1px solid #ccc; padding: 8px; text-align: center; width: 90px; overflow: hidden; font-size: 13px; }",
    );
    html.push_str("th { background-color: #f0f0f0; }");
    html.push_str(".info { margin: 10px 0; font-size: 14px; }");
    html.push_str(".action { color: blue; font-weight: bold; }");
    html.push_str(".error { color: red; font-weight: bold; }");
    html.push_str(".apples-per-turn { color: green; }");
    html.push_str(".controls { margin: 20px 0; }");
    html.push_str("</style></head><body>");

    html.push_str(&format!("<h2>Score: {}</h2>", score));
    if !err.is_empty() {
        html.push_str(&format!("<div class='error'>Error: {}</div>", err));
    }

    html.push_str("<div class='controls'>");
    html.push_str(&format!("Turn: <input type='range' id='turnSlider' min='0' max='{}' value='{}' style='width: 400px;'> ", max_turn, max_turn));
    html.push_str(&format!(
        "<span id='turnLabel'>{}</span> / {}",
        max_turn, input.T
    ));
    html.push_str("</div>");

    html.push_str("<div class='controls'>");
    html.push_str("Color mode: <select id='colorMode' onchange='showTurn(currentTurn)'>");
    html.push_str("<option value='3'>P</option>");
    html.push_str("<option value='2'>log(B)</option>");
    html.push_str("<option value='1'>Cost efficiency</option>");
    html.push_str("</select>");
    html.push_str("</div>");

    html.push_str("<div id='state'></div>");

    html.push_str("<script>\n");
    html.push_str("const states = ");
    html.push_str(&serde_json::to_string(&states).unwrap_or("[]".to_string()));
    html.push_str(";\n");
    html.push_str(&format!(
        "const inputData = {{N: {}, L: {}, T: {}, A: {}, C: {}}};\n",
        input.N,
        input.L,
        input.T,
        serde_json::to_string(&input.A).unwrap_or("[]".to_string()),
        serde_json::to_string(&input.C).unwrap_or("[]".to_string())
    ));

    html.push_str(r#"
    let currentTurn = states.length - 1;

    function formatB(n) {
        if (n === 0) return "0";
        const absN = Math.abs(n);
        const exp = Math.floor(Math.log10(absN));
        if (exp <= 3) {
            return String(n);
        } else {
            const mantissa = absN / Math.pow(10, exp);
            const sign = n < 0 ? "-" : "";
            return sign + mantissa.toFixed(3) + "E" + exp;
        }
    }

    function valueToColor(val) {
        val = Math.max(0, Math.min(1, val));
        let r, g, b;
        if (val < 0.5) {
            const t = val * 2.0;
            r = Math.round(200.0 * (1.0 - t) + 200.0 * t);
            g = Math.round(220.0 * (1.0 - t) + 235.0 * t);
            b = Math.round(255.0 * (1.0 - t) + 200.0 * t);
        } else {
            const t = (val - 0.5) * 2.0;
            r = Math.round(200.0 * (1.0 - t) + 255.0 * t);
            g = Math.round(235.0 * (1.0 - t) + 200.0 * t);
            b = Math.round(200.0 * (1.0 - t) + 200.0 * t);
        }
        return '#' + r.toString(16).padStart(2, '0') + g.toString(16).padStart(2, '0') + b.toString(16).padStart(2, '0');
    }

    const costEff = [];
    for (let level = 0; level < inputData.L; level++) {
        costEff[level] = [];
        for (let id = 0; id < inputData.N; id++) {
            const base = inputData.A[id] * Math.pow(500, level);
            if (base > 0 && inputData.C[level][id] > 0) {
                costEff[level][id] = Math.log10(inputData.C[level][id] / base);
            } else {
                costEff[level][id] = 0;
            }
        }
    }

    function isLastAction(state, level, id) {
        return state.action && state.action.Upgrade && state.action.Upgrade[0] === level && state.action.Upgrade[1] === id;
    }

    function getCellColor(state, level, id, colorMode) {
        if (colorMode === 1) {
            const val = costEff[level][id] / 2.0;
            return valueToColor(val);
        } else if (colorMode === 2) {
            let maxLogB = 0;
            for (let l = 0; l < inputData.L; l++) {
                for (let i = 0; i < inputData.N; i++) {
                    if (state.B[l][i] > 0) {
                        maxLogB = Math.max(maxLogB, Math.log10(state.B[l][i]));
                    }
                }
            }
            if (maxLogB > 0 && state.B[level][id] > 1) {
                const val = Math.log10(state.B[level][id]) / maxLogB;
                return valueToColor(val);
            } else {
                return valueToColor(0);
            }
        } else {
            let maxP = 0;
            for (let l = 0; l < inputData.L; l++) {
                for (let i = 0; i < inputData.N; i++) {
                    maxP = Math.max(maxP, state.P[l][i]);
                }
            }
            if (maxP > 0 && state.P[level][id] > 0) {
                const val = state.P[level][id] / maxP;
                return valueToColor(val);
            } else {
                return valueToColor(0);
            }
        }
    }

    function showTurn(t) {
        if (t >= states.length) t = states.length - 1;
        currentTurn = t;
        const state = states[t];
        const colorMode = parseInt(document.getElementById('colorMode').value);
        const score = state.apples > 0 ? Math.round(100000 * Math.log2(state.apples)) : 0;
        let html = '<div class="info">Turn: ' + t + ' / ' + inputData.T + '  |  Apples: ' + formatB(state.apples) + '  |  Score: ' + score + '</div>';
        html += '<div class="apples-per-turn">Apples per turn: ' + formatB(state.apples_per_turn) + '</div>';

        if (state.action) {
            if (state.action.Upgrade) {
                html += '<div class="action">Action: Upgrade level=' + state.action.Upgrade[0] + ', id=' + state.action.Upgrade[1] + '</div>';
            } else if (state.action === "Nothing") {
                html += '<div class="action">Action: Nothing</div>';
            }
        } else {
            html += '<div class="info">Initial state</div>';
        }

        html += '<h3>Machines</h3><table><tr><th>Level \\ ID</th>';
        for (let id = 0; id < inputData.N; id++) {
            html += '<th>ID ' + id + '<br>A=' + inputData.A[id] + '</th>';
        }
        html += '</tr>';

        for (let level = 0; level < inputData.L; level++) {
            html += '<tr><th>Level ' + level + '</th>';
            for (let id = 0; id < inputData.N; id++) {
                const cost = inputData.C[level][id] * (state.P[level][id] + 1);
                const bgColor = getCellColor(state, level, id, colorMode);
                const borderStyle = isLastAction(state, level, id) ? 'border: 3px solid #ffcc00;' : '';
                html += '<td style="background-color:' + bgColor + ';' + borderStyle + '">B=' + formatB(state.B[level][id]) + '<br>P=' + state.P[level][id] + '<br>C=' + formatB(cost) + '</td>';
            }
            html += '</tr>';
        }
        html += '</table>';

        document.getElementById('state').innerHTML = html;
        document.getElementById('turnLabel').textContent = t;
    }

    document.getElementById('turnSlider').addEventListener('input', function(e) {
        showTurn(parseInt(e.target.value));
    });

    showTurn(states.length - 1);
    "#);

    html.push_str("</script></body></html>");

    (score, err, html)
}

pub fn vis(input: &Input, actions: &[Action]) -> (i64, String, String) {
    let partial = PartialOutput {
        actions: actions.to_vec(),
        parse_error: None,
    };
    vis_partial(input, &partial)
}
