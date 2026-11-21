import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from urllib.request import urlopen

from verify_bins import PREVIOUS, REVISED, exercise, run, write


def main():
    base = Path(sys.argv[1]).resolve()
    base.mkdir(exist_ok=True, parents=True)
    results = Path("receipts").resolve()
    results.mkdir(exist_ok=True)
    if sys.argv[2] == "release":
        metadata = json.load(urlopen("https://registry.npmjs.org/npm/11.19.0", timeout=30))
        raw = urlopen(metadata["dist"]["tarball"], timeout=60).read()
        assert "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode() == metadata["dist"]["integrity"]
        archive = base / "npm.tgz"
        archive.write_bytes(raw)
        with tarfile.open(archive) as tar:
            tar.extractall(base / "dist", filter="data")
        rows = exercise(base / "dist/package/bin/npm-cli.js", base / "release", "11.19.0", compiler_only=True)
        write(results / "release.json", rows)
        return

    repo = base / "n"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "fetch", "-q", "--depth=2", "https://github.com/npm/cli.git", REVISED],
                   cwd=repo, check=True)
    cli = repo / "bin/npm-cli.js"
    for revision, key in ((PREVIOUS, "previous"), (REVISED, "revised")):
        subprocess.run(["git", "-c", "advice.detachedHead=false", "checkout", "-q", revision], cwd=repo, check=True)
        assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == revision
        if key == "previous":
            subprocess.run(["node", "scripts/resetdeps.js"], cwd=repo, check=True)
        resolved = Path(subprocess.check_output(["node", "-p", "require.resolve('@npmcli/arborist')"],
                                                cwd=repo, text=True).strip()).resolve()
        assert resolved.is_relative_to((repo / "workspaces/arborist").resolve()), resolved
        rows = exercise(cli, base / key, revision)
        write(results / (key + ".json"), rows)
        test = "linked strategy exposes store node_modules via NODE_PATH for lifecycle scripts"
        result = run(["node", repo/"node_modules/tap/bin/run.js", "--no-coverage", "--no-color",
                      "--reporter=tap", "-g", test, "test/arborist/reify.js"], repo/"workspaces/arborist",
                     os.environ, check=False)
        outcome = [line.strip() for line in result.stdout.splitlines() if " - " + test in line
                   and "SKIP" not in line and " # TODO" not in line]
        write(results / (key+"-lifecycle.json"), {"exit":result.returncode,"subtest":outcome,"checkout":str(repo)})
        print("Lifecycle", key, repo, result.returncode, outcome, flush=True)
        if not outcome or result.returncode:
            print(result.stdout[-8000:], result.stderr[-1500:])
            raise RuntimeError("The linked-store lifecycle test did not pass")
    write(results/"upgrade.json", exercise(cli, base/"previous", REVISED, upgrade=True))


if __name__ == "__main__":
    main()
