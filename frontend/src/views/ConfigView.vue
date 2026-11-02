<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { configApi } from '../api'
import { HttpError } from '../api/http'
import type { ConfigItem, FieldError } from '../types'

// 配置编辑: YAML 是唯一事实源,这里只是"带校验的编辑器"——保存必过各域
// pydantic schema(与 CLI 同一套),逐字段报错;写回主 YAML,下次运行生效。

const message = useMessage()
const items = ref<ConfigItem[]>([])
const domain = ref<string | null>(null)
const content = ref('')
const original = ref('')
const errors = ref<FieldError[]>([])
const errorText = ref('')
const saving = ref(false)
const validating = ref(false)

const dirty = computed(() => content.value !== original.value)

function applyError(e: unknown) {
  if (e instanceof HttpError && e.detail && typeof e.detail === 'object'
      && 'errors' in (e.detail as Record<string, unknown>)) {
    const d = e.detail as { errors: FieldError[] }
    errors.value = d.errors
    errorText.value = ''
  } else {
    errors.value = []
    errorText.value = e instanceof Error ? e.message : String(e)
  }
}

async function load() {
  if (!domain.value) return
  errors.value = []
  errorText.value = ''
  try {
    const res = await configApi.get(domain.value)
    content.value = res.content
    original.value = res.content
  } catch (e) {
    content.value = ''
    original.value = ''
    applyError(e)
  }
}

async function validate() {
  validating.value = true
  try {
    await configApi.validate(domain.value!, content.value)
    errors.value = []
    errorText.value = ''
    message.success('校验通过')
  } catch (e) {
    applyError(e)
    message.error('校验未通过')
  } finally {
    validating.value = false
  }
}

async function save() {
  saving.value = true
  try {
    await configApi.save(domain.value!, content.value)
    original.value = content.value
    errors.value = []
    errorText.value = ''
    message.success('已保存,对应命令下次运行即用新配置')
  } catch (e) {
    applyError(e)
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    items.value = await configApi.list()
    domain.value = items.value.find((i) => i.exists)?.domain ?? null
    if (domain.value) await load()
  } catch (e) {
    message.error(String(e))
  }
})
</script>

<template>
  <n-space vertical size="large">
    <n-space align="center">
      <n-select v-model:value="domain" style="width: 220px" @update:value="load"
                :options="items.map((i) => ({ label: i.domain + (i.exists ? '' : ' (无配置)'), value: i.domain }))" />
      <n-button :disabled="!domain || !dirty" @click="load">放弃修改</n-button>
      <n-button :loading="validating" :disabled="!domain" @click="validate">校验</n-button>
      <n-button type="primary" :loading="saving" :disabled="!domain || !dirty" @click="save">
        保存
      </n-button>
      <n-text depth="3" style="font-size: 12px">
        保存写入 config/domains/{{ domain }}.yaml,校验与 CLI 同一套 schema;对应命令下次运行生效
      </n-text>
    </n-space>

    <n-alert v-if="errorText" type="error" title="错误" style="white-space: pre-wrap">
      {{ errorText }}
    </n-alert>
    <n-alert v-if="errors.length" type="error" title="配置校验失败(逐字段)">
      <div v-for="err in errors" :key="err.loc + err.msg" class="mono" style="font-size: 12px">
        {{ err.loc }}: {{ err.msg }}
      </div>
    </n-alert>

    <n-input v-model:value="content" type="textarea" :rows="22" class="mono"
             :disabled="!domain" placeholder="选择一个域加载配置" />
  </n-space>
</template>
