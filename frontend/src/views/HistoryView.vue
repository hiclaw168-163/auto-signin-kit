<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { api } from '../api/client'
import type { EventRow, Overview } from '../types'

// 历史: 每日折叠视图 + 按日期下钻明细。

const message = useMessage()
const overview = ref<Overview | null>(null)
const days = ref(7)
const domain = ref<string | null>(null)
const rows = ref<{ day: string; domain: string; result: string; needs_attention: number; report: string; executed_at: string }[]>([])
const events = ref<EventRow[] | null>(null)
const eventsTitle = ref('')
const loading = ref(false)

function tagType(result: string): 'success' | 'info' | 'error' {
  if (result === 'CLAIMED') return 'success'
  if (result === 'ALREADY' || result === 'ACTIVE') return 'info'
  return 'error'
}

async function load() {
  loading.value = true
  try {
    rows.value = await api.history(days.value, domain.value ?? undefined)
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

async function drill(row: { day: string; domain: string }) {
  try {
    events.value = await api.events({ date: row.day, domain: row.domain, limit: 100 })
    eventsTitle.value = `${row.domain} · ${row.day} 明细`
  } catch (e) {
    message.error(String(e))
  }
}

onMounted(async () => {
  try {
    overview.value = await api.overview()
  } catch { /* 域下拉退化: 不填也能查全部 */ }
  await load()
})
</script>

<template>
  <n-space vertical size="large">
    <n-space align="center">
      <n-select v-model:value="domain" clearable placeholder="全部域" style="width: 160px"
                :options="(overview?.cards ?? []).map((c) => ({ label: c.domain, value: c.domain }))" />
      <n-select v-model:value="days" style="width: 120px" :options="[
        { label: '近 7 天', value: 7 },
        { label: '近 14 天', value: 14 },
        { label: '近 30 天', value: 30 },
        { label: '近 90 天', value: 90 },
      ]" />
      <n-button @click="load">刷新</n-button>
    </n-space>

    <n-table size="small" :single-line="false">
      <thead>
        <tr>
          <th>日期</th><th>域</th><th>结果</th><th>摘要</th><th>最后运行</th><th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.day + r.domain"
            :style="r.needs_attention ? 'background: rgba(255,0,0,0.04)' : ''">
          <td>{{ r.day }}</td>
          <td>{{ r.domain }}</td>
          <td><n-tag :type="tagType(r.result)" size="small">{{ r.result }}</n-tag></td>
          <td>{{ r.report }}</td>
          <td><n-text depth="3" style="font-size: 12px">{{ r.executed_at }}</n-text></td>
          <td><n-button size="tiny" quaternary @click="drill(r)">明细</n-button></td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="6">没有记录</td>
        </tr>
      </tbody>
    </n-table>

    <n-card v-if="events" :title="eventsTitle" size="small">
      <n-table size="small" :single-line="false">
        <thead>
          <tr><th>时间</th><th>动作</th><th>来源</th><th>结果</th><th>摘要</th></tr>
        </thead>
        <tbody>
          <tr v-for="(e, i) in events" :key="i">
            <td>{{ e.executed_at }}</td>
            <td>{{ e.action ?? '-' }}</td>
            <td>{{ e.source ?? '-' }}</td>
            <td>{{ e.result }}</td>
            <td>{{ e.report }}</td>
          </tr>
        </tbody>
      </n-table>
    </n-card>
  </n-space>
</template>
