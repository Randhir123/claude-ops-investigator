from claude_ops.hooks import validate_kubectl_verb, require_human_approval


def test_readonly_verbs_allowed():
    assert validate_kubectl_verb("get").allowed
    assert validate_kubectl_verb("describe").allowed
    assert validate_kubectl_verb("logs").allowed


def test_destructive_verbs_blocked():
    assert not validate_kubectl_verb("delete").allowed
    assert not validate_kubectl_verb("apply").allowed
    assert not validate_kubectl_verb("rollout").allowed
    assert not validate_kubectl_verb("exec").allowed


def test_human_approval_gate():
    assert not require_human_approval("restart_pod", approved=False).allowed
    assert require_human_approval("restart_pod", approved=True).allowed
