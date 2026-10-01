/**
 * The delegated tooltip controller (2026-09-21 audit critic-08;
 * deviation:tooltip). ui/Tooltip.tsx marks each trigger with `data-tooltip`
 * (its hidden description's id) and loads this module at idle, so none of the
 * behaviour below weighs on the initial bundle. One set of capture listeners
 * on `document` drives ONE popup: a `role="tooltip"` `popover="manual"`
 * element in the top layer, placed like the evidence hover card
 * (ui/anchorPlacement.ts: CSS anchor positioning where supported, recorded as
 * `data-anchored`, measured viewport coordinates elsewhere).
 *
 *  - Hover opens after SHOW_DELAY_MS, or at once inside the reopen grace the
 *    evidence hover card shares (skip-delay); a touch pointer never opens it.
 *  - Focus opens at once, but only keyboard-originated focus
 *    (`:focus-visible`, and never right after a pointer press).
 *  - It closes on pointer leave, blur, any pointer press, Escape (an
 *    escapeStack layer while open, so Esc closes it before any lower layer
 *    and focus stays on the trigger), a trigger unmount (a MutationObserver
 *    runs only while a tooltip is open or pending; the pending timer is
 *    cleared too) and, in rect mode, scroll or resize. The exit fades over
 *    the popup's own transition.
 *  - While open the trigger's `aria-describedby` names the popup in place of
 *    its hidden description; closing restores it.
 */
import { exitTransitionMs } from '../../hooks/useExitRetained';
import { pushEscapeLayer } from '../../lib/escapeStack';
import {
  inReopenGrace,
  markHoverClosed,
  measuredPlacement,
  SHOW_DELAY_MS,
  supportsAnchorPositioning,
} from './anchorPlacement';
import { TOOLTIP_TRIGGER_SELECTOR } from './Tooltip';
import './Tooltip.css';

export const TOOLTIP_POPUP_ID = 'mip-tooltip';
const ANCHOR_NAME = '--mip-tooltip-anchor';

let popup: HTMLDivElement | null = null;
/** The trigger whose tooltip is showing, and the one waiting out the delay. */
let current: HTMLElement | null = null;
let pending: HTMLElement | null = null;
/** The trigger carrying `anchor-name` (kept through the exit fade). */
let anchored: HTMLElement | null = null;
let timer: number | null = null;
let finishTimer: number | null = null;
let popEscape: (() => void) | null = null;
let lastModality: 'keyboard' | 'pointer' | null = null;
/** Watches for the open or pending trigger leaving the document (an unmount). */
let watcher: MutationObserver | null = null;

function triggerOf(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element ? target.closest<HTMLElement>(TOOLTIP_TRIGGER_SELECTOR) : null;
}

function swapDescribedBy(trigger: HTMLElement, from: string, to: string): void {
  const ids = (trigger.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean);
  trigger.setAttribute('aria-describedby', ids.map((id) => (id === from ? to : id)).join(' '));
}

function clearPending(): void {
  if (timer !== null) window.clearTimeout(timer);
  timer = null;
  pending = null;
  if (!current) unwatch();
}

function watch(): void {
  if (watcher || typeof MutationObserver === 'undefined') return;
  watcher = new MutationObserver(() => {
    if (pending && !pending.isConnected) clearPending();
    if (current && !current.isConnected) close();
  });
  watcher.observe(document.body, { childList: true, subtree: true });
}

function unwatch(): void {
  watcher?.disconnect();
  watcher = null;
}

function releaseAnchor(): void {
  anchored?.style.removeProperty('anchor-name');
  anchored = null;
}

function ensurePopup(): HTMLDivElement {
  if (popup?.isConnected) return popup;
  popup = document.createElement('div');
  popup.id = TOOLTIP_POPUP_ID;
  popup.setAttribute('role', 'tooltip');
  popup.setAttribute('popover', 'manual');
  popup.hidden = true;
  document.body.append(popup);
  return popup;
}

function onMove(): void {
  close();
}

