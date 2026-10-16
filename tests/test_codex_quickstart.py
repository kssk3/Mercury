"""Contracts for a native integration example without real model invocation."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "codex_quickstart.py"


def invoke(
    tmp_path: Path,
    *args: str,
    mode: str = "fix",
    config_extra: str = "",
    layer: dict[str, object] | None = None,
    requirements: object = None,
    metadata: str = "valid",
    env_updates: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    fake = tmp_path / "fake-codex"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time, tomllib\n"
        f"mode = {mode!r}\n"
        f"metadata = {metadata!r}\n"
        f"layer = {layer!r}\n"
        f"requirements = {requirements!r}\n"
        f"calls = pathlib.Path({str(tmp_path / 'calls')!r})\n"
        "def require(condition):\n"
        "    if not condition: sys.exit(91)\n"
        'with calls.open("a") as stream: stream.write(" ".join(sys.argv[1:]) + "\\n")\n'
        'if "app-server" in sys.argv:\n'
        '    (calls.parent / "metadata.pid").write_text(str(os.getpid()))\n'
        '    require(sys.argv[1:] == ["--strict-config", "app-server"])\n'
        '    require(os.getcwd() == "/")\n'
        '    config = {"model_provider": "openai"}\n'
        '    config.update(tomllib.loads((pathlib.Path(os.environ["CODEX_HOME"]) / "config.toml").read_text()))\n'
        "    if layer: config.update(layer)\n"
        '    if metadata in ("workspace", "native-defaults"):\n'
        '        config.update(chatgpt_base_url="https://chatgpt.com/backend-api/", project_root_markers=[".git"])\n'
        "    initialized = False\n"
        "    opted_out = []\n"
        "    for line in sys.stdin:\n"
        "        request = json.loads(line)\n"
        '        method = request["method"]\n'
        '        with calls.open("a") as stream: stream.write(method + "\\n")\n'
        '        if method == "initialize":\n'
        '            expected = {"clientInfo": {"name": "mercury_example", "version": "1"}}\n'
        '            if "capabilities" in request["params"]:\n'
        '                expected["capabilities"] = {"optOutNotificationMethods": ["account/updated", "remoteControl/status/changed"]}\n'
        '                opted_out = expected["capabilities"]["optOutNotificationMethods"]\n'
        '            require(request["params"] == expected)\n'
        '            result = {"userAgent": "fake-codex", "platformFamily": "unix", "platformOs": "macos"}\n'
        '        elif method == "initialized":\n'
        "            initialized = True\n"
        '            if metadata == "workspace":\n'
        '                for notification, params in (("account/updated", {"authMode": "chatgpt", "planType": "plus"}), ("remoteControl/status/changed", {"status": "disabled"})):\n'
        '                    if notification not in opted_out: print(json.dumps({"method": notification, "params": params}), flush=True)\n'
        "            continue\n"
        "        else:\n"
        "            require(initialized)\n"
        '            if method == "config/read":\n'
        '                require(request["params"] == {"includeLayers": False})\n'
        '                if metadata == "timeout": time.sleep(60)\n'
        '                if metadata == "malformed": print("invalid-json", flush=True); continue\n'
        '                if metadata == "oversized": print("x" * 300000, flush=True); continue\n'
        '                if metadata == "warning": print(json.dumps({"method": "configWarning", "params": {"summary": "synthetic-secret"}}), flush=True)\n'
        '                if metadata == "unknown-notification": print(json.dumps({"method": "unknown/updated", "params": {"value": "synthetic-secret"}}), flush=True)\n'
        '                if metadata == "error": print(json.dumps({"id": request["id"], "error": {"message": "synthetic-secret"}}), flush=True); continue\n'
        '                if metadata == "wrong-id": print(json.dumps({"id": request["id"] + 1, "result": {}}), flush=True); continue\n'
        '                if metadata == "malformed-response": print(json.dumps({"id": request["id"], "result": []}), flush=True); continue\n'
        '                if metadata == "missing-provider": config.pop("model_provider")\n'
        '                result = {"config": config, "origins": {}, "layers": None}\n'
        '            elif method == "configRequirements/read":\n'
        '                require(request["params"] == {})\n'
        '                result = {"requirements": requirements}\n'
        '                if metadata == "missing-requirements": result = {}\n'
        '            elif method == "account/read":\n'
        '                require(request["params"] == {"refreshToken": False})\n'
        '                result = {"account": {"type": "chatgpt", "email": "synthetic-secret@example.invalid", "planType": "plus"}, "requiresOpenaiAuth": True}\n'
        '                if metadata == "api-account": result["account"] = {"type": "apiKey"}\n'
        "            else: sys.exit(92)\n"
        '        print(json.dumps({"id": request["id"], "result": result}), flush=True)\n'
        "    sys.exit(0)\n"
        'require("exec" in sys.argv and "--ephemeral" in sys.argv)\n'
        'require(sys.argv[sys.argv.index("-a") + 1] == "never")\n'
        "prompt = json.load(sys.stdin)\n"
        'require(prompt["task_contract"]["allowed_paths"] == ["calculator.py"])\n'
        'if mode == "fail": sys.exit(7)\n'
        'if mode in ("fix", "interrupt-final"):\n'
        '    pathlib.Path("calculator.py").write_text('
        '"def discount(price, percent):\\n    return price * (1 - percent / 100)\\n")\n'
        'if mode == "check-timeout": pathlib.Path("calculator.py").write_text("import time\\ndef discount(price, percent):\\n    time.sleep(20)\\n")\n'
        'if mode == "protect": pathlib.Path("check.py").write_text("pass\\n")\n'
        'pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text("done")\n'
        'print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 5}}))\n',
        encoding="utf-8",
    )
    fake.chmod(0o700)
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text(
        'model = "fixture-model"\nmodel_reasoning_effort = "high"\n' + config_extra
    )
    if metadata == "unreadable":
        (home / "config.toml").unlink()
        (home / "config.toml").mkdir()
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith(("OPENAI_", "CODEX_", "CHATGPT_")):
            env.pop(key)
    env["CODEX_HOME"] = str(home)
    env.update(env_updates or {})
    launch = [sys.executable, str(EXAMPLE)]
    if mode == "interrupt-final":
        launch = [
            sys.executable,
            "-c",
            "import runpy, sys\n"
            "from agent_harness.runner import ControlledCommandRunner\n"
            "original = ControlledCommandRunner.run\n"
            "calls = 0\n"
            "def interrupted(self, *args, **kwargs):\n"
            "    global calls\n"
            "    calls += 1\n"
            "    if calls == 3: raise RuntimeError('final check interrupted')\n"
            "    return original(self, *args, **kwargs)\n"
            "ControlledCommandRunner.run = interrupted\n"
            "sys.argv = sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n",
            str(EXAMPLE),
        ]
    return subprocess.run(
        (
            *launch,
            "--codex-executable",
            str(fake),
            "--evidence-parent",
            str(tmp_path),
            *args,
        ),
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )


def test_unapproved_run_creates_no_fixture_or_native_call(tmp_path: Path) -> None:
    """C1: the explicit approval gate precedes fixture creation and native calls."""
    result = invoke(tmp_path)
    assert result.returncode == 2
    assert "approve-codex-demo" in result.stderr
    assert not (tmp_path / "calls").exists()
    assert not list(tmp_path.glob("mercury-codex-*"))


def summary(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def phase_evidence(
    result: subprocess.CompletedProcess[str],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    facts = summary(result)
    evidence = Path(str(facts["evidence_directory"]))
    assert json.loads((evidence / "summary.json").read_text()) == facts
    events = [
        json.loads(line)
        for line in (evidence / "state" / "events.jsonl").read_text().splitlines()
    ]
    return facts, events


@pytest.mark.parametrize("metadata", ["workspace", "native-defaults"])
def test_supported_native_metadata_allows_one_task(
    tmp_path: Path, metadata: str
) -> None:
    """N2: known startup traffic and effective defaults admit a saved account."""
    result = invoke(tmp_path, "--approve-codex-demo", metadata=metadata)
    assert result.returncode == 0, result.stdout + result.stderr
    facts = summary(result)
    configuration = facts["configuration"]
    assert isinstance(configuration, dict)
    assert configuration["model"] == "fixture-model"
    assert configuration["effort"] == "high"
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls.count("--strict-config app-server") == 1
    assert calls.count("account/read") == calls.count("configRequirements/read") == 1
    assert sum("exec" in line.split() for line in calls) == 1
    evidence = Path(str(facts["evidence_directory"]))
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert all(
        b"synthetic-secret" not in path.read_bytes()
        for path in evidence.rglob("*")
        if path.is_file()
    )


@pytest.mark.parametrize(
    "config_extra",
    [
        'model_provider = "custom"\n',
        'model_provider = "azure"\n',
        'model_provider = "amazon-bedrock"\n',
        'model_provider = "custom"\n[model_providers.custom]\nenv_key = "INHERITED_CUSTOM_KEY"\n',
        '[model_providers.openai]\nbase_url = "https://synthetic-secret.invalid"\n',
        'openai_base_url = "https://synthetic-secret.invalid"\n',
        'chatgpt_base_url = "https://synthetic-secret.invalid"\n',
        'chatgpt_base_url = "https://chatgpt.com/backend-api/"\n',
        'forced_login_method = "api"\n',
        'profile = "custom"\n[profiles.custom]\nmodel_provider = "azure"\n',
        "project_root_markers = []\n",
        'project_root_markers = [".git"]\n',
        'model_providers = "invalid"\n',
    ],
)
def test_nonstandard_provider_configuration_is_refused_before_work(
    tmp_path: Path, config_extra: str
) -> None:
    """A1: ChatGPT status cannot authorize a custom provider or endpoint."""
    result = invoke(
        tmp_path,
        "--approve-codex-demo",
        config_extra=config_extra,
        env_updates={"INHERITED_CUSTOM_KEY": "synthetic-secret"},
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert not list(tmp_path.glob("mercury-codex-*"))
    assert not any(
        "exec" in line.split() for line in (tmp_path / "calls").read_text().splitlines()
    )
    assert "synthetic-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "layer",
    [
        {"model_provider": "azure"},
        {"model_providers": {"openai": {"env_key": "INHERITED_CUSTOM_KEY"}}},
        {"openai_base_url": "https://synthetic-secret.invalid"},
        {"chatgpt_base_url": "https://synthetic-secret.invalid"},
        {"chatgpt_base_url": "https://chatgpt.com/backend-api/other"},
        {"project_root_markers": []},
        {"project_root_markers": [".git", "other"]},
        {"profile": "custom"},
    ],
)
def test_effective_layer_overrides_cannot_hide_behind_safe_user_config(
    tmp_path: Path, layer: dict[str, object]
) -> None:
    """A1: decisions use the CLI's merged layers, not only config.toml."""
    result = invoke(tmp_path, "--approve-codex-demo", layer=layer)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not list(tmp_path.glob("mercury-codex-*"))
    assert "synthetic-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "requirements",
    [
        {"modelProvider": "amazon-bedrock"},
        {
            "modelProviders": {
                "openai": {"base_url": "https://synthetic-secret.invalid"}
            }
        },
        {"chatgptBaseUrl": "https://synthetic-secret.invalid"},
    ],
)
def test_managed_requirements_refuse_ambiguous_execution_path(
    tmp_path: Path, requirements: dict[str, object]
) -> None:
    """A1: this narrow example declines managed paths it cannot establish."""
    result = invoke(tmp_path, "--approve-codex-demo", requirements=requirements)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not list(tmp_path.glob("mercury-codex-*"))
    assert "synthetic-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "key",
    [
        "CODEX_API_KEY",
        "OPENAI_BASE_URL",
        "CODEX_ACCESS_TOKEN",
        "CODEX_AUTH_JSON",
        "CODEX_PROFILE",
        "CHATGPT_BASE_URL",
    ],
)
def test_inherited_provider_auth_or_endpoint_overrides_are_refused(
    tmp_path: Path, key: str
) -> None:
    """A1: inherited CLI routing/auth overrides do not reach a model turn."""
    result = invoke(
        tmp_path, "--approve-codex-demo", env_updates={key: "synthetic-secret"}
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert not (tmp_path / "calls").exists()
    assert not list(tmp_path.glob("mercury-codex-*"))
    assert "synthetic-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "metadata",
    [
        "malformed",
        "oversized",
        "warning",
        "unknown-notification",
        "error",
        "wrong-id",
        "malformed-response",
        "missing-provider",
        "missing-requirements",
        "api-account",
        "unreadable",
    ],
)
def test_unavailable_or_ambiguous_metadata_fails_closed(
    tmp_path: Path, metadata: str
) -> None:
    """A1: a partial/error response cannot silently become default OpenAI."""
    result = invoke(tmp_path, "--approve-codex-demo", metadata=metadata)
    assert result.returncode == 2, result.stdout + result.stderr
    assert not list(tmp_path.glob("mercury-codex-*"))
    assert "synthetic-secret" not in result.stdout + result.stderr


