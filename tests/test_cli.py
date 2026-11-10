from cli import run_cli_optimization


def test_cli_runner_json_output(capsys):
    run_cli_optimization(target_case="case1", json_output=True)
    captured = capsys.readouterr()
    assert "case1" in captured.out
    assert "p50_before_ms" in captured.out
    assert "speedup_multiplier" in captured.out


def test_cli_runner_all_cases(capsys):
    run_cli_optimization(target_case="all", json_output=True)
    captured = capsys.readouterr()
    assert "case1" in captured.out
    assert "case2" in captured.out
    assert "case3" in captured.out
