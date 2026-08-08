/* =============================================================
 * Workbench 思维导图画布交互
 *
 * 单一 IIFE 挂在 window.MM（避免污染全局）。
 * 所有节点 DOM 由后端 Jinja2 服务端渲染首屏，避免空白闪烁；
 * JS 负责：选中、拖拽、内联编辑、工具栏增删、右键菜单、快捷键、
 *        自动同步 toast、撤销/前进、手动连线（含锚点拖出 + 连线模式 + 箭头切换）、
 *        字号 / 字体调整。
 * ============================================================= */
(function () {
    "use strict";

    const projectId = (window.MM_BOOT && window.MM_BOOT.projectId) || 0;
    if (!projectId) return;

    const svg = document.getElementById("mm-svg");
    const nodesLayer = document.getElementById("mm-nodes-layer");
    const edgesLayer = document.getElementById("mm-edges-layer");
    const anchorsLayer = document.getElementById("mm-anchors-layer");
    const tempEdgeLayer = document.getElementById("mm-temp-edge-layer");
    const toolbar = document.querySelector(".mm-toolbar");
    const contextMenu = document.getElementById("mm-context-menu");
    const edgeContextMenu = document.getElementById("mm-edge-context-menu");
    const toastEl = document.getElementById("mm-toast");
    const saveState = document.getElementById("mm-save-state");
    const syncBtn = document.getElementById("mm-sync-btn");
    const undoBtn = document.getElementById("mm-undo-btn");
    const redoBtn = document.getElementById("mm-redo-btn");
    const linkBtn = document.getElementById("mm-link-btn");

    // 状态
    let selectedNode = null;
    let selectedEdge = null;
    let clipboard = null; // 复制的节点数据（不含 id）
    let dragState = null;
    let linkMode = null; // { sourceId, sourceNodeEl } | null
    let saveTimer = null;
    let pendingPositions = {}; // 拖拽中累积待保存的位置
    let nodeById = new Map(); // id → DOM 元素
    let edgeById = new Map(); // id → DOM 元素
    let syncDiff = (window.MM_BOOT && window.MM_BOOT.syncDiff) || { added: 0, removed: 0, updated: 0 };

    // 撤销/前进栈
    const undoStack = []; // 最新在前
    const redoStack = [];
    const UNDO_LIMIT = 50;

    // 字体名 → 实际 CSS font-family 字符串（与后端 schemas.FONT_FAMILIES 保持一致）
    const FONT_STACKS = {
        system: '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
        hei:    '"PingFang SC", "Microsoft YaHei", "Heiti SC", sans-serif',
        song:   '"SimSun", "STSong", "Songti SC", serif',
        times:  '"Times New Roman", "Liberation Serif", serif',
        kai:    '"KaiTi", "STKaiti", "Kaiti SC", serif',
        mono:   'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
    };

    // ---- 初始化节点索引 ----
    function indexNodes() {
        nodeById.clear();
        nodesLayer.querySelectorAll(".mm-node").forEach((el) => {
            const id = parseInt(el.getAttribute("data-id"), 10);
            nodeById.set(id, el);
        });
    }
    indexNodes();

    // ---- 初始化手动连线索引（auto edges 没有 data-id，跳过） ----
    function indexEdges() {
        edgeById.clear();
        edgesLayer.querySelectorAll(".mm-edge.mm-edge-manual").forEach((el) => {
            const id = parseInt(el.getAttribute("data-id"), 10);
            if (!isNaN(id)) edgeById.set(id, el);
        });
    }
    indexEdges();

    // ---- 保存状态指示 ----
    function setSaveState(state) {
        if (!saveState) return;
        saveState.classList.remove("state-saving", "state-error", "state-saved");
        if (state === "saving") {
            saveState.textContent = "● 保存中…";
            saveState.classList.add("state-saving");
        } else if (state === "error") {
            saveState.textContent = "✗ 保存失败";
            saveState.classList.add("state-error");
        } else {
            saveState.textContent = "● 已保存";
            saveState.classList.add("state-saved");
        }
    }

    // ---- fetch 封装 ----
    async function api(path, opts) {
        opts = opts || {};
        if (!opts.headers) opts.headers = {};
        if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
            opts.headers["Content-Type"] = "application/json";
            opts.body = JSON.stringify(opts.body);
        }
        const r = await fetch(path, opts);
        if (!r.ok) {
            const text = await r.text();
            throw new Error("API " + r.status + ": " + text.slice(0, 200));
        }
        if (r.status === 204) return null;
        return r.json();
    }

    // ---- 选中 ----
    function selectNode(el) {
        if (selectedNode === el) return;
        if (selectedNode) selectedNode.classList.remove("selected");
        selectedNode = el || null;
        if (selectedNode) selectedNode.classList.add("selected");
        // 切换节点选中时，边的选中取消 + 重新画锚点
        clearEdgeSelection();
        renderAnchors();
    }

    function clearSelection() {
        selectNode(null);
    }

    // ---- 选中边 ----
    function selectEdge(el) {
        if (selectedEdge === el) return;
        if (selectedEdge) selectedEdge.classList.remove("selected");
        selectedEdge = el || null;
        if (selectedEdge) selectedEdge.classList.add("selected");
        // 边被选中时，节点的选中取消（且隐藏锚点）
        if (selectedEdge) {
            if (selectedNode) selectedNode.classList.remove("selected");
            selectedNode = null;
            renderAnchors();
        }
    }
    function clearEdgeSelection() {
        selectEdge(null);
    }

    // ---- 节点工具：根据 id 取 SVG 元素 ----
    function $node(id) {
        return nodeById.get(id) || nodesLayer.querySelector('.mm-node[data-id="' + id + '"]');
    }

    // ---- 新建节点的 SVG 元素（前端用 createElementNS 真正创建 SVG 元素） ----
    // 用 innerHTML 解析 SVG 字符串会让元素落到 HTML 命名空间，浏览器不会渲染。
    // 这里改成命名空间感知的创建方式，与服务端 SVG 渲染保持一致。
    const NS_SVG = "http://www.w3.org/2000/svg";
    const NS_XHTML = "http://www.w3.org/1999/xhtml";

    function svgEl(tag, attrs, children) {
        const e = document.createElementNS(NS_SVG, tag);
        if (attrs) {
            for (const k in attrs) {
                if (attrs[k] === null || attrs[k] === undefined) continue;
                e.setAttribute(k, attrs[k]);
            }
        }
        if (children) children.forEach((c) => e.appendChild(c));
        return e;
    }

    function buildShapeEl(node) {
        const w = node.w, h = node.h;
        if (node.shape_type === "ellipse") {
            return svgEl("ellipse", {
                cx: w / 2, cy: h / 2, rx: w / 2, ry: h / 2,
                class: "mm-shape",
            });
        }
        if (node.shape_type === "diamond") {
            return svgEl("polygon", {
                points: `${w/2},0 ${w},${h/2} ${w/2},${h} 0,${h/2}`,
                class: "mm-shape",
            });
        }
        if (node.shape_type === "hexagon") {
            const cut = Math.min(20, w / 4);
            return svgEl("polygon", {
                points: `${cut},0 ${w-cut},0 ${w},${h/2} ${w-cut},${h} ${cut},${h} 0,${h/2}`,
                class: "mm-shape",
            });
        }
        if (node.shape_type === "arrow") {
            return svgEl("polygon", {
                points: `0,${h*0.3} ${w*0.7},${h*0.3} ${w*0.7},0 ${w},${h/2} ${w*0.7},${h} ${w*0.7},${h*0.7} 0,${h*0.7}`,
                class: "mm-shape",
            });
        }
        if (node.shape_type === "text") return null;
        // rect / rounded / sticky-*
        let rx = "4";
        if (node.shape_type === "rounded") rx = "12";
        else if (node.shape_type.indexOf("sticky-") === 0) rx = "6";
        return svgEl("rect", { width: w, height: h, rx, class: "mm-shape" });
    }

    function buildNodeEl(node) {
        const attrs = {
            class: "mm-node kind-" + node.kind + " shape-" + node.shape_type,
            "data-id": String(node.id),
            "data-kind": node.kind,
            "data-shape": node.shape_type,
            "data-font-size": String(node.font_size != null ? node.font_size : 13),
            transform: `translate(${node.x},${node.y})`,
        };
        if (node.parent_id != null) attrs["data-parent"] = String(node.parent_id);
        attrs["data-z"] = String(node.z_index != null ? node.z_index : 0);
        const g = svgEl("g", attrs);

        const shapeEl = buildShapeEl(node);
        if (shapeEl) g.appendChild(shapeEl);

        // foreignObject + 内嵌 XHTML div（允许换行 + 内联编辑）
        const fo = svgEl("foreignObject", {
            x: 0, y: 0, width: node.w, height: node.h,
        });
        const labelDiv = document.createElementNS(NS_XHTML, "div");
        labelDiv.setAttribute("class", "mm-label");
        if (node.font_size) labelDiv.setAttribute("style", "font-size:" + node.font_size + "px");
        labelDiv.textContent = node.label || "";
        fo.appendChild(labelDiv);
        g.appendChild(fo);

        return g;
    }

    // ---- 添加新节点 ----
    async function addNode(shapeType, opts) {
        opts = opts || {};
        // 在画布中央偏右
        const wrap = document.querySelector(".mm-canvas-wrap");
        const rect = wrap.getBoundingClientRect();
        const scrollL = wrap.scrollLeft || 0;
        const scrollT = wrap.scrollTop || 0;
        const cx = (rect.width / 2) - 60 + scrollL;
        const cy = (rect.height / 2) - 30 + scrollT;
        const defaults = {
            rect: { w: 120, h: 60 },
            rounded: { w: 120, h: 60 },
            ellipse: { w: 120, h: 60 },
            diamond: { w: 120, h: 80 },
            hexagon: { w: 130, h: 60 },
            arrow: { w: 160, h: 50 },
            text: { w: 140, h: 40 },
            "sticky-yellow": { w: 130, h: 100 },
            "sticky-pink": { w: 130, h: 100 },
            "sticky-blue": { w: 130, h: 100 },
        };
        const d = defaults[shapeType] || { w: 120, h: 60 };
        const payload = {
            shape_type: shapeType,
            label: opts.label || "",
            x: opts.x != null ? opts.x : cx,
            y: opts.y != null ? opts.y : cy,
            w: d.w,
            h: d.h,
            z_index: 0,
        };
        setSaveState("saving");
        try {
            const node = await api("/api/projects/" + projectId + "/mindmap/nodes", {
                method: "POST",
                body: payload,
            });
            const el = buildNodeEl(node);
            nodesLayer.appendChild(el);
            nodeById.set(node.id, el);
            attachNodeHandlers(el);
            selectNode(el);
            if (opts.startEdit) {
                enterEditMode(el);
            }
            // 撤销栈：添加节点
            pushCommand({ type: "add-node", node: node });
            setSaveState("saved");
            return el;
        } catch (e) {
            console.error(e);
            setSaveState("error");
            showToast("添加失败：" + e.message, "error");
        }
    }

    // ---- 删除节点 ----
    async function deleteNode(id, skipUndo) {
        const el = $node(id);
        // 先抓快照（用于撤销）
        let snapshot = null;
        if (el) {
            const tm = (el.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
            snapshot = {
                id: id,
                shape_type: el.getAttribute("data-shape"),
                label: el.querySelector(".mm-label")?.textContent || "",
                x: tm ? parseFloat(tm[1]) : 0,
                y: tm ? parseFloat(tm[2]) : 0,
                w: parseFloat(el.querySelector(".mm-shape")?.getAttribute("width") || 120),
                h: parseFloat(el.querySelector(".mm-shape")?.getAttribute("height") || 60),
                z_index: parseInt(el.getAttribute("data-z") || "0", 10),
                font_size: parseInt(el.getAttribute("data-font-size") || "13", 10),
                font_family: el.getAttribute("data-font-family") || "system",
                kind: el.getAttribute("data-kind"),
            };
        }
        setSaveState("saving");
        try {
            await api("/api/mindmap/nodes/" + id, { method: "DELETE" });
            if (el) {
                el.remove();
                nodeById.delete(id);
                if (selectedNode === el) clearSelection();
            }
            if (!skipUndo && snapshot) {
                pushCommand({ type: "delete-node", node: snapshot });
            }
            setSaveState("saved");
        } catch (e) {
            console.error(e);
            setSaveState("error");
            showToast("删除失败：" + e.message, "error");
        }
    }

    // ---- 批量保存位置（拖拽 mouseup 触发） ----
    async function flushPositions() {
        const ids = Object.keys(pendingPositions);
        if (!ids.length) return;
        const newPositions = ids.map(function (id) {
            const p = pendingPositions[id];
            return { id: parseInt(id, 10), x: p.x, y: p.y };
        });
        pendingPositions = {};
        // 抓 before 用于撤销
        const before = newPositions.map(function (p) {
            const el = $node(p.id);
            if (!el) return null;
            const tm = (el.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
            return tm ? { id: p.id, x: parseFloat(tm[1]), y: parseFloat(tm[2]) } : null;
        }).filter(Boolean);
        setSaveState("saving");
        try {
            await api("/api/mindmap/nodes/bulk-position", {
                method: "POST",
                body: { positions: newPositions },
            });
            // 只在确实有位移时入撤销栈
            const changed = newPositions.filter(function (p) {
                const b = before.find(function (x) { return x.id === p.id; });
                return b && (Math.abs(b.x - p.x) > 0.5 || Math.abs(b.y - p.y) > 0.5);
            });
            if (changed.length) {
                pushCommand({
                    type: "bulk-position",
                    before: before,
                    after: newPositions,
                });
            }
            setSaveState("saved");
        } catch (e) {
            console.error(e);
            setSaveState("error");
            showToast("保存位置失败：" + e.message, "error");
        }
    }

    // ---- 更新单个节点字段 ----
    // opts.recordUndo: false 时不入撤销栈（用于 changeFontSize 内部已经独立入栈的情况）
    async function patchNode(id, data, opts) {
        opts = opts || {};
        const el = $node(id);
        // 抓 before 快照（仅入栈时）
        let beforeSnapshot = null;
        if (!opts.recordUndo && el) {
            const lbl = el.querySelector(".mm-label");
            beforeSnapshot = {};
            Object.keys(data).forEach(function (k) {
                if (k === "font_size") beforeSnapshot[k] = parseInt(el.getAttribute("data-font-size") || "13", 10);
                else if (k === "font_family") beforeSnapshot[k] = el.getAttribute("data-font-family") || "system";
                else if (k === "label") beforeSnapshot[k] = lbl ? lbl.textContent : "";
                else if (k === "x" || k === "y") {
                    const tm = (el.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
                    beforeSnapshot[k] = tm ? parseFloat(tm[k === "x" ? 1 : 2]) : 0;
                }
                else if (k === "shape_type") beforeSnapshot[k] = el.getAttribute("data-shape");
                else if (k === "z_index") beforeSnapshot[k] = parseInt(el.getAttribute("data-z") || "0", 10);
            });
        }
        setSaveState("saving");
        try {
            const node = await api("/api/mindmap/nodes/" + id, {
                method: "PATCH",
                body: data,
            });
            if (el) {
                el.setAttribute("data-shape", node.shape_type);
                // 整节点替换（避免重渲染 shape）
                const newEl = buildNodeEl(node);
                el.parentNode.replaceChild(newEl, el);
                nodeById.set(node.id, newEl);
                attachNodeHandlers(newEl);
                if (selectedNode && selectedNode.getAttribute("data-id") === String(id)) {
                    selectNode(newEl);
                }
            }
            if (!opts.recordUndo && beforeSnapshot) {
                pushCommand({
                    type: "patch-node", id: id,
                    before: beforeSnapshot,
                    after: Object.assign({}, data),
                });
            }
            setSaveState("saved");
            return node;
        } catch (e) {
            console.error(e);
            setSaveState("error");
            showToast("更新失败：" + e.message, "error");
        }
    }

    // ---- toast ----
    function showToast(msg, level) {
        if (!toastEl) return;
        toastEl.textContent = msg;
        toastEl.className = "mm-toast" + (level === "error" ? " mm-toast-error" : "");
        toastEl.hidden = false;
        setTimeout(function () { toastEl.hidden = true; }, 3500);
    }

    // ---- 进入/退出 inline 编辑 ----
    function enterEditMode(el) {
        if (el.getAttribute("data-kind") !== "manual") {
            // 自动节点也允许编辑（会同步 label 到源数据？不，只同步内部 label）
            // 简化：自动节点可以编辑文字，但只是修改画布上的 label，不回写源
            // 这里一致允许
        }
        const labelDiv = el.querySelector(".mm-label");
        if (!labelDiv) return;
        labelDiv.setAttribute("contenteditable", "true");
        labelDiv.classList.add("editing");
        // 全选
        const range = document.createRange();
        range.selectNodeContents(labelDiv);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        labelDiv.focus();

        function commit() {
            labelDiv.removeEventListener("blur", commit);
            labelDiv.removeEventListener("keydown", onKey);
            labelDiv.setAttribute("contenteditable", "false");
            labelDiv.classList.remove("editing");
            const id = parseInt(el.getAttribute("data-id"), 10);
            const newLabel = labelDiv.textContent;
            patchNode(id, { label: newLabel });
        }
        function onKey(e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                labelDiv.blur();
            } else if (e.key === "Escape") {
                labelDiv.blur();
            }
        }
        labelDiv.addEventListener("blur", commit);
        labelDiv.addEventListener("keydown", onKey);
    }

    // ===================================================================
    // 锚点（选中节点时显示 4 个蓝点，从任一锚点拖出连线）
    // ===================================================================
    function renderAnchors() {
        if (!anchorsLayer) return;
        anchorsLayer.textContent = "";
        if (!selectedNode) return;
        const t = selectedNode.getAttribute("transform") || "";
        const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
        const x = m ? parseFloat(m[1]) : 0;
        const y = m ? parseFloat(m[2]) : 0;
        const w = parseFloat(selectedNode.querySelector(".mm-shape")?.getAttribute("width") || 120);
        const h = parseFloat(selectedNode.querySelector(".mm-shape")?.getAttribute("height") || 60);
        const pts = [
            { side: "t", cx: x + w / 2, cy: y },
            { side: "r", cx: x + w,     cy: y + h / 2 },
            { side: "b", cx: x + w / 2, cy: y + h },
            { side: "l", cx: x,         cy: y + h / 2 },
        ];
        pts.forEach(function (p) {
            const c = document.createElementNS(NS_SVG, "circle");
            c.setAttribute("class", "mm-anchor");
            c.setAttribute("data-side", p.side);
            c.setAttribute("cx", p.cx);
            c.setAttribute("cy", p.cy);
            c.setAttribute("r", 6);
            anchorsLayer.appendChild(c);
        });
    }
    function clearAnchors() {
        if (anchorsLayer) anchorsLayer.textContent = "";
    }

    // ===================================================================
    // 边：build / 渲染 / 删除 / 切箭头 / 选中
    // ===================================================================
    function $edge(id) {
        return edgeById.get(id) || edgesLayer.querySelector('.mm-edge-manual[data-id="' + id + '"]');
    }

    function buildEdgeEl(edge) {
        // 从 source/target 的当前 transform 算出端点坐标（与后端 _render 公式一致）
        const src = $node(edge.source_id);
        const tgt = $node(edge.target_id);
        if (!src || !tgt) return null;
        const sT = src.getAttribute("transform") || "";
        const sM = sT.match(/translate\(([-\d.]+),([-\d.]+)\)/);
        const sx = sM ? parseFloat(sM[1]) : 0;
        const sy = sM ? parseFloat(sM[2]) : 0;
        const sw = parseFloat(src.querySelector(".mm-shape")?.getAttribute("width") || 120);
        const sh = parseFloat(src.querySelector(".mm-shape")?.getAttribute("height") || 60);
        const tT = tgt.getAttribute("transform") || "";
        const tM = tT.match(/translate\(([-\d.]+),([-\d.]+)\)/);
        const tx = tM ? parseFloat(tM[1]) : 0;
        const ty = tM ? parseFloat(tM[2]) : 0;
        const th = parseFloat(tgt.querySelector(".mm-shape")?.getAttribute("height") || 60);
        const x1 = sx + sw, y1 = sy + sh / 2;
        const x2 = tx,      y2 = ty + th / 2;
        const midX = (x1 + x2) / 2;
        const d = `M ${x1},${y1} C ${midX},${y1} ${midX},${y2} ${x2},${y2}`;
        const p = document.createElementNS(NS_SVG, "path");
        p.setAttribute("class", "mm-edge mm-edge-manual");
        p.setAttribute("data-id", String(edge.id));
        p.setAttribute("data-source", String(edge.source_id));
        p.setAttribute("data-target", String(edge.target_id));
        p.setAttribute("data-arrow", String(!!edge.arrow));
        p.setAttribute("d", d);
        if (edge.arrow) p.setAttribute("marker-end", "url(#mm-arrow)");
        return p;
    }

    function attachEdgeHandlers(el) {
        el.addEventListener("mousedown", function (e) {
            if (e.button !== 0) return;
            e.stopPropagation();
            selectEdge(el);
        });
        el.addEventListener("contextmenu", function (e) {
            e.preventDefault();
            e.stopPropagation();
            selectEdge(el);
            showEdgeContextMenu(e.clientX, e.clientY);
        });
    }

    async function createEdge(sourceId, targetId, arrow) {
        if (sourceId === targetId) {
            showToast("不能连接到自己", "error");
            return null;
        }
        setSaveState("saving");
        try {
            const edge = await api("/api/projects/" + projectId + "/mindmap/edges", {
                method: "POST",
                body: { source_id: sourceId, target_id: targetId, arrow: arrow !== false },
            });
            const el = buildEdgeEl(edge);
            if (el) {
                edgesLayer.appendChild(el);
                edgeById.set(edge.id, el);
                attachEdgeHandlers(el);
            }
            // push 到撤销栈
            pushCommand({ type: "add-edge", edge: edge });
            setSaveState("saved");
            return edge;
        } catch (e) {
            console.error(e);
            setSaveState("error");
            showToast("创建连线失败：" + e.message, "error");
            return null;
        }
    }

    async function deleteEdge(id, skipUndo) {
        setSaveState("saving");
        try {
            // 先抓快照以便撤销
            const edge = $edge(id);
            const snapshot = edge ? {
                id: parseInt(edge.getAttribute("data-id"), 10),
                source_id: parseInt(edge.getAttribute("data-source"), 10),
                target_id: parseInt(edge.getAttribute("data-target"), 10),
                arrow: edge.getAttribute("data-arrow") === "true",
            } : null;
            await api("/api/mindmap/edges/" + id, { method: "DELETE" });
            if (edge) {
                edge.remove();
                edgeById.delete(id);
                if (selectedEdge === edge) clearEdgeSelection();
            }
            if (!skipUndo && snapshot) {
                pushCommand({ type: "delete-edge", edge: snapshot });
            }
            setSaveState("saved");
        } catch (e) {
            console.error(e);
            setSaveState("error");
            showToast("删除连线失败：" + e.message, "error");
        }
    }

    async function toggleArrow(id) {
        const edge = $edge(id);
        if (!edge) return;
        const before = edge.getAttribute("data-arrow") === "true";
        const after = !before;
        setSaveState("saving");
        try {
            await api("/api/mindmap/edges/" + id, {
                method: "PATCH",
                body: { arrow: after },
            });
            edge.setAttribute("data-arrow", String(after));
            if (after) edge.setAttribute("marker-end", "url(#mm-arrow)");
            else edge.removeAttribute("marker-end");
            pushCommand({ type: "patch-edge", id: id, before: { arrow: before }, after: { arrow: after } });
            setSaveState("saved");
        } catch (e) {
            console.error(e);
            setSaveState("error");
            showToast("切换箭头失败：" + e.message, "error");
        }
    }

    // ===================================================================
    // 撤销 / 前进
    // ===================================================================
    function pushCommand(cmd) {
        undoStack.push(cmd);
        if (undoStack.length > UNDO_LIMIT) undoStack.shift();
        // 任何新动作清空 redo
        if (redoStack.length) redoStack.length = 0;
        updateUndoButtons();
    }

    function updateUndoButtons() {
        if (undoBtn) undoBtn.disabled = undoStack.length === 0;
        if (redoBtn) redoBtn.disabled = redoStack.length === 0;
    }

    // 命令类型 + 方向 → 反向 apply
    async function applyCommand(cmd, dir) {
        const opposite = (type) => type === "before" ? "after" : "before";
        // 对一个命令，dir="undo" 用 before 还原，dir="redo" 用 after 还原
        const useBefore = dir === "undo";
        try {
            if (cmd.type === "add-node") {
                if (useBefore) {
                    // 反向 = 删除（刚刚添加）
                    const id = cmd.node.id;
                    await api("/api/mindmap/nodes/" + id, { method: "DELETE" });
                    const el = $node(id);
                    if (el) { el.remove(); nodeById.delete(id); }
                } else {
                    // 重做 = 再添加
                    const n = await api("/api/projects/" + projectId + "/mindmap/nodes", {
                        method: "POST",
                        body: {
                            shape_type: cmd.node.shape_type,
                            label: cmd.node.label,
                            x: cmd.node.x, y: cmd.node.y,
                            w: cmd.node.w, h: cmd.node.h,
                            z_index: cmd.node.z_index,
                            font_size: cmd.node.font_size,
                            font_family: cmd.node.font_family,
                        },
                    });
                    const el = buildNodeEl(n);
                    nodesLayer.appendChild(el);
                    nodeById.set(n.id, el);
                    attachNodeHandlers(el);
                }
            } else if (cmd.type === "delete-node") {
                if (useBefore) {
                    // 撤销删除 = 重建
                    const n = await api("/api/projects/" + projectId + "/mindmap/nodes", {
                        method: "POST",
                        body: {
                            shape_type: cmd.node.shape_type,
                            label: cmd.node.label,
                            x: cmd.node.x, y: cmd.node.y,
                            w: cmd.node.w, h: cmd.node.h,
                            z_index: cmd.node.z_index,
                            font_size: cmd.node.font_size,
                            font_family: cmd.node.font_family,
                        },
                    });
                    const el = buildNodeEl(n);
                    nodesLayer.appendChild(el);
                    nodeById.set(n.id, el);
                    attachNodeHandlers(el);
                } else {
                    const id = cmd.node.id;
                    await api("/api/mindmap/nodes/" + id, { method: "DELETE" });
                    const el = $node(id);
                    if (el) { el.remove(); nodeById.delete(id); }
                }
            } else if (cmd.type === "patch-node") {
                const fields = useBefore ? cmd.before : cmd.after;
                if (!fields || !Object.keys(fields).length) return;
                const n = await api("/api/mindmap/nodes/" + cmd.id, {
                    method: "PATCH", body: fields,
                });
                const el = $node(cmd.id);
                if (el) {
                    const newEl = buildNodeEl(n);
                    el.parentNode.replaceChild(newEl, el);
                    nodeById.set(cmd.id, newEl);
                    attachNodeHandlers(newEl);
                    if (selectedNode && selectedNode.getAttribute("data-id") === String(cmd.id)) {
                        selectNode(newEl);
                    }
                }
            } else if (cmd.type === "bulk-position") {
                const positions = useBefore ? cmd.before : cmd.after;
                await api("/api/mindmap/nodes/bulk-position", {
                    method: "POST", body: { positions: positions },
                });
                positions.forEach(function (p) {
                    const el = $node(p.id);
                    if (el) el.setAttribute("transform", `translate(${p.x},${p.y})`);
                });
                redrawAllManualEdges();
            } else if (cmd.type === "add-edge") {
                if (useBefore) {
                    await api("/api/mindmap/edges/" + cmd.edge.id, { method: "DELETE" });
                    const el = $edge(cmd.edge.id);
                    if (el) { el.remove(); edgeById.delete(cmd.edge.id); }
                } else {
                    const e = await api("/api/projects/" + projectId + "/mindmap/edges", {
                        method: "POST",
                        body: {
                            source_id: cmd.edge.source_id,
                            target_id: cmd.edge.target_id,
                            arrow: cmd.edge.arrow,
                        },
                    });
                    const el = buildEdgeEl(e);
                    if (el) { edgesLayer.appendChild(el); edgeById.set(e.id, el); attachEdgeHandlers(el); }
                }
            } else if (cmd.type === "delete-edge") {
                if (useBefore) {
                    const e = await api("/api/projects/" + projectId + "/mindmap/edges", {
                        method: "POST",
                        body: {
                            source_id: cmd.edge.source_id,
                            target_id: cmd.edge.target_id,
                            arrow: cmd.edge.arrow,
                        },
                    });
                    const el = buildEdgeEl(e);
                    if (el) { edgesLayer.appendChild(el); edgeById.set(e.id, el); attachEdgeHandlers(el); }
                } else {
                    await api("/api/mindmap/edges/" + cmd.edge.id, { method: "DELETE" });
                    const el = $edge(cmd.edge.id);
                    if (el) { el.remove(); edgeById.delete(cmd.edge.id); }
                }
            } else if (cmd.type === "patch-edge") {
                const fields = useBefore ? cmd.before : cmd.after;
                if (!fields) return;
                await api("/api/mindmap/edges/" + cmd.id, {
                    method: "PATCH", body: fields,
                });
                const el = $edge(cmd.id);
                if (el) {
                    el.setAttribute("data-arrow", String(fields.arrow));
                    if (fields.arrow) el.setAttribute("marker-end", "url(#mm-arrow)");
                    else el.removeAttribute("marker-end");
                }
            }
        } catch (e) {
            console.error("undo/redo 失败：", e);
            showToast("撤销失败：" + e.message, "error");
        }
    }

    async function doUndo() {
        const cmd = undoStack.pop();
        if (!cmd) return;
        await applyCommand(cmd, "undo");
        redoStack.push(cmd);
        updateUndoButtons();
    }
    async function doRedo() {
        const cmd = redoStack.pop();
        if (!cmd) return;
        await applyCommand(cmd, "redo");
        undoStack.push(cmd);
        updateUndoButtons();
    }

    // ===================================================================
    // 字体切换
    // ===================================================================
    async function changeFontFamily(family) {
        if (!selectedNode) {
            showToast("请先选中一个节点", "error");
            return;
        }
        if (!FONT_STACKS[family]) return;
        const id = parseInt(selectedNode.getAttribute("data-id"), 10);
        // 抓 before
        const before = { font_family: selectedNode.getAttribute("data-font-family") || "system" };
        const after = { font_family: family };
        if (before.font_family === after.font_family) return;
        // 乐观更新
        const lbl = selectedNode.querySelector(".mm-label");
        if (lbl) lbl.setAttribute("style", "font-size:" + (lbl.style.fontSize || "13px") + ";font-family:" + FONT_STACKS[family]);
        selectedNode.setAttribute("data-font-family", family);
        setSaveState("saving");
        try {
            const n = await api("/api/mindmap/nodes/" + id, {
                method: "PATCH", body: { font_family: family },
            });
            pushCommand({ type: "patch-node", id: id, before: before, after: after });
            setSaveState("saved");
        } catch (e) {
            console.error(e);
            setSaveState("error");
            showToast("改字体失败：" + e.message, "error");
        }
    }

    // ===================================================================
    // 连线模式（toolbar 🔗 按钮）：第一次点 = source，第二次点 = target
    // ===================================================================
    function enterLinkMode() {
        if (linkMode) return;
        linkMode = { sourceId: null, sourceNodeEl: null };
        document.body.classList.add("mm-link-mode");
        if (linkBtn) linkBtn.classList.add("mm-active");
        showToast("连线模式：点第一个节点作为起点", "info");
    }
    function exitLinkMode() {
        linkMode = null;
        document.body.classList.remove("mm-link-mode");
        if (linkBtn) linkBtn.classList.remove("mm-active");
        // 清空临时连线
        if (tempEdgeLayer) tempEdgeLayer.textContent = "";
    }
    function handleLinkModeClick(nodeEl) {
        if (!linkMode) return false;
        const id = parseInt(nodeEl.getAttribute("data-id"), 10);
        if (!linkMode.sourceId) {
            linkMode.sourceId = id;
            linkMode.sourceNodeEl = nodeEl;
            selectNode(nodeEl);
            showToast("已选起点，再点一个节点作为终点", "info");
        } else {
            const targetId = id;
            const sourceId = linkMode.sourceId;
            exitLinkMode();
            createEdge(sourceId, targetId, true);
        }
        return true; // 表示吞掉了这次点击
    }

    // ---- 拖拽 ----
    function attachNodeHandlers(el) {
        el.addEventListener("mousedown", function (e) {
            if (e.button !== 0) return;
            // 阻止 foreignObject 内 contenteditable 抢焦点
            const labelDiv = el.querySelector(".mm-label");
            if (labelDiv && labelDiv.classList.contains("editing")) return;

            // 连线模式下：把点击交给 link-mode 处理（不进入拖拽）
            if (linkMode) {
                e.stopPropagation();
                handleLinkModeClick(el);
                return;
            }

            e.stopPropagation();
            selectNode(el);

            // 当前 translate
            const t = el.getAttribute("transform") || "";
            const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
            const ox = m ? parseFloat(m[1]) : 0;
            const oy = m ? parseFloat(m[2]) : 0;

            dragState = {
                el: el,
                id: parseInt(el.getAttribute("data-id"), 10),
                startX: e.clientX,
                startY: e.clientY,
                origX: ox,
                origY: oy,
                moved: false,
            };

            function onMove(ev) {
                if (!dragState) return;
                const dx = ev.clientX - dragState.startX;
                const dy = ev.clientY - dragState.startY;
                if (Math.abs(dx) + Math.abs(dy) > 2) dragState.moved = true;
                const nx = Math.max(0, dragState.origX + dx);
                const ny = Math.max(0, dragState.origY + dy);
                dragState.el.setAttribute("transform", "translate(" + nx + "," + ny + ")");
                // 同步移动相关连线 + 重画锚点
                redrawEdges();
                renderAnchors();
                // 累积待保存
                const id = dragState.el.getAttribute("data-id");
                pendingPositions[id] = { x: nx, y: ny };
                if (saveTimer) clearTimeout(saveTimer);
                saveTimer = setTimeout(flushPositions, 600);
            }
            function onUp() {
                document.removeEventListener("mousemove", onMove);
                document.removeEventListener("mouseup", onUp);
                if (dragState && dragState.moved) {
                    flushPositions();
                }
                dragState = null;
            }
            document.addEventListener("mousemove", onMove);
            document.addEventListener("mouseup", onUp);
        });

        // 双击编辑
        el.addEventListener("dblclick", function (e) {
            e.stopPropagation();
            enterEditMode(el);
        });

        // 右键菜单
        el.addEventListener("contextmenu", function (e) {
            e.preventDefault();
            e.stopPropagation();
            selectNode(el);
            showContextMenu(e.clientX, e.clientY);
        });
    }

    // ---- 锚点拖出连线（mousedown 在 .mm-anchor 上） ----
    if (anchorsLayer) {
        anchorsLayer.addEventListener("mousedown", function (e) {
            if (e.button !== 0) return;
            const anchor = e.target.closest(".mm-anchor");
            if (!anchor || !selectedNode) return;
            e.stopPropagation();
            e.preventDefault();

            const sourceId = parseInt(selectedNode.getAttribute("data-id"), 10);
            const ax = parseFloat(anchor.getAttribute("cx"));
            const ay = parseFloat(anchor.getAttribute("cy"));
            // 临时连线
            const tmp = document.createElementNS(NS_SVG, "path");
            tmp.setAttribute("class", "mm-edge mm-edge-temp");
            tmp.setAttribute("stroke", "var(--c-primary, #7C3AED)");
            tmp.setAttribute("stroke-width", "2");
            tmp.setAttribute("stroke-dasharray", "4 3");
            tmp.setAttribute("fill", "none");
            tmp.setAttribute("marker-end", "url(#mm-arrow)");
            tempEdgeLayer.appendChild(tmp);

            function setLine(curX, curY) {
                const midX = (ax + curX) / 2;
                tmp.setAttribute("d",
                    `M ${ax},${ay} C ${midX},${ay} ${midX},${curY} ${curX},${curY}`);
            }
            setLine(ax, ay);

            function onMove(ev) {
                // 把 client 坐标 → svg 坐标（粗略：直接用 client - canvas-wrap.scroll）
                const wrap = document.querySelector(".mm-canvas-wrap");
                const scrollL = wrap ? wrap.scrollLeft : 0;
                const scrollT = wrap ? wrap.scrollTop : 0;
                const rect = svg.getBoundingClientRect();
                const curX = (ev.clientX - rect.left) + scrollL;
                const curY = (ev.clientY - rect.top) + scrollT;
                setLine(curX, curY);
            }
            function onUp(ev) {
                document.removeEventListener("mousemove", onMove);
                document.removeEventListener("mouseup", onUp);
                tmp.remove();
                // 命中节点？
                const target = document.elementFromPoint(ev.clientX, ev.clientY);
                let targetNode = target ? target.closest(".mm-node") : null;
                // 也兼容：target 是 label 文字
                if (!targetNode && target) {
                    const lbl = target.closest(".mm-label");
                    if (lbl) targetNode = lbl.closest(".mm-node");
                }
                if (targetNode && targetNode !== selectedNode) {
                    const tid = parseInt(targetNode.getAttribute("data-id"), 10);
                    createEdge(sourceId, tid, true);
                }
            }
            document.addEventListener("mousemove", onMove);
            document.addEventListener("mouseup", onUp);
        });
    }

    // ---- 右键菜单 ----
    function showContextMenu(x, y) {
        if (!contextMenu) return;
        contextMenu.hidden = false;
        // 边界检查
        const rect = contextMenu.getBoundingClientRect();
        let nx = x, ny = y;
        if (nx + rect.width > window.innerWidth) nx = window.innerWidth - rect.width - 4;
        if (ny + rect.height > window.innerHeight) ny = window.innerHeight - rect.height - 4;
        contextMenu.style.left = nx + "px";
        contextMenu.style.top = ny + "px";
    }
    function hideContextMenu() {
        if (contextMenu) contextMenu.hidden = true;
    }

    function handleContextAction(action) {
        if (!selectedNode) return;
        const id = parseInt(selectedNode.getAttribute("data-id"), 10);
        if (action === "duplicate") {
            // 复制为副本：偏移 20,20
            const t = selectedNode.getAttribute("transform") || "";
            const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
            const ox = m ? parseFloat(m[1]) : 0;
            const oy = m ? parseFloat(m[2]) : 0;
            const label = selectedNode.querySelector(".mm-label").textContent;
            const shape = selectedNode.getAttribute("data-shape");
            // 只支持复制 manual 节点（auto 节点复制没意义——会变孤儿）
            const kind = selectedNode.getAttribute("data-kind");
            if (kind === "auto") {
                showToast("自动节点无法复制（它是项目数据生成的）", "error");
                hideContextMenu();
                return;
            }
            addNode(shape, { x: ox + 20, y: oy + 20, label: label });
        } else if (action === "copy") {
            const t = selectedNode.getAttribute("transform") || "";
            const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
            const ox = m ? parseFloat(m[1]) : 0;
            const oy = m ? parseFloat(m[2]) : 0;
            clipboard = {
                shape_type: selectedNode.getAttribute("data-shape"),
                label: selectedNode.querySelector(".mm-label").textContent,
                x: ox, y: oy,
                w: parseFloat(selectedNode.querySelector(".mm-shape")?.getAttribute("width") || 120),
                h: parseFloat(selectedNode.querySelector(".mm-shape")?.getAttribute("height") || 60),
                kind: selectedNode.getAttribute("data-kind"),
            };
            // auto 节点不进剪贴板（它跟源数据绑定的）
            if (clipboard.kind === "auto") clipboard = null;
        } else if (action === "paste") {
            if (!clipboard) return;
            addNode(clipboard.shape_type, {
                x: clipboard.x + 30,
                y: clipboard.y + 30,
                label: clipboard.label,
            });
        } else if (action === "bring-front") {
            const cur = parseInt(selectedNode.getAttribute("data-z") || "0", 10);
            patchNode(id, { z_index: cur + 1 });
        } else if (action === "send-back") {
            const cur = parseInt(selectedNode.getAttribute("data-z") || "0", 10);
            patchNode(id, { z_index: Math.max(0, cur - 1) });
        } else if (action === "edit") {
            enterEditMode(selectedNode);
        } else if (action === "delete") {
            deleteNode(id);
        }
        hideContextMenu();
    }

    // ---- 连线重绘（拖拽时） ----
    function redrawEdges() {
        // 只重建 auto edges（按 parent_id 派生），保留手动连线
        edgesLayer.querySelectorAll(".mm-edge.mm-edge-auto").forEach(function (el) {
            el.remove();
        });
        const frag = document.createDocumentFragment();
        nodeById.forEach(function (el) {
            const parentId = el.getAttribute("data-parent");
            if (!parentId) return;
            const parent = nodeById.get(parseInt(parentId, 10));
            if (!parent) return;
            const pm = (parent.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
            if (!pm) return;
            const px = parseFloat(pm[1]), py = parseFloat(pm[2]);
            const pw = parseFloat(parent.querySelector(".mm-shape")?.getAttribute("width") || 120);
            const ph = parseFloat(parent.querySelector(".mm-shape")?.getAttribute("height") || 60);
            const cm = (el.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
            if (!cm) return;
            const cx = parseFloat(cm[1]), cy = parseFloat(cm[2]);
            const ch = parseFloat(el.querySelector(".mm-shape")?.getAttribute("height") || 60);
            const x1 = px + pw, y1 = py + ph / 2, x2 = cx, y2 = cy + ch / 2;
            const mid = (x1 + x2) / 2;
            const path = document.createElementNS(NS_SVG, "path");
            path.setAttribute("class", "mm-edge mm-edge-auto");
            path.setAttribute("data-source", String(parent.id || ""));
            path.setAttribute("data-target", String(parseInt(el.getAttribute("data-id"), 10) || ""));
            path.setAttribute("d", "M " + x1 + "," + y1 + " C " + mid + "," + y1 + " " + mid + "," + y2 + " " + x2 + "," + y2);
            frag.appendChild(path);
        });
        // 手动边也重画一次（拖动时跟随节点）
        edgeById.forEach(function (el) {
            const sid = parseInt(el.getAttribute("data-source"), 10);
            const tid = parseInt(el.getAttribute("data-target"), 10);
            const src = nodeById.get(sid);
            const tgt = nodeById.get(tid);
            if (!src || !tgt) return;
            const sm = (src.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
            const tm = (tgt.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
            if (!sm || !tm) return;
            const sx = parseFloat(sm[1]), sy = parseFloat(sm[2]);
            const sw = parseFloat(src.querySelector(".mm-shape")?.getAttribute("width") || 120);
            const sh = parseFloat(src.querySelector(".mm-shape")?.getAttribute("height") || 60);
            const tx = parseFloat(tm[1]), ty = parseFloat(tm[2]);
            const th = parseFloat(tgt.querySelector(".mm-shape")?.getAttribute("height") || 60);
            const x1 = sx + sw, y1 = sy + sh / 2, x2 = tx, y2 = ty + th / 2;
            const mid = (x1 + x2) / 2;
            el.setAttribute("d", "M " + x1 + "," + y1 + " C " + mid + "," + y1 + " " + mid + "," + y2 + " " + x2 + "," + y2);
        });
        edgesLayer.appendChild(frag);
    }

    // 全量重画手动连线（用于 undo/redo 后节点位置变了）
    function redrawAllManualEdges() {
        edgeById.forEach(function (el) {
            const sid = parseInt(el.getAttribute("data-source"), 10);
            const tid = parseInt(el.getAttribute("data-target"), 10);
            const src = nodeById.get(sid);
            const tgt = nodeById.get(tid);
            if (!src || !tgt) return;
            const sm = (src.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
            const tm = (tgt.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
            if (!sm || !tm) return;
            const sx = parseFloat(sm[1]), sy = parseFloat(sm[2]);
            const sw = parseFloat(src.querySelector(".mm-shape")?.getAttribute("width") || 120);
            const sh = parseFloat(src.querySelector(".mm-shape")?.getAttribute("height") || 60);
            const tx = parseFloat(tm[1]), ty = parseFloat(tm[2]);
            const th = parseFloat(tgt.querySelector(".mm-shape")?.getAttribute("height") || 60);
            const x1 = sx + sw, y1 = sy + sh / 2, x2 = tx, y2 = ty + th / 2;
            const mid = (x1 + x2) / 2;
            el.setAttribute("d", "M " + x1 + "," + y1 + " C " + mid + "," + y1 + " " + mid + "," + y2 + " " + x2 + "," + y2);
        });
    }

    // ---- 给所有现有节点 + 手动连线绑定 ----
    nodesLayer.querySelectorAll(".mm-node").forEach(attachNodeHandlers);
    edgesLayer.querySelectorAll(".mm-edge.mm-edge-manual").forEach(attachEdgeHandlers);

    // ---- 给节点标记 data-parent（拖拽重绘连线用） ----
    nodesLayer.querySelectorAll(".mm-node").forEach(function (el) {
        const id = parseInt(el.getAttribute("data-id"), 10);
        // 后端没渲染 parent_id 属性 —— 我们从 .mm-node 元素的 dataset 里没有
        // 这里用一个隐藏属性：在 _render_mindmap_node 没加；要么后端加，要么前端单独请求拿到 parent_id 关系
        // 简化：直接重新拉一次节点数据来构建映射
    });
    // 拉一次完整节点数据，构建 parent map
    (async function () {
        try {
            const mm = await api("/api/projects/" + projectId + "/mindmap");
            mm.nodes.forEach(function (n) {
                const el = $node(n.id);
                if (el && n.parent_id != null) {
                    el.setAttribute("data-parent", String(n.parent_id));
                }
            });
            // 触发首屏自动同步 toast
            const d = mm.sync_diff || { added: 0, removed: 0, updated: 0 };
            if (d.added || d.removed || d.updated) {
                const parts = [];
                if (d.added) parts.push("已添加 " + d.added + " 个");
                if (d.removed) parts.push("已移除 " + d.removed + " 个");
                if (d.updated) parts.push("已更新 " + d.updated + " 个");
                showToast(parts.join("，") + " 节点");
            }
            // 更新顶部计数
            const autoCountEl = document.querySelector(".mm-title small");
            if (autoCountEl) {
                const ac = mm.nodes.filter(function (n) { return n.kind === "auto"; }).length;
                const mc = mm.nodes.filter(function (n) { return n.kind === "manual"; }).length;
                autoCountEl.innerHTML = "自动 <strong>" + ac + "</strong> · 手动 <strong>" + mc + "</strong>";
            }
        } catch (e) {
            console.error("拉取 mindmap 数据失败：", e);
        }
    })();

    // ---- 画布空白点击：取消选中 ----
    svg.addEventListener("mousedown", function (e) {
        if (e.target === svg || e.target.classList.contains("mm-grid-bg")) {
            clearSelection();
        }
    });

    // ---- 工具栏 ----
    if (toolbar) {
        toolbar.addEventListener("click", function (e) {
            const btn = e.target.closest(".mm-tb-btn");
            if (!btn) return;
            // 字体按钮（data-font）
            const font = btn.getAttribute("data-font");
            if (font) {
                changeFontFamily(font);
                return;
            }
            // 撤销 / 前进
            if (btn.id === "mm-undo-btn") { doUndo(); return; }
            if (btn.id === "mm-redo-btn") { doRedo(); return; }
            // 连线模式
            if (btn.id === "mm-link-btn") {
                if (linkMode) exitLinkMode(); else enterLinkMode();
                return;
            }
            const shape = btn.getAttribute("data-shape");
            if (shape) {
                addNode(shape, { startEdit: true });
                return;
            }
            if (btn.id === "mm-delete-btn") {
                if (selectedEdge) {
                    const id = parseInt(selectedEdge.getAttribute("data-id"), 10);
                    deleteEdge(id);
                } else if (selectedNode) {
                    const id = parseInt(selectedNode.getAttribute("data-id"), 10);
                    const kind = selectedNode.getAttribute("data-kind");
                    if (kind === "auto") {
                        showToast("自动节点无法删除（它是项目数据生成的）", "error");
                        return;
                    }
                    deleteNode(id);
                }
            } else if (btn.id === "mm-clear-manual-btn") {
                if (!confirm("确认清空所有手动节点？\n自动树节点与手动连线不受影响。")) return;
                const toDelete = [];
                nodeById.forEach(function (el) {
                    if (el.getAttribute("data-kind") === "manual") {
                        toDelete.push(parseInt(el.getAttribute("data-id"), 10));
                    }
                });
                toDelete.forEach(deleteNode);
            } else if (btn.id === "mm-font-inc" || btn.id === "mm-font-dec" || btn.id === "mm-font-reset") {
                changeFontSize(btn.id === "mm-font-inc" ? +2 : btn.id === "mm-font-dec" ? -2 : 0);
            }
        });
    }

    // 撤销 / 前进按钮点击
    if (undoBtn) undoBtn.addEventListener("click", doUndo);
    if (redoBtn) redoBtn.addEventListener("click", doRedo);

    // ---- 字号调整 ----
    // 选中节点 → 调整它的 font_size；无选中 → 提示
    // +2 / -2 / 0(=默认 13)
    const FONT_MIN = 8, FONT_MAX = 96, FONT_DEFAULT = 13, FONT_STEP = 2;
    function getCurrentFontSize() {
        if (!selectedNode) return FONT_DEFAULT;
        const lbl = selectedNode.querySelector(".mm-label");
        if (!lbl) return FONT_DEFAULT;
        // 优先读 inline style（活动编辑），其次 data-font-size（持久值）
        const inline = (lbl.getAttribute("style") || "").match(/font-size:\s*(\d+)/);
        if (inline) return parseInt(inline[1], 10);
        return parseInt(selectedNode.getAttribute("data-font-size") || FONT_DEFAULT, 10);
    }
    async function changeFontSize(deltaOrZero) {
        if (!selectedNode) {
            showToast("请先选中一个节点", "error");
            return;
        }
        const id = parseInt(selectedNode.getAttribute("data-id"), 10);
        const cur = getCurrentFontSize();
        let next;
        if (deltaOrZero === 0) next = FONT_DEFAULT;
        else next = Math.min(FONT_MAX, Math.max(FONT_MIN, cur + deltaOrZero));
        if (next === cur) return;
        // 先本地更新（乐观），失败时让 patchNode 的错误 toast 兜底
        const lbl = selectedNode.querySelector(".mm-label");
        if (lbl) lbl.setAttribute("style", "font-size:" + next + "px");
        selectedNode.setAttribute("data-font-size", String(next));
        await patchNode(id, { font_size: next });
    }

    // ---- 同步按钮 ----
    if (syncBtn) {
        syncBtn.addEventListener("click", async function () {
            setSaveState("saving");
            try {
                const d = await api("/api/projects/" + projectId + "/mindmap/sync", { method: "POST" });
                if (d.added || d.removed || d.updated) {
                    const parts = [];
                    if (d.added) parts.push("已添加 " + d.added + " 个");
                    if (d.removed) parts.push("已移除 " + d.removed + " 个");
                    if (d.updated) parts.push("已更新 " + d.updated + " 个");
                    showToast(parts.join("，") + " 节点");
                    // 重载页面简单可靠（避免手动同步节点到 DOM）
                    setTimeout(function () { location.reload(); }, 800);
                } else {
                    showToast("已是最新状态");
                }
                setSaveState("saved");
            } catch (e) {
                console.error(e);
                setSaveState("error");
                showToast("同步失败：" + e.message, "error");
            }
        });
    }

    // ---- 右键菜单点击 ----
    if (contextMenu) {
        contextMenu.addEventListener("click", function (e) {
            const li = e.target.closest("li[data-action]");
            if (!li) return;
            handleContextAction(li.getAttribute("data-action"));
        });
    }
    document.addEventListener("click", function (e) {
        if (contextMenu && !contextMenu.contains(e.target)) hideContextMenu();
    });
    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") hideContextMenu();
    });

    // ---- 键盘快捷键 ----
    document.addEventListener("keydown", function (e) {
        // 输入态下不抢快捷键
        const active = document.activeElement;
        if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.getAttribute && active.getAttribute("contenteditable") === "true")) {
            return;
        }
        const mod = e.ctrlKey || e.metaKey;

        // ===== 全局快捷键（不需要选中）=====
        if (mod && (e.key === "z" || e.key === "Z") && !e.shiftKey) {
            e.preventDefault();
            doUndo();
            return;
        }
        if (mod && ((e.key === "y" || e.key === "Y") || ((e.key === "z" || e.key === "Z") && e.shiftKey))) {
            e.preventDefault();
            doRedo();
            return;
        }
        if (!mod && (e.key === "l" || e.key === "L")) {
            e.preventDefault();
            if (linkMode) exitLinkMode(); else enterLinkMode();
            return;
        }
        if (e.key === "Escape") {
            if (linkMode) { exitLinkMode(); return; }
            if (selectedEdge) { clearEdgeSelection(); return; }
            if (selectedNode) { clearSelection(); return; }
        }

        // ===== 选中边 → 切箭头 / 删除 =====
        if (selectedEdge) {
            const eid = parseInt(selectedEdge.getAttribute("data-id"), 10);
            if ((e.key === "Delete" || e.key === "Backspace")) {
                e.preventDefault();
                deleteEdge(eid);
                return;
            }
            if (!mod && (e.key === "a" || e.key === "A")) {
                e.preventDefault();
                toggleArrow(eid);
                return;
            }
            return; // 边被选中时不再吃其他节点快捷键
        }

        // ===== 选中节点 =====
        if (!selectedNode) return;
        const id = parseInt(selectedNode.getAttribute("data-id"), 10);
        const kind = selectedNode.getAttribute("data-kind");

        if ((e.key === "Delete" || e.key === "Backspace") && kind === "manual") {
            e.preventDefault();
            deleteNode(id);
        } else if (mod && (e.key === "d" || e.key === "D")) {
            e.preventDefault();
            if (kind === "manual") {
                handleContextAction("duplicate");
            } else {
                showToast("自动节点无法复制", "error");
            }
        } else if (mod && (e.key === "c" || e.key === "C")) {
            if (kind === "manual") {
                e.preventDefault();
                handleContextAction("copy");
            }
        } else if (mod && (e.key === "v" || e.key === "V")) {
            if (clipboard) {
                e.preventDefault();
                handleContextAction("paste");
            }
        } else if (e.key === "ArrowUp" || e.key === "ArrowDown" || e.key === "ArrowLeft" || e.key === "ArrowRight") {
            e.preventDefault();
            const step = e.shiftKey ? 10 : 1;
            const t = selectedNode.getAttribute("transform") || "";
            const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
            let nx = m ? parseFloat(m[1]) : 0;
            let ny = m ? parseFloat(m[2]) : 0;
            if (e.key === "ArrowUp") ny = Math.max(0, ny - step);
            if (e.key === "ArrowDown") ny = ny + step;
            if (e.key === "ArrowLeft") nx = Math.max(0, nx - step);
            if (e.key === "ArrowRight") nx = nx + step;
            selectedNode.setAttribute("transform", "translate(" + nx + "," + ny + ")");
            pendingPositions[id] = { x: nx, y: ny };
            if (saveTimer) clearTimeout(saveTimer);
            saveTimer = setTimeout(flushPositions, 400);
            redrawEdges();
            renderAnchors();
        } else if (mod && (e.key === "=" || e.key === "+")) {
            e.preventDefault();
            changeFontSize(FONT_STEP);
        } else if (mod && (e.key === "-" || e.key === "_")) {
            e.preventDefault();
            changeFontSize(-FONT_STEP);
        } else if (mod && e.key === "0") {
            e.preventDefault();
            changeFontSize(0);
        } else if (mod && e.shiftKey && (e.key === "f" || e.key === "F")) {
            // Ctrl+Shift+F 循环下一个字体（system → hei → song → times → kai → mono → system）
            e.preventDefault();
            const order = ["system", "hei", "song", "times", "kai", "mono"];
            const cur = selectedNode.getAttribute("data-font-family") || "system";
            const idx = order.indexOf(cur);
            changeFontFamily(order[(idx + 1) % order.length]);
        }
    });

    // ---- 边的右键菜单 ----
    function showEdgeContextMenu(x, y) {
        if (!edgeContextMenu) return;
        // 复用 mm-context-menu 样式但用 edge-action 区分
        edgeContextMenu.hidden = false;
        const rect = edgeContextMenu.getBoundingClientRect();
        let nx = x, ny = y;
        if (nx + rect.width > window.innerWidth) nx = window.innerWidth - rect.width - 4;
        if (ny + rect.height > window.innerHeight) ny = window.innerHeight - rect.height - 4;
        edgeContextMenu.style.left = nx + "px";
        edgeContextMenu.style.top = ny + "px";
    }
    function hideEdgeContextMenu() {
        if (edgeContextMenu) edgeContextMenu.hidden = true;
    }
    if (edgeContextMenu) {
        edgeContextMenu.addEventListener("click", function (e) {
            const li = e.target.closest("li[data-edge-action]");
            if (!li) return;
            const action = li.getAttribute("data-edge-action");
            if (!selectedEdge) return;
            const eid = parseInt(selectedEdge.getAttribute("data-id"), 10);
            if (action === "toggle-arrow") toggleArrow(eid);
            else if (action === "delete") deleteEdge(eid);
            hideEdgeContextMenu();
        });
    }

    // 点空白 / 点别的节点取消边的选中（selectNode 里已经清掉了）
    svg.addEventListener("mousedown", function (e) {
        if (e.target === svg || e.target.classList.contains("mm-grid-bg")) {
            clearEdgeSelection();
        }
    });

    // 关闭菜单的统一处理
    document.addEventListener("click", function (e) {
        if (contextMenu && !contextMenu.contains(e.target)) hideContextMenu();
        if (edgeContextMenu && !edgeContextMenu.contains(e.target)) hideEdgeContextMenu();
    });

})();