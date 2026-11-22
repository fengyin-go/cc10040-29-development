"""批量巡检业务行为测试。

场景（与题述交班流程一致）:
1. 登记相邻两个观察窗 W1/W2，配置支付量、退款率两条已启用规则；
2. W1 只有支付量采样且触发告警，退款率缺样本，先跑一轮批量巡检；
3. 核对当班日志里的覆盖情况、可处置告警与整窗报告齐全度；
4. W2 开始后补传属于 W1 的迟到退款样本，按日志待补项两次继续原批次；
5. 核对两窗样本归属、告警数量与完成计数，确认已持久化结论只计一次。
"""
from __future__ import annotations

import pytest

from app.services.anomaly_detector import AnomalyDetectorService
from app.store import store


W1 = "W-20261122-0800"
W2 = "W-20261122-0900"
W1_START, W1_END = "2026-11-22 08:00:00", "2026-11-22 09:00:00"
W2_START, W2_END = "2026-11-22 09:00:00", "2026-11-22 10:00:00"
PAY_METRIC = "支付量"
REFUND_METRIC = "退款率"


@pytest.fixture()
def svc():
    return AnomalyDetectorService()


@pytest.fixture()
def scenario(svc):
    """搭好两个指标、两条规则和两个相邻观察窗。"""
    # 清掉批量巡检相关的运行态，保证用例相互隔离
    store.observation_windows().clear()
    store.metric_samples().clear()
    store.inspection_batches().clear()
    store.alerts().clear()
    store.anomaly_records().clear()
    store.query_logs().clear()
    # 种子规则里有一条默认“已启用”，先关闭以免混入批次；结束后还原
    seed_status = {
        int(row["id"]): row["status"] for row in store.rows("anomaly_detector")
    }
    for row in store.rows("anomaly_detector"):
        if int(row["id"]) < 100:
            row["status"] = "已关闭"

    store.rows("metric_definition").append({
        "id": 101, "status": "已发布", "pending": False, "abnormal": False,
        "指标名称": PAY_METRIC, "计算口径": "窗口内支付成功订单金额合计",
        "统计粒度": "小时", "数据源": "交易库", "业务域": "交易",
    })
    store.rows("metric_definition").append({
        "id": 102, "status": "已发布", "pending": False, "abnormal": False,
        "指标名称": REFUND_METRIC, "计算口径": "窗口内退款金额/支付金额",
        "统计粒度": "小时", "数据源": "交易库", "业务域": "交易",
    })
    store.rows("anomaly_detector").append({
        "id": 101, "status": "已启用", "pending": False, "abnormal": False,
        "规则名称": "支付量突增检测", "目标指标": PAY_METRIC,
        "检测算法": "threshold", "异常阈值": "1000", "生效时段": "全天",
    })
    store.rows("anomaly_detector").append({
        "id": 102, "status": "已启用", "pending": False, "abnormal": False,
        "规则名称": "退款率超限检测", "目标指标": REFUND_METRIC,
        "检测算法": "threshold", "异常阈值": "5", "生效时段": "全天",
    })
    assert "error" not in svc.register_window(W1, W1_START, W1_END)
    assert "error" not in svc.register_window(W2, W2_START, W2_END)
    yield svc

    # 还原种子规则状态并移除本用例追加的数据
    for row in store.rows("anomaly_detector"):
        if int(row["id"]) in seed_status:
            row["status"] = seed_status[int(row["id"])]
    for table_name in ("metric_definition", "anomaly_detector"):
        table = store.rows(table_name)
        table[:] = [row for row in table if int(row["id"]) < 100]
    store.observation_windows().clear()
    store.metric_samples().clear()
    store.inspection_batches().clear()
    store.alerts().clear()
    store.anomaly_records().clear()
    store.query_logs().clear()


