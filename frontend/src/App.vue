<script setup lang="ts">
import { darkTheme, type GlobalThemeOverrides } from 'naive-ui'
import { loadResultLabels } from './api/meta'

// 词表加载失败不挡界面: StatusTag 会退回显示原始值(如 OK),可自愈重试。
loadResultLabels().catch(() => undefined)

// 全局主题: 暗色 + 语义绿主色。token 见 styles/index.css(唯一事实源)。
const overrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#22c55e',
    primaryColorHover: '#4ade80',
    primaryColorPressed: '#16a34a',
    primaryColorSuppl: '#22c55e',
    infoColor: '#38bdf8',
    warningColor: '#f59e0b',
    errorColor: '#ef4444',
    borderRadius: '8px',
    bodyColor: '#0f1115',
    cardColor: '#171a21',
    popoverColor: '#1d222c',
    modalColor: '#1d222c',
  },
}
</script>

<template>
  <n-config-provider :theme="darkTheme" :theme-overrides="overrides">
    <n-message-provider>
      <n-dialog-provider>
        <router-view />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
