import { useEffect, useRef } from "react";
import { motion, useInView } from "framer-motion";

type LandingVideoProps = {
  src: string;
  fromStart: boolean;
  className?: string;
};

export default function LandingVideo({ src, fromStart, className = "" }: LandingVideoProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const inView = useInView(wrapRef, { once: true, amount: 0.15 });

  useEffect(() => {
    const video = videoRef.current;
    if (!inView || !video) return;
    video.play().catch(() => {
      /* autoplay blocked until user gesture */
    });
  }, [inView, src]);

  return (
    <motion.div
      ref={wrapRef}
      initial={{ opacity: 0, x: fromStart ? -120 : 120 }}
      animate={inView ? { opacity: 1, x: 0 } : { opacity: 0, x: fromStart ? -120 : 120 }}
      transition={{ type: "spring", stiffness: 65, damping: 15, mass: 0.85 }}
      className={className}
    >
      <div className="rounded-2xl overflow-hidden shadow-[0_8px_40px_rgba(99,80,219,0.12)] ring-1 ring-border/60 bg-black transition-all duration-300 hover:scale-[1.02] hover:shadow-[0_12px_50px_rgba(99,80,219,0.22)]">
        <video
          ref={videoRef}
          src={src}
          className="w-full aspect-video object-cover block min-h-[180px]"
          autoPlay
          muted
          loop
          playsInline
          preload="auto"
        />
      </div>
    </motion.div>
  );
}
