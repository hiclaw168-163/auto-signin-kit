/**
 * auto-signin-kit 桌面壳(P1,PROPOSAL-01,Electron 方案)。
 *
 * 职责边界: 壳只做 **窗口 + 托盘 + 本机服务生命周期**,不实现任何业务——
 * 业务数据全部走 Web 管理台的同一契约(OpenAPI 单一事实源,P0)。
 *
 * 行为:
 * - 启动时探测 http://127.0.0.1:8688/api/overview;服务不在则后台拉起
 *   `auto-signin-kit ui serve --no-open`(依赖本机已装的 tool 可执行文件,
 *   与 AGENTS.md"必须带 .[web] 重装 tool"的约定配套),轮询就绪后加载管理台;
 * - 服务始终起不来时加载本地兜底页(给重试按钮与排障提示,不白屏);
 * - 托盘: 每 60s 轮询总览,提示文本"签到管理台 · 今日 n/m";菜单支持
 *   显示主窗口 / 重启服务 / 退出;关窗驻留托盘(退出只能走托盘菜单);
 * - 单实例锁: 二次启动直接唤出已有窗口。
 *
 * 已知取舍: 打包体积 ~90MB(Electron 运行时)——决策记录见
 * docs/proposals/PROPOSAL-01-多端应用架构调研.md §1.2。
 */

const { app, BrowserWindow, Tray, Menu, nativeImage, shell } = require('electron')
const { spawn } = require('child_process')
const http = require('node:http')
const path = require('node:path')
const fs = require('node:fs')

/**
 * 桌面壳专属端口(默认 8690): 与 Web 端 8688 并行——两个 ui serve 实例
 * 写同一个 SQLite 台账,WAL 模式多进程安全(写入频度是每天几行,毫无压力)。
 * 这样浏览器开 8688、桌面壳跑 8690 互不干扰,可多个地方同时用。
 */
const PORT = process.env.ASK_DESKTOP_PORT || '8690'
const SERVICE_URL = process.env.ASK_SERVICE_URL || `http://127.0.0.1:${PORT}`
const POLL_MS = 60_000
const BOOT_TIMEOUT_MS = 20_000

/** @type {BrowserWindow | null} */
let win = null
/** @type {Tray | null} */
let tray = null
/** quitting 标志: 关窗驻留托盘与真正退出共用一条路径。 */
let quitting = false

// ---------------------------------------------------------------- CLI 定位

/**
 * 定位 auto-signin-kit CLI(绝对路径优先,不赌 PATH):
 * 1. env ASK_CLI 显式指定;
 * 2. uv tool 默认安装位置 %USERPROFILE%\.local\bin\(从开始菜单/安装目录
 *    启动时,GUI 进程的 PATH 可能不含这里——这是"装了不能用"的首因);
 * 3. 兜底裸命令名(仍走 PATH,交给 where 解析)。
 */
let cliPathCache = /** @type {string | null} */ (null)

function findCli() {
  if (cliPathCache) return cliPathCache
  const candidates = []
  if (process.env.ASK_CLI) candidates.push(process.env.ASK_CLI)
  if (process.env.USERPROFILE) {
    candidates.push(path.join(process.env.USERPROFILE, '.local', 'bin', 'auto-signin-kit.exe'))
  }
  candidates.push('auto-signin-kit')
  for (const c of candidates) {
    if (c.includes(path.sep)) {
      if (fs.existsSync(c)) { cliPathCache = c; return c }
      continue // 显式路径不存在就跳过,不猜
    }
    cliPathCache = c // 裸命令名交给 PATH
    return c
  }
  return null
}

// ---------------------------------------------------------------- 服务探测

function probe(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(`${SERVICE_URL}/api/overview`, { timeout: timeoutMs }, (res) => {
      res.resume()
      resolve(res.statusCode === 200)
    })
    req.on('timeout', () => { req.destroy(); resolve(false) })
    req.on('error', () => resolve(false))
  })
}

/**
 * 服务日志落盘(userData 目录): spawn 的 stdout/stderr 之前是 stdio:'ignore',
 * 服务起不来时用户和壳都不知道为什么——现在兜底页能给出真实报错。
 *
 * 注意必须用 openSync 拿**数值 fd**: createWriteStream 是异步打开,
 * fd 还没就绪就传给 spawn 会抛 ERR_INVALID_ARG_VALUE(fd: null)——
 * 这正是 0.2.0 首测"壳拉不起服务"的直接原因(2026-11-06 实测踩坑)。
 */