function show(trigger: HTMLElement): void {
  clearPending();
  const description = document.getElementById(trigger.dataset.tooltip ?? '');
  if (!trigger.isConnected || !description) return;
  if (current && current !== trigger) close();
  if (finishTimer !== null) window.clearTimeout(finishTimer);
  finishTimer = null;
  const tip = ensurePopup();
  const text = description.querySelector('[data-tooltip-text]');
  tip.replaceChildren(...Array.from(text?.childNodes ?? [], (node) => node.cloneNode(true)));
  const shortcut = trigger.dataset.tooltipShortcut;
  if (shortcut) {
    const kbd = document.createElement('kbd');
    kbd.className = 'tooltip__kbd';
    kbd.textContent = shortcut;
    tip.append(kbd);
  }
  tip.className = 'tooltip';
  tip.removeAttribute('style');
  tip.hidden = false;
  if (tip.showPopover && !tip.matches(':popover-open')) tip.showPopover();
  current = trigger;
  swapDescribedBy(trigger, description.id, TOOLTIP_POPUP_ID);
  releaseAnchor();
  if (supportsAnchorPositioning()) {
    tip.setAttribute('data-anchored', '');
    trigger.style.setProperty('anchor-name', ANCHOR_NAME);
    tip.style.setProperty('position-anchor', ANCHOR_NAME);
    anchored = trigger;
  } else {
    tip.removeAttribute('data-anchored');
    const placement = measuredPlacement(trigger, tip);
    tip.classList.add(`tooltip--${placement.side}`);
    tip.style.left = `${placement.x}px`;
    tip.style.top = `${placement.y}px`;
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
  }
  popEscape ??= pushEscapeLayer(() => {
    close();
    return true;
  });
  watch();
}

function close(): void {
  clearPending();
  const trigger = current;
  const tip = popup;
  if (!trigger || !tip) return;
  current = null;
  unwatch();
  markHoverClosed();
  popEscape?.();
  popEscape = null;
  window.removeEventListener('scroll', onMove, true);
  window.removeEventListener('resize', onMove);
  swapDescribedBy(trigger, TOOLTIP_POPUP_ID, trigger.dataset.tooltip ?? '');
  tip.classList.add('is-closing');
  const finish = () => {
    finishTimer = null;
    if (current) return;
    if (tip.hidePopover && tip.matches(':popover-open')) tip.hidePopover();
    tip.hidden = true;
    tip.classList.remove('is-closing');
    releaseAnchor();
  };
  const exitMs = exitTransitionMs(tip);
  if (exitMs > 0) finishTimer = window.setTimeout(finish, exitMs + 20);
  else finish();
}

function keyboardFocus(element: HTMLElement): boolean {
  if (lastModality === 'pointer') return false;
  try {
    return element.matches(':focus-visible');
  } catch {
    // A DOM without the selector: fall back to the last input modality.
    return lastModality === 'keyboard';
  }
}

function onPointerOver(event: PointerEvent): void {
  const trigger = triggerOf(event.target);
  if (!trigger || event.pointerType === 'touch' || trigger === current || trigger === pending) return;
  clearPending();
  if (current || inReopenGrace()) {
    show(trigger);
    return;
  }
  pending = trigger;
  timer = window.setTimeout(() => show(trigger), SHOW_DELAY_MS);
  watch();
}

function onPointerOut(event: PointerEvent): void {
  const trigger = triggerOf(event.target);
  if (!trigger || (event.relatedTarget instanceof Node && trigger.contains(event.relatedTarget))) return;
  if (trigger === pending) clearPending();
  if (trigger === current) close();
}

function onPointerDown(): void {
  lastModality = 'pointer';
  clearPending();
  close();
}

function onFocusIn(event: FocusEvent): void {
  const trigger = triggerOf(event.target);
  if (trigger && trigger === event.target && keyboardFocus(trigger)) show(trigger);
}

function onFocusOut(event: FocusEvent): void {
  const trigger = triggerOf(event.target);
  if (trigger && trigger === event.target && (trigger === current || trigger === pending)) {
    clearPending();
    close();
  }
}

let installed = false;

/** Install the delegated listeners once (importing this module does it). */
export function installTooltipController(): void {
  if (installed || typeof document === 'undefined') return;
  installed = true;
  document.addEventListener('pointerover', onPointerOver, true);
  document.addEventListener('pointerout', onPointerOut, true);
  document.addEventListener('pointerdown', onPointerDown, true);
  document.addEventListener('mousedown', () => { lastModality = 'pointer'; }, true);
  document.addEventListener('keydown', () => { lastModality = 'keyboard'; }, true);
  document.addEventListener('focusin', onFocusIn, true);
  document.addEventListener('focusout', onFocusOut, true);
  // The one popup exists (hidden) from install on: a ready marker too.
  if (document.body) ensurePopup();
}

installTooltipController();
