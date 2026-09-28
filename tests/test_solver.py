"""求解器测试：闭式算例、环状网、雅可比核对、非法输入与规模边界。

运行：python -m pytest tests/ -q  （无 pytest 时：python tests/test_solver.py）
"""

import numpy as np

from app.errors import NetworkError
from app.solver import validate_and_solve

SAMPLE = {
    "nodes": [
        {"id": "S", "type": "source", "fixed_head": 50},
        {"id": "A", "type": "demand", "demand": 30},
        {"id": "B", "type": "demand", "demand": 20},
        {"id": "C", "type": "demand", "demand": 25},
        {"id": "D", "type": "demand", "demand": 15},
    ],
    "pipes": [
        {"id": "P1", "from": "S", "to": "A", "r": 0.008},
        {"id": "P2", "from": "A", "to": "B", "r": 0.03},
        {"id": "P3", "from": "B", "to": "C", "r": 0.05},
        {"id": "P4", "from": "C", "to": "D", "r": 0.02},
        {"id": "P5", "from": "D", "to": "S", "r": 0.02},
        {"id": "P6", "from": "A", "to": "D", "r": 0.04},
        {"id": "P7", "from": "S", "to": "B", "r": 0.03},
    ],
    "min_head": 32,
}


def expect_error(payload, keyword):
    try:
        validate_and_solve(payload)
    except NetworkError as e:
        assert keyword in e.message, f"期望包含 {keyword!r}，实际：{e.message}"
        return
    raise AssertionError(f"应当报错（{keyword}），却成功了")


def independent_check(res, demands):
    """用返回结果独立复核逐段压降与节点平衡。"""
    head = {x["id"]: x["head"] for x in res["nodes"]}
    net = {nid: 0.0 for nid in head}
    for p in res["pipes"]:
        assert abs(head[p["from"]] - head[p["to"]] - p["r"] * p["q"] * abs(p["q"])) < 1e-6
        net[p["from"]] -= p["q"]
        net[p["to"]] += p["q"]
    for nid, d in demands.items():
        assert abs(net[nid] - d) < 1e-6
    src = next(n["id"] for n in res["nodes"] if n["type"] == "source")
    assert abs(-net[src] - sum(demands.values())) < 1e-6


def test_two_node_closed_form():
    res = validate_and_solve({
        "nodes": [
            {"id": "S", "type": "source", "fixed_head": 50},
            {"id": "A", "type": "demand", "demand": 10},
        ],
        "pipes": [{"id": "P1", "from": "S", "to": "A", "r": 0.1}],
    })
    assert abs(res["nodes"][1]["head"] - 40.0) < 1e-9
    assert abs(res["pipes"][0]["q"] - 10.0) < 1e-9
    assert abs(res["source_supply"] - 10.0) < 1e-9
    assert abs(res["supply_demand_closure"]) < 1e-9


def test_loop_network_sample():
    res = validate_and_solve(SAMPLE)
    assert res["converged"]
    assert res["max_node_residual"] < 1e-6
    assert res["max_pipe_residual"] < 1e-6
    assert abs(res["source_supply"] - 90.0) < 1e-6
    independent_check(res, {"A": 30, "B": 20, "C": 25, "D": 15})
    head = {x["id"]: x["head"] for x in res["nodes"]}
    assert head["C"] < 32.0  # 示例中 C 触发低水头预警
    assert head["A"] > 32.0
    assert res["below_min_nodes"] == ["C"]
    # 环状网存在反向流（P4/P5 的录入方向与实际流向相反，q 为负）
    signs = {p["id"]: p["q"] for p in res["pipes"]}
    assert signs["P4"] < 0 and signs["P5"] < 0
    assert signs["P1"] > 0
    p4 = next(p for p in res["pipes"] if p["id"] == "P4")
    assert p4["flow_from"] == "D" and p4["flow_to"] == "C"


