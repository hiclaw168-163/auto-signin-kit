<script setup lang="ts">
import { ref } from 'vue'
import Dashboard from './views/Dashboard.vue'
import HistoryView from './views/HistoryView.vue'
import ScheduleView from './views/ScheduleView.vue'
import DoctorView from './views/DoctorView.vue'

const tab = ref('dashboard')
</script>

<template>
  <n-layout style="min-height: 100vh">
    <n-layout-header bordered style="padding: 14px 24px">
      <n-h1 style="margin: 0; font-size: 20px">auto-signin-kit 管理台</n-h1>
      <n-text depth="3" style="font-size: 12px">
        本地服务 · 只绑 127.0.0.1 · 数据来源: SQLite 台账(history 域)
      </n-text>
    </n-layout-header>
    <n-layout-content content-style="padding: 16px 24px">
      <n-tabs v-model:value="tab" type="line" animated>
        <n-tab-pane name="dashboard" tab="看板" display-directive="show:lazy">
          <Dashboard />
        </n-tab-pane>
        <n-tab-pane name="history" tab="历史" display-directive="show:lazy">
          <HistoryView />
        </n-tab-pane>
        <n-tab-pane name="schedule" tab="调度" display-directive="show:lazy">
          <ScheduleView />
        </n-tab-pane>
        <n-tab-pane name="doctor" tab="诊断" display-directive="show:lazy">
          <DoctorView />
        </n-tab-pane>
      </n-tabs>
    </n-layout-content>
  </n-layout>
</template>
