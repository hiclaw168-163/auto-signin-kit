<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { appsApi, scheduleApi } from '../api'
import type { ScheduleInfo, ScheduleOpResult, ScheduleTask } from '../types'

// 调度管理: 进程内调 schedule 域 service,注册/注销会真实改动系统定时任务,
// 所以注销挂确认;部分成功(PARTIAL)如实透传失败明细。
// 触发时间编辑: 写 schedule.yaml 的 TaskOverride.times(PUT /apps/schedule
// 深合并),保存后自动重新注册(幂等覆盖)让新时间立刻生效。

const message = useMessage()
const info = ref<ScheduleInfo | null>(null)
const last = ref<ScheduleOpResult | null>(null)
const loading = ref(false)
const working = ref(false)

async function load() {
  loading.value = true
  try {
    info.value = await scheduleApi.status()
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

async function op(kind: 'install' | 'uninstall') {
  working.value = true
  try {
    last.value = kind === 'install' ? await scheduleApi.install() : await scheduleApi.uninstall()
    if (last.value.result === 'OK') message.success(`${last.value.done.length} 个任务已就绪`)
    else message.warning(last.value.result)
    await load()
  } catch (e) {
    message.error(String(e))
  } finally {
    working.value = false
  }
}

// ---------------- 触发时间编辑 ----------------

const editing = ref(false)
const editTask = ref('')
const editTimes = ref<string[]>([])
const savingEdit = ref(false)

function openEdit(task: ScheduleTask) {
  editTask.value = task.name
  editTimes.value = [...task.times]
  editing.value = true
}

function addTime() {
  editTimes.value.push('09:00')
}

function removeTime(i: number) {
  editTimes.value.splice(i, 1)
}

async function saveEdit() {
  if (!editTimes.value.length) {
    message.error('至少保留一个触发时间(多时间点是断签兜底,不要清空)')
    return
  }
  savingEdit.value = true
  try {
    const times = [...editTimes.value].sort()
    await appsApi.update('schedule', { tasks: { [editTask.value]: { times } } })
    // 注册是覆盖式幂等: 保存后立即重注册,新时间不用等下次手动注册
    const out = await scheduleApi.install()
    if (out.result === 'OK') message.success('时间已保存并重新注册生效')
    else message.warning(`时间已保存,但注册部分失败: ${out.failed.map((f) => `${f.name}: ${f.error}`).join('; ')}`)
    editing.value = false
    await load()
  } catch (e) {
    message.error(String(e))
  } finally {
    savingEdit.value = false
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

    <n-table size="small" :single-line="false" style="background: var(--ask-surface)">
      <thead>
        <tr><th>任务</th><th>触发时间</th><th>状态</th><th>操作</th></tr>
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
          <td>
            <n-button size="tiny" type="primary" ghost @click="openEdit(t)">编辑时间</n-button>
          </td>
        </tr>
      </tbody>
    </n-table>

    <n-text depth="3" style="font-size: 12px">
      触发时间可每行直接编辑(保存后自动重新注册生效);多时间点是断签兜底,
      不要清空。未注册的域由「全部注册」补齐(客户端未就绪的域会被如实拦下,
      不会悄悄注册个跑不起来的任务)。
    </n-text>

    <n-modal v-model:show="editing" preset="card" :title="`编辑触发时间: ${editTask}`"
             style="max-width: 480px">
      <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
        一天多个触发时间是断签兜底(撞上关机/断网还有后续机会),保存后立即重新注册。
      </n-alert>
      <n-space vertical size="small">
        <n-space v-for="(_, i) in editTimes" :key="i" align="center" size="small">
          <n-time-picker v-model:formatted-value="editTimes[i]" format="HH:mm"
                         value-format="HH:mm" style="width: 120px" />
          <n-button size="tiny" quaternary type="error" :disabled="editTimes.length <= 1"
                    @click="removeTime(i)">删除</n-button>
        </n-space>
        <n-button size="small" dashed @click="addTime">+ 加一个触发时间</n-button>
      </n-space>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editing = false">取消</n-button>
          <n-button type="primary" :loading="savingEdit" @click="saveEdit">
            保存并重新注册
          </n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>
