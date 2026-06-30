import { useMemo } from "react";
import { motion } from "framer-motion";

/**
 * Lightweight DOM-particle confetti burst (#180 P0 close celebration).
 *
 * No external lib — renders ~28 colored squares from a single point,
 * each with a random angle/distance/rotation/scale, then fades out.
 * Total animation ~1.2s. Designed to overlay an absolutely-positioned
 * parent (TaskCard). `pointer-events: none` so it never steals clicks.
 *
 * Remounts every time `burstKey` changes (caller bumps the key to
 * replay). When `burstKey === 0` nothing is rendered, so the card has
 * zero footprint until the first close.
 */
export interface ConfettiBurstProps {
  burstKey: number;
  /** Particle count. Default 28. */
  count?: number;
  /** Burst spread radius in px (max). Default 140. */
  radius?: number;
  /** Total visible duration in seconds. Default 1.2. */
  durationS?: number;
}

const PALETTE = [
  "#f43f5e", // rose-500
  "#f97316", // orange-500
  "#eab308", // yellow-500
  "#10b981", // emerald-500
  "#3b82f6", // blue-500
  "#a855f7", // purple-500
  "#ec4899", // pink-500
];

interface Particle {
  dx: number;
  dy: number;
  rot: number;
  color: string;
  size: number;
  delay: number;
}

function buildParticles(count: number, radius: number): Particle[] {
  const out: Particle[] = [];
  for (let i = 0; i < count; i++) {
    const angle = Math.random() * Math.PI * 2;
    const dist = radius * (0.4 + Math.random() * 0.6);
    out.push({
      dx: Math.cos(angle) * dist,
      dy: Math.sin(angle) * dist - Math.random() * 20,
      rot: (Math.random() - 0.5) * 720,
      color: PALETTE[Math.floor(Math.random() * PALETTE.length)],
      size: 5 + Math.random() * 6,
      delay: Math.random() * 0.08,
    });
  }
  return out;
}

export function ConfettiBurst({
  burstKey,
  count = 28,
  radius = 140,
  durationS = 1.2,
}: ConfettiBurstProps) {
  // Re-seed particles whenever the caller bumps burstKey so each replay
  // looks different.
  const particles = useMemo(() => buildParticles(count, radius), [burstKey, count, radius]);

  if (burstKey === 0) return null;

  return (
    <div
      key={burstKey}
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 overflow-visible"
    >
      <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
        {particles.map((p, i) => (
          <motion.span
            key={i}
            initial={{ x: 0, y: 0, rotate: 0, opacity: 1, scale: 1 }}
            animate={{
              x: p.dx,
              y: p.dy,
              rotate: p.rot,
              opacity: 0,
              scale: 0.6,
            }}
            transition={{
              duration: durationS,
              delay: p.delay,
              ease: "easeOut",
            }}
            style={{
              position: "absolute",
              width: p.size,
              height: p.size,
              background: p.color,
              borderRadius: 2,
              boxShadow: `0 0 4px ${p.color}AA`,
            }}
          />
        ))}
      </div>
    </div>
  );
}