function openServiceLog() {
  try {
    const dir = app.getPath('userData')
    fs.mkdirSync(dir, { recursive: true })
    const logPath = path.join(dir, 'serve.log')
    const fd = fs.openSync(logPath, 'a')
    fs.writeSync(fd, `\n[${new Date().toISOString()}] --- 桌面壳拉起服务 (port ${PORT}) ---\n`)
    return { fd, logPath }
  } catch (e) {
    lastBootError = `服务日志文件打不开: ${e.message}`
    return { fd: null, logPath: '(日志不可写)' }
  }
}

/** @type {string | null} 最近一次拉起失败的诊断信息(兜底页展示)。 */
let lastBootError = null

/**
 * 确保服务就绪: 先探测;不在则拉起 tool 的 ui serve(独立进程组,壳退出
 * 不杀服务——服务归调度/CLI 管,壳只是它的一个客户端),轮询到就绪或超时。
 * 失败时把诊断写进 lastBootError(兜底页渲染)。
 */
async function ensureService() {
  if (await probe()) return true
  const cli = findCli()
  if (!cli) {
    lastBootError = '没有找到 auto-signin-kit CLI。请先安装: uv tool install ".[web]" --force,' +
      '或设置环境变量 ASK_CLI 指向 auto-signin-kit.exe 的绝对路径。'
    return false
  }
  const { fd, logPath } = openServiceLog()
  try {
    const child = spawn(cli, ['ui', 'serve', `--port=${PORT}`, '--no-open'], {
      detached: true,
      windowsHide: true,
      stdio: ['ignore', fd ?? 'ignore', fd ?? 'ignore'],
    })
    child.unref()
    // 监听异步 spawn 错误(ENOENT/权限等在 exec 阶段才冒出来)
    child.on('error', (e) => { lastBootError = `服务进程异常退出: ${e.message}` })
  } catch (e) {
    lastBootError = `拉起服务进程失败: ${e.message} (CLI: ${cli})`
    if (fd != null) fs.closeSync(fd)
    return false
  }
  const deadline = Date.now() + BOOT_TIMEOUT_MS
  let ok = false
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 800))
    if (await probe(800)) { ok = true; break }
  }
  if (fd != null) fs.closeSync(fd) // 服务已接管 fd,父进程这头关掉
  if (!ok) {
    lastBootError =
      `服务在 ${Math.round(BOOT_TIMEOUT_MS / 1000)} 秒内没有就绪 (端口 ${PORT})。` +
      `常见原因: 端口被占用;配置或登录态校验失败。服务日志: ${logPath}`
  }
  return ok
}

// ---------------------------------------------------------------- 窗口

function retryHtml() {
  return `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc;
         display: grid; place-items: center; height: 100vh; margin: 0; }
  .card { text-align: left; max-width: 560px; line-height: 1.7; }
  h1 { font-size: 20px; } code { background: #1b2336; padding: 2px 6px; border-radius: 4px; }
  .err { background: #1b2336; border: 1px solid #475569; border-radius: 8px;
         padding: 12px 14px; margin: 14px 0; font-size: 13px; white-space: pre-wrap;
         color: #fca5a5; }
  button { margin-top: 6px; padding: 8px 22px; border: 0; border-radius: 8px;
           background: #22c55e; color: #052e16; font-size: 14px; cursor: pointer; }
</style></head><body><div class="card">
<h1>本地服务未就绪</h1>
<div class="err">${(lastBootError || '原因未知').replace(/</g, '&lt;')}</div>
<p style="font-size:13px;color:#94a3b8">桌面壳使用独立端口 <code>${PORT}</code>(浏览器版是 8688,互不影响)。<br>
也可以手动验证: <code>auto-signin-kit ui serve --port=${PORT} --no-open</code></p>
<button onclick="location.reload()">重试</button>
</div></body></html>`
}

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 960,
    minHeight: 640,
    title: '签到管理台',
    icon: path.join(__dirname, 'icon.png'),
    autoHideMenuBar: true,
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
    },
  })
  // 外链交给系统浏览器,壳内只放管理台
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith(SERVICE_URL)) return { action: 'allow' }
    shell.openExternal(url)
    return { action: 'deny' }
  })
  win.on('close', (e) => {
    if (!quitting) {
      e.preventDefault()
      win?.hide() // 关窗驻留托盘,退出走托盘菜单
    }
  })
  win.on('closed', () => { win = null })
}

