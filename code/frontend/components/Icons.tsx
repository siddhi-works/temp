/** Decorative icons (stroke, currentColor). Always aria-hidden: the words beside them carry the meaning. */

import type { ReactNode } from "react";

function Svg({ children, size = 30 }: { children: ReactNode; size?: number }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {children}
    </svg>
  );
}

export const IconRead = () => (
  <Svg><path d="M8 4h11l5 5v19H8z" /><path d="M19 4v5h5" /><path d="M12 15h8M12 20h8M12 25h5" /></Svg>
);
export const IconCatch = () => (
  <Svg><rect x="11" y="3" width="10" height="16" rx="5" /><path d="M6 14a10 10 0 0 0 20 0M16 24v4M11 28h10" /></Svg>
);
export const IconCalendar = () => (
  <Svg><rect x="4" y="6" width="24" height="22" rx="3" /><path d="M4 13h24M10 3v6M22 3v6" /><path d="m12 20 3 3 5-6" /></Svg>
);
export const IconSettings = () => (
  <Svg><path d="M5 9h12M23 9h4M5 23h4M15 23h12" /><circle cx="20" cy="9" r="3" /><circle cx="12" cy="23" r="3" /></Svg>
);
export const IconShare = () => (
  <Svg><path d="M16 21V4M10 10l6-6 6 6" /><path d="M6 17v9h20v-9" /></Svg>
);
export const IconEar = () => (
  <Svg><path d="M5 12v8h5l7 6V6l-7 6z" /><path d="M22 11a7 7 0 0 1 0 10M25 7a12 12 0 0 1 0 18" /></Svg>
);
export const IconCheck = ({ size = 30 }: { size?: number }) => (
  <Svg size={size}><circle cx="16" cy="16" r="12" /><path d="m10.5 16.5 4 4 7-8" /></Svg>
);
export const IconAlert = ({ size = 30 }: { size?: number }) => (
  <Svg size={size}><path d="M16 4 2.5 27h27z" /><path d="M16 13v7M16 24h.01" /></Svg>
);
export const IconShield = ({ size = 30 }: { size?: number }) => (
  <Svg size={size}><path d="M16 3 6 7v8c0 7 4 11 10 14 6-3 10-7 10-14V7z" /><path d="M16 11v6M16 21h.01" /></Svg>
);
export const IconPlay = () => (
  <Svg><circle cx="16" cy="16" r="12" /><path d="m13 11 8 5-8 5z" fill="currentColor" /></Svg>
);
