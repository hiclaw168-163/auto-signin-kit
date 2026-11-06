/**
 * 生成 desktop/icon.png(256x256 应用/托盘图标,零依赖)。
 * 与 main.js 的 buildBadge 同一套配色体系(ui-ux-pro-max #81:
 * slate 蓝黑底 + 绿 #22c55e 主色),中央画一个"签到对勾"意象的圆环+勾。
 * 修改配色/图形后: `node gen-icon.js` 重新生成。
 */

const { deflateSync } = require('node:zlib')
const fs = require('node:fs')

const SIZE = 256

// #1b2336(slate surface)圆角底 + #22c55e 环与勾
const BG = [27, 35, 54, 255]
const FG = [34, 197, 94, 255]
const TRANSPARENT = [0, 0, 0, 0]

function insideRoundedRect(x, y, size, radius) {
  if (x < 0 || y < 0 || x >= size || y >= size) return false
  const rx = x < radius ? radius - x : x >= size - radius ? x - (size - radius - 1) : 0
  const ry = y < radius ? radius - y : y >= size - radius ? y - (size - radius - 1) : 0
  return rx * rx + ry * ry <= radius * radius
}

function ringDist(x, y, cx, cy, r) {
  return Math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
}

function drawCheck(x, y) {
  // 勾: 两条线段 (78,132)->(112,166)->(180,94),线宽 ~14
  const segments = [
    { x1: 78, y1: 132, x2: 112, y2: 166 },
    { x1: 112, y1: 166, x2: 180, y2: 94 },
  ]
  const half = 7
  for (const s of segments) {
    const dx = s.x2 - s.x1
    const dy = s.y2 - s.y1
    const len = Math.hypot(dx, dy)
    const t = Math.max(0, Math.min(1, ((x - s.x1) * dx + (y - s.y1) * dy) / (len * len)))
    const px = s.x1 + t * dx
    const py = s.y1 + t * dy
    if (Math.hypot(x - px, y - py) <= half) return true
  }
  return false
}

const buf = Buffer.alloc(SIZE * SIZE * 4)
const R = 48 // 圆角半径
const CX = 128
for (let y = 0; y < SIZE; y++) {
  for (let x = 0; x < SIZE; x++) {
    const i = (y * SIZE + x) * 4
    if (!insideRoundedRect(x, y, SIZE, R)) {
      buf[i] = TRANSPARENT[0]; buf[i + 1] = TRANSPARENT[1]
      buf[i + 2] = TRANSPARENT[2]; buf[i + 3] = TRANSPARENT[3]
      continue
    }
    const d = ringDist(x, y, CX, CX, 62)
    const color = (d >= 52 && d <= 62) || (d < 52 && drawCheck(x, y)) ? FG : BG
    buf[i] = color[0]; buf[i + 1] = color[1]; buf[i + 2] = color[2]; buf[i + 3] = color[3]
  }
}

// ---- PNG 编码(与 main.js encodePng 同构) ----
const raw = Buffer.alloc((SIZE * 4 + 1) * SIZE)
for (let y = 0; y < SIZE; y++) {
  raw[y * (SIZE * 4 + 1)] = 0
  buf.copy(raw, y * (SIZE * 4 + 1) + 1, y * SIZE * 4, (y + 1) * SIZE * 4)
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
ihdr.writeUInt32BE(SIZE, 0); ihdr.writeUInt32BE(SIZE, 4)
ihdr[8] = 8; ihdr[9] = 6
const png = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  chunk('IHDR', ihdr),
  chunk('IDAT', deflateSync(raw, { level: 9 })),
  chunk('IEND', Buffer.alloc(0)),
])
fs.writeFileSync(__dirname + '/icon.png', png)
console.log(`icon.png 已生成 (${SIZE}x${SIZE}, ${png.length} bytes)`)
