<script setup lang="ts">
import { computed } from 'vue'
import { useMessage } from 'naive-ui'
import { effectiveDark, settings, type ThemeMode } from '../composables/settings'

// 设置页(SPEC-13): 只放**前端本地偏好**(localStorage,存浏览器不进后端)。
// 主题是首个落地项;后续按系统可配置范围继续往各分组卡里加行。

const message = useMessage()

const themeOptions: { label: string; value: ThemeMode; hint: string }[] = [
  { label: '跟随系统', value: 'system', hint: '跟随系统外观自动切换' },
  { label: '浅色', value: 'light', hint: '固定浅色' },
  { label: '深色', value: 'dark', hint: '固定深色' },
]

const currentTheme = computed({
  get: () => settings.theme,
  set: (value: ThemeMode) => { settings.theme = value },
})

const isDark = computed(effectiveDark)

const dayOptions = [7, 14, 30, 90].map((d) => ({ label: `近 ${d} 天`, value: d }))

function onToggleSider(value: boolean) {
  settings.siderCollapsed = value
  message.info(value ? '已设为默认折叠,刷新后生效' : '已设为默认展开,刷新后生效')
}
</script>

<template>
  <n-space vertical size="large">
    <n-card title="外观" size="small" style="background: var(--ask-surface)">
      <n-space vertical size="large">
        <n-space justify="space-between" align="center">
          <div>
            <div class="row-title">主题</div>
            <div class="row-hint">选择浅色、深色,或跟随系统外观自动切换</div>
          </div>
          <n-radio-group v-model:value="currentTheme" size="small">
            <n-radio-button v-for="opt in themeOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </n-radio-button>
          </n-radio-group>
        </n-space>
        <n-alert :type="isDark ? 'warning' : 'info'" :bordered="false" size="small">
          当前生效:{{ isDark ? '深色' : '浅色' }}
          <template v-if="settings.theme === 'system'">(跟随系统,系统切换时实时变化)</template>
        </n-alert>
      </n-space>
    </n-card>

    <n-card title="通用" size="small" style="background: var(--ask-surface)">
      <n-space vertical size="large">
        <n-space justify="space-between" align="center">
          <div>
            <div class="row-title">侧边栏默认折叠</div>
            <div class="row-hint">开启后打开管理台侧边栏默认收起;手动收展仍随时可用</div>
          </div>
          <n-switch size="small" :value="settings.siderCollapsed" @update:value="onToggleSider" />
        </n-space>
        <n-space justify="space-between" align="center">
          <div>
            <div class="row-title">台账默认查询窗口</div>
            <div class="row-hint">签到台账与总览色块的默认时间范围</div>
          </div>
          <n-select v-model:value="settings.historyDays" size="small" :options="dayOptions"
                    style="width: 130px" />
        </n-space>
      </n-space>
    </n-card>

    <n-card title="存储说明" size="small" style="background: var(--ask-surface)">
      <n-text depth="3" style="font-size: 12.5px; line-height: 1.8">
        这些偏好保存在浏览器本地(localStorage,键 ask.settings.v1),只影响这台机器上的
        管理台界面,不同步到后端;清浏览器数据会回到默认。签到、账户、任务等业务数据
        仍在 SQLite 台账,与本页设置无关。
      </n-text>
    </n-card>
  </n-space>
</template>

<style scoped>
.row-title {
  font-size: 14px;
  font-weight: 600;
}
.row-hint {
  font-size: 12px;
  color: var(--ask-muted);
  margin-top: 2px;
}
</style>
