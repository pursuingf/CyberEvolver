#![allow(non_snake_case, unused_macros)]

use itertools::Itertools;
use proconio::input;
use rand::prelude::*;
use std::{collections::BTreeSet, ops::RangeBounds};
use svg::node::element::{Circle, Group, Polygon, Rectangle, Style, Title};

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

const D: i64 = 100000;

#[derive(Clone, Debug)]
pub struct Input {
    N: usize,
    ps: Vec<(i64, i64)>,
}

impl std::fmt::Display for Input {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        writeln!(f, "{}", self.N)?;
        for &(x, y) in &self.ps {
            writeln!(f, "{} {}", x, y)?;
        }
        Ok(())
    }
}

pub fn parse_input(f: &str) -> Input {
    let f = proconio::source::once::OnceSource::from(f);
    input! {
        from f,
        N: usize,
        ps: [(i64, i64); 2 * N],
    }
    Input { N, ps }
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

pub struct Output {
    pub out: Vec<Vec<(i64, i64)>>,
}

pub fn parse_output(_input: &Input, f: &str) -> Result<Output, String> {
    let mut f = f.split_whitespace();
    let mut out = vec![];
    while let Some(m) = f.next() {
        let m = read(Some(m), 4..=1000)?;
        let mut poly = vec![];
        for _ in 0..m {
            poly.push((read(f.next(), 0..=D)?, read(f.next(), 0..=D)?));
        }
        out.push(poly);
    }
    Ok(Output { out })
}

pub fn gen(seed: u64) -> Input {
    let mut rng = rand_chacha::ChaCha20Rng::seed_from_u64(seed);
    let N = 5000;
    let mut ps = vec![];
    let mut set = BTreeSet::new();
    for iter in 0..2 {
        let n = rng.gen_range(10i32..=25i32) as usize;
        let ws = (0..n).map(|_| rng.gen::<f64>()).collect_vec();
        let cs = (0..n)
            .map(|_| (rng.gen_range(20000..=80000), rng.gen_range(20000..=80000)))
            .collect_vec();
        let sigma = (0..n)
            .map(|_| rng.gen_range(1000..=5000) as f64)
            .collect_vec();
        let is = (0..n).collect_vec();
        while set.len() < N * (1 + iter) {
            let i = *is.choose_weighted(&mut rng, |&i| ws[i]).unwrap();
            let (c, sigma) = (cs[i], sigma[i]);
            let bx: f64 = rng.sample(rand_distr::StandardNormal);
            let by: f64 = rng.sample(rand_distr::StandardNormal);
            let x = (c.0 as f64 + sigma * bx).round() as i64;
            let y = (c.1 as f64 + sigma * by).round() as i64;
            if 0 <= x && x <= D && 0 <= y && y <= D && set.insert((x, y)) {
                ps.push((x, y));
            }
        }
    }
    Input { N, ps }
}

pub fn compute_score(input: &Input, out: &Output) -> (i64, String) {
    let (mut score, err, _) = compute_score_details(input, out.out.last().unwrap_or(&vec![]));
    if err.len() > 0 {
        score = 0;
    }
    (score, err)
}

pub fn compute_score_details(input: &Input, out: &[(i64, i64)]) -> (i64, String, (Vec<bool>, i64)) {
    let mut len = 0;
    let mut covered = vec![false; input.N * 2];
    for i in 0..out.len() {
        let p = out[i];
        let q = out[(i + 1) % out.len()];
        let r = out[(i + 2) % out.len()];
        if p == q {
            return (
                0,
                "Two consecutive vertices share the same coordinates.".to_owned(),
                (covered, len),
            );
        } else if p.0 == q.0 {
            len += (p.1 - q.1).abs();
            if q.0 == r.0 && (p.1 - q.1) * (r.1 - q.1) > 0 {
                return (
                    0,
                    "The polygon is self-intersecting.".to_owned(),
                    (covered, len),
                );
            }
        } else if p.1 == q.1 {
            len += (p.0 - q.0).abs();
            if q.1 == r.1 && (p.0 - q.0) * (r.0 - q.0) > 0 {
                return (
                    0,
                    "The polygon is self-intersecting.".to_owned(),
                    (covered, len),
                );
            }
        } else {
            return (
                0,
                format!(
                    "The {}-th edge is not parallel to the axes. (({}, {}) - ({}, {}))",
                    i, p.0, p.1, q.0, q.1
                ),
                (covered, len),
            );
        }
    }
    for i in 0..out.len() {
        for j in 2..out.len() - 1 {
            let j = (i + j) % out.len();
            let p1 = out[i];
            let p2 = out[(i + 1) % out.len()];
            let q1 = out[j];
            let q2 = out[(j + 1) % out.len()];
            if p1.0.min(p2.0).max(q1.0.min(q2.0)) <= p1.0.max(p2.0).min(q1.0.max(q2.0)) {
                if p1.1.min(p2.1).max(q1.1.min(q2.1)) <= p1.1.max(p2.1).min(q1.1.max(q2.1)) {
                    return (
                        0,
                        "The polygon is self-intersecting.".to_owned(),
                        (covered, len),
                    );
                }
            }
        }
    }
    if len > 4 * D {
        return (
            0,
            format!("The length is too long: {}.", len),
            (covered, len),
        );
    }
    let mut score = 1;
    for i in 0..input.N * 2 {
        let r = input.ps[i];
        let mut inside = false;
        for j in 0..out.len() {
            let p = out[j];
            let q = out[(j + 1) % out.len()];
            let x0 = p.0.min(q.0);
            let x1 = p.0.max(q.0);
            let y0 = p.1.min(q.1);
            let y1 = p.1.max(q.1);
            if x0 <= r.0 && r.0 <= x1 && y0 <= r.1 && r.1 <= y1 {
                inside = true;
                break;
            }
            if p.1 == q.1 && p.1 > r.1 {
                if x0 <= r.0 && r.0 < x1 {
                    inside = !inside;
                }
            }
        }
        if inside {
            covered[i] = true;
            if i < input.N {
                score += 1;
            } else {
                score -= 1;
            }
        }
    }
    (score.max(0), String::new(), (covered, len))
}

/// 0 <= val <= 1
pub fn color(mut val: f64) -> String {
    val.setmin(1.0);
    val.setmax(0.0);
    let (r, g, b) = if val < 0.5 {
        let x = val * 2.0;
        (
            30. * (1.0 - x) + 144. * x,
            144. * (1.0 - x) + 255. * x,
            255. * (1.0 - x) + 30. * x,
        )
    } else {
        let x = val * 2.0 - 1.0;
        (
            144. * (1.0 - x) + 255. * x,
            255. * (1.0 - x) + 30. * x,
            30. * (1.0 - x) + 70. * x,
        )
    };
    format!(
        "#{:02x}{:02x}{:02x}",
        r.round() as i32,
        g.round() as i32,
        b.round() as i32
    )
}

pub fn rect(x: i64, y: i64, w: i64, h: i64, fill: &str) -> Rectangle {
    Rectangle::new()
        .set("x", x)
        .set("y", y)
        .set("width", w)
        .set("height", h)
        .set("fill", fill)
}

pub fn group(title: String) -> Group {
    Group::new().add(Title::new(title))
}

pub fn vis_default(input: &Input, out: &Output) -> (i64, String, String) {
    let VisResult {
        mut score,
        err,
        svg,
        ..
    } = vis(input, out.out.last().unwrap_or(&vec![]));
    if err.len() > 0 {
        score = 0;
    }
    (score, err, svg)
}

pub struct VisResult {
    pub score: i64,
    pub err: String,
    pub svg: String,
    pub length: i64,
}

pub fn vis(input: &Input, out: &[(i64, i64)]) -> VisResult {
    let W = 800;
    let H = 800;
    let (score, err, (covered, len)) = compute_score_details(input, &out);
    let mut doc = svg::Document::new()
        .set("id", "vis")
        .set("viewBox", (-500, -500, 101000, 101000))
        .set("width", W)
        .set("height", H)
        .set("style", "background-color:white");
    doc = doc.add(Style::new(format!(
        "text {{text-anchor: middle;dominant-baseline: central;}}"
    )));
    doc = doc.add(
        Polygon::new()
            .set(
                "points",
                out.iter()
                    .map(|&(x, y)| format!("{},{}", x, D - y))
                    .join(" "),
            )
            .set("fill", "lightgray")
            .set("stroke", "black")
            .set("stroke-width", 100),
    );
    for i in 0..input.N * 2 {
        doc = doc.add(
            group(format!(
                "({}, {})\n{}",
                input.ps[i].0,
                input.ps[i].1,
                if covered[i] && i < input.N {
                    "+1"
                } else if covered[i] {
                    "-1"
                } else {
                    "0"
                }
            ))
            .add(
                Circle::new()
                    .set("cx", input.ps[i].0)
                    .set("cy", D - input.ps[i].1)
                    .set("r", 150)
                    .set(
                        "fill",
                        if covered[i] {
                            if i < input.N {
                                "#ff303080"
                            } else {
                                "#3030ff80"
                            }
                        } else {
                            if i < input.N {
                                "#ffa0a080"
                            } else {
                                "#a0a0ff80"
                            }
                        },
                    ),
            ),
        );
    }
    VisResult {
        score,
        err,
        svg: doc.to_string(),
        length: len,
    }
}
