<script setup lang="ts">
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import { dashboardApi } from '../api'
import { resultOptions } from '../api/meta'
import StatusTag from '../components/common/StatusTag.vue'
import type { DailyRow, EventRow, Overview } from '../types'

// 签到台账: 每日折叠视图(每天每域最后一次运行 = 当天最终状态) + 按日期下钻明细。

const message = useMessage()
const overview = ref<Overview | null>(null)
const days = ref(14)
const domain = ref<string | null>(null)
const result = ref<string | null>(null)
// computed 包一层: 词表由 App.vue 全局加载,晚到时选项自动补上
const resultOpts = computed(resultOptions)
const rows = ref<DailyRow[]>([])
const events = ref<EventRow[] | null>(null)
const eventsTitle = ref('')
const loading = ref(false)

// 客户端分页: 台账最多 90 天 × 域数行,量级小,不必给 API 加 offset/total
function makePagination(defaultSize: number) {
  const p = reactive({
    page: 1,
    pageSize: defaultSize,
    showSizePicker: true,
    pageSizes: [10, 20, 50, 100],
    onChange: (page: number) => { p.page = page },
    onUpdatePageSize: (size: number) => { p.pageSize = size; p.page = 1 },
  })
  return p
}
const pagination = makePagination(10)
const eventPagination = makePagination(10)

async function load() {
  loading.value = true
  pagination.page = 1
  try {
    rows.value = await dashboardApi.history(days.value, domain.value ?? undefined, result.value ?? undefined)
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

async function drill(row: DailyRow) {
  try {
    events.value = await dashboardApi.events({ date: row.day, domain: row.domain, result: result.value ?? undefined, limit: 100 })
    eventsTitle.value = `${row.domain} · ${row.day} 明细`
    eventPagination.page = 1
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

const eventColumns = [
  { title: '时间', key: 'executed_at', width: 190 },
  { title: '动作', key: 'action', width: 90, render: (e: EventRow) => e.action ?? '-' },
  { title: '来源', key: 'source', width: 90, render: (e: EventRow) => e.source ?? '-' },
  {
    title: '结果', key: 'result', width: 110,
    render: (e: EventRow) => h(StatusTag, { result: e.result }),
  },
  { title: '摘要', key: 'report' },
]

onMounted(async () => {
  try {
    overview.value = await dashboardApi.overview()
  } catch { /* 域下拉退化: 不填也能查全部 */ }
  await load()
})

// 筛选变化自动重查: 不自动刷的话表格停在旧数据,看起来像"数据不对"
watch([days, domain, result], load)

// 重置 = 回到默认查询条件;watch 会自动触发重查
const filtered = computed(() => domain.value !== null || result.value !== null || days.value !== 14)
function resetFilters() {
  domain.value = null
  result.value = null
  days.value = 14
}
</script>

<template>
  <n-space vertical size="large">
    <n-space align="center">
      <n-select v-model:value="domain" clearable placeholder="全部域" style="width: 180px"
                :options="(overview?.cards ?? []).map((c) => ({ label: c.domain, value: c.domain }))" />
      <n-select v-model:value="days" style="width: 130px" :options="[
        { label: '近 14 天', value: 14 },
        { label: '近 30 天', value: 30 },
        { label: '近 90 天', value: 90 },
        { label: '近 180 天', value: 180 },
      ]" />
      <n-select v-model:value="result" clearable placeholder="全部结果" style="width: 180px"
                :options="resultOpts" />
      <n-button quaternary type="primary" :disabled="!filtered" @click="resetFilters">重置</n-button>
      <n-button @click="load">查询</n-button>
      <n-text depth="3" style="margin-left: auto">共 {{ rows.length }} 条(每天每域一行,非每次运行)</n-text>
    </n-space>

    <n-data-table :columns="columns" :data="rows" :bordered="false" size="small"
                  :pagination="pagination"
                  :row-class-name="(r: DailyRow) => (r.needs_attention ? 'row-attention' : '')"
                  style="background: var(--ask-surface)" />

    <n-card v-if="events" :title="eventsTitle" size="small" style="background: var(--ask-surface)">
      <n-data-table :columns="eventColumns" :data="events" :bordered="false" size="small"
                    :pagination="eventPagination" />
    </n-card>
  </n-space>
</template>

<style scoped>
:deep(.row-attention td) {
  background: rgba(239, 68, 68, 0.06);
}
</style>
