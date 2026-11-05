// 与 Python 侧 src/auto_signin_kit/ui/{api,config_api}.py 的响应形状对齐;
// 改 API 时两边一起改。

export interface DailyRow {
  day: string
  domain: string
  result: string
  source: string
  needs_attention: number
  report: string
  executed_at: string
}

export interface OverviewCard {
  domain: string
  today: DailyRow | null
  week_ok: number
  week_total: number
}

export interface Overview {
  cards: OverviewCard[]
  generated_at: string
}

export interface ScheduleTask {
  name: string
  installed: boolean
  times: string[]
  detail?: string
}

export interface ScheduleInfo {
  platform: string
  tasks: ScheduleTask[]
}

export interface ScheduleOpResult {
  result: 'OK' | 'PARTIAL' | 'FAILED'
  done: { name: string; times?: string[] }[]
  failed: { name: string; error: string }[]
  platform: string
}

export interface EventRow {
  executed_at: string
  domain: string
  operation?: string
  source?: string
  result: string
  needs_attention: number
  report: string
}

export interface JobInfo {
  id: string
  domain: string
  action: string
  argv: string[]
  started_at: string
  finished_at: string | null
  status: 'running' | 'ok' | 'failed' | 'timeout'
  exit_code: number | null
  output: string
  result: Record<string, unknown> | null
}

export interface ConfigItem {
  domain: string
  exists: boolean
}

export interface FieldError {
  loc: string
  msg: string
}
