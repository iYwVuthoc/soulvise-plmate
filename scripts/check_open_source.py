"""检查拟公开工作树中的常见凭据、运行数据和图片元数据。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".ini",
    ".iss",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
IMAGE_SUFFIXES = {".gif", ".ico", ".jpeg", ".jpg", ".png", ".webp"}
FORBIDDEN_SUFFIXES = {".db", ".key", ".log", ".p12", ".pem", ".pfx", ".sqlite", ".sqlite3"}
FORBIDDEN_NAMES = {"config.json"}
CREDENTIAL_FILE = re.compile(r"^(auth|credentials?|tokens?)([-_.].*)?\.json$", re.IGNORECASE)
_WINDOWS_USER_PATH = r"[A-Za-z]:" + r"\\Users\\" + r"[^\\\r\n]+"
_MAC_USER_PATH = "/" + "Users/" + r"[^/\r\n]+"
_LINUX_USER_PATH = "/" + "home/" + r"[^/\r\n]+"
TEXT_RULES = {
    "OpenAI样式密钥": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub令牌": re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}\b"),
    "Google API密钥": re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    "Slack令牌": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "私钥头": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Bearer令牌": re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}\b", re.IGNORECASE),
    "电子邮箱地址": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "本机用户绝对路径": re.compile(
        f"(?:{_WINDOWS_USER_PATH}|{_MAC_USER_PATH}|{_LINUX_USER_PATH})",
        re.IGNORECASE,
    ),
}
ALLOWED_FINDINGS = {
    # Inno Setup官方简体中文语言文件保留译者署名与联系方式，属于第三方归属信息。
    ("installer/languages/ChineseSimplified.isl", "电子邮箱地址"),
}


def _repository_files() -> list[Path]:
    """优先检查Git拟提交文件；无Git环境时安全回退到目录遍历。"""

    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0:
        return [PROJECT_ROOT / line for line in result.stdout.splitlines() if line.strip()]
    excluded = {".git", ".venv", "build", "deployment", "dist", "outputs", "work"}
    return [
        path
        for path in PROJECT_ROOT.rglob("*")
        if path.is_file()
        and not any(part in excluded for part in path.relative_to(PROJECT_ROOT).parts)
    ]


def _check_image(path: Path) -> list[str]:
    """只报告元数据类别，不输出可能包含隐私的元数据内容。"""

    try:
        with Image.open(path) as image:
            findings = []
            if image.getexif():
                findings.append("EXIF元数据")
            sensitive_keys = {"author", "comment", "description", "software", "xml:com.adobe.xmp"}
            if any(str(key).lower() in sensitive_keys for key in image.info):
                findings.append("可识别图片文本元数据")
            return findings
    except OSError:
        return ["无法读取图片元数据"]


def main() -> int:
    """扫描文件名、文本特征和图片元数据，并使用非零状态阻止误公开。"""

    findings: list[tuple[str, str]] = []
    files = _repository_files()
    for path in files:
        if not path.is_file():
            continue
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        lower_name = path.name.lower()
        lower_suffix = path.suffix.lower()
        is_private_env = lower_name == ".env" or (
            lower_name.startswith(".env.") and lower_name != ".env.example"
        )
        if is_private_env:
            findings.append((relative, "环境变量文件"))
        if (
            lower_suffix in FORBIDDEN_SUFFIXES
            or lower_name in FORBIDDEN_NAMES
            or CREDENTIAL_FILE.match(path.name)
        ):
            findings.append((relative, "运行数据或凭据文件"))
        if lower_suffix in TEXT_SUFFIXES:
            content = path.read_text(encoding="utf-8", errors="ignore")
            for name, pattern in TEXT_RULES.items():
                if pattern.search(content) and (relative, name) not in ALLOWED_FINDINGS:
                    findings.append((relative, name))
        if lower_suffix in IMAGE_SUFFIXES:
            findings.extend((relative, finding) for finding in _check_image(path))

    if findings:
        print("开源安全检查未通过：")
        for relative, category in sorted(set(findings)):
            print(f"- {relative}: {category}")
        return 1
    print(f"开源安全检查通过：已检查 {len(files)} 个拟公开文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