class TestWindowAndSampleAttribution:
    def test_sample_outside_any_window_rejected(self, scenario):
        result = scenario.ingest_sample(PAY_METRIC, 100, "2026-11-22 06:00:00")
        assert "error" in result

    def test_late_sample_still_belongs_to_original_window(self, scenario):
        # W2 已开始后才上报，但采样时间落在 W1
        result = scenario.ingest_sample(
            REFUND_METRIC, 3.2, "2026-11-22 08:30:00",
            reported_at="2026-11-22 09:10:00")
        assert result["window_id"] == W1
        assert result["is_late"] is True
        assert len(store.samples_for_window(W1, REFUND_METRIC)) == 1
        assert store.samples_for_window(W2, REFUND_METRIC) == []

    def test_realtime_sample_belongs_to_current_window(self, scenario):
        result = scenario.ingest_sample(PAY_METRIC, 900, "2026-11-22 09:05:00")
        assert result["window_id"] == W2
        assert result["is_late"] is False


class TestFirstRoundPartialInspection:
    def test_first_round_with_payment_only(self, scenario):
        # W1：有支付量采样且超过阈值，无退款率采样
        sample = scenario.ingest_sample(PAY_METRIC, 1250, "2026-11-22 08:20:00")
        assert "error" not in sample

        batch = scenario.run_batch_inspection(W1)
        assert "error" not in batch
        batch_id = batch["batch_id"]

        # 批次按原批次记录每条规则的结论状态
        by_rule = {r["rule_id"]: r for r in batch["results"]}
        pay = by_rule[101]
        refund = by_rule[102]

        # 值班员可追到每条规则取到的指标值、采样时间和判定
        assert pay["confirmed"] is True
        assert pay["sample_value"] == 1250.0
        assert pay["sample_time"] == "2026-11-22 08:20:00"
        assert pay["judgement"] == "异常"
        assert pay["alert_id"] is not None

        assert refund["confirmed"] is False
        assert refund["status"] == "待补样本"
        assert refund["sample_value"] is None
        assert refund["judgement"] is None

        summary = {k: batch[k] for k in batch if k != "results"}
        assert summary["total_rules"] == 2
        assert summary["confirmed_rules"] == 1
        assert summary["pending_rules"] == 1
        # 整窗报告不齐全
        assert summary["window_report_complete"] is False
        assert summary["anomaly_rules"] == 1
        assert summary["run_count"] == 1

    def test_payment_alert_handleable_while_window_incomplete(self, scenario):
        scenario.ingest_sample(PAY_METRIC, 1250, "2026-11-22 08:20:00")
        batch = scenario.run_batch_inspection(W1)
        alert_id = next(r["alert_id"] for r in batch["results"] if r["rule_id"] == 101)

        # 支付量规则已确认：即使整窗报告不齐，也可按这次巡检处置该告警
        alerts = scenario.list_alerts(window_id=W1)
        assert len(alerts) == 1
        assert alerts[0]["handleable"] is True
        handled = scenario.handle_alert(alert_id, handler="值班员甲")
        assert handled["ok"] is True
        assert handled["alert"]["status"] == "已处置"

        # 重复处置幂等，不产生第二条告警/异常记录
        again = scenario.handle_alert(alert_id, handler="值班员甲")
        assert again["ok"] is True
        assert len(scenario.list_alerts(window_id=W1)) == 1

        coverage = scenario.get_window_coverage(W1)
        assert coverage["window_report_complete"] is False
        assert coverage["alerts_handleable"] == 1
        assert coverage["confirmed_rules"] == 1
        assert coverage["pending_rules"] == 1

    def test_unconfirmed_detection_cannot_enter_alert_handling(self, scenario):
        scenario.ingest_sample(PAY_METRIC, 1250, "2026-11-22 08:20:00")
        batch = scenario.run_batch_inspection(W1)
        # 手工构造一条指向未确认规则的告警，闸门必须拒绝
        fake_alert = store.add_alert({
            "batch_id": batch["batch_id"], "window_id": W1, "rule_id": 102,
            "rule_name": "退款率超限检测", "metric_name": REFUND_METRIC,
            "metric_id": 102, "severity": "warning", "value": 9.9, "threshold": 5.0,
            "sample_time": None, "sample_id": None, "status": "待处置",
            "handleable": False, "handled": False, "created_at": "2026-11-22 08:40:00",
        })
        result = scenario.handle_alert(fake_alert["id"])
        assert result["ok"] is False
        assert "未确认" in result["error"]

    def test_rerun_without_new_sample_keeps_single_conclusion(self, scenario):
        scenario.ingest_sample(PAY_METRIC, 1250, "2026-11-22 08:20:00")
        first = scenario.run_batch_inspection(W1)
        batch_id = first["batch_id"]
        anomalies_before = len(store.anomaly_records())
        alerts_before = len(store.alerts())

        # 继续原批但还没补样本：支付量结论原样保留，不重复判定/告警
        second = scenario.run_batch_inspection(W1, batch_id=batch_id)
        assert second["run_count"] == 2
        assert second["confirmed_rules"] == 1
        assert second["pending_rules"] == 1
        assert len(store.anomaly_records()) == anomalies_before
        assert len(store.alerts()) == alerts_before
        pay_results = [r for r in second["results"] if r["rule_id"] == 101]
        assert len(pay_results) == 1  # 已持久化结论只计一次

    def test_log_trace_links_samples_and_coverage(self, scenario):
        scenario.ingest_sample(PAY_METRIC, 1250, "2026-11-22 08:20:00")
        batch = scenario.run_batch_inspection(W1)
        trace = scenario.get_batch_log_trace(batch["batch_id"])
        assert "error" not in trace
        # 采样接口响应可在日志侧关联到
        assert trace["sample_responses"][0]["metric_name"] == PAY_METRIC
        log_types = {log["log_type"] for log in trace["journal_logs"]}
        assert {"metric_sample", "batch_inspection", "batch_coverage"} <= log_types
        # 待补项就是退款率规则
        pending = trace["pending_items"]
        assert len(pending) == 1
        assert pending[0]["metric_name"] == REFUND_METRIC
        assert trace["coverage"]["window_report_complete"] is False


