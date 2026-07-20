from __future__ import annotations

import json

from cloud_render.cli import main


def test_readiness_is_offline_and_provisioning_disabled(capsys: object) -> None:
    assert main(["readiness"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["data"]["networkContacted"] is False
    assert output["data"]["providerProcessInvoked"] is False
    assert output["data"]["provisioningEnabled"] is False


def test_authorization_token_is_exact_and_hash_bound(capsys: object) -> None:
    assert main(
        [
            "authorization-token",
            "--scene-sha",
            "A" * 64,
            "--profile-sha",
            "B" * 64,
            "--package-sha",
            "C" * 64,
            "--max-budget",
            "12.5",
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["data"]["authorizationToken"] == (
        "AUTHORIZE BREV BENCHMARK: CCCCCCCCCCCC | BBBBBBBBBBBB | MAX $12.50"
    )


def test_invalid_token_input_returns_machine_readable_error(capsys: object) -> None:
    assert main(
        [
            "authorization-token",
            "--scene-sha",
            "bad",
            "--profile-sha",
            "B" * 64,
            "--package-sha",
            "C" * 64,
            "--max-budget",
            "10",
        ]
    ) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["error"]["code"] == "ValueError"
