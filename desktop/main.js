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

const PORT = process.env.ASK_SERVICE_PORT || '8688'
const SERVICE_URL = process.env.ASK_SERVICE_URL || `http://127.0.0.1:${PORT}`
const POLL_MS = 60_000
const BOOT_TIMEOUT_MS = 20_000

/** @type {BrowserWindow | null} */
let win = null
/** @type {Tray | null} */
let tray = null
/** quitting 标志: 关窗驻留托盘与真正退出共用一条路径。 */
let quitting = false

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
 * 确保服务就绪: 先探测;不在则拉起 tool 的 ui serve(独立进程组,壳退出
 * 不杀服务——服务归调度/CLI 管,壳只是它的一个客户端),轮询到就绪或超时。
 */
async function ensureService() {
  if (await probe()) return true
  const child = spawn('auto-signin-kit', ['ui', 'serve', '--no-open'], {
    shell: true,
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
  })
  child.unref()
  const deadline = Date.now() + BOOT_TIMEOUT_MS
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 800))
    if (await probe(800)) return true
  }
  return false
}

// ---------------------------------------------------------------- 窗口

const RETRY_HTML = `<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #f8fafc;
         display: grid; place-items: center; height: 100vh; margin: 0; }
  .card { text-align: center; max-width: 460px; line-height: 1.7; }
  h1 { font-size: 20px; } code { background: #1b2336; padding: 2px 6px; border-radius: 4px; }
  button { margin-top: 18px; padding: 8px 22px; border: 0; border-radius: 8px;
           background: #22c55e; color: #052e16; font-size: 14px; cursor: pointer; }
</style></head><body><div class="card">
<h1>本地服务未就绪</h1>
<p>已尝试自动拉起 <code>auto-signin-kit ui serve</code> 但没有成功。<br>
请手动在终端执行上面的命令看报错,或检查端口 <code>${PORT}</code> 是否被占用。</p>
<button onclick="location.reload()">重试</button>
</div></body></html>`

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
    await win.loadURL(SERVICE_URL)
  } else {
    await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(RETRY_HTML)}`)
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
  // 壳不 kill 旧服务进程(不持有句柄);直接再拉一份——serve 侧 daemon 清单
  // 兜底单实例,冲突时 UI 侧有既有的报错呈现
  spawn('auto-signin-kit', ['ui', 'serve', '--no-open'], {
    shell: true, detached: true, stdio: 'ignore', windowsHide: true,
  }).unref()
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
