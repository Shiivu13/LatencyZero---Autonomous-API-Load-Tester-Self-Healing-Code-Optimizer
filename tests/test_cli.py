import json

from cli import main


def test_cli_json_single_case(capsys):
    exit_code = main(["--case", "case3", "--requests", "4", "--concurrency", "2", "--json"])
    results = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert len(results) == 1
    result = results[0]
    assert result["case"] == "case3"
    assert result["status"] == "success"
    assert result["before"]["requests"] == 4
    assert result["after"]["p50"] > 0
