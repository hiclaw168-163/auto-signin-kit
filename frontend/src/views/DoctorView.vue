<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { api } from '../api/client'
import type { JobInfo, Overview } from '../types'

// 诊断: 走 run(domain, 'doctor') 子进程,轮询 job 取结构化报告。

const message = useMessage()
const overview = ref<Overview | null>(null)
const domain = ref<string | null>(null)
const job = ref<JobInfo | null>(null)
let timer: number | undefined

async function runDoctor() {
  if (!domain.value) return
  try {
    const res = await api.run(domain.value, 'doctor')
    job.value = res.job
    timer = window.setInterval(async () => {
      try {
        const polled = await api.job(res.job.id)
        job.value = polled.job
        if (polled.job.status !== 'running') {
          window.clearInterval(timer)
          timer = undefined
          if (polled.job.status !== 'ok') message.warning(`doctor ${polled.job.status}`)
        }
      } catch (e) {
        window.clearInterval(timer)
        timer = undefined
        message.error(String(e))
      }
    }, 1000)
  } catch (e) {
    message.warning(String(e))
  }
}

onMounted(async () => {
  try {
    overview.value = await api.overview()
    if (!domain.value && overview.value.cards.length) {
      domain.value = overview.value.cards[0].domain
    }
  } catch { /* 下拉留空也能手输 */ }
})
onBeforeUnmount(() => timer && window.clearInterval(timer))
</script>

<template>
  <n-space vertical size="large">
    <n-space align="center">
      <n-select v-model:value="domain" placeholder="选择域" style="width: 200px"
                :options="(overview?.cards ?? []).map((c) => ({ label: c.domain, value: c.domain }))" />
      <n-button type="primary" :disabled="!domain || job?.status === 'running'" @click="runDoctor">
        运行诊断
      </n-button>
    </n-space>

    <template v-if="job">
      <n-alert :type="job.status === 'ok' ? 'success' : job.status === 'running' ? 'info' : 'error'"
               :title="`doctor ${job.action} · ${job.status}`">
        <template v-if="job.result?.report">{{ job.result.report }}</template>
        <template v-else-if="job.status === 'running'">子进程运行中…</template>
        <template v-else>退出码 {{ job.exit_code ?? '?' }}</template>
      </n-alert>
      <n-card size="small" title="原始输出(JSON Lines)">
        <pre style="margin: 0; white-space: pre-wrap; font-size: 12px">{{ job.output || '(无输出)' }}</pre>
      </n-card>
    </template>
  </n-space>
</template>
