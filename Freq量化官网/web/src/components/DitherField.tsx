/*
 * 有序抖动背景（8×8 Bayer）。低分辨率画布放大到全屏，得到像素终端的质感：
 * 深紫 → 品红 → 橙 → 金 的色带缓慢漂移，越往下越暗，保证正文可读。
 */

import { useEffect, useRef } from 'react';

const W = 400;
const H = 250;

const BAYER = [
  0, 32, 8, 40, 2, 34, 10, 42,
  48, 16, 56, 24, 50, 18, 58, 26,
  12, 44, 4, 36, 14, 46, 6, 38,
  60, 28, 52, 20, 62, 30, 54, 22,
  3, 35, 11, 43, 1, 33, 9, 41,
  51, 19, 59, 27, 49, 17, 57, 25,
  15, 47, 7, 39, 13, 45, 5, 37,
  63, 31, 55, 23, 61, 29, 53, 21,
];

const RAMP: [number, number, number][] = [
  [11, 8, 25],
  [34, 15, 52],
  [82, 20, 79],
  [140, 30, 92],
  [190, 45, 100],
  [224, 69, 123],
  [240, 123, 63],
  [249, 199, 79],
  [244, 230, 200],
];

function noise(x: number, y: number, t: number): number {
  return (
    0.5 +
    0.28 * Math.sin(x * 0.055 + t * 0.9) * Math.cos(y * 0.083 - t * 0.6) +
    0.16 * Math.sin(x * 0.13 - t * 1.35 + y * 0.021) +
    0.09 * Math.cos(y * 0.21 + t * 1.7) * Math.sin(x * 0.041 + t * 0.4)
  );
}

export function DitherField() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) return;

    canvas.width = W;
    canvas.height = H;
    const img = ctx.createImageData(W, H);
    const data = img.data;
    let raf = 0;
    let last = 0;

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const draw = (t: number) => {
      for (let y = 0; y < H; y++) {
        const horizon = Math.exp(-Math.pow((y / H - 0.5) / 0.36, 2));
        const fade = 1 - Math.pow(y / H, 1.25) * 0.7;
        for (let x = 0; x < W; x++) {
          const v = Math.min(1, Math.max(0, (noise(x, y, t) * 0.62 + horizon * 0.55) * fade));
          const bayer = BAYER[(y & 7) * 8 + (x & 7)] / 64;
          const idx = Math.min(RAMP.length - 1, Math.floor((v * 0.9 + bayer * 0.22) * RAMP.length * 0.82));
          const [r, g, b] = RAMP[idx];
          const i = (y * W + x) * 4;
          data[i] = r;
          data[i + 1] = g;
          data[i + 2] = b;
          data[i + 3] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);
    };

    const loop = (now: number) => {
      if (now - last > 120) {
        last = now;
        draw(now / 1000);
      }
      raf = requestAnimationFrame(loop);
    };

    draw(0);
    if (!reduce) raf = requestAnimationFrame(loop);

    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={ref} className="rh-field" aria-hidden="true" />;
}
