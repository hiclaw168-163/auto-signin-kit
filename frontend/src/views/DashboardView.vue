<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { dashboardApi, scheduleApi } from '../api'
import { resultLabel } from '../api/meta'
import { useJob } from '../composables/useJob'
import DomainCard from '../components/dashboard/DomainCard.vue'
import StatCard from '../components/dashboard/StatCard.vue'
import StatusTag from '../components/common/StatusTag.vue'
import type { EventRow, Overview, ScheduleInfo } from '../types'

// 签到总览 = 汇总统计卡 + 每域状态卡(14 天色块/连签/积分) + 最近运行时间线。
// 所有数据来自 SQLite 台账(core/store),不重复实现业务。

const message = useMessage()
const overview = ref<Overview | null>(null)
const schedule = ref<ScheduleInfo | null>(null)
const events = ref<EventRow[]>([])
const loading = ref(false)
const { jobs, run } = useJob()

const OK = ['CLAIMED', 'ALREADY', 'ACTIVE']

const cards = computed(() => overview.value?.cards ?? [])
const signedToday = computed(() => cards.value.filter((c) => c.today && OK.includes(c.today.result)).length)
const attentionToday = computed(() => cards.value.filter((c) => c.today?.needs_attention).length)
const installedCount = computed(() => schedule.value?.tasks.filter((t) => t.installed).length ?? 0)
const todayCredits = computed(() => cards.value.reduce((s, c) => s + (c.today_credits ?? 0), 0))
const bestStreak = computed(() => Math.max(0, ...cards.value.map((c) => c.streak ?? 0)))
const nextRunTime = computed(() => {
  const times = (schedule.value?.tasks ?? [])
    .filter((t) => t.installed)
    .flatMap((t) => t.times)
    .sort()
  const now = new Date().toTimeString().slice(0, 5)
  return times.find((t) => t > now) ?? times[0] ?? '-'
})

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
    const [ov, sch, ev] = await Promise.all([
      dashboardApi.overview(),
      scheduleApi.status(),
      dashboardApi.events({ limit: 8 }),
    ])
    overview.value = ov
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
        <n-gi span="1 m:2 l:1"><StatCard label="今日积分" :value="`+${todayCredits}`"
          :sub="`最长连签 ${bestStreak} 天`" tone="info" /></n-gi>
        <n-gi span="1 m:2 l:1"><StatCard label="下次自动" :value="nextRunTime"
          :sub="`已注册 ${installedCount}/${schedule?.tasks.length ?? 0} 个任务`" tone="primary" /></n-gi>
      </n-grid>

      <n-grid :cols="2" :x-gap="12" :y-gap="12" item-responsive>
        <n-gi v-for="card in cards" :key="card.domain" span="1 m:2 l:1">
          <DomainCard :card="card" :job="jobs[card.domain]"
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
