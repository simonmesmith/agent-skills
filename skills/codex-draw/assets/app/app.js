const $ = (id) => document.getElementById(id);

const state = {
  scene: null,
  tool: "select",
  drag: null,
  dirty: false,
  editingTextId: null,
  focusTextEditor: false,
  renamingObjectId: null,
  draggingObjectId: null,
  lastTextClick: null,
  lastRevision: -1,
  lastSavedAt: null,
  suppressPollUntil: 0,
};

const canvas = $("canvas");
const statusEl = $("status");
const deleteButton = $("deleteButton");
const fillInput = $("fillInput");
const strokeInput = $("strokeInput");
const strokeWidthInput = $("strokeWidthInput");
const opacityInput = $("opacityInput");
const rotationInput = $("rotationInput");
const xInput = $("xInput");
const yInput = $("yInput");
const widthInput = $("widthInput");
const heightInput = $("heightInput");
const objectsEl = $("objects");

function setStatus(text, title = text) {
  statusEl.textContent = text;
  statusEl.title = title;
}

function savePath() {
  return compactPath(state.scene?.paths?.scene || "scene.json");
}

function fullSavePath() {
  return state.scene?.paths?.scene || "scene.json";
}

function compactPath(path) {
  const value = String(path || "");
  const workspace = state.scene?.paths?.workspace;
  if (workspace && value.startsWith(`${workspace}/`)) return `${workspace.split("/").pop()}/${value.slice(workspace.length + 1)}`;
  const marker = "/drawings/";
  const index = value.indexOf(marker);
  if (index !== -1) return value.slice(index + 1);
  return value;
}

function saveTime() {
  if (!state.lastSavedAt) return "not yet";
  return state.lastSavedAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

function saveStatus(prefix = "Saved") {
  if (prefix === "Saved") return `${savePath()} - last saved ${saveTime()}`;
  return `${prefix} - ${savePath()} - last saved ${saveTime()}`;
}

function fullSaveStatus(prefix = "Saved") {
  if (prefix === "Saved") return `${fullSavePath()} - last saved ${saveTime()}`;
  return `${prefix} - ${fullSavePath()} - last saved ${saveTime()}`;
}

function markSaved(scene, prefix = "Saved") {
  state.scene = scene;
  state.lastRevision = scene.revision || 0;
  state.lastSavedAt = new Date();
  state.dirty = false;
  setStatus(saveStatus(prefix), fullSaveStatus(prefix));
}

function setDirty(dirty) {
  state.dirty = dirty;
  if (dirty) setStatus(saveStatus("Unsaved changes"), fullSaveStatus("Unsaved changes"));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function loadScene(force = false) {
  if (!force && Date.now() < state.suppressPollUntil) return;
  if (!force && state.dirty) return;
  try {
    const scene = await api("/api/scene");
    if (!state.scene || scene.revision !== state.lastRevision || force) {
      markSaved(scene, "Saved");
      render();
    }
  } catch (error) {
    setStatus(`API unavailable: ${error.message}`);
  }
}

function sortedObjects() {
  return [...(state.scene?.objects || [])].sort((a, b) => (a.zIndex || 0) - (b.zIndex || 0));
}

function selectedIds() {
  return new Set(state.scene?.selection || []);
}

function selectedObject() {
  const ids = state.scene?.selection || [];
  if (ids.length !== 1) return null;
  return state.scene.objects.find((object) => object.id === ids[0]) || null;
}

function objectBounds(object) {
  if (object.type === "line" || object.type === "arrow") {
    const x1 = Number(object.x || 0);
    const y1 = Number(object.y || 0);
    const x2 = Number(object.x2 || x1 + 120);
    const y2 = Number(object.y2 || y1);
    return {
      x: Math.min(x1, x2),
      y: Math.min(y1, y2),
      width: Math.abs(x2 - x1),
      height: Math.abs(y2 - y1),
    };
  }
  if (object.type === "text") {
    return {
      x: Number(object.x || 0),
      y: Number(object.y || 0) - Number(object.fontSize || 24),
      width: Math.max(Number(object.width || 0), textWidth(object)),
      height: Number(object.fontSize || 24) * 1.3,
    };
  }
  return {
    x: Number(object.x || 0),
    y: Number(object.y || 0),
    width: Number(object.width || 1),
    height: Number(object.height || 1),
  };
}

function textWidth(object, text = object.text) {
  return Math.max(40, String(text || "").length * Number(object.fontSize || 24) * 0.58 + 12);
}

function objectCenter(object) {
  const bounds = objectBounds(object);
  return {
    x: bounds.x + bounds.width / 2,
    y: bounds.y + bounds.height / 2,
  };
}

function makeSvgElement(tag, attrs = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) element.setAttribute(key, String(value));
  }
  return element;
}

