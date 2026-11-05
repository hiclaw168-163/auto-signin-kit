// 与 Python 侧 src/auto_signin_kit/ui/api.py + ui/apps_api.py 的响应形状对齐;
// 改 API 时两边一起改。

// SPEC-11 应用管理: 注册表发现的应用(名称/类别/显示名/就绪探测/配置/调度时间)。
export interface AppInfo {
  name: string
  kind: string | null // web | client | null(未声明 KIND 的域)
  display: string
  ready: boolean | null
  config: Record<string, unknown> | null // 域 YAML 字段视图;null = 配置文件不存在
  times: string[] // 该域全部定时任务的触发时间(已套 schedule.yaml 覆盖)
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

// 应用配置编辑: 字段视图(表单化)与部分更新结果。
export interface AppConfigInfo {
  domain: string
  fields: Record<string, unknown>
}

export interface AppUpdateResult {
  ok: boolean
  path: string
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
  account: string | null
  argv: string[]
  started_at: string
  finished_at: string | null
  status: 'running' | 'ok' | 'failed' | 'timeout'
  exit_code: number | null
  output: string
  result: Record<string, unknown> | null
}

// SPEC-12 账户视图: 登记账户(ask_accounts) × 逐账户运行统计(overview_by_account)。
export interface AccountRow {
  domain: string
  name: string
  enabled: boolean
  daily_potential: number | null
  device_id: string | null
  client_version: string | null
  uid: string | null
  streak: number
  days14: { day: string; ok: boolean }[]
  credits: number
  today_credits: number
  last_run: string | null
}

export interface AccountsInfo {
  total: number
  active: number
  window_days: number
  accounts: AccountRow[]
}
