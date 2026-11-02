<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { dashboardApi } from '../api'
import StatusTag from '../components/common/StatusTag.vue'
import type { DailyRow, EventRow, Overview } from '../types'

// 签到台账: 每日折叠视图(每天每域最后一次运行 = 当天最终状态) + 按日期下钻明细。

const message = useMessage()
const overview = ref<Overview | null>(null)
const days = ref(7)
const domain = ref<string | null>(null)
const rows = ref<DailyRow[]>([])
const events = ref<EventRow[] | null>(null)
const eventsTitle = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    rows.value = await dashboardApi.history(days.value, domain.value ?? undefined)
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

async function drill(row: DailyRow) {
  try {
    events.value = await dashboardApi.events({ date: row.day, domain: row.domain, limit: 100 })
    eventsTitle.value = `${row.domain} · ${row.day} 明细`
  } catch (e) {
    message.error(String(e))
  }
}

const columns = [
  { title: '日期', key: 'day', width: 110 },
  { title: '域', key: 'domain', width: 120 },
  {
    title: '结果', key: 'result', width: 110,
    render: (row: DailyRow) => h(StatusTag, { result: row.result }),
  },
  { title: '摘要', key: 'report' },
  { title: '最后运行', key: 'executed_at', width: 190 },
  {
    title: '', key: 'op', width: 70,
    render: (row: DailyRow) =>
      h('a', { style: 'cursor:pointer;color:var(--ask-info)', onClick: () => drill(row) }, '明细'),
  },
]

onMounted(async () => {
  try {
    overview.value = await dashboardApi.overview()
  } catch { /* 域下拉退化: 不填也能查全部 */ }
  await load()
})
</script>

<template>
  <n-space vertical size="large">
    <n-space align="center">
      <n-select v-model:value="domain" clearable placeholder="全部域" style="width: 180px"
                :options="(overview?.cards ?? []).map((c) => ({ label: c.domain, value: c.domain }))" />
      <n-select v-model:value="days" style="width: 130px" :options="[
        { label: '近 7 天', value: 7 },
        { label: '近 14 天', value: 14 },
        { label: '近 30 天', value: 30 },
        { label: '近 90 天', value: 90 },
      ]" />
      <n-button @click="load">刷新</n-button>
    </n-space>

    <n-data-table :columns="columns" :data="rows" :bordered="false" size="small"
                  :row-class-name="(r: DailyRow) => (r.needs_attention ? 'row-attention' : '')"
                  style="background: var(--ask-surface)" />

    <n-card v-if="events" :title="eventsTitle" size="small" style="background: var(--ask-surface)">
      <n-table size="small" :single-line="false">
        <thead>
          <tr><th>时间</th><th>动作</th><th>来源</th><th>结果</th><th>摘要</th></tr>
        </thead>
        <tbody>
          <tr v-for="(e, i) in events" :key="i">
            <td>{{ e.executed_at }}</td>
            <td>{{ e.action ?? '-' }}</td>
            <td>{{ e.source ?? '-' }}</td>
            <td><StatusTag :result="e.result" /></td>
            <td>{{ e.report }}</td>
          </tr>
        </tbody>
      </n-table>
    </n-card>
  </n-space>
</template>

<style scoped>
:deep(.row-attention td) {
  background: rgba(239, 68, 68, 0.06);
}
</style>