function commonAttrs(object) {
  const center = objectCenter(object);
  return {
    "data-id": object.id,
    class: `object${selectedIds().has(object.id) ? " selected" : ""}`,
    opacity: object.opacity ?? 1,
    transform: object.rotation ? `rotate(${object.rotation} ${center.x} ${center.y})` : undefined,
  };
}

function renderObject(object) {
  const strokeWidth = object.strokeWidth ?? 2;
  if (object.visible === false) return null;
  if (object.type === "rect") {
    return makeSvgElement("rect", {
      ...commonAttrs(object),
      x: object.x,
      y: object.y,
      width: object.width,
      height: object.height,
      rx: object.rx || 0,
      fill: object.fill ?? "#ffffff",
      stroke: object.stroke ?? "#1a1c1f",
      "stroke-width": strokeWidth,
    });
  }
  if (object.type === "ellipse") {
    return makeSvgElement("ellipse", {
      ...commonAttrs(object),
      cx: Number(object.x || 0) + Number(object.width || 0) / 2,
      cy: Number(object.y || 0) + Number(object.height || 0) / 2,
      rx: Math.max(1, Number(object.width || 0) / 2),
      ry: Math.max(1, Number(object.height || 0) / 2),
      fill: object.fill ?? "#ffffff",
      stroke: object.stroke ?? "#1a1c1f",
      "stroke-width": strokeWidth,
    });
  }
  if (object.type === "line" || object.type === "arrow") {
    return makeSvgElement("line", {
      ...commonAttrs(object),
      x1: object.x,
      y1: object.y,
      x2: object.x2,
      y2: object.y2,
      stroke: object.stroke ?? "#1a1c1f",
      "stroke-width": strokeWidth,
      "stroke-linecap": "round",
      "marker-end": object.type === "arrow" ? "url(#arrowhead)" : undefined,
    });
  }
  if (object.type === "text") {
    if (state.editingTextId === object.id) {
      return renderTextEditor(object);
    }
    const element = makeSvgElement("text", {
      ...commonAttrs(object),
      x: object.x,
      y: object.y,
      fill: object.fill ?? "#1a1c1f",
      "font-size": object.fontSize || 28,
      "font-family": object.fontFamily || "Inter, system-ui, sans-serif",
    });
    element.textContent = object.text || "Text";
    return element;
  }
  if (object.type === "image") {
    return makeSvgElement("image", {
      ...commonAttrs(object),
      x: object.x,
      y: object.y,
      width: object.width,
      height: object.height,
      href: object.href,
      preserveAspectRatio: object.preserveAspectRatio || "xMidYMid meet",
    });
  }
  return null;
}

function renderTextEditor(object) {
  const fontSize = Number(object.fontSize || 32);
  const bounds = objectBounds(object);
  const foreignObject = makeSvgElement("foreignObject", {
    "data-id": object.id,
    x: object.x,
    y: bounds.y - 4,
    width: bounds.width + 12,
    height: bounds.height + 12,
    class: "text-editor-shell",
  });
  const input = document.createElement("input");
  input.className = "text-editor-input";
  input.value = object.text || "";
  input.style.fontSize = `${fontSize}px`;
  input.style.color = object.fill || "#1a1c1f";
  input.addEventListener("pointerdown", (event) => event.stopPropagation());
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      input.blur();
    }
  });
  input.addEventListener("input", () => {
    object.text = input.value;
    object.width = textWidth(object, input.value);
    object.height = fontSize * 1.3;
    foreignObject.setAttribute("width", String(object.width + 12));
    setDirty(true);
  });
  input.addEventListener("blur", async () => {
    state.editingTextId = null;
    const nextText = input.value || "Text";
    await patchObject(object.id, {
      text: nextText,
      width: textWidth(object, nextText),
      height: fontSize * 1.3,
    });
  });
  foreignObject.appendChild(input);
  if (state.focusTextEditor) {
    state.focusTextEditor = false;
    setTimeout(() => {
      input.focus();
      input.select();
    }, 0);
  }
  return foreignObject;
}

