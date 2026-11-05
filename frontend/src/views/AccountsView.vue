<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NButton, NSwitch, useDialog, useMessage } from 'naive-ui'
import { accountsApi, jobApi } from '../api'
import type { AccountRow, AccountsInfo } from '../types'

// SPEC-12 账户视图: 一行一个 (domain, account)。
// 启停只改台账登记态;调度启停真相在各域 YAML accounts[].enabled(配置页维护),
// 开关旁文案如实说明,不做静默同步。

const message = useMessage()
const dialog = useDialog()
const data = ref<AccountsInfo | null>(null)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    data.value = await accountsApi.list()
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

// 单账户手动触发: 走 /api/run 后台子进程(--account),结果落台账后刷新列表
function run(row: AccountRow, action: 'auto' | 'doctor') {
  jobApi.run(row.domain, action, row.name)
    .then(({ job }) => {
      dialog.info({
        title: '已触发',
        content: `${row.domain}/${row.name} ${action} 已在后台运行(job=${job.id}),`
          + '结果写入台账后可在"签到台账"查看,本页稍后刷新可见。',
        positiveText: '知道了',
      })
      setTimeout(load, 8000)
    })
    .catch((e) => message.error(String(e)))
}

function toggle(row: AccountRow, enabled: boolean) {
  accountsApi.setEnabled(row.domain, row.name, enabled)
    .then(() => { row.enabled = enabled })
    .catch((e) => { message.error(String(e)); row.enabled = !enabled })
}

const columns = computed(() => [
  { title: '域', key: 'domain', width: 120 },
  { title: '账户', key: 'name', width: 130 },
  {
    title: '启停', key: 'enabled', width: 90,
    render: (row: AccountRow) =>
      h(NSwitch, {
        value: row.enabled,
        size: 'small',
        'onUpdate:value': (v: boolean) => toggle(row, v),
      }),
  },
  { title: '连签', key: 'streak', width: 80, render: (r: AccountRow) => `${r.streak} 天` },
  {
    title: '14 天', key: 'days14', width: 180,
    render: (row: AccountRow) =>
      h('div', { style: 'display:flex;gap:2px' }, row.days14.map((d) =>
        h('div', {
          key: d.day,
          title: d.day,
          style: `width:10px;height:10px;border-radius:2px;background:${d.ok ? 'var(--ask-success, #18a058)' : 'rgba(128,128,128,0.25)'}`,
        }))),
  },
  { title: '累计积分', key: 'credits', width: 100 },
  { title: '今日积分', key: 'today_credits', width: 100 },
  { title: '最近运行', key: 'last_run', width: 190, render: (r: AccountRow) => r.last_run ?? '-' },
  {
    title: '', key: 'op', width: 150,
    render: (row: AccountRow) => [
      h(NButton, {
        size: 'tiny', type: 'primary', quaternary: true, style: 'margin-right:6px',
        onClick: () => run(row, 'auto'),
      }, { default: () => '立即签到' }),
      h(NButton, {
        size: 'tiny', quaternary: true, onClick: () => run(row, 'doctor'),
      }, { default: () => '体检' }),
    ],
  },
])

onMounted(load)
</script>

<template>
  <n-space vertical size="large">
    <n-space align="center">
      <n-button :loading="loading" @click="load">刷新</n-button>
      <n-text depth="3" style="margin-left: auto">
        共 {{ data?.total ?? 0 }} 个账户,启用 {{ data?.active ?? 0 }} 个。
        启停开关只改登记态;调度是否执行以各域 YAML 配置的 accounts[].enabled 为准(在"域配置"页维护)。
      </n-text>
    </n-space>

    <n-data-table :columns="columns" :data="data?.accounts ?? []" :bordered="false"
                  size="small" :loading="loading"
                  :row-class-name="(r: AccountRow) => (r.enabled ? '' : 'row-disabled')"
                  style="background: var(--ask-surface)" />
  </n-space>
</template>

<style scoped>
:deep(.row-disabled td) {
  opacity: 0.55;
}
</style>
