<script setup lang="ts">
import { computed, watch } from 'vue'
import { darkTheme, dateZhCN, zhCN, type GlobalThemeOverrides } from 'naive-ui'
import { loadResultLabels } from './api/meta'
import { effectiveDark } from './composables/settings'

// 词表加载失败不挡界面: StatusTag 会退回显示原始值(如 OK),可自愈重试。
loadResultLabels().catch(() => undefined)

// 语义色三件套两主题共用;亮色下 CSS token(styles/index.css)负责底色与文字。
const baseOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#22c55e',
    primaryColorHover: '#4ade80',
    primaryColorPressed: '#16a34a',
    primaryColorSuppl: '#22c55e',
    infoColor: '#38bdf8',
    warningColor: '#f59e0b',
    errorColor: '#ef4444',
    borderRadius: '8px',
  },
}

// 暗色专属底色(亮色交给 naive 默认浅底 + CSS token);色板 = colors.csv #81。
const darkOverrides: GlobalThemeOverrides = {
  common: {
    ...baseOverrides.common,
    bodyColor: '#0f172a',
    cardColor: '#1b2336',
    popoverColor: '#272f42',
    modalColor: '#272f42',
  },
}

const isDark = computed(effectiveDark)

// theme=null 即 naive 亮色主题;CSS 侧同步切 data-theme(组件里的
// var(--ask-*) 跟着换套),'light' 属性显式标记避免回落到 :root 暗色。
watch(isDark, (dark) => {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}, { immediate: true })
</script>

<template>
  <n-config-provider :theme="isDark ? darkTheme : null"
                     :theme-overrides="isDark ? darkOverrides : baseOverrides"
                     :locale="zhCN" :date-locale="dateZhCN">
    <n-message-provider>
      <n-dialog-provider>
        <router-view />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
