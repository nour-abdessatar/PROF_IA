/** First / last frame files present in public/frames/ (update if you add or remove PNGs). */
export const FRAME_MIN = 67;
export const FRAME_MAX = 120;

export const FRAME_NUMBERS = Array.from(
  { length: FRAME_MAX - FRAME_MIN + 1 },
  (_, i) => FRAME_MIN + i
);

export const FRAME_COUNT = FRAME_NUMBERS.length;

export const LANDING_BG_FRAME = "/frames/ezgif-frame-087.png";

export function frameSrc(frameNumber: number): string {
  return `/frames/ezgif-frame-${String(frameNumber).padStart(3, "0")}.png`;
}
