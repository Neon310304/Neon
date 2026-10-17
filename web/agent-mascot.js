import { createDefinition, createPose } from "./vendor/open-mascot/definition.js";
import { createMascot } from "./vendor/open-mascot/web.js";

export function createAgentDefinition() {
  const definition = createDefinition({ name: "Mallow · MoveInMate", shape: "soft", color: "#62a4ec", eyeColor: "#254b78", stageColor: "#edf6ff" });
  Object.assign(definition.expressions, {
    "wave-left": { label: "Nhìn sang trái", pose: createPose({ pitch: -12, yaw: -28, roll: -9, lift: -8, gazeX: -6, gazeY: -3 }) },
    "wave-right": { label: "Nhìn sang phải", pose: createPose({ pitch: 10, yaw: 28, roll: 9, lift: 4, gazeX: 6, gazeY: -2 }) },
    "bounce-up": { label: "Nhún chào", pose: createPose({ pitch: -8, yaw: -8, roll: -3, squash: -0.07, lift: -16, gazeY: -4 }) },
  });
  definition.animations.idle = {
    label: "Đồng hành", playback: "loop", ambient: 0.65, blink: "bright",
    steps: [
      { expression: "neutral", holdMs: 150, transitionMs: 780, easing: "gentle" },
      { expression: "wave-left", holdMs: 300, transitionMs: 1050, easing: "gentle" },
      { expression: "wave-right", holdMs: 300, transitionMs: 1050, easing: "gentle" },
      { expression: "bounce-up", holdMs: 180, transitionMs: 850, easing: "spring" },
    ],
  };
  // Cursor/random look targets own gaze; expressions keep only breathing and emotion.
  Object.values(definition.expressions).forEach(expression => {
    Object.assign(expression.pose.blob, { yaw: 0, pitch: 0 });
    Object.assign(expression.pose.gaze, { x: 0, y: 0 });
    expression.motion = { body: "none", eyes: "none" };
  });
  Object.values(definition.animations).forEach(animation => { animation.ambient = 0; });
  definition.animations.thinking.steps.forEach(step => { step.holdMs = 550; step.transitionMs = 650; });
  definition.animations.happy.steps = [
    { expression: "bounce-up", holdMs: 200, transitionMs: 500, easing: "spring" },
    { expression: "beaming", holdMs: 500, transitionMs: 650, easing: "spring" },
    { expression: "wave-right", holdMs: 200, transitionMs: 550, easing: "gentle" },
  ];
  return definition;
}

let materialId = 0;
function applyAgentMaterial(svg) {
  svg.setAttribute("viewBox", "-80 -110 720 780");
  const namespace = "http://www.w3.org/2000/svg";
  const gradient = svg.ownerDocument.createElementNS(namespace, "radialGradient");
  gradient.id = `moveinmate-agent-material-${++materialId}`;
  gradient.setAttribute("cx", "28%");
  gradient.setAttribute("cy", "20%");
  gradient.setAttribute("r", "85%");
  for (const [offset, color] of [["0%", "#e3f7ff"], ["28%", "#acd9ff"], ["60%", "#62a4ec"], ["85%", "#3c75c4"], ["100%", "#285399"]]) {
    const stop = svg.ownerDocument.createElementNS(namespace, "stop");
    stop.setAttribute("offset", offset);
    stop.setAttribute("stop-color", color);
    gradient.append(stop);
  }
  svg.querySelector("defs").append(gradient);
  svg.querySelector("g > path").style.fill = `url(#${gradient.id})`;
  svg.classList.add("agent-projected-3d");
}

export function agentVisualState({ busy = false, failed = false, recentReply = false, focused = false } = {}) {
  if (busy) return "thinking";
  if (failed) return "attention";
  if (recentReply) return "answered";
  return focused ? "listening" : "idle";
}

export function canAnimateAgent({ available, visible, reducedMotion, paused }) {
  return Boolean(available && visible && !reducedMotion && !paused);
}

export function shouldReduceMotion({ preference = "auto", systemReduced = false } = {}) {
  return preference === "reduced" || (preference !== "full" && systemReduced);
}

export function readMotionPreference(storage) {
  try {
    const preference = storage.getItem("moveinmate:motion");
    if (["auto", "full", "reduced"].includes(preference)) return preference;
    return storage.getItem("moveinmate:reduce-motion") === "true" ? "reduced" : "auto";
  } catch { return "auto"; }
}

