from core.mlops_quality_gate import build_quality_gate_report


def test_quality_gate_passes_with_healthy_pytest_and_matching_provider_secret(tmp_path) -> None:
    report = build_quality_gate_report(
        workspace=tmp_path,
        required_secret_names=("OPENAI_API_KEY", "KOSIS_API_KEY"),
        environment={"OPENAI_API_KEY": "configured", "KOSIS_API_KEY": "configured"},
        runner=lambda _command, _workspace, _timeout: (0, "722 passed in 17.30s"),
        python_version=(3, 12, 1),
        claim_provider="openai",
    )

    assert report.status == "PASS"
    assert report.checks["claim_provider"] == "PASS"
    assert report.checks["claim_provider_secret"] == "PASS"
    assert report.pytest_passed_count == 722
    assert report.pytest_failed_count == 0


def test_quality_gate_holds_when_selected_provider_secret_is_missing(tmp_path) -> None:
    report = build_quality_gate_report(
        workspace=tmp_path,
        required_secret_names=("OPENAI_API_KEY", "KOSIS_API_KEY"),
        environment={"OPENAI_API_KEY": "configured", "KOSIS_API_KEY": "configured"},
        runner=lambda *_: (_ for _ in ()).throw(AssertionError("tests must not run")),
        python_version=(3, 12, 1),
        claim_provider="hcx",
    )

    assert report.status == "HOLD"
    assert report.reason_code == "CLAIM_PROVIDER_SECRET_MISSING"
    assert report.checks["claim_provider"] == "PASS"
    assert report.checks["claim_provider_secret"] == "HOLD_SECRET_STORE"


def test_quality_gate_holds_for_unsupported_provider(tmp_path) -> None:
    report = build_quality_gate_report(
        workspace=tmp_path,
        required_secret_names=(),
        environment={},
        runner=lambda *_: (_ for _ in ()).throw(AssertionError("tests must not run")),
        python_version=(3, 12, 1),
        claim_provider="unknown",
    )

    assert report.status == "HOLD"
    assert report.reason_code == "CLAIM_PROVIDER_UNSUPPORTED"
    assert report.checks["claim_provider"] == "HOLD_UNSUPPORTED"


def test_quality_gate_holds_without_secret_or_when_tests_fail(tmp_path) -> None:
    missing_secret = build_quality_gate_report(
        workspace=tmp_path,
        required_secret_names=("KOSIS_API_KEY",),
        environment={},
        runner=lambda *_: (_ for _ in ()).throw(AssertionError("tests must not run")),
        python_version=(3, 12, 1),
    )
    failed_test = build_quality_gate_report(
        workspace=tmp_path,
        required_secret_names=(),
        environment={},
        runner=lambda _command, _workspace, _timeout: (1, "3 passed, 1 failed"),
        python_version=(3, 12, 1),
    )

    assert missing_secret.reason_code == "REQUIRED_SECRET_MISSING"
    assert failed_test.reason_code == "PYTEST_FAILED"
    assert failed_test.pytest_failed_count == 1