def test_malformed_user_config_is_refused_without_raw_disclosure(
    tmp_path: Path,
) -> None:
    result = invoke(
        tmp_path, "--approve-codex-demo", config_extra='bad = "synthetic-secret\n'
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert not list(tmp_path.glob("mercury-codex-*"))
    assert "synthetic-secret" not in result.stdout + result.stderr


def test_metadata_timeout_reaps_probe_before_refusal(tmp_path: Path) -> None:
    """A1: a stalled model-free probe is bounded and cannot leave a child alive."""
    result = invoke(tmp_path, "--approve-codex-demo", metadata="timeout")
    assert result.returncode == 2, result.stdout + result.stderr
    assert not list(tmp_path.glob("mercury-codex-*"))
    calls = (tmp_path / "calls").read_text().splitlines()
    assert not any("exec" in line.split() for line in calls)
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "metadata.pid").read_text()), 0)


def test_development_failure_is_not_reported_as_final(tmp_path: Path) -> None:
    """A2: no final report exists when the unchanged formula fails development."""
    result = invoke(tmp_path, "--approve-codex-demo", mode="noop")
    facts, events = phase_evidence(result)
    assert result.returncode == 1
    assert facts["baseline"] == facts["development"] == "FAIL"
    assert facts["final"] == "UNKNOWN"
    assert [
        event["phase"]
        for event in events
        if event["event"] == "verification_phase_intent"
    ] == ["development"]


