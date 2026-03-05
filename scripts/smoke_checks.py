import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(command: list[str], env_overrides: dict[str, str] | None = None, cwd: str | None = None) -> None:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, cwd=cwd)


def assert_file_exists(path: str) -> None:
    if not Path(path).exists():
        raise FileNotFoundError(f"Required file not found: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke checks for release/staging")
    parser.add_argument("--mode", default="local", choices=["local", "release", "staging"])
    args = parser.parse_args()

    assert_file_exists("docker-compose.yml")
    assert_file_exists("docker-compose.monitoring.yml")
    assert_file_exists("scripts/healthcheck.py")
    assert_file_exists("app/main.py")
    assert_file_exists("services/api/app/main.py")
    assert_file_exists("services/api/tests/test_ask_top_k_service.py")
    assert_file_exists("scripts/rollback.sh")
    assert_file_exists("infra/prometheus/prometheus.yml")
    assert_file_exists("infra/prometheus/alerts.yml")
    assert_file_exists("infra/grafana/dashboards/rag-bot-overview.json")

    run(["docker", "compose", "-f", "docker-compose.yml", "config"])
    run(["docker", "compose", "-f", "docker-compose.monitoring.yml", "config"])

    if args.mode in {"release", "staging"}:
        run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "promtool",
                "-v",
                f"{Path.cwd() / 'infra/prometheus'}:/etc/prometheus:ro",
                "prom/prometheus:v2.54.1",
                "check",
                "config",
                "/etc/prometheus/prometheus.yml",
            ]
        )
        run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "promtool",
                "-v",
                f"{Path.cwd() / 'infra/prometheus'}:/etc/prometheus:ro",
                "prom/prometheus:v2.54.1",
                "check",
                "rules",
                "/etc/prometheus/alerts.yml",
            ]
        )
        run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "amtool",
                "-v",
                f"{Path.cwd() / 'infra/alertmanager'}:/etc/alertmanager:ro",
                "prom/alertmanager:v0.27.0",
                "check-config",
                "/etc/alertmanager/alertmanager.yml",
            ]
        )
        run(
            [sys.executable, "-m", "pytest", "-q", "tests"],
            env_overrides={"PYTHONPATH": "."},
            cwd="services/api",
        )
        run([sys.executable, "scripts/evaluate_retrieval.py"])

    print(f"✅ Smoke checks passed (mode={args.mode})")


if __name__ == "__main__":
    main()
