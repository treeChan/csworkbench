"""从训练脚本里把实验结果推送到工作台的示例。

用法：
    # 先启动工作台：
    #   ./run.sh          (Mac/Linux)
    #   run.bat           (Windows)
    #
    # 然后在另一个终端跑：
    python scripts/example_log.py
    # 或者在你的训练脚本里 import 这个文件的 helper

注意：这个脚本会真的往工作台里写入数据（一个叫「声学成像」的项目）。
只是想看看效果的话，跑完可以在界面上把它删掉。
"""

from __future__ import annotations

import os
from typing import Iterable

import requests


BASE_URL = os.environ.get("WORKBENCH_URL", "http://127.0.0.1:8000")


def _post(path: str, json: dict) -> dict:
    r = requests.post(f"{BASE_URL}{path}", json=json, timeout=5)
    r.raise_for_status()
    return r.json()


def ensure_project(name: str, description: str = "") -> dict:
    """如果项目不存在则创建；存在则返回。"""
    r = requests.get(f"{BASE_URL}/api/projects", timeout=5)
    r.raise_for_status()
    for p in r.json():
        if p["name"] == name:
            return p
    return _post("/api/projects", {"name": name, "description": description})


def ensure_goal(project_id: int, name: str, description: str = "") -> dict:
    """如果大目标不存在则创建；存在则返回。

    实验必须挂在某个大目标下面（schemas.ExperimentCreate 里 goal_id 是必填），
    所以推数据之前要先确保有一个大目标。
    """
    r = requests.get(f"{BASE_URL}/api/projects/{project_id}/goals", timeout=5)
    r.raise_for_status()
    for g in r.json():
        if g["name"] == name:
            return g
    return _post(
        f"/api/projects/{project_id}/goals",
        {"name": name, "description": description},
    )


def create_experiment(
    project_id: int,
    goal_id: int,
    name: str,
    hypothesis: str = "",
) -> dict:
    """创建一次实验。

    注意 2026-08-05 之后 `config` 字段已从 JSON dict 改成 Markdown 文本 (config_md)。
    训练脚本如果要把超参推过来,请用 config_md 字段,内容是 Markdown 原文;
    也可以不传,自己在界面里手记。
    """
    return _post(
        f"/api/projects/{project_id}/experiments",
        {
            "name": name,
            "goal_id": goal_id,
            "hypothesis": hypothesis,
        },
    )


def log_metric(
    experiment_id: int, key: str, value: float,
    note: str = "", step: int | None = None,
) -> dict:
    """记录一个数据点。

    2026-08-05 后 `note` 字段取代了曲线用途 —— 它就是这一行的上下文说明,
    比如「这一轮改了 lr=0.001」「用的是验证集」。
    """
    return _post(
        f"/api/experiments/{experiment_id}/metrics",
        {"key": key, "value": value, "note": note, "step": step},
    )


def log_metrics_batch(
    experiment_id: int, items: Iterable[dict]
) -> list[dict]:
    items = list(items)
    if not items:
        return []
    r = requests.post(
        f"{BASE_URL}/api/experiments/{experiment_id}/metrics/batch",
        json=items,
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def log_note(experiment_id: int, content: str) -> dict:
    return _post(f"/api/experiments/{experiment_id}/notes", {"content": content})


# ---------------------------------------------------------------------------
# 演示
# ---------------------------------------------------------------------------


def demo() -> None:
    print(f"→ connecting to {BASE_URL}")
    proj = ensure_project("声学成像", "12x12 麦克风阵列做声源 NAH 重建")
    print(f"  project: {proj['name']} (id={proj['id']})")

    goal = ensure_goal(proj["id"], "2D 角谱反演验证", "先把 2D 情形跑通再上 3D")
    print(f"  goal: {goal['name']} (id={goal['id']})")

    exp = create_experiment(
        proj["id"],
        goal_id=goal["id"],
        name="IASA-2D-鸽子-v1",
        hypothesis="验证 2D 角谱反演在 50mm 口径下的可行性",
    )
    print(f"  experiment: {exp['name']} (id={exp['id']})")

    # 模拟记录 20 个 loss 点 + 2 个最终指标
    import math

    for step in range(20):
        loss = math.exp(-step / 8) + 0.01 * (step % 3)
        log_metric(exp["id"], "loss", loss, note=f"step={step}", step=step)

    log_metric(exp["id"], "psnr_db", 32.4, note="最终测试集")
    log_metric(exp["id"], "ssim", 0.91, note="最终测试集")
    print("  logged 20 loss points + 2 final metrics")

    log_note(
        exp["id"],
        "## 观察\n\n"
        "- loss 在第 5 个 epoch 后开始下降\n"
        "- SSIM 达到 0.91，符合预期\n"
        "- 下一步：换 3D 反演",
    )
    print("  logged 1 markdown note")
    print(f"\n✓ 在浏览器打开 {BASE_URL} 查看")


if __name__ == "__main__":
    demo()