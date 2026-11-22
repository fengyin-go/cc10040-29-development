"""异常检测器领域服务：配置和执行异常检测规则，发现数据异常。

跨模块数据流: segment_compare -> anomaly_detector
用户分群的对比分析结果触发异常检测。

批量巡检能力（同一观察窗多规则批量判定）:
- 观察窗按时间区间登记，指标采样数据按采样时间（而非上报时间）归属窗口；
- 一次批量巡检为一个批次，逐规则保留取到的指标值、采样时间和判定结论；
- 规则结论持久化，补传样本后续跑原批次时已确认的结论只计一次、不重复告警；
- 未确认（缺样本）的检测不得进入告警处置，整窗报告是否齐全与单规则能否处置分开判断；
- 查询日志关联采样接口响应，值班员可按批次核算覆盖并按待补项继续原批。
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store
from app.services.base import BaseModuleService


def _parse_threshold(raw: Any) -> float:
    try:
        return float(str(raw if raw is not None else "10").replace(">", "").replace("<", ""))
    except (ValueError, TypeError):
        return 10.0


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class AnomalyDetectorService(BaseModuleService):
    MODULE = "anomaly_detector"
    ENTITY = "检测规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["规则名称", "目标指标", "检测算法", "异常阈值", "生效时段"]
    REQUIRED_FIELDS = ["规则名称", "目标指标", "检测算法"]
    STATUS_ORDER = ["待启用", "已启用", "已关闭"]
    ACTION_RULES = {"启用检测": "已启用", "关闭检测": "已关闭"}
    NEGATIVE_ACTIONS = []

    # ── 观察窗登记 ──

    def register_window(self, window_id: str, window_start: str, window_end: str) -> dict[str, Any]:
        """登记观察窗。窗口按 [window_start, window_end) 半开区间归属样本。"""
        if not window_id or not window_start or not window_end:
            return {"error": "window_id/window_start/window_end 均为必填"}
        if window_start >= window_end:
            return {"error": "观察窗起始时间必须早于结束时间"}
        if store.find_window(window_id) is not None:
            return {"error": f"观察窗 {window_id} 已存在"}
        window = {
            "window_id": window_id,
            "window_start": window_start,
            "window_end": window_end,
            "created_at": _now_text(),
        }
        store.add_window(window)
        return window

    def list_windows(self) -> list[dict[str, Any]]:
        return store.observation_windows()

    # ── 指标采样接口 ──

    def ingest_sample(self, metric_name: str, value: float, sample_time: str,
                      reported_at: str | None = None) -> dict[str, Any]:
        """接收指标采样数据，并按采样时间把样本归属到对应观察窗。

        归属只看 sample_time：迟到样本仍归入原观察窗，实时下一窗样本归下一窗。
        """
        if not metric_name:
            return {"error": "metric_name 为必填"}
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return {"error": f"采样值无法解析为数值：{value!r}"}
        if not sample_time:
            return {"error": "sample_time 为必填"}
        window = store.window_for_time(sample_time)
        if window is None:
            return {"error": f"采样时间 {sample_time} 不属于任何已登记观察窗，样本不予接收"}
        # 未显式给上报时间时视为按时上报；迟到判定相对样本所属窗的结束时间。
        effective_reported_at = reported_at or sample_time
        sample = {
            "metric_name": metric_name,
            "value": numeric_value,
            "sample_time": sample_time,
            "reported_at": effective_reported_at,
            "window_id": window["window_id"],
            "is_late": effective_reported_at >= window["window_end"],
        }
        store.add_sample(sample)

        # 采样接口调用进查询日志，批量巡检覆盖核算通过日志关联到这次响应。
        metric_id = self._metric_id(metric_name)
        store.log_query(
            metric_id=metric_id,
            operator="采样接口",
            sql_summary=(
                f"指标采样: {metric_name}@{sample_time}={numeric_value}，"
                f"归属观察窗 {window['window_id']}"
            ),
            row_count=1,
            duration_ms=5.0,
            status="正常",
            extra={
                "log_type": "metric_sample",
                "window_id": window["window_id"],
                "metric_name": metric_name,
                "sample_time": sample_time,
                "sample_value": numeric_value,
                "sample_id": sample["id"],
            },
        )
        return {
            "sample_id": sample["id"],
            "metric_name": metric_name,
            "value": numeric_value,
            "sample_time": sample_time,
            "window_id": window["window_id"],
            "is_late": sample["is_late"],
        }

    def list_samples(self, window_id: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        if window_id is None:
            return store.metric_samples()
        if store.find_window(window_id) is None:
            return {"error": f"观察窗 {window_id} 不存在"}
        return store.samples_for_window(window_id)

    # ── 批量巡检 ──

    def run_batch_inspection(self, window_id: str, rule_ids: list[int] | None = None,
                             batch_id: str | None = None) -> dict[str, Any]:
        """对同一观察窗执行批量巡检；带 batch_id 时为继续原批次的补跑。

        - 已持久化结论的规则保留原结果，只计一次，不重复产生异常/告警；
        - 缺样本的规则保持「待补样本」，不计入已完成，也不允许进入告警处置。
        """
        window = store.find_window(window_id)
        if window is None:
            return {"error": f"观察窗 {window_id} 不存在"}

        existing = store.find_inspection_batch(batch_id) if batch_id else None
        if batch_id and existing is None:
            return {"error": f"巡检批次 {batch_id} 不存在，无法继续原批"}
        if existing is not None and existing.get("window_id") != window_id:
            return {"error": "批次与观察窗不一致，不能跨窗继续原批"}

        if existing is None:
            rules = self._select_rules(rule_ids)
            if isinstance(rules, dict):  # error payload
                return rules
            new_id = batch_id or f"BATCH-{len(store.inspection_batches()) + 1:04d}"
            if store.find_inspection_batch(new_id) is not None:
                return {"error": f"巡检批次 {new_id} 已存在"}
            batch = {
                "batch_id": new_id,
                "window_id": window_id,
                "rule_ids": [int(r["id"]) for r in rules],
                "created_at": _now_text(),
                "runs": 0,
                "results": [],
                "caliber_snapshot": {},
            }
            store.add_inspection_batch(batch)
            for rule in rules:
                threshold = _parse_threshold(rule.get("异常阈值"))
                batch["caliber_snapshot"][str(rule["id"])] = {
                    "rule_name": rule.get("规则名称"),
                    "metric_name": rule.get("目标指标"),
                    "algorithm": rule.get("检测算法"),
                    "threshold": threshold,
                }
        else:
            batch = existing
            rules = [r for r in (store.find(self.MODULE, rid) for rid in batch["rule_ids"])
                     if r is not None and r.get("status") == "已启用"]

        batch["runs"] += 1
        run_no = batch["runs"]
        missing_now: list[dict[str, Any]] = []

        for rule in rules:
            rule_id = int(rule["id"])
            prior = self._find_result(batch, rule_id)
            if prior is not None and prior.get("confirmed"):
                # 已持久化的规则结论在重试中只计一次：原样保留，不再判定、不再告警。
                continue
            if prior is not None:
                # 上一轮缺样本的结论先摘除，本轮按新到样本重算并就地替换。
                batch["results"].remove(prior)

            metric_name = rule.get("目标指标", "")
            metric_id = self._metric_id(metric_name)
            threshold = batch["caliber_snapshot"][str(rule_id)]["threshold"]
            samples = store.samples_for_window(window_id, metric_name)

            if not samples:
                result = {
                    "rule_id": rule_id,
                    "rule_name": rule.get("规则名称"),
                    "metric_name": metric_name,
                    "metric_id": metric_id,
                    "status": "待补样本",
                    "confirmed": False,
                    "sample_value": None,
                    "sample_time": None,
                    "sample_id": None,
                    "threshold": threshold,
                    "algorithm": rule.get("检测算法"),
                    "judgement": None,
                    "severity": None,
                    "anomaly_record_id": None,
                    "alert_id": None,
                    "run_no": run_no,
                    "updated_at": _now_text(),
                }
                batch["results"].append(result)
                missing_now.append(result)
                continue

            # 同一窗同一指标可能有多条采样，巡检口径取窗口内最新一条采样。
            sample = max(samples, key=lambda item: item["sample_time"])
            sample_value = float(sample["value"])
            is_anomaly = sample_value > threshold
            severity = (
                "critical" if is_anomaly and sample_value > threshold * 1.3
                else "warning" if is_anomaly else "normal"
            )
            result = {
                "rule_id": rule_id,
                "rule_name": rule.get("规则名称"),
                "metric_name": metric_name,
                "metric_id": metric_id,
                "status": "已确认",
                "confirmed": True,
                "sample_value": round(sample_value, 4),
                "sample_time": sample["sample_time"],
                "sample_id": sample["id"],
                "threshold": threshold,
                "algorithm": rule.get("检测算法"),
                "judgement": "异常" if is_anomaly else "正常",
                "severity": severity,
                "anomaly_record_id": None,
                "alert_id": None,
                "run_no": run_no,
                "updated_at": _now_text(),
            }

            if is_anomaly:
                record = store.record_anomaly(
                    metric_id=metric_id,
                    severity=severity,
                    description=(
                        f"指标 {metric_name} 批量巡检检测到异常："
                        f"{sample_value:.2f} > {threshold}（批次 {batch['batch_id']}，"
                        f"观察窗 {window_id}）"
                    ),
                    value=sample_value,
                    threshold=threshold,
                )
                alert = store.add_alert({
                    "batch_id": batch["batch_id"],
                    "window_id": window_id,
                    "rule_id": rule_id,
                    "rule_name": rule.get("规则名称"),
                    "metric_name": metric_name,
                    "metric_id": metric_id,
                    "severity": severity,
                    "value": round(sample_value, 4),
                    "threshold": threshold,
                    "sample_time": sample["sample_time"],
                    "sample_id": sample["id"],
                    # 告警只在检测确认后才会生成，因此默认可进入处置闸门。
                    "status": "待处置",
                    "handleable": True,
                    "handled": False,
                    "created_at": _now_text(),
                })
                result["anomaly_record_id"] = record["id"]
                result["alert_id"] = alert["id"]

            batch["results"].append(result)

            store.log_query(
                metric_id=metric_id,
                operator="异常检测器",
                sql_summary=(
                    f"批量巡检: 批次 {batch['batch_id']} 第{run_no}轮，"
                    f"规则 {rule.get('规则名称')} 取 {metric_name}@{sample['sample_time']}"
                    f"={sample_value:.2f}，判定={result['judgement']}"
                ),
                row_count=1,
                duration_ms=10.0,
                status="正常" if not is_anomaly else "异常终止",
                extra={
                    "log_type": "batch_inspection",
                    "batch_id": batch["batch_id"],
                    "window_id": window_id,
                    "run_no": run_no,
                    "rule_id": rule_id,
                    "rule_name": rule.get("规则名称"),
                    "metric_name": metric_name,
                    "sample_id": sample["id"],
                    "sample_time": sample["sample_time"],
                    "sample_value": round(sample_value, 4),
                    "judgement": result["judgement"],
                    "confirmed": True,
                },
            )

        # 缺样本规则同样落日志，值班员据此拿到本批待补项。
        for result in missing_now:
            store.log_query(
                metric_id=result["metric_id"],
                operator="异常检测器",
                sql_summary=(
                    f"批量巡检: 批次 {batch['batch_id']} 第{run_no}轮，"
                    f"规则 {result['rule_name']} 缺少观察窗 {window_id} 的"
                    f"{result['metric_name']} 采样，待补样本"
                ),
                row_count=0,
                duration_ms=10.0,
                status="异常终止",
                extra={
                    "log_type": "batch_inspection",
                    "batch_id": batch["batch_id"],
                    "window_id": window_id,
                    "run_no": run_no,
                    "rule_id": result["rule_id"],
                    "rule_name": result["rule_name"],
                    "metric_name": result["metric_name"],
                    "sample_id": None,
                    "sample_time": None,
                    "sample_value": None,
                    "judgement": None,
                    "confirmed": False,
                },
            )

        summary = self._summarize_batch(batch)
        store.log_query(
            metric_id=0,
            operator="值班长",
            sql_summary=(
                f"批次 {batch['batch_id']} 第{run_no}轮覆盖核算：规则 {summary['total_rules']} 条，"
                f"已确认 {summary['confirmed_rules']} 条，待补 {summary['pending_rules']} 条，"
                f"整窗报告齐全={summary['window_report_complete']}，"
                f"可处置告警 {summary['handleable_alerts']} 条"
            ),
            row_count=summary["confirmed_rules"],
            duration_ms=2.0,
            status="正常",
            extra={
                "log_type": "batch_coverage",
                "batch_id": batch["batch_id"],
                "window_id": window_id,
                "run_no": run_no,
                **summary,
                "pending_items": self.get_pending_items(batch["batch_id"]),
            },
        )
        return self.get_batch(batch["batch_id"])

    def list_batches(self, window_id: str | None = None) -> list[dict[str, Any]]:
        batches = store.inspection_batches()
        if window_id is not None:
            batches = [b for b in batches if b.get("window_id") == window_id]
        return [self._summarize_batch(batch) for batch in batches]

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        batch = store.find_inspection_batch(batch_id)
        if batch is None:
            return None
        payload = self._summarize_batch(batch)
        payload["caliber_snapshot"] = batch["caliber_snapshot"]
        payload["results"] = [dict(result) for result in batch["results"]]
        return payload

    def get_pending_items(self, batch_id: str) -> list[dict[str, Any]]:
        """按原批次列出仍缺样本的规则（待补项）。"""
        batch = store.find_inspection_batch(batch_id)
        if batch is None:
            return []
        items: list[dict[str, Any]] = []
        for result in batch["results"]:
            if not result["confirmed"]:
                items.append({
                    "rule_id": result["rule_id"],
                    "rule_name": result["rule_name"],
                    "metric_name": result["metric_name"],
                    "window_id": batch["window_id"],
                    "reason": "缺少该观察窗的指标采样",
                })
        return items

    # ── 告警处置闸门 ──

    def list_alerts(self, window_id: str | None = None, batch_id: str | None = None) -> list[dict[str, Any]]:
        alerts = store.alerts()
        if window_id is not None:
            alerts = [a for a in alerts if a.get("window_id") == window_id]
        if batch_id is not None:
            alerts = [a for a in alerts if a.get("batch_id") == batch_id]
        return [dict(alert) for alert in alerts]

    def handle_alert(self, alert_id: int, handler: str = "值班员") -> dict[str, Any]:
        """处置告警。未确认的检测不得进入告警处置，闸门不通过直接拒绝。"""
        alert = store.find_alert(alert_id)
        if alert is None:
            return {"ok": False, "error": f"告警 {alert_id} 不存在"}
        batch = store.find_inspection_batch(alert["batch_id"])
        result = self._find_result(batch, alert["rule_id"]) if batch else None
        if result is None or not result.get("confirmed"):
            alert["handleable"] = False
            return {
                "ok": False,
                "error": "检测结论未确认（缺样本），未确认的检测不得进入告警处置",
                "alert_id": alert_id,
            }
        if alert.get("handled"):
            return {
                "ok": True,
                "message": f"告警 {alert_id} 已由 {alert.get('handled_by')} 处置，无需重复处理",
                "alert": dict(alert),
            }
        alert["handleable"] = True
        alert["handled"] = True
        alert["status"] = "已处置"
        alert["handled_by"] = handler
        alert["handled_at"] = _now_text()
        return {"ok": True, "message": f"告警 {alert_id} 已处置", "alert": dict(alert)}

    # ── 日志关联与覆盖核算 ──

    def get_batch_log_trace(self, batch_id: str) -> dict[str, Any]:
        """把批次覆盖核算与查询日志、采样接口响应关联起来。"""
        batch = store.find_inspection_batch(batch_id)
        if batch is None:
            return {"error": f"巡检批次 {batch_id} 不存在"}
        window_id = batch["window_id"]
        logs = [
            log for log in store.query_logs()
            if log.get("batch_id") == batch_id
            or (log.get("log_type") == "metric_sample" and log.get("window_id") == window_id)
        ]
        sample_responses = [
            {
                "sample_id": log.get("sample_id"),
                "metric_name": log.get("metric_name"),
                "sample_time": log.get("sample_time"),
                "sample_value": log.get("sample_value"),
                "window_id": log.get("window_id"),
                "查询时间": log.get("查询时间"),
            }
            for log in logs if log.get("log_type") == "metric_sample"
        ]
        return {
            "batch_id": batch_id,
            "window_id": window_id,
            "coverage": self._summarize_batch(batch),
            "pending_items": self.get_pending_items(batch_id),
            "sample_responses": sample_responses,
            "journal_logs": [
                {key: log.get(key) for key in (
                    "id", "查询时间", "操作人", "涉及指标", "查询语句摘要",
                    "status", "log_type", "rule_id", "rule_name", "judgement",
                    "confirmed", "run_no",
                ) if key in log}
                for log in logs
            ],
        }

    def get_window_coverage(self, window_id: str) -> dict[str, Any]:
        """核算当班（某观察窗）巡检覆盖、可处置告警与整窗报告齐全度。"""
        window = store.find_window(window_id)
        if window is None:
            return {"error": f"观察窗 {window_id} 不存在"}
        batches = [b for b in store.inspection_batches() if b.get("window_id") == window_id]
        samples = store.samples_for_window(window_id)
        sample_metrics = sorted({s["metric_name"] for s in samples})
        alerts = self.list_alerts(window_id=window_id)

        def _alert_confirmed(alert: dict[str, Any]) -> bool:
            result = self._find_result(store.find_inspection_batch(alert["batch_id"]),
                                       alert["rule_id"])
            return result is not None and result.get("confirmed")

        handleable = [a for a in alerts if a.get("handled") or _alert_confirmed(a)]
        total_rules = sum(len(batch["rule_ids"]) for batch in batches)
        confirmed = sum(
            1 for batch in batches for result in batch["results"] if result["confirmed"]
        )
        pending = sum(
            1 for batch in batches for result in batch["results"] if not result["confirmed"]
        )
        complete = bool(batches) and pending == 0 and all(
            len(batch["results"]) == len(batch["rule_ids"]) for batch in batches
        )
        return {
            "window_id": window_id,
            "window_start": window["window_start"],
            "window_end": window["window_end"],
            "batch_ids": [b["batch_id"] for b in batches],
            "sample_count": len(samples),
            "sampled_metrics": sample_metrics,
            "total_rules": total_rules,
            "confirmed_rules": confirmed,
            "pending_rules": pending,
            "completed_run_count": sum(b["runs"] for b in batches),
            "window_report_complete": complete,
            "alerts_total": len(alerts),
            "alerts_handleable": len(handleable),
            "alerts_pending_confirmation": len(alerts) - len(handleable),
        }

    # ── 单规则检测（保留原逐条运行能力） ──

    def run_detection(self, entry_id: int) -> dict[str, Any] | None:
        """执行异常检测。

        跨模块数据流: anomaly_detector -> metric_definition（检测目标指标）
        """
        rule = store.find(self.MODULE, entry_id)
        if rule is None:
            return {"error": f"检测规则 {entry_id} 不存在"}
        if rule["status"] != "已启用":
            return {"error": f"检测规则 {entry_id} 未启用"}

        metric_name = rule.get("目标指标", "")
        threshold = _parse_threshold(rule.get("异常阈值"))
        algorithm = rule.get("检测算法", "threshold")

        # 查找关联指标
        metrics = store.find_by_field("metric_definition", "指标名称", metric_name)
        if not metrics:
            return {"error": f"未找到指标 {metric_name}"}

        # 模拟检测
        import random
        random.seed(entry_id)
        metric_id = int(metrics[0].get("id", 0))
        sample_value = random.uniform(threshold * 0.5, threshold * 1.5)

        is_anomaly = sample_value > threshold

        if is_anomaly:
            store.record_anomaly(
                metric_id=metric_id,
                severity="critical" if sample_value > threshold * 1.3 else "warning",
                description=f"指标 {metric_name} 检测到异常：{sample_value:.2f} > {threshold}",
                value=sample_value,
                threshold=threshold,
            )

        # 更新查询日志中相关的异常记录
        store.log_query(
            metric_id=metric_id,
            operator="异常检测器",
            sql_summary=f"异常检测: {rule.get('规则名称')}, 算法={algorithm}",
            row_count=1,
            duration_ms=10.0,
            status="正常" if not is_anomaly else "异常终止",
        )

        return {
            "rule_name": rule.get("规则名称"),
            "metric": metric_name,
            "algorithm": algorithm,
            "threshold": threshold,
            "sample_value": round(sample_value, 2),
            "is_anomaly": is_anomaly,
            "severity": "critical" if is_anomaly and sample_value > threshold * 1.3
                        else "warning" if is_anomaly else "normal",
            "detection_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def get_anomaly_report(self, metric_name: str | None = None) -> list[dict[str, Any]]:
        """获取异常报告。

        跨模块数据流: anomaly_detector -> query_journal（异常记录关联）
        """
        records = store.anomaly_records()
        if metric_name:
            records = [r for r in records if metric_name in str(r.get("描述", ""))]

        # 关联查询日志
        for record in records:
            metric_id = record.get("指标ID")
            related_logs = [log for log in store.query_logs()
                            if f"metric_{metric_id}" in str(log.get("涉及指标", ""))
                            and "异常" in str(log.get("查询语句摘要", ""))]
            record["related_queries"] = len(related_logs)

        return records

    def get_detection_coverage(self) -> dict[str, Any]:
        """获取异常检测覆盖率统计。"""
        rules = store.rows(self.MODULE)
        enabled = [r for r in rules if r.get("status") == "已启用"]
        anomalies = store.anomaly_records()

        return {
            "total_rules": len(rules),
            "enabled_rules": len(enabled),
            "disabled_rules": len(rules) - len(enabled),
            "total_anomalies_detected": len(anomalies),
            "coverage_ratio": round(len(enabled) / max(1, len(rules)), 2) if rules else 0,
        }

    # ── 内部辅助 ──

    def _select_rules(self, rule_ids: list[int] | None) -> list[dict[str, Any]] | dict[str, Any]:
        rules = store.rows(self.MODULE)
        if rule_ids:
            selected = []
            for rid in rule_ids:
                rule = store.find(self.MODULE, int(rid))
                if rule is None:
                    return {"error": f"检测规则 {rid} 不存在"}
                selected.append(rule)
            rules = selected
        enabled = [rule for rule in rules if rule.get("status") == "已启用"]
        if not enabled:
            return {"error": "没有可参与批量巡检的已启用检测规则"}
        return enabled

    def _metric_id(self, metric_name: str) -> int:
        metrics = store.find_by_field("metric_definition", "指标名称", metric_name)
        return int(metrics[0].get("id", 0)) if metrics else 0

    @staticmethod
    def _find_result(batch: dict[str, Any] | None, rule_id: int) -> dict[str, Any] | None:
        if batch is None:
            return None
        for result in batch["results"]:
            if result["rule_id"] == rule_id:
                return result
        return None

    def _summarize_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        results = batch["results"]
        confirmed = [r for r in results if r["confirmed"]]
        pending = [r for r in results if not r["confirmed"]]
        anomalies = [r for r in confirmed if r["judgement"] == "异常"]
        alerts = [a for a in store.alerts() if a.get("batch_id") == batch["batch_id"]]
        handleable_alerts = [a for a in alerts if a.get("handled") or self._result_confirmed(
            batch, a["rule_id"]
        )]
        return {
            "batch_id": batch["batch_id"],
            "window_id": batch["window_id"],
            "run_count": batch["runs"],
            "total_rules": len(batch["rule_ids"]),
            "confirmed_rules": len(confirmed),
            "pending_rules": len(pending),
            "anomaly_rules": len(anomalies),
            "handleable_alerts": len(handleable_alerts),
            "persisted_conclusion_count": len(confirmed),
            "window_report_complete": len(pending) == 0
            and len(results) == len(batch["rule_ids"]),
            "created_at": batch["created_at"],
        }

    @staticmethod
    def _result_confirmed(batch: dict[str, Any], rule_id: int) -> bool:
        result = AnomalyDetectorService._find_result(batch, rule_id)
        return bool(result and result.get("confirmed"))
