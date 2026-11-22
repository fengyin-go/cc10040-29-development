import { request } from './client'

const BASE = '/api/cross/anomaly-detector'

export type WindowRow = {
  window_id: string
  window_start: string
  window_end: string
  created_at: string
}

export type SampleRow = {
  id: number
  metric_name: string
  value: number
  sample_time: string
  reported_at: string
  window_id: string
  is_late: boolean
}

export type BatchResult = {
  rule_id: number
  rule_name: string
  metric_name: string
  status: string
  confirmed: boolean
  sample_value: number | null
  sample_time: string | null
  threshold: number
  judgement: string | null
  severity: string | null
  alert_id: number | null
  run_no: number
}

export type BatchSummary = {
  batch_id: string
  window_id: string
  run_count: number
  total_rules: number
  confirmed_rules: number
  pending_rules: number
  anomaly_rules: number
  handleable_alerts: number
  window_report_complete: boolean
  results?: BatchResult[]
}

export type WindowCoverage = {
  window_id: string
  window_start: string
  window_end: string
  batch_ids: string[]
  sample_count: number
  sampled_metrics: string[]
  total_rules: number
  confirmed_rules: number
  pending_rules: number
  completed_run_count: number
  window_report_complete: boolean
  alerts_total: number
  alerts_handleable: number
  alerts_pending_confirmation: number
}

export type AlertRow = {
  id: number
  batch_id: string
  window_id: string
  rule_id: number
  rule_name: string
  metric_name: string
  severity: string
  value: number
  threshold: number
  sample_time: string
  status: string
  handleable: boolean
  handled: boolean
  handled_by?: string
  handled_at?: string
}

export type PendingItem = {
  rule_id: number
  rule_name: string
  metric_name: string
  window_id: string
  reason: string
}

async function unwrap<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as { ok?: boolean; error?: string } & T
  if (payload.ok === false) throw new Error(payload.error ?? '请求失败')
  return payload
}

export async function listWindows(): Promise<WindowRow[]> {
  const payload = await unwrap<{ items: WindowRow[] }>(await request(`${BASE}/windows`))
  return payload.items
}

export async function registerWindow(input: {
  window_id: string
  window_start: string
  window_end: string
}): Promise<void> {
  await unwrap(await request(`${BASE}/windows`, {
    method: 'POST',
    body: JSON.stringify(input),
  }))
}

export async function ingestSample(input: {
  metric_name: string
  value: number
  sample_time: string
  reported_at?: string
}): Promise<{ sample: SampleRow }> {
  return unwrap(await request(`${BASE}/samples`, {
    method: 'POST',
    body: JSON.stringify(input),
  }))
}

export async function listSamples(windowId?: string): Promise<SampleRow[]> {
  const query = windowId ? `?window_id=${encodeURIComponent(windowId)}` : ''
  const payload = await unwrap<{ items: SampleRow[] }>(await request(`${BASE}/samples${query}`))
  return payload.items
}

export async function runBatch(input: {
  window_id: string
  rule_ids?: number[]
  batch_id?: string
}): Promise<{ batch: BatchSummary }> {
  return unwrap(await request(`${BASE}/batches`, {
    method: 'POST',
    body: JSON.stringify(input),
  }))
}

export async function listBatches(windowId?: string): Promise<BatchSummary[]> {
  const query = windowId ? `?window_id=${encodeURIComponent(windowId)}` : ''
  const payload = await unwrap<{ items: BatchSummary[] }>(await request(`${BASE}/batches${query}`))
  return payload.items
}

export async function getBatch(batchId: string): Promise<{ batch: BatchSummary }> {
  return unwrap(await request(`${BASE}/batches/${encodeURIComponent(batchId)}`))
}

export async function getPendingItems(batchId: string): Promise<PendingItem[]> {
  const payload = await unwrap<{ pending_items: PendingItem[] }>(
    await request(`${BASE}/batches/${encodeURIComponent(batchId)}/pending`),
  )
  return payload.pending_items
}

export async function getWindowCoverage(windowId: string): Promise<{ coverage: WindowCoverage }> {
  return unwrap(await request(`${BASE}/windows/${encodeURIComponent(windowId)}/coverage`))
}

export async function listAlerts(windowId?: string): Promise<AlertRow[]> {
  const query = windowId ? `?window_id=${encodeURIComponent(windowId)}` : ''
  const payload = await unwrap<{ items: AlertRow[] }>(await request(`${BASE}/alerts${query}`))
  return payload.items
}

export async function handleAlert(alertId: number, handler: string): Promise<void> {
  const query = `?handler=${encodeURIComponent(handler || '值班员')}`
  await unwrap(await request(`${BASE}/alerts/${alertId}/handle${query}`, { method: 'POST' }))
}
