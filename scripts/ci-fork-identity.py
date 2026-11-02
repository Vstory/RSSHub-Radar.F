#!/usr/bin/env python3
# SCRIPT_VERSION=1.0.0
"""CI 专用：把 runner 工作区的扩展显示名改写成 fork 形态（RSSHub Radar → RSSHub Radar Dev）。

为什么落不进仓库：显示名一旦提交，每次同步上游都撞冲突；上游改了写法还会静默失配 ——
产物照样能装，只是浏览器扩展列表里显示成上游那个扩展。

锚点逐条声明（文件, 期望原文, 改写后, 期望命中次数），任一项命中次数不符即整体失败：
上游改了写法时必须回来同步，不允许静默漏改。

用法：
    python3 scripts/ci-fork-identity.py                 # 默认追加 " Dev"
    python3 scripts/ci-fork-identity.py --suffix Dev
    python3 scripts/ci-fork-identity.py --dry-run       # 只校验锚点，不写盘
    python3 scripts/ci-fork-identity.py --selftest      # 不联网、不碰仓库
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

UPSTREAM_NAME = "RSSHub Radar"
DEFAULT_SUFFIX = "Dev"


def build_edits(suffix: str) -> list[tuple[str, str, str, int]]:
    """(相对路径, 期望原文, 改写后, 期望命中次数)。"""
    new = f"{UPSTREAM_NAME} {suffix}".strip()
    return [
        # 扩展列表 / 商店里显示的名字（wxt 的 manifest.name，未走 i18n）
        ("wxt.config.ts", f'name: "{UPSTREAM_NAME}",', f'name: "{new}",', 1),
        # 打包元数据里的 displayName
        ("package.json", f'"displayName": "{UPSTREAM_NAME}",', f'"displayName": "{new}",', 1),
        # Safari 的 Xcode 工程名与打包目录名都跟随 manifest.name：
        #   safari-convert 用 "build/<名称>/macOS (App)/Info.plist"，
        #   safari-zip 打的是 "build/<名称>" —— 这两处不改，safari-convert 之后
        #   plutil 找不到 Info.plist、zip 也打不进包（构建直接失败，不是静默失败）
        ("package.json", f"build/{UPSTREAM_NAME}", f"build/{new}", 2),
    ]


def verify_edits(edits: list[tuple[str, str, str, int]]) -> list[str]:
    """改写后逐条校验：新写法命中次数必须等于期望，旧写法必须不再出现。

    旧写法是新写法的前缀（RSSHub Radar → RSSHub Radar Dev）时跳过子串比较，否则必然误报残留。
    """
    hits: list[str] = []
    for rel, old, new, want in edits:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        got = text.count(new)
        if got != want:
            hits.append(f"{rel}: 改写后写法出现 {got} 处（应 {want} 处）: {new}")
        if old not in new and old in text:
            hits.append(f"{rel}: 仍残留旧写法: {old}")
    return hits


def validate(suffix: str) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9._ -]+", suffix):
        return [f"--suffix 只允许 A-Za-z0-9._- 和空格（它进 manifest.name 与打包路径）：{suffix!r}"]
    if not suffix.strip():
        return ["--suffix 不能为空"]
    return []


def apply(edits: list[tuple[str, str, str, int]]) -> list[str]:
    problems: list[str] = []
    for rel, old, _new, want in edits:
        path = REPO_ROOT / rel
        if not path.is_file():
            problems.append(f"{rel}: 文件不存在")
            continue
        got = path.read_text(encoding="utf-8").count(old)
        if got != want:
            problems.append(f"{rel}: 期望 {want} 处、实际 {got} 处: {old}")
    if problems:
        return problems

    for rel, old, new, _want in edits:
        path = REPO_ROOT / rel
        path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        print(f"  {rel}: {old} → {new}")
    return verify_edits(edits)


def selftest() -> int:
    """不联网、不碰仓库：临时目录造原文，验改写链路与参数闸门。"""
    global REPO_ROOT

    fails: list[str] = []
    for suffix, want_ok in (("Dev", True), ("dev-build", True), ("", False), ("Bad/Name", False)):
        got_ok = not validate(suffix)
        if got_ok != want_ok:
            fails.append(f"validate({suffix!r}) 期望 {'通过' if want_ok else '拒绝'}，实际相反")

    orig_root = REPO_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        REPO_ROOT = Path(tmp)
        try:
            cfg = 'export default defineConfig({\n  manifest: () => ({\n    name: "RSSHub Radar",\n  }),\n})\n'
            pkg = {
                "name": "rsshub-radar",
                "displayName": "RSSHub Radar",
                "version": "2.3.1",
                "scripts": {
                    "safari-convert": 'xcrun ... "build/RSSHub Radar/macOS (App)/Info.plist"',
                    "safari-zip": 'zip -r build/safari-mv3-prod.zip "build/RSSHub Radar"',
                },
            }
            (REPO_ROOT / "wxt.config.ts").write_text(cfg, encoding="utf-8")
            (REPO_ROOT / "package.json").write_text(
                json.dumps(pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )

            edits = build_edits("Dev")
            problems = apply(edits)
            if problems:
                fails.append(f"改写链路报错：{problems[:2]}")
            patched = (REPO_ROOT / "package.json").read_text(encoding="utf-8")
            loaded = json.loads(patched)
            if loaded["displayName"] != "RSSHub Radar Dev":
                fails.append("displayName 没改成 RSSHub Radar Dev")
            for k in ("safari-convert", "safari-zip"):
                if "build/RSSHub Radar Dev" not in loaded["scripts"][k]:
                    fails.append(f"{k} 里的 Safari 打包路径没跟着改")
            if loaded["version"] != "2.3.1":
                fails.append("改写动到了不该动的字段")
            if 'name: "RSSHub Radar Dev"' not in (REPO_ROOT / "wxt.config.ts").read_text(encoding="utf-8"):
                fails.append("wxt.config.ts 的 manifest.name 没改")

            # 锚点缺失必须整体失败（上游改了写法时的行为）
            (REPO_ROOT / "wxt.config.ts").write_text("export default {}\n", encoding="utf-8")
            if not apply(edits):
                fails.append("锚点缺失时没有失败")

            # 次数不符也必须失败（safari 两处少了一处）
            (REPO_ROOT / "wxt.config.ts").write_text(cfg, encoding="utf-8")
            pkg["scripts"].pop("safari-zip")
            (REPO_ROOT / "package.json").write_text(
                json.dumps(pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            if not apply(edits):
                fails.append("safari 路径只剩一处时没有失败")
        finally:
            REPO_ROOT = orig_root

    for f in fails:
        print(f"::error::selftest 失败：{f}", file=sys.stderr)
    print(f"selftest {'通过' if not fails else '失败'}（{len(fails)} 项）")
    return 0 if not fails else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suffix", default=DEFAULT_SUFFIX)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    invalid = validate(args.suffix)
    if invalid:
        for msg in invalid:
            print(f"::error::{msg}", file=sys.stderr)
        return 1

    edits = build_edits(args.suffix)

    if args.dry_run:
        for rel, old, new, want in edits:
            print(f"[dry-run] {rel}（期望 {want} 处）\n    - {old}\n    + {new}")
        return 0

    hits = apply(edits)
    if hits:
        print("::error::扩展名改写未通过：", file=sys.stderr)
        for h in hits:
            print(f"  - {h}", file=sys.stderr)
        return 1

    print(f"已改写扩展名：{UPSTREAM_NAME} → {UPSTREAM_NAME} {args.suffix}"
          f"（共 {len(edits)} 项，仅工作区，不入库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
