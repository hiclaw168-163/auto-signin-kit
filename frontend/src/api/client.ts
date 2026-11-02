import type {
  EventRow,
  JobInfo,
  Overview,
  ScheduleInfo,
  ScheduleOpResult,
} from '../types'

// fetch 薄封装: 非 2xx 把后端 detail 拼进错误消息,让 NMessage 能直接展示。

async function errText(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (body?.detail) return String(body.detail)
  } catch {
    /* body 不是 JSON 就用状态码 */
  }
  return `HTTP ${res.status}`
}

async function get<T>(url: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const qs = params
    ? '?' + new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : ''
  const res = await fetch(url + qs)
  if (!res.ok) throw new Error(await errText(res))
  return res.json() as Promise<T>
}

async function post<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? '{}' : JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await errText(res))
  return res.json() as Promise<T>
}

export const api = {
  overview: () => get<Overview>('/api/overview'),
  history: (days: number, domain?: string) =>
    get<{ day: string; domain: string; result: string; needs_attention: number; report: string; executed_at: string }[]>(
      '/api/history', { days, domain }),
  events: (query: { date?: string; domain?: string; attention_only?: boolean; limit?: number }) =>
    get<EventRow[]>('/api/events', query),
  schedule: () => get<ScheduleInfo>('/api/schedule'),
  scheduleInstall: (domains?: string[]) =>
    post<ScheduleOpResult>('/api/schedule/install', domains ? { domains } : {}),
  scheduleUninstall: (domains?: string[]) =>
    post<ScheduleOpResult>('/api/schedule/uninstall', domains ? { domains } : {}),
  run: (domain: string, action: string) =>
    post<{ job: JobInfo }>(`/api/run/${encodeURIComponent(domain)}?action=${encodeURIComponent(action)}`),
  job: (id: string) => get<{ job: JobInfo }>(`/api/jobs/${encodeURIComponent(id)}`),
}
