from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests


def load_grobid_config(config_path: Path) -> dict[str, Any]:
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def run_command(args: list[str], timeout: int = 8) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = (completed.stdout or completed.stderr or "").replace("\x00", "").strip()
        return int(completed.returncode), output
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def check_docker_engine() -> dict[str, Any]:
    docker_path = shutil.which("docker")
    result = {
        "installed": bool(docker_path),
        "path": docker_path or "",
        "ready": False,
        "message": "",
    }
    if not docker_path:
        result["message"] = "未检测到 docker 命令"
        return result

    code, output = run_command(["docker", "version", "--format", "{{.Server.Version}}"], timeout=10)
    if code == 0 and output:
        result["ready"] = True
        result["message"] = f"Docker Engine 已就绪，Server Version={output}"
        return result

    if output:
        result["message"] = output
    else:
        result["message"] = "Docker Engine 未就绪"
    return result


def check_wsl_status() -> dict[str, Any]:
    if not shutil.which("wsl"):
        return {"installed": False, "message": "未检测到 WSL 命令"}
    code, output = run_command(["wsl", "-l", "-v"], timeout=10)
    return {
        "installed": True,
        "ok": code == 0,
        "message": output or "未返回 WSL 状态",
    }


def check_grobid_server(base_url: str, timeout: int = 4) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/isalive"
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.get(url, timeout=timeout)
        text = (response.text or "").strip()
        return {
            "url": url,
            "reachable": response.status_code == 200,
            "status_code": response.status_code,
            "message": text or f"HTTP {response.status_code}",
        }
    except requests.RequestException as exc:
        return {
            "url": url,
            "reachable": False,
            "status_code": None,
            "message": str(exc),
        }
    finally:
        session.close()


def inspect_grobid_runtime(config_path: Path) -> dict[str, Any]:
    config = load_grobid_config(config_path)
    grobid_server = config.get("grobid_server", "http://localhost:8070")
    grobid = check_grobid_server(grobid_server)
    docker = check_docker_engine()
    wsl = check_wsl_status()
    return {
        "config_path": str(config_path),
        "grobid_server": grobid_server,
        "grobid": grobid,
        "docker": docker,
        "wsl": wsl,
        "ok": bool(grobid.get("reachable")),
    }


def summarize_runtime(status: dict[str, Any]) -> str:
    grobid = status["grobid"]
    docker = status["docker"]
    wsl = status["wsl"]

    lines = [
        f"GROBID 地址: {status['grobid_server']}",
    ]
    if grobid["reachable"]:
        lines.append(f"GROBID 状态: 正常 ({grobid['message']})")
        return "\n".join(lines)

    lines.append(
        "GROBID 状态: 不可用"
        + (f" (HTTP {grobid['status_code']})" if grobid["status_code"] is not None else "")
    )
    lines.append(f"GROBID 详情: {grobid['message']}")

    if docker["installed"]:
        lines.append(f"Docker 状态: {'已就绪' if docker['ready'] else '未就绪'}")
        if docker["message"]:
            lines.append(f"Docker 详情: {docker['message']}")
    else:
        lines.append("Docker 状态: 未安装或未加入 PATH")

    if wsl["installed"]:
        lines.append("WSL 状态:")
        lines.append(wsl["message"])

    lines.extend(remediation_suggestions(status))
    return "\n".join(lines)


def remediation_suggestions(status: dict[str, Any]) -> list[str]:
    lines = ["建议操作:"]
    grobid = status["grobid"]
    docker = status["docker"]

    if grobid["status_code"] == 503:
        lines.append("- GROBID 端口有响应但服务未就绪，先检查容器或 Java 服务日志。")
    elif grobid["status_code"] is None:
        lines.append("- 当前无法连接到 GROBID 端口，说明本地服务没有成功监听。")

    if docker["installed"] and not docker["ready"]:
        lines.append("- Docker Desktop 已安装，但 Linux Engine 未就绪；先修复 Docker，再启动 GROBID 容器。")
        lines.append("- 确认 Docker Desktop 界面显示 Engine running，再执行 GROBID 启动命令。")
    elif not docker["installed"]:
        lines.append("- 如果你打算用 Docker 方式运行 GROBID，请先安装或修复 Docker Desktop。")

    lines.append("- 如果你是手动 Java 启动 GROBID，请确认 config.json 中的 grobid_server 地址与实际端口一致。")
    return lines


def print_runtime_summary(config_path: Path) -> int:
    status = inspect_grobid_runtime(config_path)
    print(summarize_runtime(status))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="检查本地 GROBID 与 Docker 运行状态")
    parser.add_argument("config", nargs="?", default="", help="config.json 路径")
    args = parser.parse_args()

    target = Path(args.config) if args.config else (Path(__file__).resolve().parents[1] / "config.json")
    raise SystemExit(print_runtime_summary(target))
