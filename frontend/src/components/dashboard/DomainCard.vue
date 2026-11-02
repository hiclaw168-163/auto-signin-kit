<script setup lang="ts">
import type { DailyRow, JobInfo, OverviewCard } from '../../types'
import StatusTag from '../common/StatusTag.vue'

// 域卡片: 今日状态 + 摘要 + 近 7 天条形(绿=成功/红=失败/灰=无记录) + 一键签到。
// 长文本自然换行不裁切(ui-ux-pro-max 文字韧性要求)。
const props = defineProps<{
  card: OverviewCard
  week: DailyRow[]
  job?: JobInfo
  running?: boolean
}>()

const emit = defineEmits<{ run: [] }>()

function barType(row: DailyRow | undefined): 'ok' | 'fail' | 'miss' {
  if (!row) return 'miss'
  return ['CLAIMED', 'ALREADY', 'ACTIVE'].includes(row.result) ? 'ok' : 'fail'
}

const COLOR: Record<string, string> = {
  ok: 'var(--ask-primary)',
  fail: 'var(--ask-danger)',
  miss: 'var(--ask-border)',
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
      <div class="bars" aria-hidden="false">
        <div v-for="i in 7" :key="i" class="bar"
             :style="{ background: COLOR[barType(week[i - 1])] }"
             :title="week[i - 1] ? `${week[i - 1].day}: ${week[i - 1].result}` : `${i} 天前: 无记录`" />
        <span class="bars-label">近 7 天</span>
      </div>
      <div class="meta">
        近 7 天 {{ card.week_ok }}/{{ card.week_total }} 成功
        <template v-if="job"> · job {{ job.status }}
          <template v-if="job.result?.report">· {{ job.result.report }}</template>
        </template>
      </div>
      <n-button size="small" type="primary" :loading="running" @click="emit('run')">
        立即签到
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
.bars {
  display: flex;
  align-items: flex-end;
  gap: 4px;
}
.bar {
  width: 16px;
  height: 10px;
  border-radius: 3px;
}
.bars-label {
  margin-left: 6px;
  font-size: 11px;
  color: var(--ask-muted);
}
.meta {
  font-size: 12px;
  color: var(--ask-muted);
}
</style>
