import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Sparkles } from "lucide-react";
import { t, useLang } from "@/lib/i18n";
import {
  FRAME_COUNT,
  FRAME_NUMBERS,
  frameSrc,
} from "@/lib/scrollFrames";

const LAST_FRAME_INDEX = FRAME_COUNT - 1;

function getScrollHeight(): string {
  if (typeof window === "undefined") return "250vh";
  return window.innerWidth < 768 ? "180vh" : "250vh";
}

function clamp01(v: number): number {
  return Math.min(1, Math.max(0, v));
}

function range(progress: number, start: number, end: number): number {
  return clamp01((progress - start) / (end - start));
}

function drawBlack(ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement) {
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, canvas.clientWidth, canvas.clientHeight);
}

function drawCover(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  canvas: HTMLCanvasElement
) {
  const cw = canvas.clientWidth;
  const ch = canvas.clientHeight;
  drawBlack(ctx, canvas);

  if (!img.complete || img.naturalWidth === 0) return;

  const scale = Math.max(cw / img.width, ch / img.height);
  const w = img.width * scale;
  const h = img.height * scale;
  const x = (cw - w) / 2;
  const y = (ch - h) / 2;
  ctx.drawImage(img, x, y, w, h);
}

function pickDrawableFrame(
  frames: HTMLImageElement[],
  index: number
): HTMLImageElement | null {
  const clamped = Math.min(Math.max(index, 0), frames.length - 1);
  if (frames[clamped]?.complete && frames[clamped].naturalWidth > 0) {
    return frames[clamped];
  }
  for (let d = 1; d < frames.length; d++) {
    const lo = clamped - d;
    const hi = clamped + d;
    if (lo >= 0 && frames[lo]?.complete && frames[lo].naturalWidth > 0) return frames[lo];
    if (hi < frames.length && frames[hi]?.complete && frames[hi].naturalWidth > 0) {
      return frames[hi];
    }
  }
  return null;
}

