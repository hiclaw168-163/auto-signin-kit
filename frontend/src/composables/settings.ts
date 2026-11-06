/**
 * 管理台本地偏好(SPEC-13 设置页): 存 localStorage,纯前端偏好不动后端。
 *
 * 单例 reactive: 各视图直接 import 使用;watch 自动持久化,谁改了立刻生效。
 * 主题三态: system 跟随系统(prefers-color-scheme,实时监听)/ light / dark;
 * 落地是两件事——naive-ui 的 n-config-provider theme(见 App.vue)+ CSS token
 * 切换(documentElement.dataset.theme,见 styles/index.css 双套 --ask-* 变量)。
 */
import { reactive, ref, watch } from 'vue'

export type ThemeMode = 'system' | 'light' | 'dark'

export interface UiSettings {
  /** 主题模式;system = 跟随系统外观 */
  theme: ThemeMode
  /** 侧边栏默认折叠(仅决定初始态;用户手动收展即时生效并回写) */
  siderCollapsed: boolean
  /** 签到台账/总览默认查询窗口(天) */
  historyDays: number
}

const STORAGE_KEY = 'ask.settings.v1'

const DEFAULTS: UiSettings = {
  theme: 'system',
  siderCollapsed: false,
  historyDays: 14,
}

function load(): UiSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULTS }
    const parsed = JSON.parse(raw) as Partial<UiSettings>
    return {
      theme: isThemeMode(parsed.theme) ? parsed.theme : DEFAULTS.theme,
      siderCollapsed: typeof parsed.siderCollapsed === 'boolean'
        ? parsed.siderCollapsed : DEFAULTS.siderCollapsed,
      historyDays: typeof parsed.historyDays === 'number'
        ? parsed.historyDays : DEFAULTS.historyDays,
    }
  } catch {
    return { ...DEFAULTS } // localStorage 被禁/损坏: 回默认值,设置页可再改
  }
}

function isThemeMode(v: unknown): v is ThemeMode {
  return v === 'system' || v === 'light' || v === 'dark'
}

export const settings = reactive<UiSettings>(load())

watch(settings, (value) => {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch { /* 持久化失败只影响下次打开的默认值,不挡使用 */ }
}, { deep: true })

/** 系统当前是否深色(仅在 theme === 'system' 时有意义);响应式 ref,
 *  系统切换外观时自动更新——effectiveDark 的 computed 因此实时跟随,
 *  不需要调用方手动 watch 回写(旧写法对只读 computed 赋值,类型即报错)。 */
const MQL = matchMedia('(prefers-color-scheme: dark)')
export const prefersDark = ref(MQL.matches)
MQL.addEventListener('change', (e) => { prefersDark.value = e.matches })

/** 实际生效的主题:'system' 时跟随系统并随系统切换实时更新。 */
export function effectiveDark(): boolean {
  return settings.theme === 'dark' || (settings.theme === 'system' && prefersDark.value)
}
