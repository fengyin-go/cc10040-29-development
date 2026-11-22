<template>
  <section class="page batch-page">
    <header class="page-head">
      <div>
        <h2>异常检测器 · 批量巡检</h2>
        <p class="page-desc">
          同一观察窗多规则批量判定，逐规则留存指标值、采样时间与结论；
          迟到样本归原窗，已确认结论补跑只计一次，未确认检测不得进入告警处置。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reloadAll">刷新数据</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <div class="panel-grid">
      <article class="panel">
        <h3>1. 观察窗登记</h3>
        <form class="stack-form" @submit.prevent="submitWindow">
          <label><span>窗口ID</span><input v-model="windowForm.window_id" placeholder="如 W-0800" required /></label>
          <label><span>开始时间</span><input v-model="windowForm.window_start" placeholder="2026-11-22 08:00:00" required /></label>
          <label><span>结束时间</span><input v-model="windowForm.window_end" placeholder="2026-11-22 09:00:00" required /></label>
          <button class="btn primary" type="submit" :disabled="busy">登记观察窗</button>
        </form>
        <table class="data-table">
          <thead><tr><th>窗口ID</th><th>开始</th><th>结束</th></tr></thead>
          <tbody>
            <tr v-for="row in windows" :key="row.window_id">
              <td>{{ row.window_id }}</td><td>{{ row.window_start }}</td><td>{{ row.window_end }}</td>
            </tr>
            <tr v-if="!windows.length"><td colspan="3" class="empty-state">暂无观察窗</td></tr>
          </tbody>
        </table>
      </article>

      <article class="panel">
        <h3>2. 指标采样上报（按采样时间归窗）</h3>
        <form class="stack-form" @submit.prevent="submitSample">
          <label><span>指标名称</span><input v-model="sampleForm.metric_name" required /></label>
          <label><span>采样值</span><input v-model.number="sampleForm.value" type="number" step="0.01" required /></label>
          <label><span>采样时间</span><input v-model="sampleForm.sample_time" placeholder="2026-11-22 08:20:00" required /></label>
          <label><span>上报时间（留空视为按时）</span><input v-model="sampleForm.reported_at" placeholder="迟到补传时填写" /></label>
          <button class="btn primary" type="submit" :disabled="busy">上报采样</button>
        </form>
        <table class="data-table">
          <thead><tr><th>指标</th><th>值</th><th>采样时间</th><th>窗口</th><th>迟到</th></tr></thead>
          <tbody>
            <tr v-for="row in samples" :key="row.id">
              <td>{{ row.metric_name }}</td>
              <td>{{ row.value }}</td>
              <td>{{ row.sample_time }}</td>
              <td>{{ row.window_id }}</td>
              <td>{{ row.is_late ? '是' : '否' }}</td>
            </tr>
            <tr v-if="!samples.length"><td colspan="5" class="empty-state">暂无采样</td></tr>
          </tbody>
        </table>
      </article>
    </div>

    <article class="panel">
      <h3>3. 批量巡检与原批续跑</h3>
      <form class="inline-form" @submit.prevent="submitBatch">
        <label><span>观察窗</span>
          <select v-model="batchForm.window_id" required>
            <option value="" disabled>选择观察窗</option>
            <option v-for="row in windows" :key="row.window_id" :value="row.window_id">{{ row.window_id }}</option>
          </select>
        </label>
        <label><span>原批次ID（续跑时填）</span><input v-model="batchForm.batch_id" placeholder="首轮留空" /></label>
        <button class="btn primary" type="submit" :disabled="busy">
          {{ batchForm.batch_id ? '继续原批（补项）' : '发起批量巡检' }}
        </button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>批次</th><th>窗口</th><th>轮次</th><th>规则总数</th>
            <th>已确认</th><th>待补样本</th><th>异常规则</th><th>可处置告警</th><th>整窗报告</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in batches" :key="row.batch_id">
            <td>{{ row.batch_id }}</td>
            <td>{{ row.window_id }}</td>
            <td>{{ row.run_count }}</td>
            <td>{{ row.total_rules }}</td>
            <td>{{ row.confirmed_rules }}</td>
            <td>{{ row.pending_rules }}</td>
            <td>{{ row.anomaly_rules }}</td>
            <td>{{ row.handleable_alerts }}</td>
            <td>{{ row.window_report_complete ? '齐全' : '未齐全' }}</td>
          </tr>
          <tr v-if="!batches.length"><td colspan="9" class="empty-state">暂无巡检批次</td></tr>
        </tbody>
      </table>
    </article>

    <article v-if="selectedBatch" class="panel">
      <h3>4. 批次明细（每条规则的取值、采样时间与判定） · {{ selectedBatch.batch_id }}</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>规则</th><th>目标指标</th><th>指标值</th><th>采样时间</th>
            <th>阈值</th><th>判定</th><th>状态</th><th>告警</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="result in selectedBatch.results ?? []" :key="result.rule_id">
            <td>{{ result.rule_name }}</td>
            <td>{{ result.metric_name }}</td>
            <td>{{ result.sample_value ?? '—' }}</td>
            <td>{{ result.sample_time ?? '—' }}</td>
            <td>{{ result.threshold }}</td>
            <td>{{ result.judgement ?? '—' }}</td>
            <td>{{ result.status }}</td>
            <td>{{ result.alert_id ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="pendingItems.length" class="pending-note">
        待补项：
        <span v-for="item in pendingItems" :key="item.rule_id" class="pending-chip">
          {{ item.rule_name }}（{{ item.metric_name }}）
        </span>
      </p>
      <p v-else class="page-desc">本批无待补项，整窗报告已齐全。</p>
    </article>

    <article class="panel">
      <h3>5. 当班覆盖核算与告警处置闸门</h3>
      <form class="inline-form" @submit.prevent="loadCoverage">
        <label><span>观察窗</span>
          <select v-model="coverageWindow" required>
            <option value="" disabled>选择观察窗</option>
            <option v-for="row in windows" :key="row.window_id" :value="row.window_id">{{ row.window_id }}</option>
          </select>
        </label>
        <button class="btn" type="submit">核算覆盖</button>
      </form>
      <div v-if="coverage" class="stat-row">
        <article class="stat-card"><span class="stat-label">采样数</span><strong class="stat-value">{{ coverage.sample_count }}</strong></article>
        <article class="stat-card"><span class="stat-label">已确认规则</span><strong class="stat-value">{{ coverage.confirmed_rules }}/{{ coverage.total_rules }}</strong></article>
        <article class="stat-card"><span class="stat-label">待补规则</span><strong class="stat-value">{{ coverage.pending_rules }}</strong></article>
        <article class="stat-card"><span class="stat-label">完成轮次</span><strong class="stat-value">{{ coverage.completed_run_count }}</strong></article>
        <article class="stat-card"><span class="stat-label">整窗报告</span><strong class="stat-value">{{ coverage.window_report_complete ? '齐全' : '未齐全' }}</strong></article>
        <article class="stat-card"><span class="stat-label">可处置告警</span><strong class="stat-value">{{ coverage.alerts_handleable }}/{{ coverage.alerts_total }}</strong></article>
      </div>

      <table class="data-table">
        <thead>
          <tr><th>告警ID</th><th>窗口</th><th>批次</th><th>规则</th><th>指标值/阈值</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="alert in alerts" :key="alert.id">
            <td>{{ alert.id }}</td>
            <td>{{ alert.window_id }}</td>
            <td>{{ alert.batch_id }}</td>
            <td>{{ alert.rule_name }}</td>
            <td>{{ alert.value }} / {{ alert.threshold }}</td>
            <td>{{ alert.status }}</td>
            <td>
              <button v-if="!alert.handled" class="link" type="button" :disabled="busy" @click="dispose(alert.id)">
                处置告警
              </button>
              <span v-else>{{ alert.handled_by }} 已处置</span>
            </td>
          </tr>
          <tr v-if="!alerts.length"><td colspan="7" class="empty-state">暂无告警</td></tr>
        </tbody>
      </table>
    </article>

    <footer class="page-foot">
      <span>批量巡检数据来自异常检测器跨模块接口</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  getBatch,
  getPendingItems,
  getWindowCoverage,
  handleAlert,
  ingestSample,
  listAlerts,
  listBatches,
  listSamples,
  listWindows,
  registerWindow,
  runBatch,
  type BatchSummary,
  type PendingItem,
  type SampleRow,
  type WindowCoverage,
  type WindowRow,
  type AlertRow,
} from '@/api/batch-inspection'

const windows = ref<WindowRow[]>([])
const samples = ref<SampleRow[]>([])
const batches = ref<BatchSummary[]>([])
const alerts = ref<AlertRow[]>([])
const selectedBatch = ref<BatchSummary | null>(null)
const pendingItems = ref<PendingItem[]>([])
const coverage = ref<WindowCoverage | null>(null)
const coverageWindow = ref('')
const errorMessage = ref('')
const busy = ref(false)

const windowForm = ref({ window_id: '', window_start: '', window_end: '' })
const sampleForm = ref({ metric_name: '', value: 0, sample_time: '', reported_at: '' })
const batchForm = ref({ window_id: '', batch_id: '' })

async function guard(action: () => Promise<void>) {
  errorMessage.value = ''
  busy.value = true
  try {
    await action()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  } finally {
    busy.value = false
  }
}

async function reloadAll() {
  await guard(async () => {
    windows.value = await listWindows()
    samples.value = await listSamples()
    batches.value = await listBatches()
    alerts.value = await listAlerts()
    if (selectedBatch.value) {
      const payload = await getBatch(selectedBatch.value.batch_id)
      selectedBatch.value = payload.batch
      pendingItems.value = await getPendingItems(payload.batch.batch_id)
    }
    if (coverageWindow.value) await loadCoverage()
  })
}

async function submitWindow() {
  await guard(async () => {
    await registerWindow({ ...windowForm.value })
    windowForm.value = { window_id: '', window_start: '', window_end: '' }
    windows.value = await listWindows()
  })
}

async function submitSample() {
  await guard(async () => {
    const payload = await ingestSample({
      metric_name: sampleForm.value.metric_name,
      value: sampleForm.value.value,
      sample_time: sampleForm.value.sample_time,
      reported_at: sampleForm.value.reported_at || undefined,
    })
    errorMessage.value = payload.sample.is_late
      ? `迟到样本已归入原观察窗 ${payload.sample.window_id}`
      : ''
    sampleForm.value = { metric_name: '', value: 0, sample_time: '', reported_at: '' }
    samples.value = await listSamples()
  })
}

async function submitBatch() {
  await guard(async () => {
    const payload = await runBatch({
      window_id: batchForm.value.window_id,
      batch_id: batchForm.value.batch_id || undefined,
    })
    selectedBatch.value = payload.batch
    batchForm.value.batch_id = payload.batch.batch_id
    pendingItems.value = await getPendingItems(payload.batch.batch_id)
    batches.value = await listBatches()
    alerts.value = await listAlerts()
  })
}

async function loadCoverage() {
  await guard(async () => {
    if (!coverageWindow.value) return
    const payload = await getWindowCoverage(coverageWindow.value)
    coverage.value = payload.coverage
  })
}

async function dispose(alertId: number) {
  await guard(async () => {
    await handleAlert(alertId, '值班员')
    alerts.value = await listAlerts()
  })
}

onMounted(reloadAll)
</script>

<style scoped>
.panel-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 12px; }
.panel h3 { margin: 0 0 10px; font-size: 15px; }
.stack-form { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.stack-form label span,
.inline-form label span { display: block; font-size: 12px; color: var(--muted); }
.stack-form input,
.inline-form input,
.inline-form select { width: 100%; box-sizing: border-box; }
.inline-form { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; margin-bottom: 12px; }
.inline-form label { min-width: 220px; }
.pending-note { margin-top: 10px; font-size: 13px; }
.pending-chip { display: inline-block; background: #fff4ed; border: 1px solid #f6b28a; color: #b54708;
  border-radius: 999px; padding: 2px 10px; margin-right: 6px; }
@media (max-width: 980px) { .panel-grid { grid-template-columns: 1fr; } }
</style>
