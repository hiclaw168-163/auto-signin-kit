// 与 Python 侧 src/auto_signin_kit/ui/api.py + ui/apps_api.py 的响应形状对齐;
// 改 API 时两边一起改。

// SPEC-11 应用管理: 注册表发现的应用(名称/类别/显示名/就绪探测)。
export interface AppInfo {
  name: string
  kind: string | null // web | client | null(未声明 KIND 的域)
  display: string
  ready: boolean | null
}

export interface AppsInfo {
  apps: AppInfo[]
  generated_at: string
}

export interface AppCreateResult {
  result: string
  domain: string
  files?: number
  removed?: string[]
  message: string
}

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
  /** 截至今天的连续成功天数(今天未签则从昨天起算) */
  streak?: number
  /** 今天往前 14 天逐日成败,供色块条 */
  days14?: { day: string; ok: boolean }[]
  /** 台账累计积分(成功行 response.credits 求和;域不回传则 0) */
  credits?: number
  today_credits?: number
  last_run?: string | null
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
  action?: string
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
