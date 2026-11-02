<script setup lang="ts">
// 汇总统计卡: 数值 + 说明,语义色由调用方给(tone 只强调数值,信息仍靠文字)。
defineProps<{
  label: string
  value: string
  sub?: string
  tone?: 'primary' | 'warning' | 'danger' | 'info'
}>()

const COLOR: Record<string, string> = {
  primary: 'var(--ask-primary)',
  warning: 'var(--ask-warning)',
  danger: 'var(--ask-danger)',
  info: 'var(--ask-info)',
}
</script>

<template>
  <n-card size="small" class="stat-card">
    <div class="label">{{ label }}</div>
    <div class="value" :style="tone ? { color: COLOR[tone] } : undefined">{{ value }}</div>
    <div v-if="sub" class="sub">{{ sub }}</div>
  </n-card>
</template>

<style scoped>
.stat-card {
  background: var(--ask-surface);
  border-radius: var(--ask-radius);
}
.label {
  font-size: 12px;
  color: var(--ask-muted);
}
.value {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.4;
}
.sub {
  font-size: 12px;
  color: var(--ask-muted);
}
</style>