function renderSelection(object) {
  const bounds = objectBounds(object);
  const center = objectCenter(object);
  const group = makeSvgElement("g", {});
  group.appendChild(makeSvgElement("rect", {
    class: "selection-box",
    x: bounds.x,
    y: bounds.y,
    width: Math.max(1, bounds.width),
    height: Math.max(1, bounds.height),
  }));
  if (object.type === "line" || object.type === "arrow") {
    group.appendChild(makeSvgElement("circle", {
      class: "endpoint-handle",
      "data-line-handle": "start",
      "data-line-id": object.id,
      cx: object.x,
      cy: object.y,
      r: 6,
    }));
    group.appendChild(makeSvgElement("circle", {
      class: "endpoint-handle",
      "data-line-handle": "end",
      "data-line-id": object.id,
      cx: object.x2,
      cy: object.y2,
      r: 6,
    }));
  } else {
    group.appendChild(makeSvgElement("rect", {
      class: "resize-handle",
      "data-resize-id": object.id,
      x: bounds.x + bounds.width - 5,
      y: bounds.y + bounds.height - 5,
      width: 10,
      height: 10,
      rx: 2,
    }));
  }
  const rotateY = bounds.y - 28;
  group.appendChild(makeSvgElement("line", {
    class: "rotate-stem",
    x1: center.x,
    y1: bounds.y,
    x2: center.x,
    y2: rotateY,
  }));
  group.appendChild(makeSvgElement("circle", {
    class: "rotate-handle",
    "data-rotate-id": object.id,
    cx: center.x,
    cy: rotateY,
    r: 6,
  }));
  return group;
}

function render() {
  if (!state.scene) return;
  const { width, height, background } = state.scene.canvas;
  canvas.setAttribute("viewBox", `0 0 ${width} ${height}`);
  canvas.innerHTML = "";
  const defs = makeSvgElement("defs");
  const marker = makeSvgElement("marker", {
    id: "arrowhead",
    viewBox: "0 0 10 10",
    refX: "8",
    refY: "5",
    markerWidth: "7",
    markerHeight: "7",
    orient: "auto-start-reverse",
  });
  marker.appendChild(makeSvgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "context-stroke" }));
  defs.appendChild(marker);
  canvas.appendChild(defs);
  canvas.style.setProperty("--canvas-background", background || "#ffffff");
  canvas.appendChild(makeSvgElement("rect", {
    class: "artboard-background",
    x: 0,
    y: 0,
    width,
    height,
    fill: "transparent",
  }));

  for (const object of sortedObjects()) {
    const element = renderObject(object);
    if (element) canvas.appendChild(element);
  }
  for (const object of sortedObjects().filter((object) => selectedIds().has(object.id))) {
    canvas.appendChild(renderSelection(object));
  }
  syncControls();
  renderObjectsList();
}

function syncControls() {
  const object = selectedObject();
  const disabled = !object;
  deleteButton.classList.toggle("hidden", (state.scene?.selection || []).length === 0);
  for (const input of [fillInput, strokeInput, strokeWidthInput, opacityInput, rotationInput, xInput, yInput, widthInput, heightInput]) {
    input.disabled = disabled;
  }
  if (!object) return;
  fillInput.value = normalizeColor(object.fill || "#ffffff");
  strokeInput.value = normalizeColor(object.stroke || "#1a1c1f");
  strokeWidthInput.value = object.strokeWidth ?? 2;
  opacityInput.value = object.opacity ?? 1;
  rotationInput.value = Math.round(Number(object.rotation || 0));
  xInput.value = Math.round(Number(object.x || 0));
  yInput.value = Math.round(Number(object.y || 0));
  widthInput.value = Math.round(Number(object.width || Math.abs((object.x2 || 0) - (object.x || 0)) || 1));
  heightInput.value = Math.round(Number(object.height || Math.abs((object.y2 || 0) - (object.y || 0)) || 1));
}

