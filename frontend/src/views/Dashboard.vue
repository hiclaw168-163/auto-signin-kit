<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { api } from '../api/client'
import type { JobInfo, Overview, OverviewCard } from '../types'

// 看板: 每个签到域一张卡(今日结果 + 近 7 天占比),一键手动签到并轮询 job。

const message = useMessage()
const overview = ref<Overview | null>(null)
const loading = ref(false)
const jobs = reactive<Record<string, JobInfo>>({})
const timers = new Set<number>()

function tagType(result?: string): 'success' | 'info' | 'warning' | 'error' {
  if (result === 'CLAIMED') return 'success'
  if (result === 'ALREADY' || result === 'ACTIVE') return 'info'
  if (!result) return 'default' as never
  return 'error'
}

async function load() {
  loading.value = true
  try {
    overview.value = await api.overview()
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

function poll(domain: string, id: string) {
  const timer = window.setInterval(async () => {
    try {
      const { job } = await api.job(id)
      jobs[domain] = job
      if (job.status === 'running') return
      window.clearInterval(timer)
      timers.delete(timer)
      if (job.status === 'ok') {
        const report = (job.result?.report as string) ?? '完成'
        message.success(`${domain}: ${report}`)
      } else {
        message.error(`${domain}: ${job.status}(exit=${job.exit_code ?? '?'})`)
      }
      await load()
    } catch (e) {
      window.clearInterval(timer)
      timers.delete(timer)
      message.error(String(e))
    }
  }, 1000)
  timers.add(timer)
}

async function run(card: OverviewCard) {
  try {
    const { job } = await api.run(card.domain, 'auto')
    jobs[card.domain] = job
    poll(card.domain, job.id)
  } catch (e) {
    // 409 = 同域已在跑,是提醒不是故障
    message.warning(String(e))
  }
}

onMounted(load)
onBeforeUnmount(() => timers.forEach((t) => window.clearInterval(t)))
</script>

<template>
  <n-spin :show="loading">
    <n-grid :cols="2" :x-gap="12" :y-gap="12">
      <n-gi v-for="card in overview?.cards ?? []" :key="card.domain">
        <n-card :title="card.domain" size="small">
          <template #header-extra>
            <n-tag :type="tagType(card.today?.result)" size="small">
              {{ card.today ? card.today.result : '今日无记录' }}
            </n-tag>
          </template>
          <n-space vertical size="small">
            <n-text :type="card.today?.needs_attention ? 'error' : 'default'">
              {{ card.today?.report ?? '今天还没有运行记录(调度任务触发后会出现在这里)' }}
            </n-text>
            <n-text depth="3" style="font-size: 12px">
              近 7 天: {{ card.week_ok }}/{{ card.week_total }} 成功
            </n-text>
            <n-text depth="3" style="font-size: 12px" v-if="jobs[card.domain]">
              job {{ jobs[card.domain].status }}
              <template v-if="jobs[card.domain].result?.report">
                · {{ jobs[card.domain].result?.report }}
              </template>
            </n-text>
            <n-button size="small" type="primary" :disabled="jobs[card.domain]?.status === 'running'"
                      @click="run(card)">
              立即签到
            </n-button>
          </n-space>
        </n-card>
      </n-gi>
    </n-grid>
  </n-spin>
</template>
