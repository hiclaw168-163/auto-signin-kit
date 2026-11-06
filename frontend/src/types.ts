// 前端类型单一出口: **全部派生自 @ask/api-client 的 OpenAPI 契约生成物**
// (P0 契约层,PROPOSAL-01)。后端改契约的流程: 改 ui/contracts.py →
// `npm run gen:api` 重新生成 → 本文件与所有视图类型零改动自动跟上。
// 在视图里手写接口形状视为违例(单一事实源铁律);
// `npm run gen:api` 会顺带校验生成物与已冻结契约一致(见 tests/test_contract_gate.py)。
import type { components } from '@ask/api-client'

type S = components['schemas']

// ---- 看板/台账(api.py) ----
export type DailyRow = S['DailyRow']
export type EventRow = S['EventRow']
export type OverviewCard = S['OverviewCard']
export type Overview = S['OverviewResponse']
export type JobInfo = S['JobInfo']

// ---- 调度 ----
export type ScheduleTask = S['ScheduleTask']
export type ScheduleInfo = S['ScheduleStatusResponse']
export type ScheduleOpResult = S['ScheduleOpResult']

// ---- 账户(SPEC-12) ----
export type AccountRow = S['AccountCard']
export type AccountsInfo = S['AccountsResponse']

// ---- 积分统计(SPEC-13) ----
export type CreditTrendPoint = S['CreditTrendPoint']
export type CreditDomainRow = S['CreditDomainRow']
export type DomainBalance = S['DomainBalance']
export type CreditsStats = S['CreditsStatsResponse']

// ---- 应用管理(SPEC-11) ----
export type AppInfo = S['AppRow']
export type AppsInfo = S['AppsResponse']
export type AppCreateResult = S['AppCreateResult']
export type AppConfigInfo = S['AppConfigView']
export type AppUpdateResult = S['AppUpdateResult']

// ---- 配置编辑 ----
export type ConfigItem = S['ConfigListItem']

// ---- 非契约形状(422 逐字段错误体,前端专用视图) ----
export interface FieldError {
  loc: string
  msg: string
}
