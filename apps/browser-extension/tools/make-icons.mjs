// One-off icon generator: writes valid PNGs with node:zlib only (no image
// deps). Design: indigo square, two offset white bars - a "switch" glyph.
// Run: node tools/make-icons.mjs  (output: public/icons/icon{16,48,128}.png)
import * as zlibMod from "node:zlib";
import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlibCrc32(body) >>> 0);
  return Buffer.concat([len, body, crc]);
}

// crc32; node:zlib exposes it from v20.15 - inline fallback keeps the
// script runnable on older node if CI bumps around
function zlibCrc32(buf) {
  if ("crc32" in zlibMod) return zlibMod.crc32(buf);
  let c = ~0;
  for (const byte of buf) {
    c ^= byte;
    for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
  }
  return ~c;
}

function png(size, pixels) {
  // pixels: Uint8Array RGBA, row-major
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0; // filter: none
    Buffer.from(pixels.buffer, y * size * 4, size * 4).copy(
      raw,
      y * (size * 4 + 1) + 1,
    );
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function render(size) {
  const px = new Uint8Array(size * size * 4);
  const bg = [0x4f, 0x46, 0xe5, 0xff]; // indigo
  const fg = [0xff, 0xff, 0xff, 0xff]; // white
  const bar = (x0, y0, w, h) => {
    for (let y = y0; y < y0 + h; y++)
      for (let x = x0; x < x0 + w; x++) {
        const i = (y * size + x) * 4;
        px.set(fg, i);
      }
  };
  // fill with slight rounded-corner feel: inset corners stay transparent
  const r = Math.max(1, Math.round(size * 0.12));
  for (let y = 0; y < size; y++)
    for (let x = 0; x < size; x++) {
      const dx = Math.min(x, size - 1 - x);
      const dy = Math.min(y, size - 1 - y);
      if (dx + dy < r) continue; // corner cut
      const i = (y * size + x) * 4;
      px.set(bg, i);
    }
  const t = Math.max(2, Math.round(size * 0.12)); // bar thickness
  const pad = Math.round(size * 0.2);
  const len = size - pad * 2;
  // switch glyph: upper bar full, lower bar shorter (a toggle)
  bar(pad, Math.round(size * 0.3), len, t);
  bar(pad, Math.round(size * 0.58), Math.round(len * 0.6), t);
  return px;
}

mkdirSync(resolve(root, "public/icons"), { recursive: true });
for (const size of [16, 48, 128]) {
  writeFileSync(resolve(root, `public/icons/icon${size}.png`), png(size, render(size)));
  console.log(`wrote public/icons/icon${size}.png`);
}
