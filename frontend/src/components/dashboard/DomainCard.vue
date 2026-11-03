<script setup lang="ts">
import type { JobInfo, OverviewCard } from '../../types'
import StatusTag from '../common/StatusTag.vue'

// 域卡片: 状态徽标 + 摘要 + chips(连签/积分) + 14 天色块条 + 一键签到。
// 对齐参考项目的卡片信息密度;所有数字来自台账(core/store.overview_stats)。
const props = defineProps<{
  card: OverviewCard
  job?: JobInfo
  running?: boolean
}>()

const emit = defineEmits<{ run: [] }>()

const OK = ['CLAIMED', 'ALREADY', 'ACTIVE']
const signedToday = !!card_today_ok(props.card)

function card_today_ok(c: OverviewCard): boolean {
  return !!c.today && OK.includes(c.today.result)
}

function dayTitle(d: { day: string; ok: boolean }): string {
  return `${d.day}: ${d.ok ? '成功' : '无成功记录'}`
}

function lastRunText(last: string | null | undefined): string {
  if (!last) return '未运行'
  const diff = Date.now() - new Date(last.replace(' ', 'T')).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}
</script>

<template>
  <n-card size="small" class="domain-card">
    <template #header>
      <span class="name">{{ card.domain }}</span>
    </template>
    <template #header-extra>
      <StatusTag :result="card.today?.result" />
    </template>
    <n-space vertical size="small">
      <div class="report" :class="{ attention: card.today?.needs_attention }">
        {{ card.today?.report ?? '今天还没有运行记录(调度任务触发后会出现在这里)' }}
      </div>
      <div v-if="card.streak || card.credits" class="chips">
        <span v-if="card.streak" class="chip">连续 {{ card.streak }} 天</span>
        <span v-if="card.credits" class="chip">累计 {{ card.credits }} credits</span>
        <span v-if="card.today_credits" class="chip accent">今日 +{{ card.today_credits }}</span>
      </div>
      <div class="dots" role="img" aria-label="近 14 天签到记录">
        <span v-for="d in card.days14 ?? []" :key="d.day" class="dot"
              :class="d.ok ? 'ok' : 'miss'" :title="dayTitle(d)" />
        <span class="dots-label">近 14 天 · 上次 {{ lastRunText(card.last_run) }}</span>
      </div>
      <div class="meta">
        近 7 天 {{ card.week_ok }}/{{ card.week_total }} 成功
        <template v-if="job"> · job {{ job.status }}
          <template v-if="job.result?.report">· {{ job.result.report }}</template>
        </template>
      </div>
      <n-button size="small" type="primary" :loading="running"
                :disabled="running" @click="emit('run')">
        {{ signedToday ? '再跑一次(幂等)' : '立即签到' }}
      </n-button>
    </n-space>
  </n-card>
</template>

<style scoped>
.domain-card {
  background: var(--ask-surface);
  border-radius: var(--ask-radius);
}
.name {
  font-family: var(--ask-font);
  font-weight: 600;
}
.report {
  min-height: 20px;
  line-height: 1.6;
  word-break: break-word;
}
.report.attention {
  color: var(--ask-danger);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  background: color-mix(in srgb, var(--ask-primary) 12%, transparent);
  color: var(--ask-primary);
  font-weight: 600;
}
.chip.accent {
  background: color-mix(in srgb, var(--ask-danger) 10%, transparent);
  color: var(--ask-danger);
}
.dots {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.dot {
  width: 12px;
  height: 12px;
  border-radius: 4px;
}
.dot.ok {
  background: var(--ask-primary);
}
.dot.miss {
  background: var(--ask-border);
}
.dots-label {
  margin-left: 6px;
  font-size: 11px;
  color: var(--ask-muted);
}
.meta {
  font-size: 12px;
  color: var(--ask-muted);
}
</style>
