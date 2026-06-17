import { useMediaQuery } from "./useMediaQuery";

/** Responsive breakpoint between mobile and desktop layouts (~768px). */
export const MOBILE_BREAKPOINT_PX = 768;

/**
 * True when the viewport is at least the desktop breakpoint width.
 * Desktop -> 3-pane layout; mobile -> full-screen map + bottom sheet.
 */
export function useIsDesktop(): boolean {
  return useMediaQuery(`(min-width: ${MOBILE_BREAKPOINT_PX}px)`);
}

/**
 * True on small touch devices (phones). Combines a coarse pointer with a narrow viewport so a
 * merely-resized desktop window isn't treated as mobile — only genuine handheld devices match.
 * Used to gate the experience behind a "switch to desktop" screen (this app is map-first and
 * needs the screen real estate).
 */
export function useIsMobile(): boolean {
  return useMediaQuery(`(max-width: ${MOBILE_BREAKPOINT_PX - 1}px) and (pointer: coarse)`);
}