def test_interrupted_final_cannot_reuse_development_pass(tmp_path: Path) -> None:
    """A2: final intent without a returned report leaves final UNKNOWN."""
    result = invoke(tmp_path, "--approve-codex-demo", mode="interrupt-final")
    facts, events = phase_evidence(result)
    assert result.returncode == 1
    assert facts["baseline"] == "FAIL"
    assert facts["development"] == "PASS"
    assert facts["final"] == "UNKNOWN"
    assert [
        event["phase"]
        for event in events
        if event["event"] == "verification_phase_intent"
    ] == ["development", "final"]
    assert not any(event["event"] == "attempt_verification" for event in events)


@pytest.mark.parametrize("mode", ["fix", "noop"])
def test_optimized_python_still_requires_correct_discount(
    tmp_path: Path, mode: str
) -> None:
    """A4: removable assertions must never authorize a wrong calculator."""
    result = invoke(
        tmp_path, "--approve-codex-demo", mode=mode, env_updates={"PYTHONOPTIMIZE": "2"}
    )
    facts, events = phase_evidence(result)
    assert facts["baseline"] == "FAIL"
    assert result.returncode == (0 if mode == "fix" else 1)
    assert facts["development"] == ("PASS" if mode == "fix" else "FAIL")
    assert facts["final"] == ("PASS" if mode == "fix" else "UNKNOWN")
    assert facts["criteria_passed"] is (mode == "fix")
    assert any(event["event"] == "attempt_verification" for event in events)


