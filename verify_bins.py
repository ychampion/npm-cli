"""Run published packages through ordinary npm install, ci, rebuild, run and exec."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

PREVIOUS = "9517da53ac7dff5ac4f1f8d3cffd764c62002789"
REVISED = "c23773ceb10b6449bdc06b2abb5da18658d7b93d"


def write(path, data):
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def run(args, cwd, env, check=True):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300)
    if check and result.returncode:
        raise RuntimeError(f"{args} in {cwd}\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    return result


def exercise(cli, cases, label, *, upgrade=False, compiler_only=False):
    receipts = []
    cases.mkdir(exist_ok=True, parents=True)
    env = dict(os.environ, npm_config_cache=str(cases / "cache"), npm_config_update_notifier="false",
               npm_config_fund="false", npm_config_audit="false", npm_config_registry="https://registry.npmjs.org",
               npm_config_userconfig=os.devnull, npm_config_globalconfig=str(cases / "global.npmrc"),
               PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1", FORCE_COLOR="0")
    version = run(["node", str(cli), "--version"], cases, env).stdout.strip()
    node = run(["node", "--version"], cases, env).stdout.strip()

    def npm(folder, *args, check=True):
        return run(["node", str(cli), *args], folder, env, check)

    def receipt(case, phase, **values):
        row = dict(revision=label, platform=os.name, npm=version, node=node, case=case, phase=phase, **values)
        receipts.append(row)
        print(json.dumps(row, ensure_ascii=True), flush=True)

    def create_compiler(alias):
        project = cases / f"ts-{alias}"
        write(project / "package.json", {"name": "alias-bin-repro", "version": "1.0.0", "private": True,
              "devDependencies": {f"@typescript/{alias}-native": "npm:typescript@~7.0.2",
                                  "typescript": "npm:@typescript/typescript6@~6.0.2"}})
        for phase, command in (("fresh", ["install"]), ("clean", ["ci", "--offline"])):
            npm(project, *command)
            tsc = npm(project, "exec", "tsc", "--", "--version").stdout.strip()
            api = run(["node", "-p", "require('typescript').version"], project, env).stdout.strip()
            assert tsc.startswith("Version ") and api.startswith("6."), (tsc, api)
            receipt(project.name, phase, compiler=tsc, api=api)

    def create_workspaces(case):
        project = cases / case
        child = project / "tooling"
        root_deps = {"playwright": "1.57.0"}
        child_deps = {"@playwright/test": "1.56.1"}
        if case == "mixed":
            root_deps["@playwright/browser-chromium"] = "1.57.0"
            child_deps = {"@playwright/experimental-ct-react": "1.56.1", "@playwright/test": "1.57.0"}
        if case == "separate":
            root_deps["@playwright/test"] = "1.57.0"
            child_deps["playwright"] = "1.56.1"
        scripts = {"bin-version": "playwright --version",
                   "run-test": "playwright test --workers=1 --reporter=line"}
        write(project / "package.json", {"name":"root", "private":True, "workspaces":["tooling"],
              "devDependencies":root_deps, "scripts":scripts})
        write(child / "package.json", {"name":"tooling", "version":"1.0.0", "private":True,
              "dependencies":child_deps, "scripts":scripts})
        for path in (project, child):
            (path / "playwright.config.cjs").write_text("module.exports = { testDir: './specs' }\n")
            (path / "specs").mkdir(exist_ok=True)
            (path / "specs" / "smoke.spec.cjs").write_text(
                "const { test, expect } = require('@playwright/test')\n"
                "test('consumer test', () => { expect(2+2).toBe(4) })\n")
        if case == "mixed":
            (project / "playwright.config.cjs").write_text("module.exports = { testDir: './tooling/specs' }\n")
        return project

    def check_workspace(project, phase):
        result = {}
        for who, prefix in (("root", []), ("child", ["--workspace=tooling"])):
            selected = npm(project, "--silent", *prefix, "run", "bin-version").stdout.strip()
            assert re.search(r"Version 1\.5[67]\.\d", selected), selected
            result[who] = {"version": selected}
            should_test = (project.name, who) in {("mixed", "root"), ("reduced", "child")} or project.name == "separate"
            if should_test:
                play = npm(project, "--silent", *prefix, "run", "run-test", check=False)
                output = play.stdout + play.stderr
                assert "1 passed" in output or "Playwright Test did not expect" in output, output
                result[who]["test"] = "passed" if play.returncode == 0 else "runner and test API differ"
            if project.name == "reduced" and who == "child":
                child = project / "tooling"
                manifest = Path(run(["node", "-p", "require.resolve('@playwright/test/package.json')"],
                                    child, env).stdout.strip())
                declared = json.loads(manifest.read_text(encoding="utf-8"))
                executable = manifest.parent / declared["bin"]["playwright"]
                control = run(["node", executable, "test", "--workers=1", "--reporter=line"], child, env)
                assert "1 passed" in control.stdout
                child_bin = child / "node_modules/.bin" / ("playwright.cmd" if os.name == "nt" else "playwright")
                result[who].update(declared=declared["version"], declared_test="passed", has_local_bin=child_bin.is_file())
        receipt(project.name, phase, **result)

    if not upgrade:
        for name in ("z","a"):
            create_compiler(name)
    if not compiler_only:
        for name in ("mixed", "reduced", "separate"):
            project = cases/name if upgrade else create_workspaces(name)
            phases = [("same-tree-install", ["install", "--offline"]), ("rebuild", ["rebuild", "--offline"])] if upgrade else [("fresh", ["install"])]
            phases.append(("clean", ["ci", "--offline"]))
            for phase, cmd in phases:
                npm(project, *cmd)
                check_workspace(project, phase)
    return receipts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cli", type=Path)
    parser.add_argument("cases", type=Path)
    parser.add_argument("label")
    parser.add_argument("out", type=Path)
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--compiler-only", action="store_true")
    a = parser.parse_args()
    write(a.out, exercise(a.cli.resolve(), a.cases.resolve(), a.label, upgrade=a.upgrade, compiler_only=a.compiler_only))
