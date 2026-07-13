import type { CSSProperties, ReactElement, SVGProps } from 'react';

/**
 * Minimal inline SVG icon set ported verbatim from the Module 0 prototype.
 * Stroke-based, Geist-like. One component, one name prop. Keep the prototype
 * naming (sparkle, bolt, db, flow, target, permit, tag, investor, equity,
 * money, audit, tweak, etc.) — pages, rail, and topbar all rely on it.
 */
export type IconName =
  | 'search' | 'filter' | 'map' | 'layers' | 'bolt' | 'home' | 'user' | 'pin'
  | 'sparkle' | 'chat' | 'close' | 'check' | 'cross' | 'chevdown' | 'chevright'
  | 'up' | 'down' | 'thumbup' | 'thumbdown' | 'info' | 'shield' | 'bell' | 'settings' | 'db' | 'flow'
  | 'target' | 'permit' | 'tag' | 'building' | 'doc' | 'audit' | 'link'
  | 'play' | 'send' | 'tweak' | 'sun' | 'moon' | 'money' | 'equity'
  | 'investor' | 'export';

type Paths = Record<IconName, ReactElement>;

const paths: Paths = {
  search:     <><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></>,
  filter:     <path d="M3 6h18M7 12h10M10 18h4"/>,
  map:        <><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/></>,
  layers:     <><path d="m12 2 10 6-10 6L2 8z"/><path d="m2 14 10 6 10-6"/></>,
  bolt:       <path d="M13 2 4 13h7l-1 9 9-11h-7z"/>,
  home:       <><path d="m3 10 9-7 9 7"/><path d="M5 9v11h14V9"/></>,
  user:       <><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-7 8-7s8 3 8 7"/></>,
  pin:        <><path d="M12 22s7-8 7-13a7 7 0 1 0-14 0c0 5 7 13 7 13z"/><circle cx="12" cy="9" r="2.5"/></>,
  sparkle:    <path d="M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3 3M16 16l3 3M19 5l-3 3M8 16l-3 3"/>,
  chat:       <path d="M4 5h16v11H8l-4 4z"/>,
  close:      <path d="M6 6l12 12M18 6 6 18"/>,
  check:      <path d="m5 12 5 5L20 7"/>,
  cross:      <path d="M6 6l12 12M18 6 6 18"/>,
  chevdown:   <path d="m6 9 6 6 6-6"/>,
  chevright:  <path d="m9 6 6 6-6 6"/>,
  up:         <path d="m6 15 6-6 6 6"/>,
  down:       <path d="m6 9 6 6 6-6"/>,
  thumbup:    <><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2h0a3.13 3.13 0 0 1 3 3.88Z"/></>,
  thumbdown:  <><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22h0a3.13 3.13 0 0 1-3-3.88Z"/></>,
  info:       <><circle cx="12" cy="12" r="9"/><path d="M12 8v.01M11 12h1v5h1"/></>,
  shield:     <><path d="M12 2 4 5v7c0 5 3.5 8.5 8 10 4.5-1.5 8-5 8-10V5z"/><path d="m9 12 2 2 4-4"/></>,
  bell:       <><path d="M6 8a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 19a2 2 0 0 0 4 0"/></>,
  settings:   <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h0a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></>,
  db:         <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></>,
  flow:       <><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
  target:     <><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1" fill="currentColor"/></>,
  permit:     <><path d="M6 3h9l5 5v13H6z"/><path d="M15 3v5h5M9 14h6M9 17h4"/></>,
  tag:        <><path d="M20 12 12 20 3 11V3h8z"/><circle cx="8" cy="8" r="1.5"/></>,
  building:   <><path d="M4 21V5l8-3 8 3v16"/><path d="M9 9h.01M15 9h.01M9 13h.01M15 13h.01M9 17h.01M15 17h.01"/></>,
  doc:        <><path d="M7 3h8l4 4v14H7z"/><path d="M15 3v5h4M10 12h6M10 16h4"/></>,
  audit:      <><path d="M3 6h18M6 12h12M9 18h6"/></>,
  link:       <><path d="M9 15c2 2 5 2 7 0l3-3c2-2 2-5 0-7s-5-2-7 0l-1 1"/><path d="M15 9c-2-2-5-2-7 0l-3 3c-2 2-2 5 0 7s5 2 7 0l1-1"/></>,
  play:       <path d="M7 4v16l14-8z"/>,
  send:       <path d="M4 12 20 4l-4 16-4-7z"/>,
  tweak:      <><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h14M18 18h2"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="16" cy="18" r="2"/></>,
  sun:        <><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"/></>,
  moon:       <path d="M20 15A8 8 0 1 1 9 4a6 6 0 0 0 11 11z"/>,
  money:      <><circle cx="12" cy="12" r="9"/><path d="M15 9a3 3 0 0 0-6 0c0 3 6 1 6 4a3 3 0 0 1-6 0M12 6v2M12 16v2"/></>,
  equity:     <><path d="M3 21h18"/><path d="M5 21V9l7-5 7 5v12"/><path d="M9 21V13h6v8"/></>,
  investor:   <><path d="M4 21h16"/><rect x="6" y="14" width="3" height="7"/><rect x="10.5" y="9" width="3" height="12"/><rect x="15" y="5" width="3" height="16"/></>,
  export:     <><path d="M12 3v12M7 8l5-5 5 5M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></>,
};

interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}

export function Icon({ name, size = 16, className, style, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
      {...rest}
    >
      {paths[name] ?? paths.info}
    </svg>
  );
}
