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
