/**
 * 壳桥(contextIsolation,最小暴露)。
 *
 * P1 壳是"纯窗口",网页端不需要任何壳能力;这里只登记版本号供页面
 * 展示与排障。后续如果要做"页面里一键驻留/开机自启"等壳操作,
 * 在这里加 `window.askDesktop.xxx`(invoke → ipcMain.handle),仍然
 * 不允许把业务逻辑放进壳——契约消费规则不变(PROPOSAL-01 §3.1)。
 */

const { contextBridge } = require('electron')

contextBridge.exposeInMainWorld('askDesktop', {
  /** 壳标识与版本,页面可用于区分"浏览器打开"和"桌面壳打开"。 */
  shell: 'electron',
  version: process.env.npm_package_version || '0.1.0',
  serviceUrl: process.env.ASK_SERVICE_URL || `http://127.0.0.1:${process.env.ASK_SERVICE_PORT || '8688'}`,
})
