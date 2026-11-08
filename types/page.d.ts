// Development only (jsconfig.json, `tsc -p jsconfig.json`): what the page's plain scripts use that the browser's own
// types do not say. Nothing here is loaded by Pulsar.

// Pulsar's window (macos/PulsarWindow.swift) injects these before the page runs; in a browser they are missing.
interface Window {
  PULSAR_APP?: any;
  PULSAR_NATIVE_WARP?: number;
  pulsarNative?: any;
  pulsarUnsaved?: any;
  webkit?: any;
}

// The page handles events by delegation: an event's target is always one of its HTML elements, and the elements it
// looks up are HTML elements (inputs, buttons, dialogs, images), while the DOM's own types only promise
// EventTarget, Node or Element. These members are the ones the page uses on them.
interface HTMLish {
  closest(selectors: string): HTMLElement | null;
  matches(selectors: string): boolean;
  contains(other: any): boolean;
  dataset: DOMStringMap;
  focus(options?: FocusOptions): void;
  click(): void;
  onclick: ((this: GlobalEventHandlers, ev: MouseEvent) => any) | null;
  onchange: ((this: GlobalEventHandlers, ev: Event) => any) | null;
  tabIndex: number;
  hidden: boolean;
  style: CSSStyleDeclaration;
  tagName: string;
  value: any;
  disabled: boolean;
  placeholder: string;
  src: string;
  open: boolean;
}
interface EventTarget extends HTMLish {}
interface Node extends HTMLish {}
interface Element extends HTMLish {}

interface Event {
  detail?: any;
  animationName?: string;
}
