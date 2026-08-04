/* charts.js —— 纯前端（无库）折线图渲染
 *
 * 每个 .metric-chart 元素上有两个 data-* 属性：
 *   data-key    — 指标名（用作标题）
 *   data-points — JSON 数组 [{step, value, id}, ...]
 *
 * 此脚本把所有 .metric-chart 自动画成一张简单的折线图。
 */

(function () {
    "use strict";

    const charts = document.querySelectorAll(".metric-chart");
    charts.forEach(drawChart);

    function drawChart(canvas) {
        const ctx = canvas.getContext("2d");
        const dpr = window.devicePixelRatio || 1;

        // 适配高分屏
        const cssWidth = canvas.clientWidth || 600;
        const cssHeight = canvas.clientHeight || 160;
        canvas.width = cssWidth * dpr;
        canvas.height = cssHeight * dpr;
        ctx.scale(dpr, dpr);

        let points;
        try {
            points = JSON.parse(canvas.dataset.points || "[]");
        } catch (e) {
            console.error("Bad points JSON", e);
            return;
        }

        if (!points.length) {
            drawEmpty(ctx, cssWidth, cssHeight);
            return;
        }

        // 用 step 作为 x 轴；缺失 step 时退化为索引
        const useStep = points.every((p) => p.step !== null && p.step !== undefined);
        const xs = points.map((p, i) => useStep ? p.step : i);
        const ys = points.map((p) => p.value);

        const xMin = Math.min(...xs);
        const xMax = Math.max(...xs);
        const yMin = Math.min(...ys);
        const yMax = Math.max(...ys);

        const padL = 50, padR = 10, padT = 10, padB = 25;
        const plotW = cssWidth - padL - padR;
        const plotH = cssHeight - padT - padB;

        const xRange = xMax - xMin || 1;
        const yRange = yMax - yMin || 1;

        const X = (x) => padL + ((x - xMin) / xRange) * plotW;
        const Y = (y) => padT + (1 - (y - yMin) / yRange) * plotH;

        // 背景
        ctx.fillStyle = getComputedStyle(document.body).backgroundColor || "#fff";
        ctx.fillRect(0, 0, cssWidth, cssHeight);

        // 网格
        ctx.strokeStyle = "rgba(128,128,128,0.2)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let i = 0; i <= 4; i++) {
            const y = padT + (plotH * i) / 4;
            ctx.moveTo(padL, y);
            ctx.lineTo(cssWidth - padR, y);
        }
        ctx.stroke();

        // y 轴标签
        ctx.fillStyle = "rgba(128,128,128,0.9)";
        ctx.font = "11px sans-serif";
        ctx.textAlign = "right";
        ctx.textBaseline = "middle";
        for (let i = 0; i <= 4; i++) {
            const v = yMax - (yRange * i) / 4;
            const y = padT + (plotH * i) / 4;
            ctx.fillText(formatNum(v), padL - 5, y);
        }

        // x 轴标签（首尾 + 中间）
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        [xMin, (xMin + xMax) / 2, xMax].forEach((x) => {
            ctx.fillText(formatNum(x), X(x), padT + plotH + 6);
        });

        // 折线
        ctx.strokeStyle = "#1095c1";
        ctx.lineWidth = 2;
        ctx.beginPath();
        points.forEach((p, i) => {
            const x = X(xs[i]);
            const y = Y(ys[i]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();

        // 数据点
        ctx.fillStyle = "#1095c1";
        points.forEach((p, i) => {
            ctx.beginPath();
            ctx.arc(X(xs[i]), Y(ys[i]), 3, 0, Math.PI * 2);
            ctx.fill();
        });
    }

    function drawEmpty(ctx, w, h) {
        ctx.fillStyle = getComputedStyle(document.body).backgroundColor || "#fff";
        ctx.fillRect(0, 0, w, h);
        ctx.fillStyle = "rgba(128,128,128,0.6)";
        ctx.font = "13px sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("(暂无数据)", w / 2, h / 2);
    }

    function formatNum(v) {
        if (!isFinite(v)) return "";
        if (Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-3 && v !== 0)) {
            return v.toExponential(2);
        }
        return Number(v.toFixed(3)).toString();
    }
})();