<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon, type MenuOption } from 'naive-ui'
import {
  AppsOutline,
  CalendarClearOutline,
  MedkitOutline,
  PersonCircleOutline,
  SettingsOutline,
  StatsChartOutline,
  TimeOutline,
} from '@vicons/ionicons5'

// 管理台布局壳: 左侧暗色导航 + 右侧内容。SVG 图标(禁 emoji 当图标)。
const route = useRoute()
const router = useRouter()
const collapsed = ref(false)

function icon(render: () => typeof NIcon) {
  return () => h(NIcon, null, { default: render })
}

const menus: MenuOption[] = [
  { label: '签到总览', key: 'dashboard', icon: icon(() => h(StatsChartOutline)) },
  { label: '账户管理', key: 'accounts', icon: icon(() => h(PersonCircleOutline)) },
  { label: '应用管理', key: 'apps', icon: icon(() => h(AppsOutline)) },
  { label: '签到台账', key: 'history', icon: icon(() => h(TimeOutline)) },
  { label: '定时任务', key: 'schedule', icon: icon(() => h(CalendarClearOutline)) },
  { label: '域配置', key: 'config', icon: icon(() => h(SettingsOutline)) },
  { label: '体检', key: 'doctor', icon: icon(() => h(MedkitOutline)) },
]

const active = computed(() => String(route.name ?? 'dashboard'))
const title = computed(() => String(route.meta.title ?? ''))

// 菜单点击 → 路由跳转必须显式接 @update:value: n-menu 不会自己改路由,
// 而上面的 active 是从路由反推的——不接这个,除默认页外全都点不动。
function onMenu(key: string) {
  router.push({ name: key })
}
</script>

<template>
  <n-layout has-sider position="absolute">
    <n-layout-sider
      bordered
      collapse-mode="width"
      :collapsed="collapsed"
      :collapsed-width="64"
      :width="210"
      show-trigger
      @collapse="collapsed = true"
      @expand="collapsed = false"
      :native-scrollbar="false"
      style="background: var(--ask-surface)"
    >
      <div class="brand">
        <div class="logo">ASK</div>
        <span v-if="!collapsed" class="brand-name">签到管理台</span>
      </div>
      <n-menu :value="active" :options="menus" :collapsed="collapsed" :collapsed-width="64"
              :collapsed-icon-size="20" style="background: transparent"
              @update:value="onMenu" />
      <div class="sider-footer" v-if="!collapsed">
        <n-text depth="3" style="font-size: 11px">只绑 127.0.0.1<br />数据来源: SQLite 台账</n-text>
      </div>
    </n-layout-sider>
    <n-layout :native-scrollbar="false">
      <header class="page-header">
        <h1 class="page-title">{{ title }}</h1>
      </header>
      <main class="page-body">
        <router-view />
      </main>
    </n-layout>
  </n-layout>
</template>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 20px 12px;
}
.logo {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: var(--ask-primary);
  color: #06210f;
  font-weight: 800;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  letter-spacing: 0.5px;
}
.brand-name {
  font-size: 15px;
  font-weight: 600;
}
.sider-footer {
  position: absolute;
  bottom: 12px;
  left: 20px;
  line-height: 1.6;
}
.page-header {
  padding: 18px 28px 0;
}
.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}
.page-body {
  padding: 16px 28px 28px;
}
</style>
