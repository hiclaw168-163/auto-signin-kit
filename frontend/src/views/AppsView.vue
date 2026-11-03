<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { appsApi } from '../api'
import type { AppInfo } from '../types'

// 应用管理(SPEC-11): 新建触发脚手架生成+后台重建(服务会短暂离线并自动回来,
// 前端轮询恢复);删除是终局操作,输入域名二次确认。

const router = useRouter()
const message = useMessage()
const apps = ref<AppInfo[]>([])
const loading = ref(false)
const working = ref(false)
const rebuilding = ref(false)

const creating = ref(false)
const form = reactive({
  name: '',
  kind: 'web',
  display: '',
  base_url: '',
  daily_time: '09:05',
})

async function load() {
  loading.value = true
  try {
    apps.value = (await appsApi.list()).apps
  } catch (e) {
    message.error(String(e))
  } finally {
    loading.value = false
  }
}

function kindTag(kind: string | null): { label: string; type: 'success' | 'info' | 'default' } {
  if (kind === 'web') return { label: '网页版签', type: 'info' }
  if (kind === 'client') return { label: '客户端签到', type: 'success' }
  return { label: '未分类', type: 'default' }
}

// 配置摘要: 标量字段拼成 key=value;复杂字段只报个数,完整编辑走弹窗
function configSummary(config: Record<string, unknown> | null): string[] {
  if (!config) return []
  return Object.entries(config)
    .filter(([, v]) => v !== null && typeof v !== 'object')
    .map(([k, v]) => `${k}=${v}`)
}

function complexCount(config: Record<string, unknown> | null): number {
  if (!config) return 0
  return Object.values(config).filter((v) => v !== null && typeof v === 'object').length
}

async function create() {
  if (!/^[a-z][a-z0-9_]*$/.test(form.name)) {
    message.error('域名须为小写字母开头的小写字母/数字/下划线')
    return
  }
  working.value = true
  try {
    const out = await appsApi.create({ ...form, display: form.display || undefined })
    message.info(out.message)
    creating.value = false
    await pollUntilBack()
  } catch (e) {
    message.error(String(e))
  } finally {
    working.value = false
  }
}

async function remove(app: AppInfo) {
  working.value = true
  try {
    const out = await appsApi.remove(app.name)
    message.info(out.message)
    await pollUntilBack()
  } catch (e) {
    message.error(String(e))
  } finally {
    working.value = false
  }
}

// 客户端档案同步: 逐域采集版本/设备标识/登录身份,写入账户维表(即时,无重建)
const syncingInfo = ref(false)

async function syncClientInfo() {
  syncingInfo.value = true
  try {
    const out = await appsApi.syncClientInfo()
    if (out.result === 'OK') {
      message.success(`${out.done.length} 个域的客户端档案已更新`)
    } else {
      message.warning(`部分失败: ${out.failed.map((f) => `${f.domain}(${f.error})`).join('; ')}`)
    }
    await load()
  } catch (e) {
    message.error(String(e))
  } finally {
    syncingInfo.value = false
  }
}

// 重建期间服务离线: 轮询 /api/apps 直到回来,再刷新列表
async function pollUntilBack() {
  rebuilding.value = true
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 3000))
    try {
      apps.value = (await appsApi.list()).apps
      rebuilding.value = false
      message.success('服务已恢复')
      return
    } catch {
      /* 还没回来,继续等 */
    }
  }
  rebuilding.value = false
  message.warning('等待超时,请稍后手动刷新;重建日志见 logs/scaffold-rebuild.log')
}

// ---------------- 配置编辑(表单化部分更新,不触发重建) ----------------

interface FieldRow {
  key: string
  value: string | number | boolean | null
  original: unknown
}

const editing = ref(false)
const editName = ref('')
const editLoading = ref(false)
const savingEdit = ref(false)
const editFields = ref<FieldRow[]>([])

function isScalar(v: unknown): boolean {
  return v === null || ['string', 'number', 'boolean'].includes(typeof v)
}

async function openEdit(app: AppInfo) {
  editName.value = app.name
  editLoading.value = true
  editing.value = true
  try {
    const out = await appsApi.getConfig(app.name)
    editFields.value = Object.entries(out.fields).map(([key, value]) => ({
      key,
      value: (isScalar(value) ? value : null) as FieldRow['value'],
      original: value,
    }))
  } catch (e) {
    message.error(String(e))
    editing.value = false
  } finally {
    editLoading.value = false
  }
}