def test_check_timeout_preserves_unknown_unexecuted_final(tmp_path: Path) -> None:
    """A2: a timeout cannot establish a passing phase or start final verification."""
    result = invoke(tmp_path, "--approve-codex-demo", mode="check-timeout")
    facts, events = phase_evidence(result)
    assert result.returncode == 1
    assert facts["baseline"] == "FAIL"
    assert facts["development"] == facts["final"] == "UNKNOWN"
    assert [
        event["phase"]
        for event in events
        if event["event"] == "verification_phase_intent"
    ] == ["development"]


def test_success_requires_checks_and_preserves_external_receipts(
    tmp_path: Path,
) -> None:
    """C2/C5/C6: real adapter with fake CLI yields attributable checks and cleanup."""
    result = invoke(tmp_path, "--approve-codex-demo")
    assert result.returncode == 0, result.stderr + result.stdout
    facts = summary(result)
    assert facts["status"] == "pass"
    assert facts["baseline"] == "FAIL"
    assert facts["development"] == facts["final"] == "PASS"
    assert facts["command_boundaries"] == 4
    assert facts["criteria_passed"] is True
    assert facts["scope_clear"] is True
    assert facts["protection_clear"] is True
    assert facts["changed_paths"] == ["calculator.py"]
    native = facts["native"]
    assert isinstance(native, dict)
    assert native["attempts"] == 1
    assert native["usage"] == {"input_tokens": 12, "output_tokens": 5}
    evidence = Path(str(facts["evidence_directory"]))
    assert (evidence / "private-native.json").exists()
    assert (evidence / "summary.json").exists()
    assert not (evidence / "state" / "lock").exists()
    events = [
        json.loads(line)
        for line in (evidence / "state" / "events.jsonl").read_text().splitlines()
    ]
    root = next(
        event["repository_root"] for event in events if event["event"] == "turn_intent"
    )
    assert not Path(root).exists()
    assert facts["fixture_removed"] is True
    configuration = facts["configuration"]
    assert isinstance(configuration, dict)
    assert configuration["model"] == "fixture-model"
    assert configuration["effort"] == "high"
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls[:6] == [
        "--strict-config app-server",
        "initialize",
        "initialized",
        "config/read",
        "configRequirements/read",
        "account/read",
    ]
    assert len(calls) == 7 and "exec" in calls[-1].split()
    assert (tmp_path / "codex-home" / "config.toml").read_text() == (
        'model = "fixture-model"\nmodel_reasoning_effort = "high"\n'
    )


