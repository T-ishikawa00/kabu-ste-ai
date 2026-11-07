#!/bin/sh
# Linux/WSL with existing Python 3.12 + venv + unshare. No installation/network.
set -eu
project=$(CDPATH= cd -- "${1:-.}" && pwd)
results=${2:?Specify an empty results directory}
mkdir -p "$results"
results=$(CDPATH= cd -- "$results" && pwd)
if [ -n "$(ls -A "$results")" ]; then
    echo 'Refusing to overwrite nonempty results' >&2
    exit 2
fi
root=$(mktemp -d /tmp/kabu-ste-isolation.XXXXXX)
chmod 700 "$root"
# Copy an explicit allowlist only, no original repository or home mounts.
mkdir -p "$root/work" "$root/tmp" "$root/dev" "$root/results" "$root/usr"
touch "$root/dev/null"
# Validate containment and hashes before copying any source bytes.
python3 -I - "$project" "$root/work" <<'COPY'
import hashlib, json, pathlib, shutil, sys
project = pathlib.Path(sys.argv[1]).resolve()
output = pathlib.Path(sys.argv[2]).resolve()
def approved_file(relative):
    path = project / relative
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(project) or not path.is_file():
        raise SystemExit("Copy source escaped reviewed project")
    for part in (path, *path.parents):
        if part == project:
            break
        if part.is_symlink():
            raise SystemExit("Symlink is not allowed in isolation inputs")
    if ".git" in pathlib.Path(relative).parts:
        raise SystemExit("Git history is not an isolation input")
    if path.name.lower() in {"config.yaml", "credentials.json", "token.pickle", ".env"} or path.suffix.lower() in {".pkl", ".pickle", ".zip", ".pt", ".pth", ".pem", ".key"}:
        raise SystemExit("Private runtime asset is not an isolation input")
    return path
def copy(relative):
    path = approved_file(relative)
    target = output / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, target)
manifest = json.loads(approved_file("docs/source-map.json").read_text(encoding="utf-8"))
records = manifest["retained_files"]
names = [row["target_path"] for row in records]
if len(names) != 91 or len(set(names)) != 91:
    raise SystemExit("Unexpected source-map scope")
for row in records:
    name = row["target_path"]
    if pathlib.PurePosixPath(name).name != name or pathlib.Path(name).suffix not in {".py", ".bat", ".html"}:
        raise SystemExit("Legacy source path must be one reviewed root file")
    data = approved_file(name).read_bytes()
    if hashlib.sha256(data).hexdigest() != row["latest_sha256"]:
        raise SystemExit("Legacy source hash changed before isolation")
    copy(name)
for directory in ("tools", "tests", "docs", "config", "examples"):
    base = project / directory
    if base.is_symlink():
        raise SystemExit("Input directory is a symlink")
    if base.exists():
        for path in base.rglob("*"):
            if path.is_symlink():
                raise SystemExit("Nested symlink is not allowed")
            if path.is_file():
                copy(path.relative_to(project).as_posix())
for name in ("README.md", "AGENTS.md", "pyproject.toml", ".gitignore", ".gitattributes", ".env.example"):
    copy(name)
COPY
ln -s usr/bin "$root/bin"
ln -s usr/sbin "$root/sbin"
ln -s usr/lib "$root/lib"
ln -s usr/lib64 "$root/lib64"
cat > "$root/run.sh" <<'INNER'
set -eu
cd /work
export PYTHONDONTWRITEBYTECODE=1
python3 --version > /results/python.txt
python3 -I -m venv /venv-one
/venv-one/bin/python -I -m pip --version > /results/pip.txt
/venv-one/bin/python -I tools/run_safe.py audit > /results/source-audit.json
/venv-one/bin/python -I tools/run_safe.py selfcheck > /results/guard-controls.json
/venv-one/bin/python -I tools/run_safe.py tests > /results/tests-one.txt 2>&1
/venv-one/bin/python -I tools/run_safe.py simulate > /results/simulation-one.txt
mkdir /tmp/arbitrary-cwd
cd /tmp/arbitrary-cwd
/venv-one/bin/python -I /work/tools/run_safe.py simulate > /results/simulation-arbitrary.txt
cmp /results/simulation-one.txt /results/simulation-arbitrary.txt
cd /work
python3 -I -m venv /venv-two
/venv-two/bin/python -I tools/run_safe.py tests > /results/tests-two.txt 2>&1
/venv-two/bin/python -I tools/run_safe.py simulate > /results/simulation-two.txt
cmp /results/simulation-one.txt /results/simulation-two.txt
printf '%s\n' 'Two clean stdlib-only venvs passed; source audited without imports; simulation output reproduced.' > /results/outcome.txt
INNER
# The namespace exists only for this child. Host networking/firewall/config stays intact.
unshare --user --map-root-user --net --mount --pid --fork sh -eu -c '
    root=$1
    results=$2
    mount --make-rprivate /
    # Recursive bind preserves inherited locked submounts under user namespaces.
    mount --rbind /usr "$root/usr"
    findmnt -R -r -n -o TARGET "$root/usr" | sort -r | while IFS= read -r target; do
        mount -o remount,bind,ro,nosuid,nodev "$target"
    done
    mount --bind /dev/null "$root/dev/null"
    mount --bind "$results" "$root/results"
    ip link set dev lo down
    echo "Network namespace interfaces and addresses (netlink):" > "$results/isolation.txt"
    ip -brief address show >> "$results/isolation.txt"
    echo "IPv4 and IPv6 routing tables:" >> "$results/isolation.txt"
    ip -4 route show >> "$results/isolation.txt"
    ip -6 route show >> "$results/isolation.txt"
    echo "Namespace mount access:" >> "$results/isolation.txt"
    findmnt -R -r -n -o TARGET,OPTIONS "$root/usr" >> "$results/isolation.txt"
    echo "Root exposes /usr read-only plus synthetic review project, temp and results; no /home, /mnt, /proc or host /etc." >> "$results/isolation.txt"
    exec env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/sbin/chroot "$root" /bin/sh /run.sh
' isolation "$root" "$results"
# Retain only generated results; transient test roots are intentionally left in
# private /tmp for this run to avoid broad recursive deletion in a review tool.
printf '%s\n' "$root" > "$results/private-temp-root.txt"
cat "$results/outcome.txt"
