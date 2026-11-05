// 应用档案枚举与展示文案(全库统一口径),与后端 core/declarative/constants.py
// 镜像——曾经"网页版签/客户端签到"两处各写各的导致文案漂移,现在标签只从
// 这里取;改值时后端一起改(见 docs/standards/STD-07)。
// 分类标准 = 凭据来源: 凭据来自本地客户端(ss_aes 解桌面端存储 / chromium_cookies
// / CDP)都是"客户端签到";凭据由人手动维护(令牌文件/环境变量)才是"网页版签"。
export const KIND_WEB = 'web'
export const KIND_CLIENT = 'client'

export const KIND_SHORT_LABELS: Record<string, string> = {
  web: '网页版签',
  client: '客户端签到',
}

export const KIND_LABELS: Record<string, string> = {
  web: '网页版签(令牌文件/环境变量)',
  client: '客户端签到(本地客户端/CDP)',
}

export const REGION_LABELS: Record<string, string> = { cn: '国内版', intl: '国际版' }

export const SOURCE_LABELS: Record<string, string> = { auto: '自动同步', manual: '手动新建' }

// 接口地址(声明字段 base_url)的统一说明: 创建/编辑两处共用同一句,
// 说清"是什么 + 从哪里获取"。
export const BASE_URL_HINT =
  '签到接口的域名根地址(如 https://api.trae.cn)。' +
  '获取方式: 打开客户端或网页,用开发者工具的 Network 面板找到签到/领取请求,' +
  '取其域名部分;一般与应用官网同域或 api.* 子域'

// 参数配置页签的字段标签(后端字段名 → 中文说明);未收录的原样展示字段名。
export const FIELD_LABELS: Record<string, string> = {
  base_url: '接口地址',
  budget_seconds: '单次预算(秒)',
  daily_time: '每日时间',
}
