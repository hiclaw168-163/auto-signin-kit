<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { NCard, NText, type DataTableColumns } from 'naive-ui'
import { useMessage } from 'naive-ui'
import { creditsApi } from '../api'
import { settings } from '../composables/settings'
import type { CreditsStats, DomainBalance } from '../types'

// 积分统计页(SPEC-13): 数据全部来自本地 SQLite 台账(ask_daily_credits 视图
// + 成功行 response 的余额快照),不调官方接口——参考 workbuddy-switch 的
// "官方数据不可用回退本地快照"口径,我们直接以本地事实为准。

interface DomainRow {
  domain: string
  total: number
  window_total: number
  last_day?: string | null
  balance: DomainBalance | undefined
}

const message = useMessage()
const data = ref<CreditsStats | null>(null)
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    data.value = await creditsApi.stats(settings.historyDays)
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

onMounted(load)

const maxTrend = computed(() =>
  Math.max(1, ...(data.value?.trend ?? []).map((p) => p.credits)))

const domainRows = computed<DomainRow[]>(() => {
  const balances = data.value?.balances ?? {}
  return (data.value?.by_domain ?? []).map((r) => ({
    ...r, balance: balances[r.domain],
  }))
})

const domainColumns: DataTableColumns<DomainRow> = [
  { title: '域', key: 'domain', width: 140 },
  {
    title: '累计所得快照', key: 'balance', width: 140,
    render: (row) => h(NText, { strong: true },
      { default: () => row.balance?.total_credits != null
          ? row.balance.total_credits.toLocaleString() : '—' }),
  },
  {
    title: '窗口内所得', key: 'window_total', width: 140,
    render: (row) => h(NText, null, { default: () => row.window_total.toLocaleString() }),
  },
  {
    title: '累计所得', key: 'total', width: 140,
    render: (row) => h(NText, { depth: 2 }, { default: () => row.total.toLocaleString() }),
  },
  {
    title: '最近所得日', key: 'last_day', width: 130,
    render: (row) => h(NText, { depth: 3 }, { default: () => row.last_day ?? '—' }),
  },
]

function barHeight(credits: number): string {
  return `${Math.round((credits / maxTrend.value) * 100)}%`
}
</script>

<template>
  <n-space vertical size="large">
    <n-space align="center">
      <n-button :loading="loading" @click="load">刷新</n-button>
      <n-text depth="3" style="margin-left: auto">
        统计口径: 本地 SQLite 台账成功行,窗口近 {{ data?.window_days ?? settings.historyDays }} 天
      </n-text>
    </n-space>

    <!-- 当前余额(workbuddy 回传 total_credits;其他域暂无余额来源,如实显示 —) -->
    <n-space :size="12" v-if="domainRows.length">
      <n-card v-for="row in domainRows" :key="row.domain" size="small"
              style="width: 235px; background: var(--ask-surface)">
        <n-statistic :label="`${row.domain} · 累计所得快照`">
          <span class="balance-num">
            {{ row.balance?.total_credits != null ? row.balance.total_credits.toLocaleString() : '—' }}
          </span>
          <n-text depth="3" style="font-size: 11px; display: block; margin-top: 4px">
            {{ row.balance?.updated_at
              ? `更新于 ${row.balance.updated_at}`
              : `暂无累计快照 · 累计所得 ${row.total.toLocaleString()}` }}
          </n-text>
        </n-statistic>
      </n-card>
    </n-space>

    <!-- 每日所得趋势(纯 CSS 柱状,不引图表库) -->
    <n-card title="每日所得趋势" size="small" style="background: var(--ask-surface)">
      <div class="trend" v-if="(data?.trend ?? []).length">
        <div v-for="p in data!.trend" :key="p.day" class="trend-col"
             :title="`${p.day}: ${p.credits}`">
          <div class="trend-bar" :style="{ height: barHeight(p.credits) }" />
          <div class="trend-day">{{ p.day.slice(5) }}</div>
        </div>
      </div>
      <n-empty v-else description="窗口内暂无积分所得" size="small" />
    </n-card>

    <!-- 按域汇总 -->
    <n-card title="按域汇总" size="small" style="background: var(--ask-surface)">
      <n-data-table :columns="domainColumns" :data="domainRows" :bordered="false" size="small" />
      <n-alert type="info" :bordered="false" size="small" style="margin-top: 12px">
        口径说明: 本页统计的是**所得流水**(每次签到/领奖实际到账的数值,累计而得);
        各域官网/客户端显示的"积分余额"是**可用余额**——会随消耗与过期减少
        (如 qoder 积分领取后 30 天有效),两者本来就不相等。workbuddy 的
        "累计所得快照"来自其签到状态接口的 total_credits;doubao 与
        qoder/trae 暂无余额来源,如实显示"—"。
      </n-alert>
    </n-card>
  </n-space>
</template>

<style scoped>
.balance-num {
  font-size: 22px;
  font-weight: 700;
}
.trend {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 140px;
  padding-top: 8px;
}
.trend-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  align-items: center;
  height: 100%;
  min-width: 0;
}
.trend-bar {
  width: 100%;
  max-width: 26px;
  background: var(--ask-primary);
  opacity: 0.85;
  border-radius: 3px 3px 0 0;
  min-height: 2px;
  transition: height 0.2s;
}
.trend-day {
  font-size: 9px;
  color: var(--ask-muted);
  margin-top: 4px;
  white-space: nowrap;
  overflow: hidden;
  max-width: 100%;
}
</style>