function renderObjectsList() {
  const ids = selectedIds();
  objectsEl.innerHTML = "";
  for (const object of displayObjects()) {
    const li = document.createElement("li");
    li.draggable = true;
    li.dataset.id = object.id;
    li.addEventListener("dragstart", (event) => {
      state.draggingObjectId = object.id;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", object.id);
    });
    li.addEventListener("dragover", (event) => {
      event.preventDefault();
      li.classList.add("drag-over");
    });
    li.addEventListener("dragleave", () => li.classList.remove("drag-over"));
    li.addEventListener("drop", async (event) => {
      event.preventDefault();
      li.classList.remove("drag-over");
      const draggedId = event.dataTransfer.getData("text/plain") || state.draggingObjectId;
      await reorderObjectList(draggedId, object.id);
    });
    li.addEventListener("dragend", () => {
      state.draggingObjectId = null;
      li.classList.remove("drag-over");
    });
    if (state.renamingObjectId === object.id) {
      const input = document.createElement("input");
      input.className = "object-name-input";
      input.value = object.name || object.id;
      input.addEventListener("click", (event) => event.stopPropagation());
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") input.blur();
        if (event.key === "Escape") {
          state.renamingObjectId = null;
          renderObjectsList();
        }
      });
      input.addEventListener("blur", async () => {
        state.renamingObjectId = null;
        const nextName = input.value.trim() || object.id;
        await patchObject(object.id, { name: nextName });
      });
      li.appendChild(input);
      objectsEl.appendChild(li);
      setTimeout(() => {
        input.focus();
        input.select();
      }, 0);
      continue;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = ids.has(object.id) ? "active" : "";
    button.textContent = object.name || `${object.type} ${object.id}`;
    button.addEventListener("mousedown", (event) => {
      if (event.detail < 2) return;
      event.preventDefault();
      event.stopPropagation();
      startObjectRename(object.id);
    });
    button.addEventListener("click", () => setSelection([object.id]));
    li.appendChild(button);
    objectsEl.appendChild(li);
  }
}

function startObjectRename(id) {
  state.renamingObjectId = id;
  renderObjectsList();
}

function displayObjects() {
  return sortedObjects().reverse();
}

async function reorderObjectList(draggedId, targetId) {
  if (!draggedId || !targetId || draggedId === targetId) return;
  const displayOrder = displayObjects().map((object) => object.id);
  const from = displayOrder.indexOf(draggedId);
  const to = displayOrder.indexOf(targetId);
  if (from === -1 || to === -1) return;
  displayOrder.splice(from, 1);
  displayOrder.splice(to, 0, draggedId);
  const backToFrontOrder = [...displayOrder].reverse();
  const scene = await api("/api/objects/reorder", { method: "POST", body: JSON.stringify({ order: backToFrontOrder }) });
  markSaved(scene, "Saved");
  render();
}

