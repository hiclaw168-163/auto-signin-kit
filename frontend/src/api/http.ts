// fetch 薄封装: 非 2xx 抛 HttpError(保留后端 detail 原始结构),
// 让"要区分错误形态"的调用方(如配置编辑的逐字段报错)能拿到结构化数据。

export class HttpError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, detail: unknown) {
    super(formatDetail(detail))
    this.status = status
    this.detail = detail
  }
}

function formatDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const d = detail as { message: string; errors?: { loc: string; msg: string }[] }
    const lines = (d.errors ?? []).map((e) => `${e.loc}: ${e.msg}`)
    return lines.length ? `${d.message}\n${lines.join('\n')}` : d.message
  }
  return `HTTP 请求失败`
}

async function parseError(res: Response): Promise<HttpError> {
  let detail: unknown = `HTTP ${res.status}`
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (body?.detail !== undefined) detail = body.detail
  } catch {
    /* body 不是 JSON,用状态码 */
  }
  return new HttpError(res.status, detail)
}

export async function get<T>(
  url: string,
  params?: Record<string, string | number | boolean | undefined>,
): Promise<T> {
  const qs = params
    ? '?' +
      new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => [k, String(v)]),
      ).toString()
    : ''
  const res = await fetch(url + qs)
  if (!res.ok) throw await parseError(res)
  return res.json() as Promise<T>
}

export async function post<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? '{}' : JSON.stringify(body),
  })
  if (!res.ok) throw await parseError(res)
  return res.json() as Promise<T>
}

export async function put<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw await parseError(res)
  return res.json() as Promise<T>
}

export async function del<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: 'DELETE' })
  if (!res.ok) throw await parseError(res)
  return res.json() as Promise<T>
}
