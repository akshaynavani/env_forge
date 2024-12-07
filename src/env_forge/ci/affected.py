"""Work out which components a change touches, so CI only tests what matters."""

import os

from env_forge.errors import CommandError, EnvForgeError

# files whose change affects every component
GLOBAL_FILES = ("envforge.yaml", "envforge.lock")


def changed_files(runner, root, base, head="HEAD"):
    try:
        res = runner.run(["git", "diff", "--name-only", "{}...{}".format(base, head)],
                         cwd=root, capture=True)
    except CommandError as exc:
        raise EnvForgeError("git diff against '{}' failed: {}".format(base, exc.output.strip()))
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def _owns(component_path, filename):
    path = os.path.normpath(component_path)
    if path in (".", ""):
        return True
    return filename == path or filename.startswith(path.rstrip("/") + "/")


def affected_components(manifest, files):
    """Components whose own path changed, plus every component depending on them."""
    files = [os.path.normpath(f) for f in files]
    if any(f in GLOBAL_FILES for f in files):
        return sorted(manifest.components)

    direct = set()
    for comp in manifest.components.values():
        if any(_owns(comp.path, f) for f in files):
            direct.add(comp.name)

    # reverse dependency closure: if libs/common changed, everything using it is affected
    affected = set(direct)
    changed = True
    while changed:
        changed = False
        for comp in manifest.components.values():
            if comp.name not in affected and any(d in affected for d in comp.depends_on):
                affected.add(comp.name)
                changed = True
    return sorted(affected)
