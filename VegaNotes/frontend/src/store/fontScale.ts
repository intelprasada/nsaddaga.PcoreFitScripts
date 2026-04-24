import { create } from "zustand";
import { persist } from "zustand/middleware";

export type FontScale = "sm" | "md" | "lg";

/** px values for each scale level */
export const FONT_SCALE_MAP: Record<FontScale, { title: string; ar: string; bubble: string }> = {
  sm: { title: "text-[12px]", ar: "text-[11px]", bubble: "text-[11px]" },
  md: { title: "text-sm",     ar: "text-[13px]", bubble: "text-[13px]" },
  lg: { title: "text-base",   ar: "text-[15px]", bubble: "text-[15px]" },
};

interface FontScaleState {
  scale: FontScale;
  setScale: (s: FontScale) => void;
}

export const useFontScale = create<FontScaleState>()(
  persist(
    (set) => ({
      scale: "md",
      setScale: (scale) => set({ scale }),
    }),
    { name: "veganotes-font-scale" },
  ),
);
