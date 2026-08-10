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

    // 状态
    let selectedNode = null;          // 主选中 (兼容旧代码)
    let selectedNodes = new Set();    // 多选集合 (含主选中)
    let selectedEdge = null;
    // 框选
    let marquee = null;               // { rectEl, startX, startY, moved }
    let marqueeRect = null;           // SVG <rect> 元素
    let clipboard = null; // 复制的节点数据（不含 id）
    let dragState = null;
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

    // 取节点尺寸 (从 data-w/data-h 读, 兼容旧的 .mm-shape width/height)
    // 椭圆/菱形/六边形/箭头 不是 <rect>, .mm-shape 上没有 width/height 属性
    function getNodeSize(el) {
        const w = parseFloat(el.getAttribute("data-w") || "0");
        const h = parseFloat(el.getAttribute("data-h") || "0");
        if (w > 0 && h > 0) return { w: w, h: h };
        // 兜底: 尝试从 .mm-shape 读 (仅 rect 有效)
        const shape = el.querySelector(".mm-shape");
        if (shape) {
            const sw = parseFloat(shape.getAttribute("width") || "120");
            const sh = parseFloat(shape.getAttribute("height") || "60");
            return { w: sw, h: sh };
        }
        return { w: 120, h: 60 };
    }

    // 智能选边端点: 取源/目标中心 x 中点, 谁在左就从源右侧出 + 进入目标左侧;
    // 谁在右就从源左侧出 + 进入目标右侧. 这样无论节点在哪个方向都能从最近一侧连.
    function edgeAnchors(sx, sy, sw, sh, tx, ty, tw, th) {
        const srcCx = sx + sw / 2;
        const tgtCx = tx + tw / 2;
        let x1, x2;
        if (srcCx <= tgtCx) {
            // 源在左 (或同列) → 从源右侧出, 进入目标左侧
            x1 = sx + sw;
            x2 = tx;
        } else {
            // 源在右 → 从源左侧出, 进入目标右侧
            x1 = sx;
            x2 = tx + tw;
        }
        return {
            x1: x1,
            y1: sy + sh / 2,
            x2: x2,
            y2: ty + th / 2,
        };
    }

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
    // opts.additive = true 时为多选模式 (Shift+点击 / 框选释放)
    function selectNode(el, opts) {
        opts = opts || {};
        if (!el) {
            // 清空所有选中
            selectedNodes.forEach(function (n) { n.classList.remove("selected"); });
            selectedNodes.clear();
            selectedNode = null;
            clearEdgeSelection();
            renderAnchors();
            return;
        }
        if (opts.additive) {
            // 切换式多选
            if (selectedNodes.has(el)) {
                selectedNodes.delete(el);
                el.classList.remove("selected");
                // 主选中挪到剩余的最后一个；如果空了，置 null
                if (selectedNodes.size > 0) {
                    selectedNode = Array.from(selectedNodes).pop();
                } else {
                    selectedNode = null;
                }
            } else {
                selectedNodes.add(el);
                el.classList.add("selected");
                selectedNode = el;
            }
            // 边选中清掉
            if (selectedEdge) selectedEdge.classList.remove("selected");
            selectedEdge = null;
            renderAnchors();
            return;
        }
        // 普通单选：清空其他
        if (selectedNodes.has(el) && selectedNodes.size === 1) {
            // 已经是唯一选中 → 不动
            return;
        }
        selectedNodes.forEach(function (n) { n.classList.remove("selected"); });
        selectedNodes.clear();
        selectedNodes.add(el);
        el.classList.add("selected");
        selectedNode = el;
        // 切换节点选中时, 边的选中取消 + 重新画锚点
        clearEdgeSelection();
        renderAnchors();
        // 同步填色/字色 picker 显示当前节点的颜色
        syncColorPickers(el);
    }

    function syncColorPickers(el) {
        const fc = document.getElementById("mm-fill-color");
        const ffc = document.getElementById("mm-font-color");
        if (!el) {
            if (fc) fc.value = "#ffffff";
            if (ffc) ffc.value = "#1f2937";
            return;
        }
        const fcVal = el.getAttribute("data-fill-color") || "#ffffff";
        const ffcVal = el.getAttribute("data-font-color") || "#1f2937";
        if (fc) fc.value = fcVal;
        if (ffc) ffc.value = ffcVal;
    }

    function clearSelection() {
        selectNode(null);
    }

    // ---- 选中边 ----
    function selectEdge(el) {
        if (selectedEdge === el && !el) return;
        if (selectedEdge) selectedEdge.classList.remove("selected");
        selectedEdge = el || null;
        if (selectedEdge) selectedEdge.classList.add("selected");
        // 边被选中时, 节点的选中清空 + 隐藏锚点
        if (selectedEdge) {
            selectedNodes.forEach(function (n) { n.classList.remove("selected"); });
            selectedNodes.clear();
            selectedNode = null;
            renderAnchors();
        }
    }
    function clearEdgeSelection() {
        if (!selectedEdge) return;
        selectedEdge.classList.remove("selected");
        selectedEdge = null;
    }

    // 辅助: 遍历所有选中的 manual 节点 (批量操作时跳过 auto)
    function forEachSelectedManual(cb) {
        selectedNodes.forEach(function (el) {
            if (el.getAttribute("data-kind") === "manual") cb(el);
        });
    }
    function getSelectedIds() {
        const ids = [];
        selectedNodes.forEach(function (el) {
            ids.push(parseInt(el.getAttribute("data-id"), 10));
        });
        return ids;
    }

    // 批量删除选中的 manual 节点 (跳过 auto)
    async function deleteSelectedNodes() {
        const ids = [];
        selectedNodes.forEach(function (el) {
            if (el.getAttribute("data-kind") === "manual") {
                ids.push(parseInt(el.getAttribute("data-id"), 10));
            }
        });
        const autoCount = selectedNodes.size - ids.length;
        if (autoCount > 0) showToast("已跳过 " + autoCount + " 个自动节点", "warn");
        if (ids.length === 0) return;
        // 先清空选中 (deleteNode 内会移除 DOM 和 nodeById)
        selectedNodes.forEach(function (el) { el.classList.remove("selected"); });
        selectedNodes.clear();
        selectedNode = null;
        for (const id of ids) {
            await deleteNode(id, true);  // skipUndo: 单个删除不进栈, 整体进一个栈
        }
        // 整体作为一个撤销单元
        if (ids.length > 1) {
            pushCommand({ type: "bulk-delete-nodes", ids: ids.slice(), label: "删除 " + ids.length + " 个节点" });
        }
        renderAnchors();
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
            "data-w": String(node.w),
            "data-h": String(node.h),
            transform: `translate(${node.x},${node.y})`,
        };
        if (node.fill_color) attrs["data-fill-color"] = node.fill_color;
        if (node.font_color) attrs["data-font-color"] = node.font_color;
        if (node.parent_id != null) attrs["data-parent"] = String(node.parent_id);
        attrs["data-z"] = String(node.z_index != null ? node.z_index : 0);
        const g = svgEl("g", attrs);

        const shapeEl = buildShapeEl(node);
        if (shapeEl) {
            // 自定义填色: inline style 覆盖 CSS 默认 (sticky-* 那种预定义色)
            if (node.fill_color) shapeEl.setAttribute("style", "fill:" + node.fill_color);
            g.appendChild(shapeEl);
        }

        // foreignObject + 内嵌 XHTML div（允许换行 + 内联编辑）
        const fo = svgEl("foreignObject", {
            x: 0, y: 0, width: node.w, height: node.h,
        });
        const labelDiv = document.createElementNS(NS_XHTML, "div");
        labelDiv.setAttribute("class", "mm-label");
        const labelStyleParts = [];
        if (node.font_size) labelStyleParts.push("font-size:" + node.font_size + "px");
        if (node.font_color) labelStyleParts.push("color:" + node.font_color);
        if (labelStyleParts.length) labelDiv.setAttribute("style", labelStyleParts.join(";"));
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
                w: getNodeSize(el).w,
                h: getNodeSize(el).h,
                z_index: parseInt(el.getAttribute("data-z") || "0", 10),
                font_size: parseInt(el.getAttribute("data-font-size") || "13", 10),
                font_family: el.getAttribute("data-font-family") || "system",
                kind: el.getAttribute("data-kind"),
            };
        }
        // 收集跟这个节点相连的 manual edges (后端 FK CASCADE 已删, 但前端 DOM 还在)
        // auto edges 由 redrawEdges() 根据 parent_id 重生成, 不需要管
        const orphanEdges = [];
        edgeById.forEach(function (edgeEl, edgeId) {
            const sid = parseInt(edgeEl.getAttribute("data-source"), 10);
            const tid = parseInt(edgeEl.getAttribute("data-target"), 10);
            if (sid === id || tid === id) {
                orphanEdges.push({
                    id: edgeId,
                    source_id: sid,
                    target_id: tid,
                    arrow: edgeEl.getAttribute("data-arrow") === "true",
                });
            }
        });
        setSaveState("saving");
        try {
            await api("/api/mindmap/nodes/" + id, { method: "DELETE" });
            if (el) {
                el.remove();
                nodeById.delete(id);
                if (selectedNode === el) clearSelection();
            }
            // 立即移除 orphan edges (前端), 否则用户右键这条边找不到目标节点, 没法删
            orphanEdges.forEach(function (e) {
                const edgeEl = edgeById.get(e.id);
                if (edgeEl) {
                    edgeEl.remove();
                    edgeById.delete(e.id);
                }
            });
            // 触发 auto edges 重画 (父节点 / 子节点引用刚被删, 路径要更新)
            redrawEdges();
            if (!skipUndo && snapshot) {
                pushCommand({
                    type: "delete-node",
                    node: snapshot,
                    orphanEdges: orphanEdges,
                });
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
        // 锚点只在选中唯一节点时显示 (多选/边选中时隐藏, 避免散落)
        if (!selectedNode) return;
        if (selectedNodes.size > 1) return;
        if (selectedEdge) return;
        const t = selectedNode.getAttribute("transform") || "";
        const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
        const x = m ? parseFloat(m[1]) : 0;
        const y = m ? parseFloat(m[2]) : 0;
        const w = parseFloat(selectedNode.getAttribute("data-w") || "120");
        const h = parseFloat(selectedNode.getAttribute("data-h") || "60");
        // 锚点向外推 8px, 让圆点完全在节点轮廓外 (避免视觉上"压"在边上)
        const offset = 8;
        const pts = [
            { side: "t", cx: x + w / 2,       cy: y - offset },
            { side: "r", cx: x + w + offset, cy: y + h / 2 },
            { side: "b", cx: x + w / 2,       cy: y + h + offset },
            { side: "l", cx: x - offset,     cy: y + h / 2 },
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
        const sSize = getNodeSize(src);
        const tSize = getNodeSize(tgt);
        const sw = sSize.w, sh = sSize.h;
        const tT = tgt.getAttribute("transform") || "";
        const tM = tT.match(/translate\(([-\d.]+),([-\d.]+)\)/);
        const tx = tM ? parseFloat(tM[1]) : 0;
        const ty = tM ? parseFloat(tM[2]) : 0;
        const tw = tSize.w, th = tSize.h;
        const a = edgeAnchors(sx, sy, sw, sh, tx, ty, tw, th);
        const x1 = a.x1, y1 = a.y1, x2 = a.x2, y2 = a.y2;
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
                    // 同步重建关联的 manual edges
                    if (cmd.orphanEdges && cmd.orphanEdges.length) {
                        for (const oe of cmd.orphanEdges) {
                            // 源/目标两端都得还活着 (另一端可能也在这条 undo 链里被恢复)
                            // 用 cmd.node.id 替换新节点的 id (因为 cmd.node.id 是旧 id)
                            const sid = oe.source_id === cmd.node.id ? n.id : oe.source_id;
                            const tid = oe.target_id === cmd.node.id ? n.id : oe.target_id;
                            if (!$node(sid) || !$node(tid)) continue;
                            try {
                                const edge = await api(
                                    "/api/projects/" + projectId + "/mindmap/edges",
                                    { method: "POST", body: {
                                        source_id: sid, target_id: tid, arrow: oe.arrow,
                                    } }
                                );
                                const edgeEl = buildEdgeEl(edge);
                                if (edgeEl) {
                                    edgesLayer.appendChild(edgeEl);
                                    edgeById.set(edge.id, edgeEl);
                                    attachEdgeHandlers(edgeEl);
                                }
                            } catch (e) { /* 已存在等错误, 静默 */ }
                        }
                    }
                } else {
                    const id = cmd.node.id;
                    await api("/api/mindmap/nodes/" + id, { method: "DELETE" });
                    const el = $node(id);
                    if (el) { el.remove(); nodeById.delete(id); }
                    // redo 时也清掉关联 edges (orphanEdges 是快照, 用原始 ids 找)
                    if (cmd.orphanEdges && cmd.orphanEdges.length) {
                        cmd.orphanEdges.forEach(function (oe) {
                            const edgeEl = edgeById.get(oe.id);
                            if (edgeEl) {
                                edgeEl.remove();
                                edgeById.delete(oe.id);
                            }
                        });
                    }
                    redrawEdges();
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
            } else if (cmd.type === "bulk-color") {
                const list = useBefore ? cmd.before : cmd.after;
                const prop = cmd.prop;
                for (const item of list) {
                    const body = {};
                    body[prop === "fill" ? "fill_color" : "font_color"] = item.value || null;
                    await api("/api/mindmap/nodes/" + item.id, {
                        method: "PATCH", body: body,
                    });
                    const el = $node(item.id);
                    if (el) applyNodeColor(el, prop, item.value);
                }
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
        if (selectedNodes.size === 0) {
            showToast("请先选中一个节点", "error");
            return;
        }
        if (!FONT_STACKS[family]) return;
        // 多选: 每个节点独立 patch
        const targets = [];
        selectedNodes.forEach(function (el) {
            targets.push({
                el: el,
                id: parseInt(el.getAttribute("data-id"), 10),
                before: el.getAttribute("data-font-family") || "system",
            });
        });
        setSaveState("saving");
        try {
            for (const t of targets) {
                if (t.before === family) continue;
                t.el.setAttribute("data-font-family", family);
                const lbl = t.el.querySelector(".mm-label");
                if (lbl) lbl.setAttribute("style", "font-size:" + (lbl.style.fontSize || "13px") + ";font-family:" + FONT_STACKS[family]);
                await api("/api/mindmap/nodes/" + t.id, {
                    method: "PATCH", body: { font_family: family },
                });
                pushCommand({ type: "patch-node", id: t.id, before: { font_family: t.before }, after: { font_family: family } });
            }
            setSaveState("saved");
        } catch (e) {
            console.error(e);
            setSaveState("error");
            showToast("改字体失败：" + e.message, "error");
        }
    }

    // ===================================================================
    // 连边交互：Figma 风格 — 普通点击只选中, 连边必须从锚点拖到目标节点
    // (mindmap.js:1195-1254 的锚点拖拽连边是唯一入口)
    // ===================================================================

    // ---- 拖拽 ----
    function attachNodeHandlers(el) {
        el.addEventListener("mousedown", function (e) {
            if (e.button !== 0) return;
            // 阻止 foreignObject 内 contenteditable 抢焦点
            const labelDiv = el.querySelector(".mm-label");
            if (labelDiv && labelDiv.classList.contains("editing")) return;

            e.stopPropagation();

            if (e.shiftKey) {
                // Shift+点击 → 多选切换
                selectNode(el, { additive: true });
            } else if (selectedNodes.has(el)) {
                // 点击已选中的唯一节点 → 取消选中, 隐藏锚点
                if (selectedNodes.size === 1) {
                    clearSelection();
                }
                // 多选状态下点击某个 → 保持当前多选 (Figma 行为)
            } else {
                // 普通点击: 单选 (清空其他). 不会自动连边, 连边请拖锚点.
                selectNode(el);
            }

            // 记录每个选中节点的 origX/origY, 一起拖
            const dragItems = [];
            selectedNodes.forEach(function (n) {
                const t = n.getAttribute("transform") || "";
                const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
                const ox = m ? parseFloat(m[1]) : 0;
                const oy = m ? parseFloat(m[2]) : 0;
                dragItems.push({
                    el: n,
                    id: parseInt(n.getAttribute("data-id"), 10),
                    origX: ox,
                    origY: oy,
                    kind: n.getAttribute("data-kind"),
                });
            });

            dragState = {
                items: dragItems,
                startX: e.clientX,
                startY: e.clientY,
                moved: false,
            };

            function onMove(ev) {
                if (!dragState) return;
                const dx = ev.clientX - dragState.startX;
                const dy = ev.clientY - dragState.startY;
                if (Math.abs(dx) + Math.abs(dy) > 2) dragState.moved = true;
                dragState.items.forEach(function (it) {
                    const nx = Math.max(0, it.origX + dx);
                    const ny = Math.max(0, it.origY + dy);
                    it.el.setAttribute("transform", "translate(" + nx + "," + ny + ")");
                    pendingPositions[it.id] = { x: nx, y: ny };
                });
                // 同步移动相关连线 + 重画锚点
                redrawEdges();
                renderAnchors();
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
                w: getNodeSize(selectedNode).w,
                h: getNodeSize(selectedNode).h,
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
            const pSize = getNodeSize(parent);
            const pw = pSize.w, ph = pSize.h;
            const cm = (el.getAttribute("transform") || "").match(/translate\(([-\d.]+),([-\d.]+)\)/);
            if (!cm) return;
            const cx = parseFloat(cm[1]), cy = parseFloat(cm[2]);
            const cSize = getNodeSize(el);
            const cw = cSize.w, ch = cSize.h;
            const a = edgeAnchors(px, py, pw, ph, cx, cy, cw, ch);
            const x1 = a.x1, y1 = a.y1, x2 = a.x2, y2 = a.y2;
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
            const sSize = getNodeSize(src);
            const sw = sSize.w, sh = sSize.h;
            const tx = parseFloat(tm[1]), ty = parseFloat(tm[2]);
            const tSize = getNodeSize(tgt);
            const tw = tSize.w, th = tSize.h;
            const a = edgeAnchors(sx, sy, sw, sh, tx, ty, tw, th);
            const x1 = a.x1, y1 = a.y1, x2 = a.x2, y2 = a.y2;
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
            const sSize = getNodeSize(src);
            const sw = sSize.w, sh = sSize.h;
            const tx = parseFloat(tm[1]), ty = parseFloat(tm[2]);
            const tSize = getNodeSize(tgt);
            const tw = tSize.w, th = tSize.h;
            const a = edgeAnchors(sx, sy, sw, sh, tx, ty, tw, th);
            const x1 = a.x1, y1 = a.y1, x2 = a.x2, y2 = a.y2;
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

    // ---- 画布空白按下：进入框选模式 (空白拖拽出矩形框选节点; 仅点击 = 取消选中) ----
    svg.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        // 仅在 SVG 自身或网格背景按下时才算空白 (节点/边/锚点的 mousedown 会 stopPropagation)
        if (!(e.target === svg || e.target.classList.contains("mm-grid-bg"))) return;

        const ctm = svg.getScreenCTM();
        if (!ctm) return;
        const pt = svg.createSVGPoint();
        pt.x = e.clientX;
        pt.y = e.clientY;
        const local = pt.matrixTransform(ctm.inverse());

        marqueeRect = document.createElementNS(NS_SVG, "rect");
        marqueeRect.setAttribute("class", "mm-marquee");
        marqueeRect.setAttribute("x", String(local.x));
        marqueeRect.setAttribute("y", String(local.y));
        marqueeRect.setAttribute("width", "0");
        marqueeRect.setAttribute("height", "0");
        // 放到最顶层, 但在节点上方 → 让用户看到拖到哪些节点上
        nodesLayer.parentNode.insertBefore(marqueeRect, nodesLayer);

        marquee = {
            startX: local.x,
            startY: local.y,
            moved: false,
        };

        function onMove(ev) {
            if (!marqueeRect) return;
            const pt2 = svg.createSVGPoint();
            pt2.x = ev.clientX;
            pt2.y = ev.clientY;
            const cur = pt2.matrixTransform(svg.getScreenCTM().inverse());
            const x = Math.min(marquee.startX, cur.x);
            const y = Math.min(marquee.startY, cur.y);
            const w = Math.abs(cur.x - marquee.startX);
            const h = Math.abs(cur.y - marquee.startY);
            if (w > 2 || h > 2) marquee.moved = true;
            marqueeRect.setAttribute("x", String(x));
            marqueeRect.setAttribute("y", String(y));
            marqueeRect.setAttribute("width", String(w));
            marqueeRect.setAttribute("height", String(h));
        }
        function onUp() {
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
            if (marqueeRect && marquee) {
                if (marquee.moved) {
                    // 框选: 计算相交节点
                    const bx = parseFloat(marqueeRect.getAttribute("x"));
                    const by = parseFloat(marqueeRect.getAttribute("y"));
                    const bw = parseFloat(marqueeRect.getAttribute("width"));
                    const bh = parseFloat(marqueeRect.getAttribute("height"));
                    // 先清空选中
                    selectedNodes.forEach(function (n) { n.classList.remove("selected"); });
                    selectedNodes.clear();
                    selectedNode = null;
                    // 把相交的 manual + auto 节点都选上 (auto 也允许选中用来排版, 但拖拽时跳过)
                    nodeById.forEach(function (el) {
                        if (isNodeInRect(el, bx, by, bw, bh)) {
                            selectedNodes.add(el);
                            el.classList.add("selected");
                            selectedNode = el;
                        }
                    });
                    if (selectedNodes.size === 0) {
                        // 框了个寂寞
                        renderAnchors();
                    } else {
                        clearEdgeSelection();
                        renderAnchors();
                    }
                } else {
                    // 单纯空白点击 → 清空选中
                    clearSelection();
                }
                if (marqueeRect.parentNode) marqueeRect.parentNode.removeChild(marqueeRect);
            }
            marqueeRect = null;
            marquee = null;
        }
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
    });

    // 节点矩形与框选矩形相交判定 (用 g 的 data-x data-y data-w data-h / transform)
    function isNodeInRect(el, rx, ry, rw, rh) {
        const t = el.getAttribute("transform") || "";
        const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
        const x = m ? parseFloat(m[1]) : 0;
        const y = m ? parseFloat(m[2]) : 0;
        const w = parseFloat(el.getAttribute("data-w") || "120");
        const h = parseFloat(el.getAttribute("data-h") || "60");
        // 矩形相交: !(左 > 右 || 右 < 左 || 上 > 下 || 下 < 上)
        return !(x + w < rx || x > rx + rw || y + h < ry || y > ry + rh);
    }

    // ---- 工具栏 ----
    if (toolbar) {
        toolbar.addEventListener("click", function (e) {
            const btn = e.target.closest(".mm-tb-btn");
            if (!btn) return;
            // 撤销 / 前进
            if (btn.id === "mm-undo-btn") { doUndo(); return; }
            if (btn.id === "mm-redo-btn") { doRedo(); return; }
            // 导出 PNG
            if (btn.id === "mm-export-btn") { exportPNG(); return; }
            const shape = btn.getAttribute("data-shape");
            if (shape) {
                addNode(shape, { startEdit: true });
                return;
            }
            if (btn.id === "mm-delete-btn") {
                if (selectedEdge) {
                    const id = parseInt(selectedEdge.getAttribute("data-id"), 10);
                    deleteEdge(id);
                } else if (selectedNodes.size > 0) {
                    deleteSelectedNodes();
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
                changeFontSize(btn.id === "mm-font-inc" ? +FONT_STEP : btn.id === "mm-font-dec" ? -FONT_STEP : 0);
            }
        });
    }

    // 撤销 / 前进按钮点击
    if (undoBtn) undoBtn.addEventListener("click", doUndo);
    if (redoBtn) redoBtn.addEventListener("click", doRedo);

    // ---- 字体下拉框 ----
    const fontSelect = document.getElementById("mm-font-select");
    if (fontSelect) {
        fontSelect.addEventListener("change", function () {
            const v = fontSelect.value;
            changeFontFamily(v);
            // 视觉上回滚到上次 (changeFontFamily 内部会修改 data-font-family)
            // 但下拉框要保持显示用户选的, 不回滚
        });
    }

    // ---- 填色 / 字色 ----
    function applyColorToSelection(prop, value) {
        if (selectedNodes.size === 0) {
            showToast("请先选中一个节点", "error");
            return;
        }
        const before = [];
        const after = [];
        const ids = [];
        selectedNodes.forEach(function (el) {
            const id = parseInt(el.getAttribute("data-id"), 10);
            const prev = el.getAttribute(prop === "fill" ? "data-fill-color" : "data-font-color") || null;
            before.push({ id: id, value: prev });
            ids.push(id);
            // 立即更新 DOM (视觉)
            applyNodeColor(el, prop, value);
            after.push({ id: id, value: value });
        });
        pushCommand({ type: "bulk-color", prop: prop, before: before, after: after });
        // 持久化
        const payload = {};
        payload[prop === "fill" ? "fill_color" : "font_color"] = value;
        ids.forEach(function (id) {
            patchNode(id, payload, { recordUndo: false });
        });
    }

    function applyNodeColor(el, prop, value) {
        if (value === null || value === undefined) {
            // 清掉自定义
            if (prop === "fill") {
                el.removeAttribute("data-fill-color");
                const shape = el.querySelector(".mm-shape");
                if (shape) shape.removeAttribute("style");
            } else {
                el.removeAttribute("data-font-color");
                const lbl = el.querySelector(".mm-label");
                if (lbl) {
                    const s = (lbl.getAttribute("style") || "").replace(/color:[^;]*;?/g, "").trim();
                    if (s) lbl.setAttribute("style", s);
                    else lbl.removeAttribute("style");
                }
            }
            return;
        }
        if (prop === "fill") {
            el.setAttribute("data-fill-color", value);
            const shape = el.querySelector(".mm-shape");
            if (shape) shape.setAttribute("style", "fill:" + value);
        } else {
            el.setAttribute("data-font-color", value);
            const lbl = el.querySelector(".mm-label");
            if (lbl) {
                const cur = (lbl.getAttribute("style") || "");
                const without = cur.replace(/color:[^;]*;?/g, "").trim();
                const next = without ? without + ";color:" + value : "color:" + value;
                lbl.setAttribute("style", next);
            }
        }
    }

    const fillColorInput = document.getElementById("mm-fill-color");
    if (fillColorInput) {
        fillColorInput.addEventListener("input", function () {
            applyColorToSelection("fill", fillColorInput.value);
        });
    }
    const fontColorInput = document.getElementById("mm-font-color");
    if (fontColorInput) {
        fontColorInput.addEventListener("input", function () {
            applyColorToSelection("font", fontColorInput.value);
        });
    }

    // ---- 字号调整 ----
    // 选中节点 → 调整它的 font_size；无选中 → 提示
    // +2 / -2 / 0(=默认 13)
    const FONT_MIN = 8, FONT_MAX = 96, FONT_DEFAULT = 13, FONT_STEP = 2;
    function getCurrentFontSize() {
        if (selectedNodes.size === 0) return FONT_DEFAULT;
        const el = selectedNode || selectedNodes.values().next().value;
        const lbl = el.querySelector(".mm-label");
        if (!lbl) return FONT_DEFAULT;
        // 优先读 inline style（活动编辑），其次 data-font-size（持久值）
        const inline = (lbl.getAttribute("style") || "").match(/font-size:\s*(\d+)/);
        if (inline) return parseInt(inline[1], 10);
        return parseInt(el.getAttribute("data-font-size") || FONT_DEFAULT, 10);
    }
    async function changeFontSize(deltaOrZero) {
        if (selectedNodes.size === 0) {
            showToast("请先选中一个节点", "error");
            return;
        }
        // 多选: 对每个 manual 节点应用 (auto 节点 font_size 跟着同步节点走, 不让手动改)
        const targets = [];
        selectedNodes.forEach(function (el) {
            if (el.getAttribute("data-kind") !== "manual") return;
            const lbl = el.querySelector(".mm-label");
            const inline = (lbl ? lbl.getAttribute("style") || "" : "").match(/font-size:\s*(\d+)/);
            const cur = inline ? parseInt(inline[1], 10) : parseInt(el.getAttribute("data-font-size") || FONT_DEFAULT, 10);
            let next;
            if (deltaOrZero === 0) next = FONT_DEFAULT;
            else next = Math.min(FONT_MAX, Math.max(FONT_MIN, cur + deltaOrZero));
            if (next !== cur) targets.push({ el: el, id: parseInt(el.getAttribute("data-id"), 10), next: next });
        });
        const skipped = selectedNodes.size - targets.length;
        // 先本地更新（乐观）
        targets.forEach(function (t) {
            const lbl = t.el.querySelector(".mm-label");
            if (lbl) lbl.setAttribute("style", "font-size:" + t.next + "px");
            t.el.setAttribute("data-font-size", String(t.next));
        });
        for (const t of targets) {
            await patchNode(t.id, { font_size: t.next });
        }
        if (skipped > 0 && targets.length === 0) {
            showToast("自动节点字号由项目数据决定，不能改", "error");
        }
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
        if (e.key === "Escape") {
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

        // ===== Ctrl+A → 全选 =====
        if (mod && !e.shiftKey && (e.key === "a" || e.key === "A")) {
            e.preventDefault();
            selectedNodes.forEach(function (n) { n.classList.remove("selected"); });
            selectedNodes.clear();
            selectedNode = null;
            nodeById.forEach(function (el) {
                selectedNodes.add(el);
                el.classList.add("selected");
            });
            if (selectedNodes.size > 0) selectedNode = Array.from(selectedNodes).pop();
            renderAnchors();
            return;
        }

        // ===== 选中节点 =====
        if (selectedNodes.size === 0) return;
        const id = parseInt(selectedNode.getAttribute("data-id"), 10);
        const kind = selectedNode.getAttribute("data-kind");

        if ((e.key === "Delete" || e.key === "Backspace")) {
            e.preventDefault();
            // 多选时: 批量删 manual; 单选 auto 提示
            if (selectedNodes.size === 1 && kind === "auto") {
                showToast("自动节点无法删除（它是项目数据生成的）", "error");
                return;
            }
            deleteSelectedNodes();
        } else if (mod && (e.key === "d" || e.key === "D")) {
            e.preventDefault();
            if (selectedNodes.size === 1 && kind === "manual") {
                handleContextAction("duplicate");
            } else if (selectedNodes.size > 1) {
                showToast("多选状态下不支持复制（请单选后 Ctrl+D）", "warn");
            } else {
                showToast("自动节点无法复制", "error");
            }
        } else if (mod && (e.key === "c" || e.key === "C")) {
            // 仅单选 manual 时复制; 多选不抢键盘
            if (selectedNodes.size === 1 && kind === "manual") {
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
            // 多选: 一起平移
            selectedNodes.forEach(function (el) {
                if (el.getAttribute("data-kind") === "auto") return;
                const t = el.getAttribute("transform") || "";
                const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
                let nx = m ? parseFloat(m[1]) : 0;
                let ny = m ? parseFloat(m[2]) : 0;
                if (e.key === "ArrowUp") ny = Math.max(0, ny - step);
                if (e.key === "ArrowDown") ny = ny + step;
                if (e.key === "ArrowLeft") nx = Math.max(0, nx - step);
                if (e.key === "ArrowRight") nx = nx + step;
                el.setAttribute("transform", "translate(" + nx + "," + ny + ")");
                const eid = parseInt(el.getAttribute("data-id"), 10);
                pendingPositions[eid] = { x: nx, y: ny };
            });
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

    // ===================================================================
    // 导出 PNG: 自己构造纯 SVG (不依赖 foreignObject), 然后 Image → Canvas → 下载
    // 避开 foreignObject 是因为 Chrome 在 Image → Canvas 路径下对它的渲染支持很差
    // ===================================================================
    async function exportPNG() {
        setSaveState("saving");
        try {
            // 1) 收集节点 + 连线数据 (从当前 DOM 拿真实位置/尺寸)
            const nodeList = [];
            nodeById.forEach(function (el) {
                const t = el.getAttribute("transform") || "";
                const m = t.match(/translate\(([-\d.]+),([-\d.]+)\)/);
                const x = m ? parseFloat(m[1]) : 0;
                const y = m ? parseFloat(m[2]) : 0;
                const w = parseFloat(el.getAttribute("data-w") || "120");
                const h = parseFloat(el.getAttribute("data-h") || "60");
                // 颜色: 用存储的 data-fill-color / data-font-color, 避免依赖 getComputedStyle
                // (Image → Canvas 路径不解析 CSS 变量, 之前太黑的根因)
                const kind = el.getAttribute("data-kind");
                const shape = el.getAttribute("data-shape");
                const fillColor = el.getAttribute("data-fill-color")
                    || (kind === "auto" ? "#f3e8ff"
                        : shape && shape.indexOf("sticky-") === 0 ? null  // sticky 用预定义
                        : "#ffffff");
                const fontColor = el.getAttribute("data-font-color") || "#1f2937";
                // stroke 按 kind / shape 给默认, 用户没单独改过 stroke 就用这个
                const stroke = (kind === "auto") ? "#7C3AED" : "#7C3AED";
                // 标签
                const lblEl = el.querySelector(".mm-label");
                let label = lblEl ? (lblEl.textContent || "") : "";
                // fontSize: 内联 style 上的优先
                let fontSize = parseInt(el.getAttribute("data-font-size") || "13", 10);
                if (lblEl) {
                    const inline = (lblEl.getAttribute("style") || "").match(/font-size:\s*(\d+)/);
                    if (inline) fontSize = parseInt(inline[1], 10);
                }
                const fontFamily = el.getAttribute("data-font-family") || "system";
                const parentId = el.getAttribute("data-parent");
                nodeList.push({
                    id: parseInt(el.getAttribute("data-id"), 10),
                    kind: kind,
                    shape: shape,
                    x: x, y: y, w: w, h: h,
                    label: label,
                    fontSize: fontSize,
                    fontFamily: fontFamily,
                    fillColor: fillColor,
                    fontColor: fontColor,
                    strokeColor: stroke,
                    parentId: parentId ? parseInt(parentId, 10) : null,
                });
            });

            if (nodeList.length === 0) {
                showToast("画布为空，无可导出的节点", "warn");
                setSaveState("saved");
                return;
            }

            const nodeByIdMap = {};
            nodeList.forEach(function (n) { nodeByIdMap[n.id] = n; });

            // 2) 收集连线
            const edgeList = [];
            // auto edges (parent → child)
            nodeList.forEach(function (n) {
                if (n.kind === "auto" && n.parentId) {
                    const p = nodeByIdMap[n.parentId];
                    if (p) edgeList.push({ source: p, target: n, manual: false });
                }
            });
            // manual edges
            svg.querySelectorAll(".mm-edge-manual").forEach(function (e) {
                const sid = parseInt(e.getAttribute("data-source"), 10);
                const tid = parseInt(e.getAttribute("data-target"), 10);
                const src = nodeByIdMap[sid], tgt = nodeByIdMap[tid];
                if (src && tgt) edgeList.push({ source: src, target: tgt, manual: true });
            });

            // 3) viewBox
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            nodeList.forEach(function (n) {
                minX = Math.min(minX, n.x);
                minY = Math.min(minY, n.y);
                maxX = Math.max(maxX, n.x + n.w);
                maxY = Math.max(maxY, n.y + n.h);
            });
            const padding = 24;
            minX -= padding; minY -= padding;
            maxX += padding; maxY += padding;
            const vbW = maxX - minX;
            const vbH = maxY - minY;

            // 4) 构造纯 SVG (无 foreignObject, 只用 SVG 基本元素)
            const scale = 2;
            const parts = [];
            parts.push('<?xml version="1.0" encoding="UTF-8"?>');
            parts.push('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                + 'viewBox="' + minX + ' ' + minY + ' ' + vbW + ' ' + vbH + '" '
                + 'width="' + Math.ceil(vbW * scale) + '" height="' + Math.ceil(vbH * scale) + '">');
            // 箭头 marker
            parts.push('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#7C3AED"/></marker></defs>');
            // 白色背景: Image → Canvas 路径不会自动铺白, 先铺一块保证对比
            parts.push('<rect x="' + minX + '" y="' + minY + '" width="' + vbW + '" height="' + vbH + '" fill="#ffffff"/>');

            // 5) 边 (auto 在底层, manual 在上层)
            edgeList.forEach(function (e) {
                const anchors = edgeAnchors(
                    e.source.x, e.source.y, e.source.w, e.source.h,
                    e.target.x, e.target.y, e.target.w, e.target.h,
                );
                const mid = (anchors.x1 + anchors.x2) / 2;
                const d = "M " + anchors.x1 + "," + anchors.y1
                    + " C " + mid + "," + anchors.y1 + " " + mid + "," + anchors.y2
                    + " " + anchors.x2 + "," + anchors.y2;
                if (e.manual) {
                    parts.push('<path d="' + d + '" stroke="#7C3AED" stroke-width="2" fill="none" marker-end="url(#arrow)"/>');
                } else {
                    parts.push('<path d="' + d + '" stroke="#A78BFA" stroke-width="1.5" fill="none" opacity="0.55"/>');
                }
            });

            // 6) 节点 (用 <text> 替代 foreignObject; 多行拆成多个 <tspan>)
            function escXml(s) {
                return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
            }
            // sticky 默认填色 (Image 路径下不读 CSS 类, 单独给值)
            const STICKY_FILLS = {
                "sticky-yellow": "#fff4a3",
                "sticky-pink":   "#ffd6e0",
                "sticky-blue":   "#cfe6ff",
            };
            const STICKY_STROKES = {
                "sticky-yellow": "#b58a00",
                "sticky-pink":   "#b5506b",
                "sticky-blue":   "#4a78a8",
            };
            nodeList.forEach(function (n) {
                const w = n.w, h = n.h;
                // 计算最终填色/描边 (sticky 用预定义, text 框不画形状)
                let fill = n.fillColor;
                let stroke = n.strokeColor;
                if (n.shape && n.shape.indexOf("sticky-") === 0) {
                    fill = fill || STICKY_FILLS[n.shape] || "#fff4a3";
                    stroke = stroke || STICKY_STROKES[n.shape] || "#7C3AED";
                } else if (n.shape === "text") {
                    fill = "none";
                    stroke = "none";
                } else {
                    fill = fill || "#ffffff";
                    stroke = stroke || "#7C3AED";
                }
                const fc = n.fontColor || "#1f2937";
                parts.push('<g transform="translate(' + n.x + ',' + n.y + ')">');
                // 形状
                if (n.shape === "ellipse") {
                    parts.push('<ellipse cx="' + w/2 + '" cy="' + h/2 + '" rx="' + w/2 + '" ry="' + h/2 + '" stroke="' + stroke + '" stroke-width="1.5" fill="' + fill + '"/>');
                } else if (n.shape === "diamond") {
                    parts.push('<polygon points="' + w/2 + ',0 ' + w + ',' + h/2 + ' ' + w/2 + ',' + h + ' 0,' + h/2 + '" stroke="' + stroke + '" stroke-width="1.5" fill="' + fill + '"/>');
                } else if (n.shape === "hexagon") {
                    const cut = Math.min(20, w / 4);
                    parts.push('<polygon points="' + cut + ',0 ' + (w-cut) + ',0 ' + w + ',' + h/2 + ' ' + (w-cut) + ',' + h + ' ' + cut + ',' + h + ' 0,' + h/2 + '" stroke="' + stroke + '" stroke-width="1.5" fill="' + fill + '"/>');
                } else if (n.shape === "arrow") {
                    parts.push('<polygon points="0,' + h*0.3 + ' ' + w*0.7 + ',' + h*0.3 + ' ' + w*0.7 + ',0 ' + w + ',' + h/2 + ' ' + w*0.7 + ',' + h + ' ' + w*0.7 + ',' + h*0.7 + ' 0,' + h*0.7 + '" stroke="' + stroke + '" stroke-width="1.5" fill="' + fill + '"/>');
                } else if (n.shape === "text") {
                    // text 形状: 无背景, 只画文字
                } else {
                    // rect / rounded / sticky-*
                    let rx = "4";
                    if (n.shape === "rounded") rx = "12";
                    else if (n.shape && n.shape.indexOf("sticky-") === 0) rx = "6";
                    parts.push('<rect width="' + w + '" height="' + h + '" rx="' + rx + '" stroke="' + stroke + '" stroke-width="1.5" fill="' + fill + '"/>');
                }
                // 文字: 多行拆 tspan, 居中
                const rawLines = (n.label || "").split(/\r?\n/);
                const safeLines = rawLines.length > 0 ? rawLines : [""];
                const family = FONT_STACKS[n.fontFamily] || FONT_STACKS.system;
                // 估算行高
                const lineHeight = n.fontSize * 1.25;
                const totalH = safeLines.length * lineHeight;
                // 首行 y: 垂直居中起点 (dominant-baseline 在 Chrome canvas 里支持度不一, 直接算 y)
                let startY = (h - totalH) / 2 + n.fontSize * 0.9;
                // family 必须 XML 转义 (含双引号会破坏 font-family="..." 边界)
                parts.push('<text x="' + (w/2) + '" y="' + startY + '" text-anchor="middle" font-family="' + escXml(family) + '" font-size="' + n.fontSize + '" fill="' + fc + '">');
                safeLines.forEach(function (line, idx) {
                    const dy = idx === 0 ? 0 : lineHeight;
                    parts.push('<tspan x="' + (w/2) + '" dy="' + dy + '">' + escXml(line) + '</tspan>');
                });
                parts.push('</text>');
                parts.push('</g>');
            });

            parts.push('</svg>');
            const svgString = parts.join("");

            // 7) 渲染到 canvas
            const svgBlob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
            const url = URL.createObjectURL(svgBlob);
            const img = new Image();
            img.onload = function () {
                const canvas = document.createElement("canvas");
                canvas.width = Math.ceil(vbW * scale);
                canvas.height = Math.ceil(vbH * scale);
                const ctx = canvas.getContext("2d");
                ctx.fillStyle = "#ffffff";
                ctx.fillRect(0, 0, canvas.width, canvas.height);
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                URL.revokeObjectURL(url);

                canvas.toBlob(function (blob) {
                    if (!blob) {
                        showToast("导出失败：canvas 转换失败", "error");
                        setSaveState("error");
                        return;
                    }
                    const a = document.createElement("a");
                    a.href = URL.createObjectURL(blob);
                    const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
                    a.download = "mindmap-" + projectId + "-" + ts + ".png";
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1500);
                    showToast("已导出 PNG");
                    setSaveState("saved");
                }, "image/png");
            };
            img.onerror = function (ev) {
                URL.revokeObjectURL(url);
                // 输出 SVG 字符串到 console + 暴露到 window, 方便调试
                window.__lastExportError = svgString;
                const head = svgString.slice(0, 300).replace(/\s+/g, " ");
                console.error("PNG export failed. SVG head (first 300 chars):", head);
                showToast("导出失败：SVG 转图片失败。SVG 已存到 window.__lastExportError, 控制台可看开头", "error");
                setSaveState("error");
            };
            img.src = url;
        } catch (e) {
            console.error(e);
            showToast("导出失败：" + e.message, "error");
            setSaveState("error");
        }
    }

})();