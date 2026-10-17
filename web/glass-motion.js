// Presentation only. The portal owns navigation, focus and business actions.
export function motionAllowed({ reduced = false, paused = false, hidden = false } = {}) {
  return !reduced && !paused && !hidden;
}
function initializeGlassMotion() {
  const root = document.documentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const fine = matchMedia('(pointer: fine)');
  const tabs = document.querySelector('.tabs');
  const lens = document.querySelector('.tab-lens');
  const stages = [...document.querySelectorAll('.mascot-stage')];
  const animations = new Set();
  let pointerFrame = null;
  let pointer = null;
  let highlighted = null;
  let litStage = null;
  let tabFrame = null;
  const allowed = () => motionAllowed({ reduced: reduced.matches && !root.classList.contains('motion-enabled'), paused: root.classList.contains('motion-paused'), hidden: document.hidden });
  function clearHighlight() {
    highlighted?.style.removeProperty('--sheen-x');
    highlighted?.style.removeProperty('--sheen-y');
    highlighted = null;
  }
  function clearStage() {
    litStage?.style.removeProperty('--look-x');
    litStage?.style.removeProperty('--look-y');
    litStage?.style.removeProperty('--light-x');
    litStage?.style.removeProperty('--light-y');
    litStage = null;
  }
  function clearPointer() {
    if (pointerFrame !== null) cancelAnimationFrame(pointerFrame);
    pointerFrame = null;
    pointer = null;
    clearHighlight();
    clearStage();
  }
  function cancelMotion() {
    animations.forEach(animation => animation.cancel());
    animations.clear();
    clearPointer();
    stages.forEach(stage => {
      stage.style.removeProperty('--look-x');
      stage.style.removeProperty('--look-y');
    });
  }
  function measureTab() {
    tabFrame = null;
    const active = tabs?.querySelector('button.active:not([hidden])');
    if (!active || !lens || !tabs.offsetWidth) return;
    lens.style.width = `${active.offsetWidth}px`;
    lens.style.height = `${active.offsetHeight}px`;
    lens.style.transform = `translate(${active.offsetLeft}px, ${active.offsetTop}px)`;
    tabs.dataset.measured = 'true';
  }
  function scheduleTab() {
    if (tabFrame === null) tabFrame = requestAnimationFrame(measureTab);
  }
  function animate(element, frames, options) {
    if (!allowed() || !element.animate) return;
    const animation = element.animate(frames, options);
    animations.add(animation);
    animation.finished.catch(() => {}).finally(() => { animations.delete(animation); animation.cancel(); });
  }
  new MutationObserver(scheduleTab).observe(tabs, { subtree: true, attributes: true, attributeFilter: ['class', 'hidden'] });
  const sizes = new ResizeObserver(scheduleTab);
  sizes.observe(tabs);
  tabs.querySelectorAll('button').forEach(button => sizes.observe(button));
  document.fonts?.ready.then(scheduleTab);
  document.querySelectorAll('.tab-panel').forEach(panel => new MutationObserver(() => {
    if (panel.hidden) return;
    clearPointer();
    animate(panel, [{ opacity: .35 }, { opacity: 1 }], { duration: 220, easing: 'ease-out' });
    [...panel.querySelectorAll('.service-action, .request-card, .task, .regulation-card')].slice(0, 16).forEach((card, index) => {
      animate(card, [{ opacity: 0, transform: 'translateY(12px) scale(.99)' }, { opacity: 1, transform: 'translateY(0) scale(1)' }], { duration: 420, delay: Math.min(index, 5) * 30, easing: 'cubic-bezier(.2,.75,.25,1)', fill: 'backwards' });
    });
  }).observe(panel, { attributes: true, attributeFilter: ['hidden'] }));
  document.addEventListener('pointermove', event => {
    if (!allowed() || !fine.matches || event.pointerType !== 'mouse') return;
    const stage = event.target.closest?.('.mascot-stage');
    const highlight = event.target.closest?.('.auth-card, .service-action, .a2ui-procedure, .start-chat-button');
    pointer = { stage, highlight, x: event.clientX, y: event.clientY };
    if (pointerFrame !== null) return;
    pointerFrame = requestAnimationFrame(() => {
      pointerFrame = null;
      if (!pointer || !allowed()) return;
      const { stage: target, highlight: card, x, y } = pointer;
      if (litStage !== target) clearStage();
      if (highlighted !== card) clearHighlight();
      if (target) {
        const bounds = target.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return;
        litStage = target;
        target.style.setProperty('--look-x', ((x - bounds.left) / bounds.width - .5).toFixed(3));
        target.style.setProperty('--look-y', ((y - bounds.top) / bounds.height - .5).toFixed(3));
        target.style.setProperty('--light-x', `${(x - bounds.left) / bounds.width * 100}%`);
        target.style.setProperty('--light-y', `${(y - bounds.top) / bounds.height * 100}%`);
      }
      if (card) {
        const bounds = card.getBoundingClientRect();
        if (!bounds.width || !bounds.height) return;
        highlighted = card;
        card.style.setProperty('--sheen-x', `${(x - bounds.left) / bounds.width * 100}%`);
        card.style.setProperty('--sheen-y', `${(y - bounds.top) / bounds.height * 100}%`);
      }
    });
  }, { passive: true });
  root.addEventListener('pointerleave', clearPointer);
  window.addEventListener('blur', clearPointer);
  fine.addEventListener('change', clearPointer);
  function synchronize() {
    root.classList.toggle('scene-suspended', document.hidden);
    if (!allowed()) cancelMotion();
    scheduleTab();
  }
  new MutationObserver(synchronize).observe(root, { attributes: true, attributeFilter: ['class'] });
  document.addEventListener('visibilitychange', synchronize);
  reduced.addEventListener('change', synchronize);
  window.addEventListener('pageshow', synchronize);
  window.addEventListener('pagehide', () => { cancelMotion(); if (tabFrame !== null) cancelAnimationFrame(tabFrame); tabFrame = null; });
  synchronize();
}
if (typeof document !== 'undefined') initializeGlassMotion();