async function saveEdit() {
  const updates: Record<string, unknown> = {}
  for (const f of editFields.value) {
    if (!isScalar(f.original) || f.value === f.original) continue
    // 字符串字段的留空: 原值是 null 时保持 null,否则按空串提交
    updates[f.key] = f.value === '' && f.original === null ? null : f.value
  }
  if (!Object.keys(updates).length) {
    message.info('没有修改')
    return
  }
  savingEdit.value = true
  try {
    const out = await appsApi.update(editName.value, updates)
    message.success(out.message)
    editing.value = false
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
      <n-button type="primary" @click="creating = true">新建应用</n-button>
      <n-button quaternary :loading="syncingInfo" @click="syncClientInfo">
        同步客户端档案
      </n-button>
      <n-button quaternary :loading="loading" @click="load">刷新</n-button>
      <n-text v-if="rebuilding" type="warning" depth="1">
        正在重建生效(服务短暂离线)...
      </n-text>
    </n-space>

    <n-alert type="info" :show-icon="false">
      新建应用会生成标准域骨架(引用 core 内核)并自动注册生效;生成的签到逻辑是桩,
      请到 <code>src/auto_signin_kit/domains/&lt;域名&gt;/service.py</code> 填写签到逻辑,
      并处理对应契约测试的 xfail 标记。调度注册在
      <n-button text type="primary" @click="router.push({ name: 'schedule' })">定时任务</n-button>
      页操作。
    </n-alert>

    <n-table size="small" :single-line="false" style="background: var(--ask-surface)">
      <thead>
        <tr><th>应用</th><th>类别</th><th>就绪探测</th><th>每日时间</th><th>配置</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="a in apps" :key="a.name">
          <td>
            <span style="font-weight: 600">{{ a.display }}</span>
            <n-text depth="3" style="margin-left: 8px; font-size: 12px">{{ a.name }}</n-text>
          </td>
          <td>
            <n-tag :type="kindTag(a.kind).type" size="small">{{ kindTag(a.kind).label }}</n-tag>
          </td>
          <td>
            <n-tag :type="a.ready === false ? 'warning' : a.ready ? 'success' : 'default'" size="small">
              {{ a.ready === false ? '客户端未就绪' : a.ready ? '就绪' : '未声明' }}
            </n-tag>
          </td>
          <td>
            <n-text v-if="a.times.length" style="font-size: 12px">{{ a.times.join(' / ') }}</n-text>
            <n-text v-else depth="3" style="font-size: 12px">未调度</n-text>
          </td>
          <td style="max-width: 320px">
            <template v-if="a.config">
              <n-space size="small" :wrap="true">
                <n-tag v-for="item in configSummary(a.config)" :key="item" size="small"
                       :bordered="false" style="font-size: 11px">{{ item }}</n-tag>
                <n-tag v-if="complexCount(a.config)" size="small" type="info"
                       :bordered="false" style="font-size: 11px">
                  复杂字段 ×{{ complexCount(a.config) }}
                </n-tag>
              </n-space>
            </template>
            <n-text v-else depth="3" style="font-size: 12px">配置文件不存在</n-text>
          </td>
          <td>
            <n-space size="small">
              <n-button size="tiny" type="primary" ghost @click="openEdit(a)">编辑配置</n-button>
              <n-popconfirm :show-icon="false">
                <template #trigger>
                  <n-button size="tiny" type="error" ghost :disabled="working">删除</n-button>
                </template>
                删除是终局操作(物理删除代码与配置)。确认删除
                <b>{{ a.name }}</b>?
              </n-popconfirm>
            </n-space>
          </td>
        </tr>
        <tr v-if="!apps.length && !loading">
          <td colspan="6" style="text-align: center">
            <n-text depth="3">还没有应用,点"新建应用"开始</n-text>
          </td>
        </tr>
      </tbody>
    </n-table>

    <n-modal v-model:show="creating" preset="card" title="新建应用" style="max-width: 520px">
      <n-form label-placement="left" label-width="92">
        <n-form-item label="域名">
          <n-input v-model:value="form.name" placeholder="如 github_discuss(小写字母/数字/下划线)" />
        </n-form-item>
        <n-form-item label="签到类型">
          <n-radio-group v-model:value="form.kind">
            <n-radio value="web">网页版签(HTTP 接口)</n-radio>
            <n-radio value="client">客户端签到(本地客户端/CDP)</n-radio>
          </n-radio-group>
        </n-form-item>
        <n-form-item label="显示名">
          <n-input v-model:value="form.display" placeholder="缺省用域名" />
        </n-form-item>
        <n-form-item label="接口宿主">
          <n-input v-model:value="form.base_url" placeholder="https://..." />
        </n-form-item>
        <n-form-item label="每日时间">
          <n-time-picker v-model:formatted-value="form.daily_time" format="HH:mm"
                         value-format="HH:mm" />
        </n-form-item>
      </n-form>
      <template #footer>
        <n-space justify="end">
          <n-button @click="creating = false">取消</n-button>
          <n-button type="primary" :loading="working" @click="create">
            生成并重建生效
          </n-button>
        </n-space>
      </template>
    </n-modal>

    <n-modal v-model:show="editing" preset="card" :title="`编辑配置: ${editName}`"
             style="max-width: 560px">
      <n-spin :show="editLoading">
        <n-alert type="info" :show-icon="false" style="margin-bottom: 12px">
          保存后<b>下次运行命令时生效</b>(不需要重建)。重写会移除 YAML 注释,
          需要保留注释的精细编辑请到
          <n-button text type="primary" @click="router.push({ name: 'config' })">配置</n-button>
          页用文本编辑。
        </n-alert>
        <n-form label-placement="left" label-width="150">
          <n-form-item v-for="f in editFields" :key="f.key" :label="f.key">
            <n-input-number v-if="typeof f.original === 'number'"
                            v-model:value="f.value as number | null"
                            style="width: 100%" />
            <n-switch v-else-if="typeof f.original === 'boolean'"
                      v-model:value="f.value as boolean" />
            <n-input v-else-if="isScalar(f.original)"
                     v-model:value="f.value as string | null"
                     :placeholder="f.original === null ? '留空 = 未设置(null)' : ''" />
            <n-text v-else depth="3" style="font-size: 12px">
              复杂字段(列表/映射),请到「配置」页用 YAML 编辑
            </n-text>
          </n-form-item>
          <n-form-item v-if="!editFields.length">
            <n-text depth="3">该域还没有配置文件(可从 config/examples 复制)</n-text>
          </n-form-item>
        </n-form>
      </n-spin>
      <template #footer>
        <n-space justify="end">
          <n-button @click="editing = false">取消</n-button>
          <n-button type="primary" :loading="savingEdit" @click="saveEdit">保存</n-button>
        </n-space>
      </template>
    </n-modal>
  </n-space>
</template>
