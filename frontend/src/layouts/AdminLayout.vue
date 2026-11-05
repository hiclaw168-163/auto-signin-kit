<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon, type MenuOption } from 'naive-ui'
import {
  AppsOutline,
  CalendarClearOutline,
  MedkitOutline,
  OptionsOutline,
  PersonCircleOutline,
  PieChartOutline,
  SettingsOutline,
  StatsChartOutline,
  TimeOutline,
} from '@vicons/ionicons5'
import { settings } from '../composables/settings'

// 管理台布局壳: 左侧导航 + 右侧内容。SVG 图标(禁 emoji 当图标)。
//
// 导航分组(SPEC-13): 按业务角色归三组,新功能按归属落组不再平铺——
// - 总览: 全局一眼
// - 签到运营: 与"把签到跑好/看清楚"直接相关(积分统计、Token 统计、
//   会话同步等后续功能都落这组)
// - 系统: 工具自身的安装/配置/健康
const route = useRoute()
const router = useRouter()
// 折叠初始值来自设置页;用户手动收展即时生效并回写为默认态。
const collapsed = ref(settings.siderCollapsed)

function icon(render: () => typeof NIcon) {
  return () => h(NIcon, null, { default: render })
}

const menus: MenuOption[] = [
  {
    type: 'group', label: '总览', key: 'g-overview',
    children: [
      { label: '签到总览', key: 'dashboard', icon: icon(() => h(StatsChartOutline)) },
    ],
  },
  {
    type: 'group', label: '签到运营', key: 'g-ops',
    children: [
      { label: '账户管理', key: 'accounts', icon: icon(() => h(PersonCircleOutline)) },
      { label: '积分统计', key: 'credits', icon: icon(() => h(PieChartOutline)) },
      { label: '签到台账', key: 'history', icon: icon(() => h(TimeOutline)) },
      { label: '定时任务', key: 'schedule', icon: icon(() => h(CalendarClearOutline)) },
    ],
  },
  {
    type: 'group', label: '系统', key: 'g-system',
    children: [
      { label: '应用管理', key: 'apps', icon: icon(() => h(AppsOutline)) },
      { label: '域配置', key: 'config', icon: icon(() => h(SettingsOutline)) },
      { label: '体检', key: 'doctor', icon: icon(() => h(MedkitOutline)) },
      { label: '设置', key: 'settings', icon: icon(() => h(OptionsOutline)) },
    ],
  },
]

const active = computed(() => String(route.name ?? 'dashboard'))
const title = computed(() => String(route.meta.title ?? ''))

// 菜单点击 → 路由跳转必须显式接 @update:value: n-menu 不会自己改路由,
// 而上面的 active 是从路由反推的——不接这个,除默认页外全都点不动。
function onMenu(key: string) {
  router.push({ name: key })
}

function onCollapse(value: boolean) {
  collapsed.value = value
  settings.siderCollapsed = value
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
      @collapse="onCollapse(true)"
      @expand="onCollapse(false)"
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
}
/* 内容区留白: 不留白时表格直接贴着侧栏/窗口边(既无边界又遮数据) */
.page-body {
  padding: 8px 28px 28px;
}
</style>
