from cu.state import can_run_stage, can_rerun_stage, next_pending_stage, stages_after


def test_can_run_bootstrap_with_no_history():
    assert can_run_stage("bootstrap", stage_status={}) is True


def test_can_run_conversation_only_after_bootstrap_success():
    assert can_run_stage("conversation", stage_status={"bootstrap": "success"}) is True
    assert can_run_stage("conversation", stage_status={"bootstrap": "pending"}) is False
    assert can_run_stage("conversation", stage_status={"bootstrap": "failed"}) is False


def test_can_rerun_compile_requires_conversation_success():
    assert can_rerun_stage(
        "compile",
        stage_status={"bootstrap": "success", "conversation": "success", "compile": "failed"},
    ) is True
    assert can_rerun_stage(
        "compile",
        stage_status={"bootstrap": "success", "conversation": "pending"},
    ) is False


def test_next_pending_returns_first_runnable():
    s = {"bootstrap": "success", "conversation": "success", "compile": "pending", "build": "pending"}
    assert next_pending_stage(s) == "compile"


def test_next_pending_none_when_all_done():
    s = {"bootstrap": "success", "conversation": "success", "compile": "success", "build": "success"}
    assert next_pending_stage(s) is None


def test_next_pending_blocked_when_predecessor_failed():
    s = {"bootstrap": "failed", "conversation": "pending", "compile": "pending", "build": "pending"}
    assert next_pending_stage(s) is None


def test_stages_after():
    assert stages_after("bootstrap") == ("bootstrap", "conversation", "compile", "build")
    assert stages_after("compile") == ("compile", "build")
    assert stages_after("build") == ("build",)
    assert stages_after("unknown") == ()


def test_can_run_unknown_stage_returns_false():
    assert can_run_stage("nope", stage_status={}) is False