export function gazeForPointer(point, bounds) {
  const clamp = value => Math.max(-1, Math.min(1, value));
  return { x: clamp((point.x - bounds.left - bounds.width / 2) / Math.max(140, bounds.width)), y: clamp((point.y - bounds.top - bounds.height / 2) / Math.max(140, bounds.height)) };
}

export function pointerHitsMascot(point, bounds) {
  if (!point || !bounds || bounds.width <= 0 || bounds.height <= 0) return false;
  const horizontal = (point.x - bounds.left - bounds.width / 2) / (bounds.width / 2);
  const vertical = (point.y - bounds.top - bounds.height / 2) / (bounds.height / 2);
  return horizontal * horizontal + vertical * vertical <= 1;
}

export function smileEyePath(eye, curvature = .8) {
  if (Array.isArray(eye.smilePoints) && eye.smilePoints.length > 1) {
    return eye.smilePoints.map((point, index) => `${index ? "L" : "M"} ${point.x.toFixed(3)} ${point.y.toFixed(3)}`).join(" ");
  }
  const width = Math.max(5, eye.rx * 1.08);
  const rise = width * Math.max(.4, Math.min(1.2, curvature));
  const baseline = eye.cy + 3;
  return `M ${eye.cx - width} ${baseline} Q ${eye.cx} ${baseline - rise * 2} ${eye.cx + width} ${baseline}`;
}

export function smileVariation(random = Math.random) {
  const sample = () => Math.max(0, Math.min(1, Number(random()) || 0));
  return { curvature: .65 + sample() * .4, duration: 1400 + Math.round(sample() * 900) };
}

function createAgentSmile(target) {
  const svg = target.element;
  const blob = svg.querySelector("g > path");
  const eyes = [...svg.querySelectorAll("g > g > path")].map(natural => {
    const smiling = svg.ownerDocument.createElementNS("http://www.w3.org/2000/svg", "path");
    natural.classList.add("agent-natural-eyes");
    smiling.classList.add("agent-smile-eyes");
    smiling.setAttribute("fill", "none");
    smiling.setAttribute("stroke-linecap", "round");
    natural.parentElement.append(smiling);
    return smiling;
  });
  let state = "idle";
  let hovered = false;
  let smiling = false;
  let smileTimer = null;
  let lastSmileEnd = -Infinity;
  let variation = smileVariation();

  function renderEyes() {
    target.getScene().eyes.forEach((eye, index) => {
      eyes[index].setAttribute("d", smileEyePath(eye, variation.curvature));
      eyes[index].setAttribute("stroke", eye.fill);
      eyes[index].setAttribute("stroke-width", Math.max(5, eye.rx * .4));
      eyes[index].setAttribute("stroke-linejoin", "round");
    });
  }

  function stopSmile() {
    clearTimeout(smileTimer);
    smileTimer = null;
    if (smiling) lastSmileEnd = performance.now();
    smiling = false;
    svg.removeAttribute("data-agent-smiling");
  }

  function touchesBody(point) {
    if (!pointerHitsMascot(point, blob.getBoundingClientRect())) return false;
    const matrix = blob.getScreenCTM();
    if (!matrix || !blob.isPointInFill) return true;
    const localPoint = svg.createSVGPoint();
    localPoint.x = point.x;
    localPoint.y = point.y;
    return blob.isPointInFill(localPoint.matrixTransform(matrix.inverse()));
  }

  const unsubscribe = target.subscribe(() => { if (smiling) renderEyes(); });
  renderEyes();
  return {
    lookAt(point) {
      const touching = Boolean(point && touchesBody(point));
      if (touching && !hovered && !smiling && !["thinking", "attention"].includes(state) && performance.now() - lastSmileEnd >= 700) {
        variation = smileVariation();
        smiling = true;
        renderEyes();
        svg.dataset.agentSmiling = "true";
        smileTimer = setTimeout(stopSmile, variation.duration);
      }
      hovered = touching;
    },
    setState(nextState) {
      state = nextState;
      if (["thinking", "attention"].includes(state)) stopSmile();
    },
    clear() {
      hovered = false;
      stopSmile();
    },
    destroy() {
      stopSmile();
      unsubscribe();
    },
  };
}

