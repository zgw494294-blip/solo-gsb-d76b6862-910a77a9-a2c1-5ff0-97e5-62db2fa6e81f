"""闭环供水管网稳态水力求解器。

模型
----
- 管段 k（录入方向 i -> j，阻力 r_k > 0）上的有符号流量 q_k 满足
      H_i - H_j = r_k * q_k * |q_k|
  即 q_k = s * sqrt(|H_i - H_j| / r_k)，s = sign(H_i - H_j)。
- 各用水（需水）节点：流入该节点的净流量 = 需水量 d >= 0。
- 恰有一个水源节点，水头固定为 H0（压力边界）；其流入水量即水源总供水量。

以 m 个自由节点水头为未知量，用 SciPy 的牛顿型求解器（hybr，解析雅可比）
求节点质量守恒残差 F(H)=0，再按 1e-6 容差独立复核守恒与逐段压降。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.optimize import root

from .errors import NetworkError

TOL = 1e-6  # 用户要求的校验容差
_SQRT_EPS = 1e-12  # dH 趋近 0 时导数正则化，避免 1/sqrt 奇异


def _num(value: Any, what: str) -> float:
    """把 JSON 传来的数值转 float，并拒绝 None/布尔/NaN/Inf。"""
    if isinstance(value, bool) or value is None:
        raise NetworkError(f"{what}必须是数值")
    try:
        x = float(value)
    except (TypeError, ValueError):
        raise NetworkError(f"{what}必须是数值")
    if not math.isfinite(x):
        raise NetworkError(f"{what}必须是有限数值（不能为 NaN 或无穷大）")
    return x


def validate_and_solve(payload: Any) -> dict:
    """校验输入并求稳态解。成功返回结果字典，失败抛 NetworkError。"""

    if not isinstance(payload, dict):
        raise NetworkError("请求体必须是 JSON 对象")

    raw_nodes = payload.get("nodes")
    raw_pipes = payload.get("pipes")
    if not isinstance(raw_nodes, list) or not isinstance(raw_pipes, list):
        raise NetworkError("nodes 与 pipes 必须是数组")

    # ---------------- 节点校验 ----------------
    n = len(raw_nodes)
    if n < 2 or n > 10:
        raise NetworkError(f"节点数必须在 2～10 个之间（当前 {n} 个）")

    node_ids: list[str] = []
    seen: set[str] = set()
    for k, nd in enumerate(raw_nodes):
        if not isinstance(nd, dict):
            raise NetworkError(f"第 {k + 1} 个节点格式不正确")
        nid = nd.get("id")
        if not isinstance(nid, str) or not nid.strip():
            raise NetworkError(f"第 {k + 1} 个节点缺少有效的 id")
        nid = nid.strip()
        if nid in seen:
            raise NetworkError(f"节点 id 必须唯一：“{nid}”重复出现")
        seen.add(nid)
        node_ids.append(nid)

    sources = [k for k, nd in enumerate(raw_nodes) if nd.get("type") == "source"]
    if len(sources) != 1:
        raise NetworkError(f"必须恰有 1 个水源节点（当前有 {len(sources)} 个）")
    src = sources[0]

    fixed_head = [math.nan] * n
    demand = [0.0] * n
    for k, nd in enumerate(raw_nodes):
        if k == src:
            if nd.get("fixed_head") is None:
                raise NetworkError(f"水源节点“{node_ids[k]}”必须指定固定水头")
            h = _num(nd.get("fixed_head"), f"水源节点“{node_ids[k]}”的固定水头")
            fixed_head[k] = h
        else:
            if nd.get("demand") is None:
                raise NetworkError(f"用水节点“{node_ids[k]}”必须填写需水量")
            d = _num(nd.get("demand"), f"用水节点“{node_ids[k]}”的需水量")
            if d < 0.0:
                raise NetworkError(f"用水节点“{node_ids[k]}”的需水量不能为负（当前 {d:g}）")
            demand[k] = d

    # ---------------- 管段校验 ----------------
    p = len(raw_pipes)
    if p < 1 or p > 16:
        raise NetworkError(f"管段数必须在 1～16 根之间（当前 {p} 根）")

    pipe_id: list[str] = []
    pipe_a: list[int] = []
    pipe_b: list[int] = []
    pipe_r: list[float] = []
    seen_pairs: set[frozenset[int]] = set()
    seen_pid: set[str] = set()
    for k, pp in enumerate(raw_pipes):
        if not isinstance(pp, dict):
            raise NetworkError(f"第 {k + 1} 根管段格式不正确")
        pid = pp.get("id")
        if not isinstance(pid, str) or not pid.strip():
            pid = f"P{k + 1}"
        pid = pid.strip()
        if pid in seen_pid:
            raise NetworkError(f"管段 id 必须唯一：“{pid}”重复出现")
        seen_pid.add(pid)

        a_id, b_id = pp.get("from"), pp.get("to")
        if not isinstance(a_id, str) or not isinstance(b_id, str):
            raise NetworkError(f"管段“{pid}”必须指定起、终节点 id")
        a_id, b_id = a_id.strip(), b_id.strip()
        if a_id not in seen or b_id not in seen:
            raise NetworkError(f"管段“{pid}”端点“{a_id}”或“{b_id}”不存在")
        if a_id == b_id:
            raise NetworkError(f"管段“{pid}”的起、终点不能相同（不允许自环）")
        a, b = node_ids.index(a_id), node_ids.index(b_id)
        pair = frozenset((a, b))
        if pair in seen_pairs:
            raise NetworkError(
                f"管段“{pid}”与既有管段端点重复（{a_id}、{b_id} 之间不允许并联管）"
            )
        seen_pairs.add(pair)
        r = _num(pp.get("r"), f"管段“{pid}”的阻力 r")
        if not (r > 0.0):
            raise NetworkError(f"管段“{pid}”的阻力必须为正数（当前 {r:g}）")

        pipe_id.append(pid)
        pipe_a.append(a)
        pipe_b.append(b)
        pipe_r.append(r)

    # ---------------- 无向连通性 ----------------
    adj: list[list[int]] = [[] for _ in range(n)]
    for a, b in zip(pipe_a, pipe_b):
        adj[a].append(b)
        adj[b].append(a)
    visited = {src}
    stack = [src]
    while stack:
        u = stack.pop()
        for v in adj[u]:
            if v not in visited:
                visited.add(v)
                stack.append(v)
    if len(visited) != n:
        missing = "、".join(node_ids[k] for k in range(n) if k not in visited)
        raise NetworkError(f"管网必须是无向连通图，以下节点与水源不连通：{missing}")

    # ---------------- 组装求解 ----------------
    free = [k for k in range(n) if k != src]
    m = len(free)
    pos = {node: j for j, node in enumerate(free)}  # 节点 -> 未知量列号
    demand_arr = np.array([demand[k] for k in free], dtype=float)
    h0 = fixed_head[src]

    # 每根管段：row_a/row_b 为对残差行的贡献位置，-1 表示水源行（不参与方程）
    ra = np.array([pos[pipe_a[k]] if pipe_a[k] != src else -1 for k in range(p)])
    rb = np.array([pos[pipe_b[k]] if pipe_b[k] != src else -1 for k in range(p)])
    rr = np.array(pipe_r, dtype=float)

    def fun_jac(x: np.ndarray):
        heads = x  # 自由节点水头
        dH = np.empty(p)
        q = np.empty(p)
        c = np.empty(p)  # dq/d(dH)
        for k in range(p):
            hi = h0 if pipe_a[k] == src else heads[pos[pipe_a[k]]]
            hj = h0 if pipe_b[k] == src else heads[pos[pipe_b[k]]]
            d = hi - hj
            dH[k] = d
            s = 1.0 if d >= 0.0 else -1.0
            ad = abs(d)
            q[k] = s * math.sqrt(ad / rr[k])
            c[k] = 0.5 / math.sqrt(rr[k] * max(ad, _SQRT_EPS))

        f = -demand_arr.copy()
        jac = np.zeros((m, m))
        for k in range(p):
            qa, qb, ck = ra[k], rb[k], c[k]
            # q 沿 a->b 为正：a 出流（净流入 -q），b 入流（净流入 +q）
            if qa >= 0:
                f[qa] -= q[k]
                jac[qa][qa] -= ck
            if qb >= 0:
                f[qb] += q[k]
                jac[qb][qb] -= ck
            if qa >= 0 and qb >= 0:
                jac[qa][qb] += ck
                jac[qb][qa] += ck
        return f, jac

    # 多初值牛顿（hybr + 解析雅可比）
    guesses = [
        np.full(m, h0),
        np.full(m, h0 - 1.0),
        np.full(m, h0 - 10.0),
    ]
    sol = None
    used_guess = -1
    for gi, x0 in enumerate(guesses):
        if np.any(~np.isfinite(x0)):
            continue
        res = root(
            lambda xx: fun_jac(xx)[0],
            x0,
            jac=lambda xx: fun_jac(xx)[1],
            method="hybr",
            options={"xtol": 1e-12, "maxfev": 200},
        )
        if res.success and np.all(np.isfinite(res.x)):
            sol = res.x
            used_guess = gi
            break
    if sol is None:
        raise NetworkError(
            "求解未收敛：牛顿迭代未能达到稳态（可尝试检查阻力/需水量的量级是否合理）",
            code="not_converged",
        )

    # ---------------- 还原全场结果 ----------------
    head = [0.0] * n
    head[src] = h0
    for j, node in enumerate(free):
        head[node] = float(sol[j])

    flows = [0.0] * p
    drops = [0.0] * p
    net_in = [0.0] * n
    for k in range(p):
        a, b = pipe_a[k], pipe_b[k]
        d = head[a] - head[b]
        s = 1.0 if d >= 0.0 else -1.0
        qk = s * math.sqrt(abs(d) / pipe_r[k])
        flows[k] = qk
        drops[k] = d
        net_in[a] -= qk   # q>0 时管段从 a 出流
        net_in[b] += qk   # q>0 时管段向 b 入流

    # ---------------- 按 1e-6 独立复核 ----------------
    # 水源的供水量由其管段净流出直接定义；含注入量的平衡恒闭合。
    source_supply = -net_in[src]
    total_demand = sum(demand)
    closure = source_supply - total_demand  # 供用水全局闭合差

    node_residuals = [0.0] * n
    max_node_res = 0.0
    worst_node = ""
    for k in range(n):
        if k == src:
            node_residuals[k] = net_in[k] + source_supply  # 管段净流入 + 注入 = 0
        else:
            node_residuals[k] = net_in[k] - demand[k]  # 净流入 - 需水
            if abs(node_residuals[k]) > max_node_res:
                max_node_res = abs(node_residuals[k])
                worst_node = node_ids[k]

    pipe_residuals = [0.0] * p
    max_pipe_res = 0.0
    worst_pipe = ""
    for k in range(p):
        a, b = pipe_a[k], pipe_b[k]
        pipe_residuals[k] = head[a] - head[b] - pipe_r[k] * flows[k] * abs(flows[k])
        if abs(pipe_residuals[k]) > max_pipe_res:
            max_pipe_res = abs(pipe_residuals[k])
            worst_pipe = pipe_id[k]

    f_final, _ = fun_jac(sol)
    solver_res = float(np.max(np.abs(f_final))) if m else 0.0

    if max_node_res > TOL or max_pipe_res > TOL or not np.all(np.isfinite(sol)):
        raise NetworkError(
            "求解未收敛：迭代解未通过 1e-6 容差校验"
            f"（用水节点最大守恒残差 {max_node_res:.3e} @ {worst_node}，"
            f"管段最大压降残差 {max_pipe_res:.3e} @ {worst_pipe}）",
            code="not_converged",
        )

    min_head_raw = payload.get("min_head")
    min_head: float | None = None
    if min_head_raw is not None and min_head_raw != "":
        min_head = _num(min_head_raw, "最低水头预警值")

    nodes_out = []
    for k, nid in enumerate(node_ids):
        nodes_out.append(
            {
                "id": nid,
                "type": "source" if k == src else "demand",
                "head": head[k],
                "demand": demand[k],
                "net_in": net_in[k],
                "residual": node_residuals[k],
                "below_min": (min_head is not None and head[k] < min_head - TOL),
            }
        )

    pipes_out = []
    for k in range(p):
        a, b = pipe_a[k], pipe_b[k]
        qk = flows[k]
        if qk > 0.0:
            flow_from, flow_to = node_ids[a], node_ids[b]
        elif qk < 0.0:
            flow_from, flow_to = node_ids[b], node_ids[a]
        else:
            flow_from, flow_to = node_ids[a], node_ids[b]
        pipes_out.append(
            {
                "id": pipe_id[k],
                "from": node_ids[a],
                "to": node_ids[b],
                "r": pipe_r[k],
                "q": qk,
                "flow_from": flow_from,
                "flow_to": flow_to,
                "head_loss": drops[k],
                "residual": pipe_residuals[k],
            }
        )

    return {
        "converged": True,
        "tolerance": TOL,
        "nodes": nodes_out,
        "pipes": pipes_out,
        "source_supply": source_supply,
        "total_demand": total_demand,
        "supply_demand_closure": closure,
        "min_head": min_head,
        "below_min_nodes": [
            node_ids[k]
            for k in range(n)
            if min_head is not None and head[k] < min_head - TOL
        ],
        "max_node_residual": max_node_res,
        "max_pipe_residual": max_pipe_res,
        "solver_residual": solver_res,
        "worst_node": worst_node,
        "worst_pipe": worst_pipe,
        "initial_guess_index": used_guess,
    }
