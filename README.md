# 闭环供水管网平衡台

本地运行的环状（闭环）供水管网稳态水力平衡计算工具：在网页表格里录入节点与管段，
后端用 **Python + FastAPI + SciPy**（牛顿法 + 解析雅可比）求稳态水头分布与各管段有符号流量，
前端用**原生 HTML/CSS/JavaScript** 绘制可拖动的网络图、流向箭头与低水压节点。

## 一条命令启动

需要本机已安装 Docker（含 Docker Compose 插件）：

```bash
docker compose up --build
```

启动后浏览器访问：

> **http://localhost:8000**

停止：在终端按 `Ctrl+C`（或另开终端执行 `docker compose down`）。

## 模型与校验规则

- **节点（2～10 个，id 唯一）**：恰有 **1 个水源节点**，填写固定水头 H₀；
  其余为用水节点，填写**非负**需水量 d。
- **管段（1～16 根，id 唯一）**：不同端点（无自环、无并联管）、阻力 **r > 0**。
- 只接受**无向连通图**（所有节点必须能与水源连通）。
- 所有数值必须是有限数（拒绝 NaN / Infinity）。
- 管段按录入方向（起 → 终）记有符号流量 q：

  $$H_{起} - H_{终} = r\,q\,|q| \quad\Longleftrightarrow\quad q = \operatorname{sgn}(\Delta H)\sqrt{|\Delta H|/r}$$

  q > 0 表示流向与录入方向相同，q < 0 相反；水源总供水量 = 水源各管段净流出之和。

- 求解方法：以各用水节点水头为未知量列节点质量守恒方程（净流入 = 需水量），
  SciPy `root(method="hybr")` 牛顿迭代，附带解析雅可比；多初值兜底。

### 求解后按 **1×10⁻⁶** 容差独立校验

1. 每个用水节点的质量守恒残差（净流入 − 需水量）；
2. 每根管段的压降残差（H起 − H终 − r·q·|q|）；
3. 供用水全局闭合差（水源总供水量 − 总需水量）。

任一校验不过，返回 422 并说明残差量级与所在节点/管段，不出具结果。

## 功能说明

- **可编辑表格**：增删节点/管段、切换水源、改编号时联动更新管段端点选项。
- **网络图**：力导向布局、节点可拖动；水源蓝色、用水节点灰色、**低于设定最低水头的节点红色**；
  管段粗细 ∝ |q|，蓝色箭头标出实际流向，零流量单独标记。
- **结果表**：各节点水头/净流入/守恒残差，各管段有符号流量/实际流向/压降与残差；
  显示水源总供水量、总需水量与最大残差。
- **最低水头预警**：在求解区填入阈值（可留空），水头低于阈值的节点在表格和网络图中标红。
- **非法输入 / 不收敛**：页面红条与接口 JSON 均给出中文原因（如“以下节点与水源不连通：B”
  “管段 P2 与既有管段端点重复”“阻力必须为正数”等）。
- 点击 **「载入示例」** 可快速加载一个 5 节点 7 管的环状管网（含反向流与低水压节点演示）。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| POST | `/api/solve` | 求解，请求/响应均为 JSON |

请求示例：

```json
{
  "min_head": 32,
  "nodes": [
    {"id": "S", "type": "source", "fixed_head": 50},
    {"id": "A", "type": "demand", "demand": 30}
  ],
  "pipes": [
    {"id": "P1", "from": "S", "to": "A", "r": 0.008}
  ]
}
```

成功返回 `{"ok": true, "result": {...}}`，其中包含：

- `heads` / `nodes`：各节点稳态水头、净流入、守恒残差、`below_min` 标记；
- `pipes`：各管 `q`（有符号）、`flow_from`/`flow_to`（实际流向）、`head_loss`、压降残差；
- `source_supply`（水源总供水量）、`total_demand`、`supply_demand_closure`；
- `max_node_residual`、`max_pipe_residual`、`below_min_nodes`。

失败返回 `{"ok": false, "error": "中文原因", "code": "..."}`：输入非法为 **400**，
求解未收敛为 **422**。

## 不用 Docker 的本地运行方式（可选）

Python 3.11+：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 项目结构

```
.
├── app/
│   ├── main.py        # FastAPI：/api/solve、错误处理、静态文件托管
│   ├── solver.py      # 输入校验 + 牛顿求解 + 1e-6 容差复核
│   └── errors.py      # 业务异常
├── static/
│   ├── index.html     # 页面
│   ├── style.css      # 样式
│   └── app.js         # 表格交互、力导向网络图、结果渲染
├── tests/test_solver.py
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 测试

```bash
PYTHONPATH=. python tests/test_solver.py
# 或
pip install pytest && PYTHONPATH=. python -m pytest tests/ -q
```
