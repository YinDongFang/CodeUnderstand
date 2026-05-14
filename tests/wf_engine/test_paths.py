from pathlib import Path
from wf_engine.paths import task_layout


def test_task_layout_under_tasks_root():
    root = Path("/data/tasks") / "tid-1"
    lo = task_layout(root)
    assert lo.workspace == root / "workspace"
    assert lo.zips == root / "artifacts" / "zips"
    assert lo.logs == root / "logs"