function initializeAgentAppearance() {
  const launcher = document.getElementById("chat-launcher");
  const widget = document.getElementById("chat-widget");
  const input = document.getElementById("chat-input");
  const messages = document.getElementById("messages");
  const motionButton = document.getElementById("agent-motion-toggle");
  const stateLabel = document.getElementById("agent-state-label");
  const managerLauncher = document.getElementById("manager-agent-launcher");
  const managerWidget = document.getElementById("manager-agent-dialog");
  const managerInput = document.getElementById("manager-agent-input");
  const managerMessages = document.getElementById("manager-agent-messages");
  if (!launcher || !widget || !input || !messages || !motionButton) return;
  const motionHome = motionButton.parentElement;
  const motionAnchor = motionButton.nextElementSibling;
  const chatActions = widget.querySelector(".chat-widget-actions");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const coarsePointer = window.matchMedia("(pointer: coarse), (hover: none)");
  const definition = createAgentDefinition();
  const hosts = Array.from(document.querySelectorAll("[data-agent-mascot]"));
  const fallbacks = new Map(hosts.map(host => [host, host.firstElementChild.cloneNode(true)]));
  const animations = { idle: "idle", listening: "listening", thinking: "thinking", attention: "curious", answered: "happy" };
  const labels = { idle: "Đồng hành cùng bạn, từng thủ tục", listening: "Bạn cứ viết điều cần hỗ trợ", thinking: "Đang chuẩn bị câu trả lời", attention: "Bạn có thể thử lại hoặc mở biểu mẫu", answered: "Đã trả lời · bạn kiểm tra lại nhé" };
  let preference = "auto";
  const minimizesMotion = () => shouldReduceMotion({ preference, systemReduced: reducedMotion.matches });
  let activeHost = null;
  let controller = null;
  let activeState = null;
  const smiles = new Map();
  const sceneControllers = new Map();
  const sceneVisibility = new Map();
  const sceneHosts = hosts.filter(host => ['auth', 'hero', 'welcome', 'start'].includes(host.dataset.agentMascot));
  const sceneObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => sceneVisibility.set(entry.target, entry.isIntersecting));
    scheduleUpdate();
  }, { threshold: .08 });
  sceneHosts.forEach(host => sceneObserver.observe(host));
  let acknowledgedReply = messages.querySelector(".message.assistant:last-of-type");
  let celebrateUntil = 0;
  let celebrateTimer = null;
  let updateFrame = null;
  let gazeTimer = null;
  let pointerPosition = null;
  let pointerFrame = null;
  let mobileLook = null;

  try { preference = readMotionPreference(localStorage); } catch {}

  function removeActiveMascot() {
    smiles.get(controller)?.destroy();
    smiles.delete(controller);
    controller?.destroy();
    if (activeHost) activeHost.replaceChildren(fallbacks.get(activeHost).cloneNode(true));
    activeHost = null;
    activeState = null;
    controller = null;
  }

  function removeSceneMascots() {
    sceneControllers.forEach((scene, host) => {
      smiles.get(scene.controller)?.destroy();
      smiles.delete(scene.controller);
      scene.controller.destroy();
      host.replaceChildren(fallbacks.get(host).cloneNode(true));
    });
    sceneControllers.clear();
  }

  function synchronizeScenes(state) {
    sceneHosts.forEach(host => {
      const available = Boolean(host.offsetWidth) && sceneVisibility.get(host);
      const scene = sceneControllers.get(host);
      if (!canAnimateAgent({ available, visible: !document.hidden, reducedMotion: minimizesMotion(), paused: false })) {
        if (scene) {
          smiles.get(scene.controller)?.destroy();
          smiles.delete(scene.controller);
          scene.controller.destroy();
          host.replaceChildren(fallbacks.get(host).cloneNode(true));
          sceneControllers.delete(host);
        }
        return;
      }
      const sceneState = host.dataset.agentMascot === 'auth' ? 'idle' : state;
      if (!scene) {
        const controller = createMascot(host, { definition, animation: animations[sceneState], autoplay: false, reducedMotion: false });
        applyAgentMaterial(controller.element);
        smiles.set(controller, createAgentSmile(controller));
        smiles.get(controller).setState(sceneState);
        controller.element.setAttribute('aria-hidden', 'true');
        controller.element.removeAttribute('role');
        controller.element.removeAttribute('aria-label');
        controller.play(animations[sceneState]);
        sceneControllers.set(host, { controller, state: sceneState });
      } else if (scene.state !== sceneState) {
        smiles.get(scene.controller).setState(sceneState);
        scene.controller.play(animations[sceneState]);
        scene.state = sceneState;
      }
    });
  }

  function synchronizeAppearance() {
    updateFrame = null;
    const minimizeMotion = minimizesMotion();
    document.documentElement.classList.toggle("motion-paused", minimizeMotion);
    document.documentElement.classList.toggle("motion-enabled", preference === "full");
    motionButton.setAttribute("aria-pressed", String(minimizeMotion));
    const motionAction = minimizeMotion ? "Bật hiệu ứng" : "Tắt hiệu ứng";
    motionButton.title = minimizeMotion && reducedMotion.matches
      ? "Bật hiệu ứng riêng trên web · thiết bị đang yêu cầu giảm chuyển động"
      : motionAction;
    motionButton.setAttribute("aria-label", motionAction);
    motionButton.querySelector(".motion-label").textContent = motionAction;
    const managerActive = Boolean(managerLauncher && !managerLauncher.hidden);
    const activeLauncher = managerActive ? managerLauncher : launcher;
    const activeWidget = managerActive ? managerWidget : widget;
    const activeInput = managerActive ? managerInput : input;
    const activeMessages = managerActive ? managerMessages : messages;
    const available = !activeLauncher.hidden;
    const opened = available && (managerActive ? managerWidget.open : widget.classList.contains("open"));
    const activeActions = managerActive ? managerWidget.querySelector(".chat-widget-actions") : chatActions;
    if (opened && activeActions && motionButton.parentElement !== activeActions) activeActions.prepend(motionButton);
    else if (!opened && motionButton.parentElement !== motionHome) motionHome.insertBefore(motionButton, motionAnchor);
    const busy = activeInput.disabled || Boolean(activeMessages.querySelector('[aria-busy="true"], .typing-message')) || (managerActive && document.getElementById("manager-agent-form").getAttribute("aria-busy") === "true");
    const replies = activeMessages.querySelectorAll(managerActive ? ".manager-agent-assistant" : ".message.assistant:not(.typing-message)");
    const reply = replies[replies.length - 1];
    if (!busy && reply && reply !== acknowledgedReply) {
      acknowledgedReply = reply;
      celebrateUntil = performance.now() + 2600;
      clearTimeout(celebrateTimer);
      celebrateTimer = setTimeout(scheduleUpdate, 2700);
    }
    const state = agentVisualState({ busy, failed: reply?.dataset.agentError === "true", recentReply: performance.now() < celebrateUntil, focused: opened && document.activeElement === activeInput });
    if (stateLabel) {
      stateLabel.textContent = minimizeMotion && ['idle', 'listening'].includes(state) ? "Hiệu ứng tắt" : labels[state];
      stateLabel.title = minimizeMotion ? `${labels[state]} · Bấm Bật hiệu ứng để tiếp tục chuyển động` : labels[state];
    }
    synchronizeScenes(state);
    activeWidget.dataset.agentState = state;
    activeLauncher.dataset.agentState = state;
    const host = document.querySelector(`[data-agent-mascot="${managerActive ? "manager-" : ""}${opened ? "header" : "launcher"}"]`);
    if (!host || !canAnimateAgent({ available, visible: !document.hidden, reducedMotion: minimizeMotion, paused: false })) {
      removeActiveMascot();
      return;
    }
    if (activeHost !== host) {
      removeActiveMascot();
      controller = createMascot(host, { definition, animation: animations[state], autoplay: false, reducedMotion: false });
      applyAgentMaterial(controller.element);
      smiles.set(controller, createAgentSmile(controller));
      controller.element.setAttribute("aria-hidden", "true");
      controller.element.removeAttribute("role");
      controller.element.removeAttribute("aria-label");
      activeHost = host;
    }
    if (activeState !== state) {
      smiles.get(controller).setState(state);
      controller.play(animations[state]);
      activeState = state;
    }
  }

  function scheduleUpdate() {
    if (document.hidden) {
      removeActiveMascot();
      removeSceneMascots();
      clearTimeout(gazeTimer);
      gazeTimer = null;
      return;
    }
    if (updateFrame === null) updateFrame = requestAnimationFrame(() => { synchronizeAppearance(); synchronizeGaze(); });
  }

  const observer = new MutationObserver(scheduleUpdate);
  ['auth-screen', 'main-content'].forEach(id => observer.observe(document.getElementById(id), { attributes: true, attributeFilter: ['hidden'] }));
  observer.observe(launcher, { attributes: true, attributeFilter: ["hidden", "aria-expanded"] });
  observer.observe(widget, { attributes: true, attributeFilter: ["hidden", "class"] });
  observer.observe(input, { attributes: true, attributeFilter: ["disabled"] });
  observer.observe(messages, { childList: true, subtree: true, attributes: true, attributeFilter: ["aria-busy", "data-agent-error"] });
  if (managerLauncher && managerWidget && managerMessages && managerInput) {
    observer.observe(managerLauncher, { attributes: true, attributeFilter: ["hidden", "aria-expanded"] });
    observer.observe(managerWidget, { attributes: true, attributeFilter: ["open", "class"] });
    observer.observe(managerMessages, { childList: true, subtree: true });
    observer.observe(document.getElementById("manager-agent-form"), { attributes: true, attributeFilter: ["aria-busy"] });
    managerInput.addEventListener("focus", scheduleUpdate);
    managerInput.addEventListener("blur", scheduleUpdate);
  }
  input.addEventListener("focus", scheduleUpdate);
  input.addEventListener("blur", scheduleUpdate);
  document.addEventListener("visibilitychange", scheduleUpdate);
  reducedMotion.addEventListener("change", scheduleUpdate);
  window.addEventListener("pageshow", scheduleUpdate);
  window.addEventListener("pagehide", () => {
    if (updateFrame !== null) cancelAnimationFrame(updateFrame);
    updateFrame = null;
    clearTimeout(celebrateTimer);
    clearTimeout(gazeTimer);
    gazeTimer = null;
    if (pointerFrame !== null) cancelAnimationFrame(pointerFrame);
    pointerFrame = null;
    removeActiveMascot();
    removeSceneMascots();
  });
  motionButton.addEventListener("click", () => {
    preference = minimizesMotion() ? "full" : "reduced";
    try { localStorage.setItem("moveinmate:motion", preference); } catch {}
    scheduleUpdate();
  });
  window.addEventListener("storage", event => {
    if (!["moveinmate:motion", "moveinmate:reduce-motion", null].includes(event.key)) return;
    try { preference = readMotionPreference(localStorage); } catch { preference = "auto"; }
    scheduleUpdate();
  });
  function applyLook() {
    const targets = [...sceneControllers.entries()].map(([host, scene]) => [host, scene.controller]);
    if (controller && activeHost) targets.push([activeHost, controller]);
    for (const [host, target] of targets) {
      if (coarsePointer.matches && mobileLook) target.setLookTarget(mobileLook);
      else if (!coarsePointer.matches && pointerPosition) target.setLookTarget(gazeForPointer(pointerPosition, host.getBoundingClientRect()));
      else target.clearLookTarget();
      smiles.get(target)?.lookAt(!coarsePointer.matches && !document.hidden && !minimizesMotion() ? pointerPosition : null);
    }
  }

  function synchronizeGaze() {
    const active = !document.hidden && !minimizesMotion() && (controller || sceneControllers.size);
    if (!active || !coarsePointer.matches) {
      clearTimeout(gazeTimer);
      gazeTimer = null;
      mobileLook = null;
    } else if (gazeTimer === null) {
      mobileLook = { x: Math.random() * 1.5 - .75, y: Math.random() * 1.1 - .55 };
      gazeTimer = setTimeout(() => { gazeTimer = null; synchronizeGaze(); }, 2200 + Math.random() * 2200);
    }
    if (active) applyLook();
  }

  window.addEventListener("pointermove", event => {
    if (event.pointerType !== "mouse" || coarsePointer.matches || minimizesMotion() || document.hidden) return;
    pointerPosition = { x: event.clientX, y: event.clientY };
    if (pointerFrame === null) pointerFrame = requestAnimationFrame(() => { pointerFrame = null; applyLook(); });
  }, { passive: true });
  function clearPointer() {
    pointerPosition = null;
    smiles.forEach(reaction => reaction.clear());
    applyLook();
  }
  document.documentElement.addEventListener("pointerleave", clearPointer);
  window.addEventListener("scroll", () => { if (pointerPosition && !coarsePointer.matches) applyLook(); }, { passive: true });
  window.addEventListener("blur", clearPointer);
  coarsePointer.addEventListener("change", scheduleUpdate);
  synchronizeAppearance();
  synchronizeGaze();
}

if (typeof document !== "undefined") {
  initializeAgentAppearance();
}
