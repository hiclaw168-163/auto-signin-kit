<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { dashboardApi, scheduleApi } from '../api'
import { resultLabel } from '../api/meta'
import { useJob } from '../composables/useJob'
import DomainCard from '../components/dashboard/DomainCard.vue'
import StatCard from '../components/dashboard/StatCard.vue'
import StatusTag from '../components/common/StatusTag.vue'
import type { DailyRow, EventRow, Overview, ScheduleInfo } from '../types'

// 签到总览 = 汇总统计卡 + 每域状态卡(近 7 天条形) + 最近运行时间线。
// 所有数据来自 SQLite 台账(core/store),不重复实现业务。

const message = useMessage()
const overview = ref<Overview | null>(null)
const week = ref<DailyRow[]>([])
const schedule = ref<ScheduleInfo | null>(null)
const events = ref<EventRow[]>([])
const loading = ref(false)
const { jobs, run } = useJob()

const OK = ['CLAIMED', 'ALREADY', 'ACTIVE']
const today = new Date().toISOString().slice(0, 10)

const cards = computed(() => overview.value?.cards ?? [])
const signedToday = computed(() => cards.value.filter((c) => c.today && OK.includes(c.today.result)).length)
const attentionToday = computed(() => cards.value.filter((c) => c.today?.needs_attention).length)
const weekRatio = computed(() => {
  const ok = cards.value.reduce((s, c) => s + c.week_ok, 0)
  const total = cards.value.reduce((s, c) => s + c.week_total, 0)
  return total ? Math.round((ok / total) * 100) : 0
})
const installedCount = computed(() => schedule.value?.tasks.filter((t) => t.installed).length ?? 0)

function weekRows(domain: string): DailyRow[] {
  return week.value.filter((r) => r.domain === domain)
}

async function runCard(domain: string) {
  await run(domain, 'auto', (job) => {
    if (job.status === 'ok') message.success(`${domain}: ${(job.result?.report as string) ?? '完成'}`)
    else message.error(`${domain}: ${job.status}(exit=${job.exit_code ?? '?'})`)
    load()
  })
}

async function load() {
  loading.value = true
  try {
    const [ov, wk, sch, ev] = await Promise.all([
      dashboardApi.overview(),
      dashboardApi.history(7),
      scheduleApi.status(),
      dashboardApi.events({ limit: 8 }),
    ])
    overview.value = ov
    week.value = wk
    schedule.value = sch
    events.value = ev
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <n-spin :show="loading">
    <n-space vertical size="large">
      <n-grid :cols="4" :x-gap="12" item-responsive>
        <n-gi span="1 m:2 l:1"><StatCard label="今日已签" :value="`${signedToday}/${cards.length}`"
          sub="按各域台账最新记录" tone="primary" /></n-gi>
        <n-gi span="1 m:2 l:1"><StatCard label="今日需关注" :value="String(attentionToday)"
          sub="登录过期/失败,需人工介入" :tone="attentionToday ? 'danger' : 'info'" /></n-gi>
        <n-gi span="1 m:2 l:1"><StatCard label="近 7 天成功率" :value="`${weekRatio}%`"
          sub="全部签到域合计" tone="info" /></n-gi>
        <n-gi span="1 m:2 l:1"><StatCard label="定时任务" :value="`${installedCount}/${schedule?.tasks.length ?? 0}`"
          :sub="`平台: ${schedule?.platform ?? '-'}`" tone="primary" /></n-gi>
      </n-grid>

      <n-grid :cols="2" :x-gap="12" :y-gap="12" item-responsive>
        <n-gi v-for="card in cards" :key="card.domain" span="1 m:2 l:1">
          <DomainCard :card="card" :week="weekRows(card.domain)" :job="jobs[card.domain]"
                      :running="jobs[card.domain]?.status === 'running'" @run="runCard(card.domain)" />
        </n-gi>
      </n-grid>

      <n-card title="最近运行" size="small" style="background: var(--ask-surface)">
        <n-empty v-if="!events.length" description="还没有任何运行记录" />
        <n-timeline v-else>
          <n-timeline-item v-for="(e, i) in events" :key="i"
            :type="e.needs_attention ? 'error' : 'success'"
            :title="`${e.domain} · ${resultLabel(e.result)}`"
            :content="`${e.executed_at} — ${e.report}`" />
        </n-timeline>
      </n-card>
    </n-space>
  </n-spin>
</template>