def test_native_failure_never_retries_and_preserves_receipt(tmp_path: Path) -> None:
    """C4: a nonzero native turn cannot trigger another attempt."""
    result = invoke(tmp_path, "--approve-codex-demo", mode="fail")
    assert result.returncode == 1
    facts = summary(result)
    assert facts["status"] != "pass"
    native = facts["native"]
    assert isinstance(native, dict)
    assert native["attempts"] == 1
    assert native["exit_code"] == 7
    calls = (tmp_path / "calls").read_text().splitlines()
    assert sum("exec" in call.split() for call in calls) == 1
    evidence = Path(str(facts["evidence_directory"]))
    assert (evidence / "private-native.json").exists()
    assert not (evidence / "state" / "lock").exists()


def test_native_success_message_cannot_clear_protected_changes(tmp_path: Path) -> None:
    """C3: exit zero and model text do not permit protected edits."""
    result = invoke(tmp_path, "--approve-codex-demo", mode="protect")
    assert result.returncode == 1
    facts = summary(result)
    assert facts["status"] != "pass"
    assert facts["scope_clear"] is False
    assert facts["criteria_passed"] is False
    changed_paths = facts["changed_paths"]
    assert isinstance(changed_paths, list)
    assert "check.py" in changed_paths


def test_native_success_without_fix_cannot_substitute_for_check(tmp_path: Path) -> None:
    """C2/C5: a successful CLI process cannot establish fixture correctness."""
    result = invoke(tmp_path, "--approve-codex-demo", mode="noop")
    assert result.returncode == 1
    facts = summary(result)
    assert facts["status"] != "pass"
    assert facts["criteria_passed"] is False
    assert facts["baseline"] == "FAIL"
    assert facts["final"] != "PASS"