class TestLateBackfillAndContinuation:
    def test_two_continuation_runs_after_late_backfill(self, scenario):
        # 第一轮：W1 只有支付量
        scenario.ingest_sample(PAY_METRIC, 1250, "2026-11-22 08:20:00")
        first = scenario.run_batch_inspection(W1)
        batch_id = first["batch_id"]
        w1_anomalies = len(store.anomaly_records())
        w1_alerts = len(scenario.list_alerts(window_id=W1))

        # 下一窗 W2 的实时样本：与 W1 分别归属
        scenario.ingest_sample(PAY_METRIC, 900, "2026-11-22 09:05:00")
        scenario.ingest_sample(REFUND_METRIC, 6.5, "2026-11-22 09:15:00")
        w2_batch = scenario.run_batch_inspection(W2)
        assert w2_batch["window_report_complete"] is True
        assert w2_batch["confirmed_rules"] == 2

        # W2 开始后补传属于 W1 的退款率样本（迟到样本归原窗）
        backfill = scenario.ingest_sample(
            REFUND_METRIC, 3.2, "2026-11-22 08:30:00",
            reported_at="2026-11-22 09:20:00")
        assert backfill["window_id"] == W1
        assert backfill["is_late"] is True

        # 按日志待补项第一次继续原批
        pending = scenario.get_pending_items(batch_id)
        assert [item["rule_id"] for item in pending] == [102]
        continued = scenario.run_batch_inspection(W1, batch_id=batch_id)
        assert continued["run_count"] == 2
        by_rule = {r["rule_id"]: r for r in continued["results"]}
        assert by_rule[101]["judgement"] == "异常"  # 原结论保留
        assert by_rule[102]["confirmed"] is True
        assert by_rule[102]["sample_value"] == 3.2
        assert by_rule[102]["sample_time"] == "2026-11-22 08:30:00"
        assert by_rule[102]["judgement"] == "正常"
        assert continued["window_report_complete"] is True
        assert continued["confirmed_rules"] == 2
        assert continued["pending_rules"] == 0

        # 已持久化结论只计一次：异常记录与告警数量不因补跑增加
        assert len(store.anomaly_records()) == w1_anomalies + 1  # 仅 W2 退款率新增 1 条
        assert len(scenario.list_alerts(window_id=W1)) == w1_alerts

        # 按日志补项第二次：无待补项，结论仍只计一次
        second_continue = scenario.run_batch_inspection(W1, batch_id=batch_id)
        assert second_continue["run_count"] == 3
        assert second_continue["confirmed_rules"] == 2
        assert len(second_continue["results"]) == 2
        assert len(store.anomaly_records()) == w1_anomalies + 1
        assert len(scenario.list_alerts(window_id=W1)) == w1_alerts
        assert scenario.get_pending_items(batch_id) == []

    def test_two_windows_sample_alert_and_completion_counts(self, scenario):
        # W1 第一轮
        scenario.ingest_sample(PAY_METRIC, 1250, "2026-11-22 08:20:00")
        batch1 = scenario.run_batch_inspection(W1)
        # W2 实时一轮
        scenario.ingest_sample(PAY_METRIC, 900, "2026-11-22 09:05:00")
        scenario.ingest_sample(REFUND_METRIC, 6.5, "2026-11-22 09:15:00")
        batch2 = scenario.run_batch_inspection(W2)
        # 迟到补传 + 两次继续原批
        scenario.ingest_sample(
            REFUND_METRIC, 3.2, "2026-11-22 08:30:00", reported_at="2026-11-22 09:20:00")
        scenario.run_batch_inspection(W1, batch_id=batch1["batch_id"])
        scenario.run_batch_inspection(W1, batch_id=batch1["batch_id"])

        # 样本归属核对
        assert len(store.samples_for_window(W1)) == 2
        assert len(store.samples_for_window(W2)) == 2

        cov1 = scenario.get_window_coverage(W1)
        cov2 = scenario.get_window_coverage(W2)
        assert cov1["sample_count"] == 2 and cov2["sample_count"] == 2
        # 两窗报告均齐全
        assert cov1["window_report_complete"] is True
        assert cov2["window_report_complete"] is True
        # 完成计数：W1 跑了 3 轮（首轮 + 两次继续），W2 跑了 1 轮
        assert cov1["completed_run_count"] == 3
        assert cov2["completed_run_count"] == 1
        # 告警：W1 仅支付量 1 条；W2 退款率 1 条（支付量 900 未超阈值）
        assert cov1["alerts_total"] == 1
        assert cov2["alerts_total"] == 1
        assert len(scenario.list_alerts()) == 2
        # 批次列表按窗可查
        w1_batches = scenario.list_batches(W1)
        assert [b["batch_id"] for b in w1_batches] == [batch1["batch_id"]]
        assert [b["batch_id"] for b in scenario.list_batches(W2)] == [batch2["batch_id"]]

    def test_continuation_uses_original_window_caliber_snapshot(self, scenario):
        scenario.ingest_sample(PAY_METRIC, 1250, "2026-11-22 08:20:00")
        batch = scenario.run_batch_inspection(W1)
        # 轮次中途修改规则口径，原批次仍按观察窗当时的规则口径计算
        rule = store.find("anomaly_detector", 101)
        rule["异常阈值"] = "9999"
        continued = scenario.run_batch_inspection(W1, batch_id=batch["batch_id"])
        pay = next(r for r in continued["results"] if r["rule_id"] == 101)
        assert pay["threshold"] == 1000.0
        assert continued["caliber_snapshot"]["101"]["threshold"] == 1000.0

    def test_cross_window_continuation_rejected(self, scenario):
        scenario.ingest_sample(PAY_METRIC, 1250, "2026-11-22 08:20:00")
        batch = scenario.run_batch_inspection(W1)
        # 不能拿 W1 的批次去跑 W2
        result = scenario.run_batch_inspection(W2, batch_id=batch["batch_id"])
        assert "error" in result

    def test_unknown_batch_continuation_rejected(self, scenario):
        result = scenario.run_batch_inspection(W1, batch_id="BATCH-NOPE")
        assert "error" in result
