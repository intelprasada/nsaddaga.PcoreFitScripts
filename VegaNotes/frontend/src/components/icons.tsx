import type { SVGProps } from "react";

/**
 * Inline stroke icons (currentColor, 1.75 stroke) — no icon-library dependency,
 * so nothing is added to the bundle and glyphs never fall back to tofu boxes
 * the way emoji did. 16px default; size via the `size` prop or `className`.
 */

type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 16, children, ...rest }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconMyTasks = (p: IconProps) => (
  <Svg {...p}><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></Svg>
);
export const IconEditor = (p: IconProps) => (
  <Svg {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /><path d="M8 13h6" /><path d="M8 17h4" /></Svg>
);
export const IconKanban = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="3" width="6" height="14" rx="1" /><rect x="9.5" y="3" width="6" height="9" rx="1" transform="translate(0.5 0)" /><path d="M10 3h4a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" /><path d="M17 3h4a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" /></Svg>
);
export const IconAgenda = (p: IconProps) => (
  <Svg {...p}><path d="M8 6h13" /><path d="M8 12h13" /><path d="M8 18h13" /><path d="M3 6h.01" /><path d="M3 12h.01" /><path d="M3 18h.01" /></Svg>
);
export const IconTimeline = (p: IconProps) => (
  <Svg {...p}><path d="M3 3v18h18" /><path d="M7 15l3-3 3 2 5-6" /></Svg>
);
export const IconCalendar = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4" /><path d="M8 2v4" /><path d="M3 10h18" /></Svg>
);
export const IconGraph = (p: IconProps) => (
  <Svg {...p}><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="M8.6 13.5l6.8 4" /><path d="M15.4 6.5l-6.8 4" /></Svg>
);
export const IconArchive = (p: IconProps) => (
  <Svg {...p}><rect x="3" y="4" width="18" height="4" rx="1" /><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8" /><path d="M10 12h4" /></Svg>
);
export const IconMe = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></Svg>
);
export const IconHelp = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="10" /><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3" /><path d="M12 17h.01" /></Svg>
);
export const IconDashboard = (p: IconProps) => (
  <Svg {...p}><path d="M3 3v18h18" /><rect x="7" y="10" width="3" height="8" rx="0.5" /><rect x="12" y="6" width="3" height="12" rx="0.5" /><rect x="17" y="13" width="3" height="5" rx="0.5" /></Svg>
);
export const IconAdmin = (p: IconProps) => (
  <Svg {...p}><path d="M12 2l8 4v6c0 5-3.4 8.4-8 10-4.6-1.6-8-5-8-10V6l8-4Z" /><path d="M9 12l2 2 4-4" /></Svg>
);

export const IconSearch = (p: IconProps) => (
  <Svg {...p}><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></Svg>
);
export const IconChevronDown = (p: IconProps) => (
  <Svg {...p}><path d="M6 9l6 6 6-6" /></Svg>
);
export const IconKey = (p: IconProps) => (
  <Svg {...p}><circle cx="7.5" cy="15.5" r="4.5" /><path d="M10.5 12.5L21 2" /><path d="M16 7l3 3" /></Svg>
);
export const IconLogout = (p: IconProps) => (
  <Svg {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /></Svg>
);
export const IconRefresh = (p: IconProps) => (
  <Svg {...p}><path d="M3 12a9 9 0 0 1 15-6.7L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-15 6.7L3 16" /><path d="M3 21v-5h5" /></Svg>
);
export const IconTarget = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1.5" /></Svg>
);
