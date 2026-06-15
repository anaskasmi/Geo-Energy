import type { LucideIcon, LucideProps } from "lucide-react";

/**
 * Central icon wrapper (design system §3). Renders a Lucide glyph with the app's defaults so
 * every icon is visually consistent: 18px (16 in dense rows), 2px stroke held even at small
 * sizes, `currentColor` so it inherits the control's text color, and `aria-hidden` because
 * icon-only controls carry their label on the *button* (never the SVG).
 *
 * Usage: `<Icon icon={Hexagon} />`, `<Icon icon={X} size={16} />`.
 * Pin Lucide v1 names — many 0.x names were renamed (HelpCircle → CircleHelp). Import the
 * glyph as a named barrel import (`import { Hexagon } from "lucide-react"`); deep paths don't
 * resolve and the dev pre-bundle (vite optimizeDeps) keeps the barrel fast.
 */
export interface IconProps extends Omit<LucideProps, "ref"> {
  icon: LucideIcon;
}

export function Icon({ icon: Glyph, size = 18, strokeWidth = 2, ...rest }: IconProps) {
  return <Glyph size={size} strokeWidth={strokeWidth} aria-hidden focusable={false} {...rest} />;
}
