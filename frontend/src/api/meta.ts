import { ref } from 'vue'
import { get } from './http'

// 结果标准词表(来源: 后端 core/report.py 的 RESULT_LABELS,经 /api/meta/results
// 下发)。前端**不自造**标签: 词表加一条,界面自动跟上,不存在"两层各翻各的"。

const labels = ref<Record<string, string>>({})
let loaded = false

export async function loadResultLabels(): Promise<void> {
  if (loaded) return
  labels.value = await get<Record<string, string>>('/api/meta/results')
  loaded = true
}

export function resultLabel(result?: string | null): string {
  if (!result) return '无记录'
  return labels.value[result] ?? result
}

export function isOkResult(result?: string | null): boolean {
  // 成功语义判断: 词表后端不发集合,这里按同源规则判定(与 RESULT_OK 一致)
  return !!result && ['CLAIMED', 'ALREADY', 'ACTIVE', 'OK'].includes(result)
}
