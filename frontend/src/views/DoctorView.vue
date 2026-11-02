<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { dashboardApi } from '../api'
import { useJob } from '../composables/useJob'
import type { Overview } from '../types'

// 诊断: 走 run(domain, 'doctor') 子进程,轮询取结构化报告 + 原始 JSON。

const message = useMessage()
const overview = ref<Overview | null>(null)
const domain = ref<string | null>(null)
const { jobs, run } = useJob()

const job = () => (domain.value ? jobs[domain.value] : undefined)

async function runDoctor() {
  if (!domain.value) return
  try {
    await run(domain.value, 'doctor', (done) => {
      if (done.status !== 'ok') message.warning(`doctor ${done.status}`)
    })
  } catch (e) {
    message.warning(String(e))
  }
}

onMounted(async () => {
  try {
    overview.value = await dashboardApi.overview()
    if (!domain.value && overview.value.cards.length) {
      domain.value = overview.value.cards[0].domain
    }
  } catch { /* 下拉留空也能手输 */ }
})
</script>

<template>
  <n-space vertical size="large">
    <n-space align="center">
      <n-select v-model:value="domain" placeholder="选择域" style="width: 220px"
                :options="(overview?.cards ?? []).map((c) => ({ label: c.domain, value: c.domain }))" />
      <n-button type="primary" :disabled="!domain || job()?.status === 'running'" @click="runDoctor">
        运行诊断
      </n-button>
    </n-space>

    <template v-if="job()">
      <n-alert :type="job()!.status === 'ok' ? 'success' : job()!.status === 'running' ? 'info' : 'error'"
               :title="`doctor ${job()!.action} · ${job()!.status}`">
        <template v-if="job()!.result?.report">{{ job()!.result.report }}</template>
        <template v-else-if="job()!.status === 'running'">子进程运行中…</template>
        <template v-else>退出码 {{ job()!.exit_code ?? '?' }}</template>
      </n-alert>
      <n-card size="small" title="原始输出(JSON Lines)" style="background: var(--ask-surface)">
        <pre class="mono" style="margin: 0; white-space: pre-wrap; font-size: 12px">{{ job()!.output || '(无输出)' }}</pre>
      </n-card>
    </template>
  </n-space>
</template>
