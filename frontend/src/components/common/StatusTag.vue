<script setup lang="ts">
import { computed } from 'vue'

// 状态标签: 颜色不是唯一信息载体(无障碍),永远带文字。
const props = defineProps<{ result?: string | null; size?: 'small' | 'medium' }>()

const META: Record<string, { type: 'success' | 'info' | 'warning' | 'error'; label: string }> = {
  CLAIMED: { type: 'success', label: '已领取' },
  ALREADY: { type: 'info', label: '已签到' },
  ACTIVE: { type: 'info', label: '活跃' },
  NO_AUTH: { type: 'error', label: '登录过期' },
  AUTH_ERROR: { type: 'error', label: '认证失败' },
  ERROR: { type: 'error', label: '失败' },
  NETWORK: { type: 'error', label: '网络异常' },
  TIMEOUT: { type: 'error', label: '超时' },
}

const meta = computed(() =>
  props.result ? META[props.result] ?? { type: 'warning' as const, label: props.result }
    : { type: 'default' as const, label: '无记录' },
)
</script>

<template>
  <n-tag :type="meta.type" :size="size ?? 'small'" round>
    {{ meta.label }}
  </n-tag>
</template>