def test_api_key_override_is_refused_before_work(tmp_path: Path) -> None:
    """C7: refuse account/billing overrides without any native launch."""
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = "synthetic-test-override"
    result = subprocess.run(
        (
            sys.executable,
            str(EXAMPLE),
            "--approve-codex-demo",
            "--codex-executable",
            "/never-call-real-codex",
        ),
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 2
    assert "API-key overrides" in result.stderr
    assert not list(tmp_path.glob("mercury-codex-*"))


@pytest.fixture
def example_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codex_quickstart", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def configure_preflight_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
    effective: dict[str, object],
    *,
    saved_extra: str = "",
    requirements: object = None,
    account: dict[str, object] | None = None,
) -> Path:
    """Keep the real preflight consumer while replacing only native metadata."""
    home = tmp_path / "isolated-codex-home"
    home.mkdir()
    config = home / "config.toml"
    config.write_text(
        'model = "configured-model"\nmodel_reasoning_effort = "high"\n' + saved_extra
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    identity = (
        {
            "account": {"type": "chatgpt"},
            "requiresOpenaiAuth": True,
        }
        if account is None
        else account
    )

    def fake_metadata(executable: str) -> list[dict[str, object]]:
        assert executable == "never-launch-native"
        return [
            {"userAgent": "fake", "platformFamily": "unix", "platformOs": "macos"},
            {"config": effective},
            {"requirements": requirements},
            identity,
        ]

    monkeypatch.setattr(example_module, "metadata", fake_metadata)
    return config


def test_effective_null_provider_preserves_model_and_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, example_module: ModuleType
) -> None:
    """Present-null default accepts the real consumer without model/settings writes."""
    config = configure_preflight_metadata(
        tmp_path,
        monkeypatch,
        example_module,
        {
            "model_provider": None,
            "model": "configured-model",
            "model_reasoning_effort": "high",
        },
    )
    before = config.read_bytes()
    selected = example_module.configuration("never-launch-native")
    assert selected["model"] == "configured-model"
    assert selected["effort"] == "high"
    assert config.read_bytes() == before


@pytest.mark.parametrize(
    ("effective_extra", "saved_extra", "requirements", "account"),
    [
        ({"model_providers": {"custom": {}}}, "", None, None),
        ({"profiles": {"legacy": {}}}, "", None, None),
        ({"profile": "custom"}, "", None, None),
        ({"openai_base_url": "https://example.invalid"}, "", None, None),
        (
            {"experimental_realtime_ws_base_url": "wss://example.invalid"},
            "",
            None,
            None,
        ),
        ({"chatgpt_base_url": "https://example.invalid"}, "", None, None),
        ({"project_root_markers": []}, "", None, None),
        ({"forced_login_method": "api"}, "", None, None),
        ({"cli_auth_credentials_store": "invalid"}, "", None, None),
        ({}, 'model_provider = "custom"\n', None, None),
        ({}, '[profiles.legacy]\nmodel_provider = "openai"\n', None, None),
        ({}, "", {"modelProvider": "custom"}, None),
        ({}, "", None, {"account": {"type": "apiKey"}, "requiresOpenaiAuth": True}),
        ({}, "", None, {"account": {"type": "chatgpt"}, "requiresOpenaiAuth": False}),
    ],
)
def test_effective_null_provider_keeps_security_restrictions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    example_module: ModuleType,
    effective_extra: dict[str, object],
    saved_extra: str,
    requirements: object,
    account: dict[str, object] | None,
) -> None:
    """Null default routing must continue every existing refusal boundary."""
    effective: dict[str, object] = {"model_provider": None}
    effective.update(effective_extra)
    config = configure_preflight_metadata(
        tmp_path,
        monkeypatch,
        example_module,
        effective,
        saved_extra=saved_extra,
        requirements=requirements,
        account=account,
    )
    before = config.read_bytes()
    with pytest.raises(ValueError):
        example_module.configuration("never-launch-native")
    assert config.read_bytes() == before