async function loadApp() {
  if (!win) return
  if (await ensureService()) {
    lastBootError = null
    await win.loadURL(SERVICE_URL)
  } else {
    await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(retryHtml())}`)
  }
}

function showWindow() {
  if (!win) createWindow()
  win?.show()
  win?.focus()
}

// ---------------------------------------------------------------- 托盘

function trayIcon() {
  const img = nativeImage.createFromPath(path.join(__dirname, 'icon.png'))
  return img.isEmpty() ? nativeImage.createEmpty() : img.resize({ width: 16, height: 16 })
}

async function pollOverview() {
  try {
    const res = await fetch(`${SERVICE_URL}/api/overview`)
    const data = await res.json()
    const cards = data.cards || []
    const signed = cards.filter((c) => c.today_signed).length
    const total = cards.length
    tray?.setToolTip(`签到管理台 · 今日已签 ${signed}/${total}`)
    tray?.setImage(buildBadge(signed, total))
  } catch {
    tray?.setToolTip('签到管理台 · 服务未就绪')
  }
}

/**
 * 托盘角标(纯 Node 绘制 16x16 PNG,零依赖): 底色按"全签/部分/全未"分档,
 * 下沿画完成比例条——原生 nativeImage 画不了文字,数字进 tooltip。
 */
function buildBadge(signed, total) {
  const size = 16
  const bg = total > 0 && signed === total ? [34, 197, 94, 255] // #22c55e 全签
    : signed > 0 ? [234, 179, 8, 255] // #eab308 部分
      : [100, 116, 139, 255] // #64748b 全未
  const buf = Buffer.alloc(size * size * 4)
  const filled = total > 0 ? Math.round((signed / total) * size) : 0
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = (y * size + x) * 4
      const inBar = y >= 12 && x < filled
      const color = inBar ? [15, 23, 42, 255] : bg // 比例条用深色压在底色上
      buf[i] = color[0]; buf[i + 1] = color[1]; buf[i + 2] = color[2]; buf[i + 3] = color[3]
    }
  }
  const png = encodePng(buf, size, size)
  return nativeImage.createFromBuffer(png)
}

/** 最小 PNG 编码器(8bit RGBA 单 IDAT,零依赖,只为托盘角标)。 */
function encodePng(rgba, width, height) {
  const { deflateSync } = require('node:zlib')
  // 每行前置 filter 字节 0(None)
  const raw = Buffer.alloc((width * 4 + 1) * height)
  for (let y = 0; y < height; y++) {
    raw[y * (width * 4 + 1)] = 0
    rgba.copy(raw, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4)
  }
  const crcTable = [...Array(256)].map((_, n) => {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    return c >>> 0
  })
  const crc32 = (b) => {
    let c = 0xffffffff
    for (const byte of b) c = crcTable[(c ^ byte) & 0xff] ^ (c >>> 8)
    return (c ^ 0xffffffff) >>> 0
  }
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type), data])
    const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body))
    return Buffer.concat([len, body, crc])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 8; ihdr[9] = 6 // 8bit RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

function createTray() {
  tray = new Tray(trayIcon())
  tray.setToolTip('签到管理台 · 启动中…')
  rebuildTrayMenu()
  pollOverview()
  setInterval(pollOverview, POLL_MS)
}

function rebuildTrayMenu() {
  tray?.setContextMenu(Menu.buildFromTemplate([
    { label: '显示主窗口', click: showWindow },
    { label: '重启服务', click: restartService },
    { type: 'separator' },
    { label: '退出', click: quitApp },
  ]))
}

async function restartService() {
  // 壳不 kill 旧服务进程(不持有句柄);直接再拉一份(桌面壳自己的端口),
  // serve 侧 daemon 清单兜底单实例,冲突时兜底页呈现真实报错
  await ensureService()
  await loadApp()
  pollOverview()
}

function quitApp() {
  quitting = true
  app.quit()
}

// ---------------------------------------------------------------- 生命周期

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', showWindow)

  app.whenReady().then(async () => {
    createWindow()
    createTray()
    await loadApp()
    pollOverview()
  })

  app.on('window-all-closed', () => {
    // 关窗驻留托盘: 不随窗口关闭退出,只有托盘"退出"真正走 quitApp
  })

  app.on('before-quit', () => { quitting = true })
}
