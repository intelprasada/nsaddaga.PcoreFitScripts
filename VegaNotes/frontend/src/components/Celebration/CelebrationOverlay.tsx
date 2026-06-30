import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion } from "framer-motion";
import { onCelebration, type CelebrationEvent } from "../../lib/celebration";
import { isGamifyEnabled, subscribeGamify } from "../../lib/gamify";

/**
 * Global, full-viewport celebration overlay (#180 tier-1).
 *
 * Mounted ONCE at app root (see App.tsx). Subscribes to the global
 * `onCelebration` channel. Renders each burst as ~36 framer-motion
 * particles into a `position: fixed` portal on `document.body`, so:
 *
 *   - particles can travel anywhere on screen (no per-card clipping);
 *   - the overlay is decoupled from card mount/unmount cycles
 *     (Kanban re-bucketing on close no longer kills the trigger);
 *   - all interactions pass through (`pointer-events: none`).
 *
 * Burst-storm guard: at most 3 concurrent bursts; extras evict oldest.
 * Each burst auto-cleans after ~1.5 s. Honours `veganotes.gamify`
 * opt-out — when off, the overlay subscribes to no events.
 */

interface ActiveBurst {
  id: number;
  origin: { x: number; y: number };
  particles: Particle[];
}

interface Particle {
  dx: number;
  dy: number;
  rot: number;
  color: string;
  size: number;
  delay: number;
}

const PALETTE = [
  "#f43f5e", "#f97316", "#eab308",
  "#10b981", "#3b82f6", "#a855f7", "#ec4899",
];

const PARTICLE_COUNT = 36;
const RADIUS_PX = 360;
const DURATION_S = 1.3;
const MAX_CONCURRENT = 3;

let _seq = 0;

function buildParticles(count: number, radius: number): Particle[] {
  const out: Particle[] = [];
  for (let i = 0; i < count; i++) {
    const angle = Math.random() * Math.PI * 2;
    const dist = radius * (0.35 + Math.random() * 0.65);
    out.push({
      dx: Math.cos(angle) * dist,
      // Slight upward bias so the burst looks "thrown up" against gravity.
      dy: Math.sin(angle) * dist - 40 - Math.random() * 60,
      rot: (Math.random() - 0.5) * 900,
      color: PALETTE[Math.floor(Math.random() * PALETTE.length)],
      size: 6 + Math.random() * 8,
      delay: Math.random() * 0.08,
    });
  }
  return out;
}

function viewportCenter(): { x: number; y: number } {
  if (typeof window === "undefined") return { x: 0, y: 0 };
  return { x: window.innerWidth / 2, y: window.innerHeight / 2 };
}

export function CelebrationOverlay() {
  const [bursts, setBursts] = useState<ActiveBurst[]>([]);
  const [gamifyOn, setGamifyOn] = useState<boolean>(() => isGamifyEnabled());
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => subscribeGamify(setGamifyOn), []);

  useEffect(() => {
    if (!gamifyOn) return;
    return onCelebration((ev: CelebrationEvent) => {
      const origin = ev.origin ?? viewportCenter();
      const id = ++_seq;
      const burst: ActiveBurst = {
        id,
        origin,
        particles: buildParticles(PARTICLE_COUNT, RADIUS_PX),
      };
      setBursts((prev) => {
        const next = prev.length >= MAX_CONCURRENT ? prev.slice(1) : prev;
        return [...next, burst];
      });
      window.setTimeout(() => {
        if (!mounted.current) return;
        setBursts((prev) => prev.filter((b) => b.id !== id));
      }, Math.ceil((DURATION_S + 0.2) * 1000));
    });
  }, [gamifyOn]);

  if (typeof document === "undefined") return null;
  if (bursts.length === 0) return null;

  return createPortal(
    <div
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 overflow-visible"
      style={{ zIndex: 9999 }}
    >
      {bursts.map((b) => (
        <div
          key={b.id}
          className="absolute"
          style={{ left: b.origin.x, top: b.origin.y }}
        >
          {b.particles.map((p, i) => (
            <motion.span
              key={i}
              initial={{ x: 0, y: 0, rotate: 0, opacity: 1, scale: 1 }}
              animate={{
                x: p.dx,
                y: p.dy,
                rotate: p.rot,
                opacity: 0,
                scale: 0.5,
              }}
              transition={{
                duration: DURATION_S,
                delay: p.delay,
                ease: "easeOut",
              }}
              style={{
                position: "absolute",
                width: p.size,
                height: p.size,
                background: p.color,
                borderRadius: 2,
                boxShadow: `0 0 6px ${p.color}AA`,
              }}
            />
          ))}
        </div>
      ))}
    </div>,
    document.body,
  );
}
