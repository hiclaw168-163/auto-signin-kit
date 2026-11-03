import type {
  AppCreateResult,
  AppConfigInfo,
  AppInfo,
  AppUpdateResult,
  AppsInfo,
  ConfigItem,
  DailyRow,
  EventRow,
  JobInfo,
  Overview,
  ScheduleInfo,
  ScheduleOpResult,
} from '../types'
import { del, get, post, put } from './http'

// 接口层按资源分组;URL 与 Python 侧 ui/api.py + ui/config_api.py 一一对应。

export const dashboardApi = {
  overview: () => get<Overview>('/api/overview'),
  history: (days: number, domain?: string, result?: string) =>
    get<DailyRow[]>('/api/history', { days, domain, result }),
  events: (query: { date?: string; domain?: string; attention_only?: boolean; result?: string; limit?: number }) =>
    get<EventRow[]>('/api/events', query),
}

export const scheduleApi = {
  status: () => get<ScheduleInfo>('/api/schedule'),
  install: (domains?: string[]) =>
    post<ScheduleOpResult>('/api/schedule/install', domains ? { domains } : {}),
  uninstall: (domains?: string[]) =>
    post<ScheduleOpResult>('/api/schedule/uninstall', domains ? { domains } : {}),
}

export const jobApi = {
  run: (domain: string, action: string) =>
    post<{ job: JobInfo }>(
      `/api/run/${encodeURIComponent(domain)}?action=${encodeURIComponent(action)}`,
    ),
  get: (id: string) => get<{ job: JobInfo }>(`/api/jobs/${encodeURIComponent(id)}`),
}

// SPEC-11 应用管理;create/delete 会让服务短暂离线重建,调用方负责轮询恢复。
// 配置编辑(getConfig/update)只写 YAML,不触发重建,下次命令运行时生效。
export const appsApi = {
  list: () => get<AppsInfo>('/api/apps'),
  create: (body: { name: string; kind: string; display?: string; base_url: string; daily_time: string }) =>
    post<AppCreateResult>('/api/apps', body),
  remove: (name: string) =>
    del<AppCreateResult>(`/api/apps/${encodeURIComponent(name)}`),
  getConfig: (name: string) =>
    get<AppConfigInfo>(`/api/apps/${encodeURIComponent(name)}/config`),
  update: (name: string, updates: Record<string, unknown>) =>
    put<AppUpdateResult>(`/api/apps/${encodeURIComponent(name)}`, { updates }),
  syncClientInfo: () =>
    post<{ result: string; done: { domain: string }[]; failed: { domain: string; error: string }[] }>(
      '/api/apps/client-info/sync', {}),
}

export const configApi = {
  list: () => get<ConfigItem[]>('/api/config'),
  get: (domain: string) => get<{ domain: string; content: string }>(`/api/config/${encodeURIComponent(domain)}`),
  validate: (domain: string, content: string) =>
    post<{ ok: boolean }>(`/api/config/${encodeURIComponent(domain)}/validate`, { content }),
  save: (domain: string, content: string) =>
    put<{ ok: boolean; path: string }>(`/api/config/${encodeURIComponent(domain)}`, { content }),
}