export default function ScrollAnimation() {
  const lang = useLang();
  const navigate = useNavigate();

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const framesRef = useRef<HTMLImageElement[]>([]);
  const progressRef = useRef(0);
  const frameIndexRef = useRef(0);
  const rafRef = useRef<number>(0);

  const [scrollHeight, setScrollHeight] = useState(getScrollHeight);
  const [loadProgress, setLoadProgress] = useState(0);
  const [isReady, setIsReady] = useState(false);
  const [scrollProgress, setScrollProgress] = useState(0);
  const [loadError, setLoadError] = useState(false);

  const resizeCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const w = window.innerWidth;
    const h = window.innerHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    const ctx = canvas.getContext("2d");
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }, []);

  const updateProgress = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;

    const containerTop = container.offsetTop;
    const containerHeight = container.offsetHeight;
    const windowHeight = window.innerHeight;
    const scrollable = containerHeight - windowHeight;

    if (scrollable <= 0) {
      progressRef.current = 0;
      return;
    }

    const scrolled = window.scrollY - containerTop;
    progressRef.current = Math.min(Math.max(scrolled / scrollable, 0), 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const images: HTMLImageElement[] = new Array(FRAME_COUNT);
    let settled = 0;
    let succeeded = 0;

    const tryFinish = () => {
      if (cancelled || settled < FRAME_COUNT) return;
      if (succeeded === 0) {
        setLoadError(true);
        return;
      }
      framesRef.current = images;
      setIsReady(true);
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      const first = pickDrawableFrame(images, 0);
      if (ctx && canvas) {
        if (first) drawCover(ctx, first, canvas);
        else drawBlack(ctx, canvas);
      }
    };

    const onSettled = (ok: boolean) => {
      if (cancelled) return;
      if (ok) succeeded += 1;
      settled += 1;
      setLoadProgress(settled / FRAME_COUNT);
      tryFinish();
    };

    FRAME_NUMBERS.forEach((frameNumber, i) => {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => onSettled(true);
      img.onerror = () => onSettled(false);
      img.src = frameSrc(frameNumber);
      images[i] = img;
    });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onResize = () => {
      setScrollHeight(getScrollHeight());
      resizeCanvas();
    };
    window.addEventListener("resize", onResize);
    resizeCanvas();
    return () => window.removeEventListener("resize", onResize);
  }, [resizeCanvas, isReady]);

  useEffect(() => {
    if (!isReady) return;

    const onScroll = () => updateProgress();
    window.addEventListener("scroll", onScroll, { passive: true });
    updateProgress();

    const tick = () => {
      const frames = framesRef.current;
      const canvas = canvasRef.current;
      const progress = progressRef.current;

      if (frames.length && canvas) {
        const frameIndex = Math.floor(progress * LAST_FRAME_INDEX);
        frameIndexRef.current = frameIndex;
        const ctx = canvas.getContext("2d");
        const img = pickDrawableFrame(frames, frameIndex);
        if (ctx) {
          if (img) drawCover(ctx, img, canvas);
          else drawBlack(ctx, canvas);
        }
      }

      setScrollProgress(progress);
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);

    return () => {
      window.removeEventListener("scroll", onScroll);
      cancelAnimationFrame(rafRef.current);
    };
  }, [isReady, updateProgress]);

  const heroIn = range(scrollProgress, 0.22, 0.42);
  const heroHold = 1 - range(scrollProgress, 0.78, 0.94);
  const heroOpacity = heroIn * heroHold;
  const heroY = (1 - heroIn) * 48 - range(scrollProgress, 0.78, 0.94) * 28;
  const lightBlend = range(scrollProgress, 0.62, 0.86);
  const exitFade = range(scrollProgress, 0.72, 1);

  const titleLight = `rgba(255,255,255,${1 - lightBlend * 0.15})`;
  const titleDark = `rgba(15,23,42,${lightBlend})`;
  const subtitleColor = lightBlend > 0.5 ? "rgb(100 116 139)" : "rgba(255,255,255,0.82)";
  const badgeClass =
    lightBlend > 0.45
      ? "bg-primary/10 text-primary border border-primary/15"
      : "bg-white/12 text-white border border-white/20";

  const canInteract = heroOpacity > 0.35 && scrollProgress < 0.92;

  return (
    <div
      ref={containerRef}
      className="relative w-full bg-black"
      style={{ height: scrollHeight }}
    >
      <div className="sticky top-0 left-0 w-full h-screen overflow-hidden bg-black">
        <canvas
          ref={canvasRef}
          className="block w-full h-full bg-black"
          aria-hidden={!isReady}
        />

        {!isReady && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-4 bg-black px-6">
            {loadError ? (
              <p className="text-sm text-white/70 text-center max-w-sm">
                {lang === "ar"
                  ? `تعذّر تحميل الإطارات (${FRAME_NUMBERS[0]}–${FRAME_NUMBERS[FRAME_NUMBERS.length - 1]}).`
                  : `Impossible de charger les images (frames ${FRAME_NUMBERS[0]}–${FRAME_NUMBERS[FRAME_NUMBERS.length - 1]}).`}
              </p>
            ) : (
              <>
                <div className="w-full max-w-xs h-2 rounded-full bg-white/10 overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-150 ease-out"
                    style={{
                      width: `${loadProgress * 100}%`,
                      backgroundColor: "#7C3AED",
                    }}
                  />
                </div>
                <p className="text-sm text-white/60">
                  {Math.round(loadProgress * 100)}%
                </p>
              </>
            )}
          </div>
        )}

        {isReady && (
          <div
            className="absolute inset-0 z-10 flex flex-col items-center justify-center px-6 text-center"
            style={{
              opacity: heroOpacity,
              transform: `translateY(${heroY}px)`,
              pointerEvents: canInteract ? "auto" : "none",
              willChange: "opacity, transform",
            }}
          >
            <span
              className={`inline-flex items-center gap-1.5 px-4 py-1.5 rounded-full text-xs font-semibold mb-6 transition-colors duration-300 ${badgeClass}`}
              style={{ opacity: range(scrollProgress, 0.26, 0.38) }}
            >
              <Sparkles className="w-3.5 h-3.5" />
              {t("hero_badge", lang)}
            </span>

            <h1 className="text-5xl sm:text-6xl font-extrabold tracking-tight leading-tight max-w-3xl">
              <span
                className="block transition-colors duration-300"
                style={{ color: lightBlend > 0.5 ? titleDark : titleLight }}
              >
                {t("hero_line1", lang)}
              </span>
              <span
                className={`block transition-all duration-300 ${
                  lightBlend > 0.55
                    ? "text-gradient"
                    : "bg-gradient-to-r from-violet-200 via-purple-200 to-fuchsia-200 bg-clip-text text-transparent"
                }`}
              >
                {t("hero_line2", lang)}
              </span>
            </h1>

            <p
              className="mt-6 text-lg max-w-xl mx-auto leading-relaxed transition-colors duration-300"
              style={{
                color: subtitleColor,
                opacity: range(scrollProgress, 0.3, 0.44),
              }}
            >
              {t("hero_subtitle", lang)}
            </p>

            <div
              className="mt-10"
              style={{ opacity: range(scrollProgress, 0.34, 0.48) }}
            >
              <button
                type="button"
                onClick={() => navigate("/auth")}
                className="px-8 py-3.5 rounded-2xl gradient-primary text-primary-foreground font-semibold text-sm inline-flex items-center gap-2 hover:opacity-90 transition-all shadow-soft hover:shadow-hover"
              >
                {t("start", lang)} <ArrowRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        <div
          className="absolute inset-0 z-30 pointer-events-none"
          style={{
            opacity: exitFade,
            background:
              "linear-gradient(to bottom, transparent 0%, rgba(255,255,255,0.35) 35%, rgba(255,255,255,0.92) 70%, #ffffff 100%)",
          }}
        />
      </div>
    </div>
  );
}