def test_jacobian_finite_difference():
    import scipy.optimize as opt

    res = validate_and_solve(SAMPLE)
    head = {x["id"]: x["head"] for x in res["nodes"]}
    order = [1, 2, 3, 4]
    posmap = {nd: i for i, nd in enumerate(order)}
    pa = [0, 1, 2, 3, 4, 1, 0]
    pb = [1, 2, 3, 4, 0, 4, 2]
    rr = np.array([0.008, 0.03, 0.05, 0.02, 0.02, 0.04, 0.03])
    h0 = 50.0
    ra = np.array([posmap[a] if a != 0 else -1 for a in pa])
    rb = np.array([posmap[b] if b != 0 else -1 for b in pb])
    demand = np.array([30.0, 20.0, 25.0, 15.0])

    def fj(x):
        dh = np.array([
            (h0 if a == 0 else x[posmap[a]]) - (h0 if b == 0 else x[posmap[b]])
            for a, b in zip(pa, pb)
        ])
        q = np.sign(dh) * np.sqrt(np.abs(dh) / rr)
        c = 0.5 / np.sqrt(rr * np.maximum(np.abs(dh), 1e-12))
        f = -demand.copy()
        j = np.zeros((4, 4))
        for k in range(7):
            a, b = ra[k], rb[k]
            if a >= 0:
                f[a] -= q[k]; j[a, a] -= c[k]
            if b >= 0:
                f[b] += q[k]; j[b, b] -= c[k]
            if a >= 0 and b >= 0:
                j[a, b] += c[k]; j[b, a] += c[k]
        return f, j

    x = np.array([head["A"], head["B"], head["C"], head["D"]])
    f, j = fj(x)
    assert np.max(np.abs(f)) < 1e-7
    jfd = opt.approx_fprime(x, lambda z: fj(z)[0], 1e-7)
    assert np.max(np.abs(j - jfd)) < 1e-4


def test_invalid_inputs():
    base_nodes, base_pipes = SAMPLE["nodes"], SAMPLE["pipes"]
    two_nodes = [
        {"id": "S", "type": "source", "fixed_head": 50},
        {"id": "A", "type": "demand", "demand": 1},
    ]
    one_pipe = [{"id": "p", "from": "S", "to": "A", "r": 1}]

    expect_error({"nodes": base_nodes[:1], "pipes": base_pipes}, "节点数")
    expect_error({"nodes": base_nodes * 2, "pipes": base_pipes}, "唯一")
    expect_error(
        {"nodes": [
            {"id": "X", "type": "source", "fixed_head": 50},
            {"id": "Y", "type": "source", "fixed_head": 40},
            {"id": "A", "type": "demand", "demand": 1},
        ], "pipes": [{"id": "p", "from": "X", "to": "A", "r": 1}]},
        "恰有 1 个水源",
    )
    expect_error(
        {"nodes": [
            {"id": "S", "type": "source", "fixed_head": 50},
            {"id": "A", "type": "demand", "demand": -1},
        ], "pipes": one_pipe},
        "不能为负",
    )
    expect_error(
        {"nodes": two_nodes, "pipes": [{"id": "p", "from": "S", "to": "A", "r": 0}]},
        "正数",
    )
    expect_error(
        {"nodes": two_nodes, "pipes": [{"id": "p", "from": "S", "to": "S", "r": 1}]},
        "不能相同",
    )
    expect_error(
        {"nodes": two_nodes, "pipes": [
            {"id": "p1", "from": "S", "to": "A", "r": 1},
            {"id": "p2", "from": "A", "to": "S", "r": 2},
        ]},
        "端点重复",
    )
    expect_error(
        {"nodes": [
            {"id": "S", "type": "source", "fixed_head": 50},
            {"id": "A", "type": "demand", "demand": 1},
            {"id": "B", "type": "demand", "demand": 1},
        ], "pipes": [{"id": "p", "from": "S", "to": "A", "r": 1}]},
        "不连通",
    )
    expect_error({}, "数组")
    expect_error(
        {"nodes": [
            {"id": "S", "type": "source", "fixed_head": float("nan")},
            {"id": "A", "type": "demand", "demand": 1},
        ], "pipes": one_pipe},
        "有限",
    )
    expect_error(
        {"nodes": [
            {"id": "S", "type": "source"},
            {"id": "A", "type": "demand", "demand": 1},
        ], "pipes": one_pipe},
        "固定水头",
    )
    expect_error(
        {"nodes": [
            {"id": "S", "type": "source", "fixed_head": 50},
            {"id": "A", "type": "demand"},
        ], "pipes": one_pipe},
        "需水量",
    )
    expect_error(
        {"nodes": two_nodes, "pipes": [{"id": "p", "from": "S", "to": "Z", "r": 1}]},
        "不存在",
    )
    expect_error(
        {"nodes": base_nodes + [{"id": f"E{i}", "type": "demand", "demand": 1} for i in range(6)],
         "pipes": base_pipes},
        "节点数",
    )
    expect_error({"nodes": two_nodes, "pipes": []}, "管段数")
    expect_error(
        {"nodes": two_nodes, "pipes": [{"id": "p", "from": "S", "to": "A", "r": float("inf")}]},
        "有限",
    )