@pytest.mark.parametrize(
    ("data", "effective"),
    [
        ({}, True),
        ({"model_provider": ""}, True),
        ({"model_provider": "custom"}, True),
        ({"model_provider": True}, True),
        ({"model_provider": False}, True),
        ({"model_provider": 0}, True),
        ({"model_provider": 1}, True),
        ({"model_provider": {}}, True),
        ({"model_provider": []}, True),
        ({"model_provider": None}, False),
        ({"model_provider": "custom"}, False),
    ],
)
def test_provider_field_shape_stays_fail_closed(
    example_module: ModuleType, data: dict[str, object], effective: bool
) -> None:
    """Missing effective data and invalid shapes are not a native default."""
    with pytest.raises(ValueError, match="unsupported provider"):
        example_module.require_standard_configuration(data, effective=effective)


def test_cleanup_failure_preserves_receipt_without_removal_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, example_module: ModuleType
) -> None:
    """C5/C6: failed context cleanup cannot establish fixture removal."""
    scratch = tmp_path / "owned-fixture"
    scratch.mkdir()

    @contextmanager
    def failed_cleanup(*, prefix: str) -> Iterator[str]:
        yield str(scratch)
        raise OSError("owned fixture cleanup failed")

    for key in tuple(os.environ):
        if key.startswith(("OPENAI_", "CODEX_", "CHATGPT_")):
            monkeypatch.delenv(key)
    monkeypatch.setattr(example_module.shutil, "which", lambda value: sys.executable)
    monkeypatch.setattr(example_module, "configuration", lambda executable: {})
    monkeypatch.setattr(example_module, "git", lambda *args: None)
    monkeypatch.setattr(example_module, "build_context_packet", lambda *a, **k: None)
    monkeypatch.setattr(
        example_module,
        "run_loop",
        lambda *args, **kwargs: SimpleNamespace(
            status="pass",
            reason="completed",
            usage=SimpleNamespace(commands=0),
            criteria=(),
            report=None,
        ),
    )
    monkeypatch.setattr(example_module.tempfile, "TemporaryDirectory", failed_cleanup)
    result = example_module.main(
        ["--approve-codex-demo", "--evidence-parent", str(tmp_path)]
    )
    assert result == 1
    receipts = list(tmp_path.glob("mercury-codex-*/summary.json"))
    assert len(receipts) == 1
    facts = json.loads(receipts[0].read_text())
    assert facts["status"] == "unknown"
    assert facts["reason"] == "OSError"
    assert facts.get("fixture_removed") is not True
    assert scratch.exists()
    assert not (receipts[0].parent / "state" / "lock").exists()


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"type": "turn.completed"}, "UNKNOWN"),
        ({"type": "turn.completed", "usage": None}, "UNKNOWN"),
        ({"type": "turn.completed", "usage": {}}, "UNKNOWN"),
        ({"type": "turn.completed", "usage": {"other": 3}}, "UNKNOWN"),
        (
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": -1,
                    "cached_input_tokens": True,
                    "output_tokens": "5",
                },
            },
            "UNKNOWN",
        ),
        (
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 5,
                },
            },
            {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 5},
        ),
        (
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 12, "output_tokens": None},
            },
            {"input_tokens": 12},
        ),
    ],
)
def test_usage_reports_unknown_only_without_valid_fields(
    tmp_path: Path,
    example_module: ModuleType,
    event: dict[str, object],
    expected: object,
) -> None:
    """C5: absent/invalid usage is unknown; nonnegative integers include zero."""
    adapter = example_module.RecordingAdapter("never-invoked", tmp_path)
    captured = SimpleNamespace(
        text=json.dumps(event), retained_bytes=0, truncated=False
    )
    adapter.result = SimpleNamespace(
        stdout=captured,
        stderr=captured,
        final_message=None,
        process_status="completed",
        exit_code=0,
        duration_seconds=0,
        final_output_status="missing",
    )
    assert adapter.facts()["usage"] == expected
