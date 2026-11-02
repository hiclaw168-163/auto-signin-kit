<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { api } from '../api/client'
import type { ScheduleInfo, ScheduleOpResult } from '../types'

// 调度管理: 进程内调 schedule 域 service,注册/注销会真实改动系统定时任务,
// 所以按钮都挂确认气泡;部分成功如实透传(PARTIAL + 失败明细)。

const message = useMessage()
const info = ref<ScheduleInfo | null>(null)
const last = ref<ScheduleOpResult | null>(null)
const loading = ref(false)
const working = ref(false)

async function load() {
  loading.value = true
  try {
    info.value = await api.schedule()
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

async function op(kind: 'install' | 'uninstall') {
  working.value = true
  try {
    last.value = kind === 'install' ? await api.scheduleInstall() : await api.scheduleUninstall()
    if (last.value.result === 'OK') message.success(last.value.done.length + ' 个任务已就绪')
    else message.warning(last.value.result)
    await load()
  } catch (e) {
    message.error(String(e))
  } finally {
    working.value = false
  }
}

onMounted(load)
</script>

<template>
  <n-space vertical size="large">
    <n-space align="center">
      <n-button type="primary" :loading="working" @click="op('install')">全部注册</n-button>
      <n-popconfirm @positive-click="op('uninstall')">
        <template #trigger>
          <n-button :loading="working" type="warning" ghost>全部注销</n-button>
        </template>
        确认注销全部定时任务?注销后每日自动签到停止。
      </n-popconfirm>
      <n-button quaternary @click="load">刷新</n-button>
      <n-text depth="3" style="font-size: 12px">平台: {{ info?.platform ?? '-' }}</n-text>
    </n-space>

    <n-alert v-if="last && last.result !== 'OK'" type="warning" title="部分成功/失败">
      <div v-for="f in last.failed" :key="f.name">{{ f.name }}: {{ f.error }}</div>
    </n-alert>

    <n-table size="small" :single-line="false">
      <thead>
        <tr><th>任务</th><th>触发时间</th><th>状态</th></tr>
      </thead>
      <tbody>
        <tr v-for="t in info?.tasks ?? []" :key="t.name">
          <td>{{ t.name }}</td>
          <td>
            <n-tag v-for="time in t.times" :key="time" size="small" style="margin-right: 4px">
              {{ time }}
            </n-tag>
          </td>
          <td>
            <n-tag :type="t.installed ? 'success' : 'default'" size="small">
              {{ t.installed ? '已注册' : '未注册' }}
            </n-tag>
          </td>
        </tr>
      </tbody>
    </n-table>

    <n-text depth="3" style="font-size: 12px">
      触发时间在 config/domains/schedule.yaml 调整;未注册的域会由「全部注册」补齐
      (客户端未就绪的域会被如实拦下)。
    </n-text>
  </n-space>
</template>
