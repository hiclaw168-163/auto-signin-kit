/**
 * @ask/api-client 对外出口: 契约类型 + 请求封装。
 *
 * 类型层(``components["schemas"]``)由 openapi-typescript 从契约生成
 * (``npm run gen``),手写代码只允许**引用**生成类型,不允许手写平行形状
 * (单一事实源铁律)。请求封装后续按端注入 fetch 实现(浏览器 fetch /
 * Tauri IPC / uni.request),本包只管 URL 与契约类型,不绑死任何运行时。
 */

export type { components, operations } from "./schema";
