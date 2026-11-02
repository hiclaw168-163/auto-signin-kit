<script setup lang="ts">
import { computed } from 'vue'
import { resultLabel } from '../../api/meta'

// 状态标签: 文案来自后端标准词表(core.report.RESULT_LABELS),前端不自造;
// 颜色不是唯一信息载体(无障碍),永远带文字。
const props = defineProps<{ result?: string | null; size?: 'small' | 'medium' }>()

const TYPE: Record<string, 'success' | 'info' | 'warning' | 'error'> = {
  CLAIMED: 'success',
  ALREADY: 'info',
  ACTIVE: 'info',
  OK: 'success',
}

const type = computed(() => {
  const r = props.result
  if (!r) return 'default' as const
  return TYPE[r] ?? 'warning' as const
})

const label = computed(() => resultLabel(props.result))
</script>

<template>
  <n-tag :type="type" :size="size ?? 'small'" round>
    {{ label }}
  </n-tag>
</template>