function normalizeColor(value) {
  if (/^#[0-9a-f]{6}$/i.test(value || "")) return value;
  return "#000000";
}

function pointFromEvent(event) {
  const point = canvas.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  const matrix = canvas.getScreenCTM();
  return matrix ? point.matrixTransform(matrix.inverse()) : { x: 0, y: 0 };
}

async function setSelection(selection) {
  const scene = await api("/api/selection", { method: "POST", body: JSON.stringify({ selection }) });
  state.scene = scene;
  state.lastRevision = scene.revision || 0;
  render();
}

async function patchObject(id, patch) {
  state.suppressPollUntil = Date.now() + 700;
  const scene = await api(`/api/objects/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  markSaved(scene, "Saved");
  render();
}

async function addObject(type, point) {
  const zIndex = Math.max(0, ...state.scene.objects.map((object) => object.zIndex || 0)) + 1;
  const base = {
    type,
    x: Math.round(point.x),
    y: Math.round(point.y),
    width: 160,
    height: 100,
    fill: type === "text" || type === "line" || type === "arrow" ? "#1a1c1f" : "#ffffff",
    stroke: "#1a1c1f",
    strokeWidth: 2,
    opacity: 1,
    zIndex,
  };
  if (type === "ellipse") {
    base.width = 120;
    base.height = 120;
  }
  if (type === "line" || type === "arrow") {
    base.x2 = Math.round(point.x + 160);
    base.y2 = Math.round(point.y);
    base.fill = "none";
  }
  if (type === "text") {
    base.text = "Text";
    base.fontSize = 32;
    base.width = 160;
    base.height = 42;
    base.strokeWidth = 0;
  }
  const scene = await api("/api/objects", { method: "POST", body: JSON.stringify(base) });
  markSaved(scene, "Saved");
  if (type === "text") {
    state.editingTextId = scene.selection?.[0] || null;
    state.focusTextEditor = true;
  }
  state.tool = "select";
  updateToolButtons();
  render();
}

async function addObjectAtCenter(type) {
  if (!state.scene) return;
  const { width, height } = state.scene.canvas;
  const point = { x: width / 2 - 80, y: height / 2 - 50 };
  if (type === "ellipse") {
    point.x = width / 2 - 60;
    point.y = height / 2 - 60;
  }
  if (type === "line" || type === "arrow") {
    point.x = width / 2 - 80;
    point.y = height / 2;
  }
  if (type === "text") {
    point.x = width / 2 - 70;
    point.y = height / 2;
  }
  await addObject(type, point);
}

function updateToolButtons() {
  document.querySelectorAll(".tool[data-tool]").forEach((button) => {
    button.classList.toggle("active", button.dataset.tool === state.tool);
  });
}

canvas.addEventListener("pointerdown", async (event) => {
  const point = pointFromEvent(event);
  const rotateId = event.target?.dataset?.rotateId;
  if (rotateId) {
    const object = state.scene.objects.find((candidate) => candidate.id === rotateId);
    state.drag = { mode: "rotate", id: rotateId, start: point, object: { ...object }, center: objectCenter(object) };
    canvas.setPointerCapture(event.pointerId);
    return;
  }
  const lineId = event.target?.dataset?.lineId;
  const lineHandle = event.target?.dataset?.lineHandle;
  if (lineId && lineHandle) {
    const object = state.scene.objects.find((candidate) => candidate.id === lineId);
    state.drag = { mode: "line-end", id: lineId, handle: lineHandle, start: point, object: { ...object } };
    canvas.setPointerCapture(event.pointerId);
    return;
  }
  const resizeId = event.target?.dataset?.resizeId;
  if (resizeId) {
    const object = state.scene.objects.find((candidate) => candidate.id === resizeId);
    state.drag = { mode: "resize", id: resizeId, start: point, object: { ...object } };
    canvas.setPointerCapture(event.pointerId);
    return;
  }
  const id = event.target?.dataset?.id;
  if (id) {
    const object = state.scene.objects.find((candidate) => candidate.id === id);
    if (object?.type === "text" && isSecondTextClick(id, event)) {
      event.preventDefault();
      await startTextEditing(id);
      return;
    }
  }
  if (state.tool !== "select") {
    await addObject(state.tool, point);
    return;
  }
  if (id) {
    const object = state.scene.objects.find((candidate) => candidate.id === id);
    await setSelection([id]);
    if (object?.type === "text") rememberTextClick(id, event);
    state.drag = { mode: "move", id, start: point, object: { ...object } };
    canvas.setPointerCapture(event.pointerId);
  } else {
    state.lastTextClick = null;
    await setSelection([]);
  }
});

async function startTextEditing(id) {
  await setSelection([id]);
  state.editingTextId = id;
  state.focusTextEditor = true;
  render();
}

function rememberTextClick(id, event) {
  state.lastTextClick = {
    id,
    time: Date.now(),
    x: event.clientX,
    y: event.clientY,
  };
}

function isSecondTextClick(id, event) {
  if (event.detail >= 2) return true;
  const previous = state.lastTextClick;
  if (!previous || previous.id !== id) return false;
  const elapsed = Date.now() - previous.time;
  const distance = Math.hypot(event.clientX - previous.x, event.clientY - previous.y);
  return elapsed < 700 && distance < 10;
}

canvas.addEventListener("pointermove", (event) => {
  if (!state.drag) return;
  const point = pointFromEvent(event);
  const dx = point.x - state.drag.start.x;
  const dy = point.y - state.drag.start.y;
  const object = state.scene.objects.find((candidate) => candidate.id === state.drag.id);
  if (!object) return;
  if (state.drag.mode === "move") {
    object.x = Math.round(Number(state.drag.object.x || 0) + dx);
    object.y = Math.round(Number(state.drag.object.y || 0) + dy);
    if (object.type === "line" || object.type === "arrow") {
      object.x2 = Math.round(Number(state.drag.object.x2 || 0) + dx);
      object.y2 = Math.round(Number(state.drag.object.y2 || 0) + dy);
    }
  } else if (state.drag.mode === "resize") {
    object.width = Math.max(4, Math.round(Number(state.drag.object.width || 1) + dx));
    object.height = Math.max(4, Math.round(Number(state.drag.object.height || 1) + dy));
  } else if (state.drag.mode === "line-end") {
    if (state.drag.handle === "start") {
      object.x = Math.round(point.x);
      object.y = Math.round(point.y);
    } else {
      object.x2 = Math.round(point.x);
      object.y2 = Math.round(point.y);
    }
  } else if (state.drag.mode === "rotate") {
    const angle = Math.atan2(point.y - state.drag.center.y, point.x - state.drag.center.x) * 180 / Math.PI + 90;
    object.rotation = Math.round(angle);
  }
  setDirty(true);
  render();
});

canvas.addEventListener("pointerup", async (event) => {
  if (!state.drag) return;
  canvas.releasePointerCapture(event.pointerId);
  const object = state.scene.objects.find((candidate) => candidate.id === state.drag.id);
  let patch;
  if (state.drag.mode === "resize") {
    patch = { width: object.width, height: object.height };
  } else if (state.drag.mode === "rotate") {
    patch = { rotation: object.rotation };
  } else {
    patch = { x: object.x, y: object.y, x2: object.x2, y2: object.y2 };
  }
  const id = state.drag.id;
  state.drag = null;
  await patchObject(id, patch);
});

document.querySelectorAll(".tool[data-tool]").forEach((button) => {
  button.addEventListener("click", async () => {
    state.tool = button.dataset.tool;
    updateToolButtons();
    if (state.tool !== "select") {
      await addObjectAtCenter(state.tool);
    }
  });
});

function bindPatch(input, key, transform = (value) => value) {
  input.addEventListener("change", async () => {
    const object = selectedObject();
    if (!object) return;
    await patchObject(object.id, { [key]: transform(input.value) });
  });
}

bindPatch(fillInput, "fill");
bindPatch(strokeInput, "stroke");
bindPatch(strokeWidthInput, "strokeWidth", Number);
bindPatch(opacityInput, "opacity", Number);
bindPatch(rotationInput, "rotation", Number);
bindPatch(xInput, "x", Number);
bindPatch(yInput, "y", Number);
bindPatch(widthInput, "width", Number);
bindPatch(heightInput, "height", Number);

$("deleteButton").addEventListener("click", async () => {
  await deleteSelectedObjects();
});

async function deleteSelectedObjects() {
  const ids = state.scene.selection || [];
  if (ids.length === 0) return;
  for (const id of ids) {
    state.scene = await api(`/api/objects/${encodeURIComponent(id)}`, { method: "DELETE" });
  }
  markSaved(state.scene, "Saved");
  render();
}

$("newButton").addEventListener("click", async () => {
  if (!state.scene) return;
  const hasObjects = (state.scene.objects || []).length > 0;
  if (state.dirty) {
    const shouldContinue = confirm("Start a new drawing? The current drawing will stay saved in its current file.");
    if (!shouldContinue) return;
  } else if (hasObjects) {
    const shouldContinue = confirm("Start a new drawing? The current drawing will stay saved in its current file.");
    if (!shouldContinue) return;
  }
  const result = await api("/api/new", { method: "POST", body: "{}" });
  markSaved(result.scene, "Saved");
  render();
});

$("exportButton").addEventListener("click", async () => {
  const result = await api("/api/export/svg-content", { method: "POST", body: "{}" });
  await saveSvgAs(result.svg, result.filename || "codex-draw.svg");
});

async function saveSvgAs(svg, filename) {
  if ("showSaveFilePicker" in window) {
    const blob = new Blob([svg], { type: "image/svg+xml" });
    const handle = await window.showSaveFilePicker({
      suggestedName: filename,
      types: [{ description: "SVG image", accept: { "image/svg+xml": [".svg"] } }],
    });
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    return;
  }
  const dialogResult = await api("/api/export/svg-save-as", { method: "POST", body: "{}" });
  if (dialogResult.ok || dialogResult.cancelled) return;
  const blob = new Blob([svg], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function isEditingField(event) {
  const tagName = event.target?.tagName;
  return tagName === "INPUT" || tagName === "TEXTAREA" || event.target?.isContentEditable;
}

window.addEventListener("keydown", async (event) => {
  if (isEditingField(event)) return;
  if (event.key === "Delete" || event.key === "Backspace") {
    if ((state.scene?.selection || []).length === 0) return;
    event.preventDefault();
    await deleteSelectedObjects();
  }
});

window.addEventListener("beforeunload", (event) => {
  if (!state.dirty) return;
  event.preventDefault();
  event.returnValue = "";
});

loadScene(true);
setInterval(() => loadScene(false), 900);
