"""按 model_profiles.json 启动唯一一个 SpaceMIT llama-server。"""
import argparse
import json
import os
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "model_profiles.json"
DEFAULT_PROFILE_FILE = Path("/etc/medicine-llm/profile")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_profile_name(args, config):
    if args.profile:
        return args.profile
    if args.profile_file.is_file():
        value = args.profile_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return config["default_profile"]


def supported_extra_args(binary, requested):
    if not requested:
        return []
    try:
        help_text = subprocess.run(
            [binary, "--help"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [arg for arg in requested if arg.split("=", 1)[0] in help_text]


def build_command(config, profile, config_dir: Path | None = None):
    binary = config.get("server_binary", "/usr/bin/llama-server")
    if not Path(binary).is_file():
        raise SystemExit(f"找不到 SpaceMIT llama-server：{binary}")
    if profile.get("backend") != "llama_cpp_chat":
        raise SystemExit("此启动器只接受 llama_cpp_chat 类型，Ollama 基线请单独运行")

    model_path = Path(profile["model_path"])
    if not model_path.is_absolute():
        model_path = ((config_dir or Path.cwd()) / model_path).resolve()
    if not model_path.is_file():
        raise SystemExit(f"模型不存在：{model_path}")
    expected_size = profile.get("expected_size_bytes")
    if expected_size and model_path.stat().st_size != expected_size:
        raise SystemExit(
            f"模型大小不符：实际 {model_path.stat().st_size}，期望 {expected_size}，拒绝启动"
        )
    # Do not pin LLM to a fixed CPU thread count.  The deployment may still
    # override this explicitly through K1_LLM_THREADS for a benchmark.
    thread_override = os.environ.get("K1_LLM_THREADS", "").strip()
    threads = int(thread_override) if thread_override else None
    if threads is not None and not 1 <= threads <= 8:
        raise SystemExit("K1_LLM_THREADS 必须在1到8之间")

    command = [
        binary,
        "--model", str(model_path),
        "--host", str(config.get("host", "127.0.0.1")),
        "--port", str(config.get("port", 8080)),
        "--ctx-size", str(profile.get("ctx_size", 1024)),
        "--batch-size", str(profile.get("batch_size", 128)),
    ]
    if threads is not None:
        command.extend(["--threads", str(threads)])
    ubatch_flag = supported_extra_args(binary, ["--ubatch-size"])
    if ubatch_flag:
        command.extend(["--ubatch-size", str(profile.get("ubatch_size", 128))])
    parallel = profile.get("parallel", 1)
    optional = ["--parallel", str(parallel), "--alias", profile.get("model_alias", "medicine-llm")]
    optional_flags = supported_extra_args(binary, optional[::2])
    for flag, value in zip(optional[::2], optional[1::2]):
        if flag in optional_flags:
            command.extend([flag, str(value)])
    requested_options = profile.get("optional_options", {})
    supported_options = supported_extra_args(binary, list(requested_options))
    for flag, value in requested_options.items():
        if flag in supported_options:
            command.extend([flag, str(value)])
    command.extend(supported_extra_args(binary, profile.get("extra_args", [])))
    return command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--profile")
    parser.add_argument("--profile-file", type=Path, default=DEFAULT_PROFILE_FILE)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    # The LLM is intentionally CPU + IME1 only in the fixed K1 allocation.
    # This is an execution policy, not an API or lifecycle change.
    os.environ["SPACEMIT_DISABLE_TCM"] = "1"
    config = load_json(args.config)
    profile_name = read_profile_name(args, config)
    try:
        profile = config["profiles"][profile_name]
    except KeyError:
        raise SystemExit(f"未知模型配置：{profile_name}") from None
    command = build_command(config, profile, args.config.resolve().parent)
    print(f"profile={profile_name}")
    print("command=" + " ".join(command), flush=True)
    if args.dry_run:
        return
    os.execv(command[0], command)


if __name__ == "__main__":
    main()
