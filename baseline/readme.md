# Cyber Attack Agent Baseline Reproduction Guide

## 🎯 目标

本文档用于指导复现以下三个 benchmark 上的代表性 baseline（单 agent + 多 agent）：

* **NYU CTF Bench**
* **AutoPenBench**
* **CVE-Bench**

重点是：
👉 **选择社区认可 baseline + 能直接跑通 + reviewer 认可**

---

# 1. NYU CTF Bench

## ✅ 推荐 Baselines

| 类型      | 方法                    |
| ------- | --------------------- |
| 单 agent | NYUCTF Baseline Agent |
| 多 agent | D-CIPHER              |

---

## 📦 仓库

### NYUCTF Agents（官方）

* GitHub: [https://github.com/NYU-LLM-CTF/nyuctf_agents](https://github.com/NYU-LLM-CTF/nyuctf_agents)

包含：

* `nyuctf_baseline`（单 agent）
* `nyuctf_multiagent`（D-CIPHER）

---

## ⚙️ 安装

```bash
git clone https://github.com/NYU-LLM-CTF/nyuctf_agents.git
cd nyuctf_agents

pip install -r requirements.txt
```

下载数据：

```bash
bash scripts/download_data.sh
```

---

## ▶️ 运行

### 单 agent（baseline）

```bash
python run_baseline.py
```

### 多 agent（D-CIPHER）

```bash
python run_dcipher.py
```

---

## 📌 注意

* 官方 baseline → **必须跑（reviewer默认对照）**
* D-CIPHER → **主流多 agent baseline**
* 两者必须一起出现才合理

---

# 2. AutoPenBench

## ✅ 推荐 Baselines

| 类型      | 方法                           |
| ------- | ---------------------------- |
| 单 agent | Fully Autonomous Agent（论文原始） |
| 多 agent | VulnBot（推荐）                  |

---

## 📦 仓库

### AutoPenBench（官方 benchmark）

* GitHub: [https://github.com/lucagioacchini/auto-pen-bench](https://github.com/lucagioacchini/auto-pen-bench)

---

## ⚙️ 安装

```bash
git clone https://github.com/lucagioacchini/auto-pen-bench.git
cd auto-pen-bench

bash setup.sh
```

---

## ▶️ 运行

```bash
cd experiments
bash run_autonomous.sh
```

---

## 📌 说明

* 官方提供两类 agent：

  * fully autonomous（推荐 baseline）
  * human-assisted（❌ 不要用）
* 你的 baseline 应该选：

  * **fully autonomous version**

---

## 🧠 VulnBot（多 agent）

论文：

* [https://arxiv.org/abs/2501.13411](https://arxiv.org/abs/2501.13411)

⚠️ 状态：

* 部分实现可能未完全开源
* 可以：

  * 复现结构
  * 或作为对比引用

---

# 3. CVE-Bench

## ✅ 推荐 Baselines

| 类型      | 方法       |
| ------- | -------- |
| 单 agent | Cy-Agent |
| 多 agent | T-Agent  |

---

## 📦 仓库

### CVE-Bench（官方）

* GitHub: [https://github.com/uiuc-kang-lab/cve-bench](https://github.com/uiuc-kang-lab/cve-bench)

---

## ⚙️ 安装

```bash
git clone https://github.com/uiuc-kang-lab/cve-bench.git
cd cve-bench

pip install -r requirements.txt
```

---

## ▶️ 运行

```bash
python run_benchmark.py
```

---

## 📌 Baseline 说明

### Cy-Agent

* 来源：CyBench 系统
* 类型：单 agent（ReAct-style）
* 特点：

  * memory + action + observation loop

---

### T-Agent

* 多 agent（supervisor + hacker）
* 专门做 web exploitation

⚠️ 状态：

* **未发现公开 repo**
* 通常：

  * 作为论文 baseline 引用
  * 或 black-box 对比

---

# 📊 推荐复现优先级

## 第一阶段（必须跑通）

1. NYUCTF baseline
2. NYUCTF D-CIPHER
3. AutoPenBench autonomous agent
4. CVE-Bench benchmark

---

## 第二阶段（增强对比）

5. VulnBot（如可复现）
6. Cy-Agent（单独实现或找替代实现）

---

## 第三阶段（可选）

7. EnIGMA（作为补充强 baseline）

---

# ⚠️ 关键注意事项

## 1. 不要统一 baseline

每个 benchmark 用自己的：

* NYUCTF → NYUCTF agent
* AutoPenBench → autonomous agent
* CVE → Cy-Agent

---

## 2. 保持公平性

尽量统一：

* LLM（例如 GPT-4 / Claude）
* API 设置
* temperature / max tokens
* tool 权限

---

## 3. 明确 baseline 类型

---

# 4. 项目内 `common/agent_runtime/challenge_client.py` 使用说明

这一节的重点不是“`ChallengeClient` 内部有多少方法”，而是：

**如果你要把一个 baseline agent 框架接到这套 benchmark/runtime 上，`ChallengeClient` 应该怎么用。**

从接入视角看，baseline 真正要解决的是 3 件事：

1. 怎么把 agent 连到正确的靶机
2. 怎么让 prompt 渲染成对应 benchmark 的格式
3. 怎么在并发运行时不串题、不共享错 target

`ChallengeClient` 正好就是这 3 件事的交汇点。

## 4.1 先记住一句话：不要绕开 `ChallengeClient`

如果你要接 baseline，不推荐直接：

- 自己请求 `challenge_server`
- 自己解析 `services`
- 自己拼 target 地址
- 自己决定该不该并发共享 target

推荐做法是：

`baseline runner -> ChallengeClient -> challenge data (含 runtime overlay) -> agent`

因为在这个项目里，`ChallengeClient` 实际负责的是：

- benchmark 元数据统一
- runtime 启动
- target 地址解析
- runtime policy 记忆
- cleanup 生命周期

所以它不是一个“普通工具类”，而是：

**baseline 和靶机系统之间的标准适配层**

## 4.2 从 baseline 接入的角度，它解决什么

你可以把 `ChallengeClient` 看成一个函数：

`challenge_id + runtime policy -> chal_data`

这里的 `chal_data` 不是静态题目描述，而是：

`静态 metadata + 已启动 runtime + agent 应该读的 target 信息 + prompt 所需字段`

一个 baseline 真正需要的，就是这份 `chal_data`。

因为它同时包含：

- benchmark 侧信息  
  例如：
  - `benchmark_family`
  - `task`
  - `default_variant`
  - `variant_names`
  - `application_service_keys`
  - `proof_upload_service_key`

- runtime 侧信息  
  例如：
  - `target_status`
  - `target_info`
  - `runtime.run_id`
  - `runtime.network_name`
  - `runtime.scoring`

## 4.3 baseline 接入时，最重要的不是 metadata，而是这条链

实际接 baseline 时，推荐你按这条顺序理解：

1. 先用 `ChallengeClient` 拿到 `chal_data`
2. 再把 `chal_data` 传给 prompt 模板
3. 再把 `chal_data["target_info"]` 传给 agent/runtime
4. 最后由 `ChallengeClient` 做 teardown

也就是说：

- **靶机连接信息** 不应该从 benchmark 原文件里自己猜
- **prompt 内容** 不应该自己重新拼一套字符串
- **并发策略** 不应该由 baseline agent 自己硬编码

这些都应该由：

- `ChallengeClient`
- `benchmark prompt profiles`
- `runtime args`

三者配合解决

## 4.4 `ChallengeClientConfig` 要怎么配

入口配置类在 `common/agent_runtime/challenge_client.py` 里叫 `ChallengeClientConfig`。

最常用字段有这些：

- `benchmark_root`
  benchmark 根目录，默认是 `./benchmarks`

- `benchmark_sources`
  更推荐的方式。显式告诉 manager 去哪里发现 benchmark，以及每一类 benchmark 用哪个 adapter

- `run_mode`
  可选：
  - `remote`
  - `local`

- `server_url`
  `remote` 模式下 `challenge_server` 的地址

- `use_ssh_tunnel`
  是否通过 SSH 跳板访问远端 `challenge_server` 和服务端口

- `use_external_access`
  这是 baseline 接入时非常关键的一个开关。它决定 `target_info[*].host/port` 最终给 agent 的是：
  - 外部地址
  - 还是容器内网地址

- `host_ip_for_agent`
  开了 SSH tunnel 时，给 agent 使用的宿主机侧地址

一个很实用的理解方式是：

- 如果你的 baseline agent 运行在宿主机上  
  通常更适合 `use_external_access=True`

- 如果你的 baseline agent 运行在和靶机同一个 Docker 网络的 sandbox 里  
  通常更适合 `use_external_access=False`

这样上层 agent 代码可以只消费：

- `target_info[*]["host"]`
- `target_info[*]["port"]`

而不用自己决定该用 `inner_ip` 还是 `external_port`

## 4.5 `ChallengeClient` 最常见的用法

最常用的方法是：

```python
challenge = challenge_client.get_challenge_data(
    challenge_id,
    auto_init=True,
    runtime_args=None,
)
```

它会做三件事：

1. 先从 benchmark registry 里拿静态 metadata
2. 看本地 runtime cache 里有没有已启动实例
3. 如果需要，再通过 backend 去真正启动靶机

返回值可以理解成：

`challenge metadata + runtime overlay`

对于 baseline，最值得直接读的字段是：

- `benchmark_family`
- `task`
- `target_status`
- `target_info`
- `runtime`
- `default_variant`
- `variant_names`

其中：

### `target_info`

这是 baseline 最应该依赖的连接信息。

结构大致是：

```python
{
  "target": {
    "host": "...",
    "port": ...,
    "inner_host": "...",
    "inner_ip": "...",
    "inner_port": ...,
    "external_host": "...",
    "external_port": ...,
    "url": "...",
    "netcat": "..."
  },
  ...
}
```

这里最重要的约定是：

- `host/port` 是当前 access policy 处理后的默认入口
- baseline agent 正常应该优先读它
- 不要自己硬编码 `target:9090`
- 不要自己从 `application_url` 里解析 host

### `runtime`

这个字段是 runtime 上下文，常见结构是：

```python
{
  "run_id": "...",
  "project_name": "...",
  "network_name": "...",
  "scoring": {...},
  "debug": {...}
}
```

这里对 baseline 接入最重要的是：

- `runtime.run_id`
- `runtime.network_name`
- `runtime.scoring`

## 4.6 baseline 到靶机的连接，正确姿势是什么

这是最容易绕错的地方。

### 正确做法

让 baseline agent 只读 `chal_data["target_info"]`。

例如：

```python
target = chal_data["target_info"]["target"]
host = target["host"]
port = target["port"]
```

### 不推荐做法

- 从 benchmark 原始 `metadata.application_url` 里硬拆 host/port
- 自己写 `target:9090`
- 自己猜应该访问 `inner_ip` 还是 `external_port`

为什么不推荐：

- benchmark 原始 metadata 的语义和 agent 真正该访问的地址，不一定总是一回事
- `ChallengeClient` 已经根据：
  - `use_external_access`
  - SSH tunnel
  - remote backend 返回的 services
  处理过一轮

baseline 再自己解释一遍，很容易和 runtime policy 打架

## 4.7 prompt 要怎么接，才不会和 benchmark 语义错位

这个项目里，prompt 的正确接法不是在 Python 里硬拼一大串字符串，而是：

**把完整 `chal_data` 传进 benchmark-family 对应的模板。**

当前 prompt profile 的分层是：

- 默认模板：`gen0_root/skill_based/`
- benchmark 覆盖：`benchmarks/prompt_profiles/<family>/`
- variant 覆盖：`benchmarks/prompt_profiles/<family>/<variant>/`

实际物化逻辑在：

- [utils/prompt_profiles.py](/data/pxd-team/workspace/fyh/evolve_ctf_agent/utils/prompt_profiles.py)
- [evolve/orchestrator.py](/data/pxd-team/workspace/fyh/evolve_ctf_agent/evolve/orchestrator.py)

对 baseline 接入来说，最重要的结论是：

- prompt 模板应该吃 `chal_data`
- 而不是再做一层 benchmark-specific Python 拼接

当前 `skill_based` agent 的实例模板渲染方式就是：

```python
Template(self.prompt_templates.instance_prompt_template).render(instance_data=chal_data)
```

这意味着：

- 只要 `chal_data` 里字段对
- benchmark-family 和 variant 选对
- prompt 就能自动切到对应 benchmark 的模板

### variant 切换

如果 benchmark 支持多个 prompt variant，例如 `cvebench` 的：

- `zero_day`
- `one_day`

推荐做法是：

- 静态默认值放在 `challenge.json["default_variant"]`
- 运行时覆盖通过 runner 参数控制

在当前项目里，对应的是：

- `--prompt-variant zero_day`
- `--prompt-variant one_day`

## 4.8 并发到底应该怎么跑

这部分是 baseline 接入里最容易踩坑的第二个点。

当前项目里，真正影响并发语义的 runtime args 很少，核心就两个：

- `parallel_mode`
- `target_scope`

### `parallel_mode`

它告诉 `challenge_server` challenge runtime 应该按什么隔离策略启动。

当前 `cvebench` 常见的是：

- `parallel_mode=network`

这表示：

- challenge 主要依赖网络隔离来并行
- 不应该靠随机改 prompt 里的 host 名来解决并发

### `target_scope`

可选值：

- `per_challenge`
- `per_agent`

默认规则来自 `utils/runtime_policy.py`：

- `cvebench -> per_agent`
- 其他 benchmark -> `per_challenge`

这两个值对 baseline 的意义非常直接：

- `per_challenge`
  一道题共享一份 target，多个 sample/agent 可以共用

- `per_agent`
  每个 agent/sample 必须起自己那份靶机群

所以如果你要接的是：

### 普通 CTF baseline

通常更接近：

```python
runtime_args = {
    "target_scope": "per_challenge",
}
```

### CVE-Bench baseline

通常应该是：

```python
runtime_args = {
    "parallel_mode": "network",
    "target_scope": "per_agent",
}
```

这也是现在 `cvebench` 能做到：

**一个 agent 对应一个靶机群**

的关键。

## 4.9 为什么 `run_evolve_batch_skill.py` 里会有两个 `ChallengeClient`

这是因为它本身就在兼容两种 target scope。

### 全局 manager

在 `main()` 里先创建一个全局 `challenge_client`，它主要负责：

- challenge 发现
- challenge 提交阶段拿 metadata
- 全局 cleanup

### sample-local manager

如果运行时判定当前题目是 `target_scope=per_agent`，`run_node_task()` 里会再创建一个 sample-local `ChallengeClient`。

这个 manager 只服务当前 sample，用完就 teardown。

所以从 baseline 的视角理解就是：

- 如果 benchmark 允许共享 target  
  一个 manager 足够

- 如果 benchmark 要求一个 agent 一份靶机群  
  就要有 sample-local manager，或者等价的 sample-local runtime handle

## 4.10 baseline 接入的推荐最小模板

如果你要把一个 baseline 接到这套系统里，推荐按下面这个最小模式走：

```python
from common.agent_runtime.challenge_client import ChallengeClient, ChallengeClientConfig

config = ChallengeClientConfig(
    benchmark_root="./benchmarks",
    run_mode="remote",
    server_url="http://10.1.2.146:7900",
    use_external_access=True,
)

mgr = ChallengeClient(config=config)

runtime_args = {
    "parallel_mode": "network",
    "target_scope": "per_agent",
}

try:
    chal_data = mgr.get_challenge_data(
        "cvb-CVE-2024-30542",
        auto_init=True,
        runtime_args=runtime_args,
    )

    target = chal_data["target_info"]["target"]
    host = target["host"]
    port = target["port"]

    # 这里不要自己再拼 target 地址
    # 直接把 chal_data 整体交给 prompt / agent
    print(host, port)
    print(chal_data["runtime"])
finally:
    mgr.finish_challenge("cvb-CVE-2024-30542")
    mgr.close()
```

如果你自己的 baseline 框架还有 prompt 模板层，建议继续遵守这个约定：

- `chal_data` 整体传给模板
- 地址从 `target_info` 读
- 不自己二次解释 benchmark 原始 metadata

## 4.11 baseline 接入时最常见的坑

### 1. 把 `target_info` 当成静态字段

不是。  
它是静态 metadata 和 runtime 叠出来的结果。

### 2. 忘了 cleanup

如果不显式调：

- `finish_challenge(...)`
- `close()`

就很容易留下：

- 远端靶机
- Docker network
- SSH tunnel

### 3. `per_agent` 题目提前共享启动

这会导致：

- challenge 提交阶段先起一份共享 target
- sample 运行时又再起一份自己的 target

最后行为会很乱。

### 4. 自己写 benchmark-specific target 地址

比如：

- `target:9090`
- `target:9091/done`

这些都不应该在 baseline runner 里硬编码。  
应该让：

- `ChallengeClient`
- prompt profile
- `chal_data`

一起决定最终怎么访问。

### 5. prompt 仍然走默认 `skill_based`

如果你希望 benchmark prompt 正确切换，要确保：

- `chal_data["benchmark_family"]` 是对的
- `chal_data["default_variant"]` 是对的
- 根节点初始化时确实物化了对应的 prompt profile

否则 agent 很容易退回默认模板。

## 4.12 一句话总结

如果从“把 baseline agent 真正接上靶机系统”的视角看，`ChallengeClient` 最准确的定位是：

**“把 benchmark 元数据、runtime 启动、target 地址选择、prompt 所需字段、以及 cleanup 生命周期统一收口的接入层。”**

所以如果你的目标是：

- baseline 能连上正确 target
- prompt 能切到正确 benchmark
- 多 agent / 多 sample 并发时不串题

那最该优先依赖的不是 `challenge_server` 细节，而是 `ChallengeClient + chal_data + prompt profile + runtime args` 这套组合。

论文中必须区分：

* benchmark-native baseline
* external baseline（VulnBot / T-Agent）

---

# ✅ 最终推荐组合

| Benchmark    | 单 agent          | 多 agent  |
| ------------ | ---------------- | -------- |
| NYUCTF       | NYUCTF agent     | D-CIPHER |
| AutoPenBench | Autonomous agent | VulnBot  |
| CVE-Bench    | Cy-Agent         | T-Agent  |

---

# 🚀 一句话总结

👉 **NYUCTF 用官方 baseline，AutoPenBench 用 autonomous，CVE 用 Cy-Agent，对应多 agent 分别用 D-CIPHER / VulnBot / T-Agent**
