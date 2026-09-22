import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, vi } from 'vitest';

// jsdom has no real matchMedia. Default every test to "prefers-reduced-motion:
// reduce" so useRouteReplay's animation never starts (and never leaves
// dangling setTimeout chains) unless a test explicitly opts in by stubbing
// this differently.
beforeEach(() => {
  window.matchMedia = vi.fn().mockImplementation(query => ({
    matches: true, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }));
});

// jsdom does not implement HTMLDialogElement.showModal/close. Polyfill the
// minimum: reflect the `open` attribute and fire the native `close` event, so
// components using <dialog> behave the same in tests as in a real browser.
if (typeof HTMLDialogElement !== 'undefined' && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal() { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute('open');
    this.dispatchEvent(new Event('close'));
  };
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
