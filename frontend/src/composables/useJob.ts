import { onBeforeUnmount, reactive } from 'vue'
import { jobApi } from '../api'

// 手动触发 + 轮询 job 的共用状态: 签到总览的"立即签到"与体检页的 doctor 共用。
// 每域同时只允许一个在跑(后端也拦,双保险),轮询到终态自动停并回调。

export type JobDone = (job: import('../types').JobInfo) => void

export function useJob() {
  const jobs = reactive<Record<string, import('../types').JobInfo>>({})
  const timers = new Set<number>()

  function poll(domain: string, id: string, onDone?: JobDone) {
    const timer = window.setInterval(async () => {
      try {
        const { job } = await jobApi.get(id)
        jobs[domain] = job
        if (job.status === 'running') return
        window.clearInterval(timer)
        timers.delete(timer)
        onDone?.(job)
      } catch (e) {
        window.clearInterval(timer)
        timers.delete(timer)
        onDone?.({
          id,
          domain,
          action: '',
          argv: [],
          started_at: '',
          finished_at: null,
          status: 'failed',
          exit_code: null,
          output: String(e),
          result: null,
        })
      }
    }, 1000)
    timers.add(timer)
  }

  async function run(domain: string, action: string, onDone?: JobDone) {
    const { job } = await jobApi.run(domain, action)
    jobs[domain] = job
    poll(domain, job.id, onDone)
  }

  onBeforeUnmount(() => timers.forEach((t) => window.clearInterval(t)))
  return { jobs, run }
}