def test_max_size_random_network():
    rng = np.random.default_rng(0)
    nodes = [{"id": "S", "type": "source", "fixed_head": 100}]
    for i in range(9):
        nodes.append({"id": f"N{i}", "type": "demand", "demand": float(rng.uniform(1, 30))})
    pairs, edges = set(), []
    for i in range(9):
        a, b = "S", f"N{i}"
        pairs.add(tuple(sorted((a, b))))
        edges.append((a, b))
    while len(edges) < 16:
        i, j = rng.integers(0, 10, 2)
        if i == j:
            continue
        a, b = ["S" if x == 0 else f"N{x - 1}" for x in (i, j)]
        key = tuple(sorted((a, b)))
        if key in pairs:
            continue
        pairs.add(key)
        edges.append((a, b))
    pipes = [
        {"id": f"P{k}", "from": a, "to": b, "r": float(rng.uniform(0.01, 0.5))}
        for k, (a, b) in enumerate(edges)
    ]
    res = validate_and_solve({"nodes": nodes, "pipes": pipes})
    assert res["max_node_residual"] < 1e-6
    assert res["max_pipe_residual"] < 1e-6
    total = sum(n["demand"] for n in nodes[1:])
    assert abs(res["source_supply"] - total) < 1e-6


def test_reversed_entry_direction():
    res = validate_and_solve({
        "nodes": [
            {"id": "S", "type": "source", "fixed_head": 50},
            {"id": "A", "type": "demand", "demand": 10},
        ],
        "pipes": [{"id": "P1", "from": "A", "to": "S", "r": 0.1}],
    })
    assert res["pipes"][0]["q"] < 0
    assert abs(res["pipes"][0]["q"] + 10) < 1e-9
    assert res["pipes"][0]["flow_from"] == "S"
    assert res["pipes"][0]["flow_to"] == "A"


def test_zero_demand_loop():
    res = validate_and_solve({
        "nodes": [
            {"id": "S", "type": "source", "fixed_head": 50},
            {"id": "A", "type": "demand", "demand": 0},
            {"id": "B", "type": "demand", "demand": 0},
        ],
        "pipes": [
            {"id": "1", "from": "S", "to": "A", "r": 1},
            {"id": "2", "from": "A", "to": "B", "r": 1},
            {"id": "3", "from": "B", "to": "S", "r": 1},
        ],
    })
    assert all(abs(p["q"]) < 1e-9 for p in res["pipes"])
    assert all(abs(nd["head"] - 50) < 1e-9 for nd in res["nodes"])


def test_tiny_flow_regularization():
    res = validate_and_solve({
        "nodes": [
            {"id": "S", "type": "source", "fixed_head": 30},
            {"id": "A", "type": "demand", "demand": 0.001},
        ],
        "pipes": [{"id": "p", "from": "S", "to": "A", "r": 1e6}],
    })
    assert res["max_node_residual"] < 1e-6
    assert res["max_pipe_residual"] < 1e-6


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n全部 {len(fns)} 项测试通过")
