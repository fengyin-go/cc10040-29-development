# 多维分析查询与指标平台

支撑多维分析查询、指标计算、口径管理和数据服务发布的数据应用平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8015/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5195/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8015`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标定义 | `metric_definition` | 指标 | 指标名称、计算口径、统计粒度 |
| 维度建模 | `dimension_model` | 维度模型 | 模型名称、事实表、维度列表 |
| 查询构建器 | `query_builder` | 查询模板 | 模板名称、关联指标、维度组合 |
| 数据服务 | `data_service` | 服务接口 | 接口名称、关联指标或模型、请求方式 |
| 口径版本 | `caliber_version` | 口径版本 | 所属指标、版本号、口径描述 |
| 查询日志 | `query_journal` | 查询日志 | 查询时间、操作人、涉及指标 |
| 下钻分析 | `drill_down` | 下钻路径 | 路径名称、起始指标、下钻维度序列 |
| 报告模板 | `report_template` | 报告模板 | 模板名称、包含指标集、报告周期 |
| 实验分群 | `experiment_group` | 分群规则 | 规则名称、筛选条件、群组大小 |
| 数据看板配置 | `data_dashboard` | 看板配置 | 看板名称、包含图表、刷新间隔 |
| 用户分群 | `user_cohort` | 分群结果 | 分群名称、分群条件、群组大小 |
| 异常检测器 | `anomaly_detector` | 检测规则 | 规则名称、目标指标、检测算法 |
| 数据导出服务 | `data_export_service` | 导出任务 | 任务名称、数据范围、目标格式 |
| 分群对比分析 | `segment_compare` | 对比任务 | 对比名称、分群A、分群B |
| 透视表配置 | `pivot_table` | 透视配置 | 配置名称、数据源指标、行维度 |
| 数据订阅 | `data_subscription` | 订阅任务 | 订阅名称、关联指标、推送条件 |
| 查询缓存层 | `cache_layer` | 缓存策略 | 策略名称、关联查询模板、缓存时长 |
| 数据追溯系统 | `trace_system` | 追溯链条 | 追溯ID、起点指标、中间步骤 |
| 口径重算引擎 | `recalc_engine` | 重算任务 | 关联指标、新口径版本、历史时间范围 |
| 字段血缘 | `field_lineage` | 字段血缘 | 目标字段、来源字段列表、转换逻辑 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 异常检测器：批量巡检

同一观察窗的多条检测规则可批量巡检，值班员按批次追踪每条规则取到的指标值、采样时间和判定。

- 观察窗按 `[window_start, window_end)` 半开区间登记；样本按**采样时间**归窗，
  迟到补传仍归原观察窗，实时下一窗样本归下一窗，落不进任何窗的样本直接拒收。
- 巡检以批次持久化逐规则结论（含规则口径快照）；继续原批时已确认结论只计一次、
  不重复产生异常和告警，缺样本规则按新到样本重算，其他规则结果留在当轮。
- 整窗报告是否齐全与单条告警能否处置分开判断：未确认（缺样本）的检测不得进入
  告警处置；已确认规则触发的告警即使整窗未齐全也可处置。
- 采样上报、逐规则判定、每轮覆盖核算都进查询日志，可按批次关联采样接口响应和待补项。

接口前缀 `/api/cross/anomaly-detector`：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST/GET | `/windows` | 登记/列出观察窗 |
| POST/GET | `/samples` | 采样上报（按采样时间归窗）/查询样本 |
| POST/GET | `/batches` | 发起或继续（带 `batch_id`）批量巡检/批次汇总 |
| GET | `/batches/{batch_id}` | 批次明细：逐规则指标值、采样时间、判定 |
| GET | `/batches/{batch_id}/pending` | 按原批次列出待补样本的规则 |
| GET | `/batches/{batch_id}/log-trace` | 日志关联采样响应与覆盖核算 |
| GET | `/windows/{window_id}/coverage` | 当班覆盖、可处置告警、整窗齐全度 |
| GET | `/alerts`、POST `/alerts/{id}/handle` | 告警列表与处置闸门 |

前端在侧边栏「批量巡检」页（`/anomaly_detector/batch-inspection`）提供上述流程的操作界面。

领域：数据工程
子方向：数据分析与数据应用
