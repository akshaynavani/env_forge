"""Package indexes the resolver can query.

``PackageIndex`` is the abstraction; ``PyPIIndex`` talks to the PyPI JSON API and
``StaticIndex`` reads a YAML/JSON file (offline CI, air-gapped networks, tests).
"""

import abc
import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Dict, List, Optional

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from env_forge.errors import ResolutionError
from env_forge.log import get_logger

log = get_logger("index")


class PackageIndex(abc.ABC):
    """Source of available versions and their dependency metadata."""

    @abc.abstractmethod
    def versions(self, name):
        """Return all known versions of ``name`` (any order)."""

    @abc.abstractmethod
    def dependencies(self, name, version):
        """Return the list of ``Requirement`` objects declared by ``name==version``."""

    def requires_python(self, name, version):
        """Return a SpecifierSet for Requires-Python, or None if unknown/unrestricted."""
        return None


def _parse_versions(raw):
    out = []
    for v in raw:
        try:
            out.append(Version(str(v)))
        except InvalidVersion:
            log.debug("skipping unparseable version %r", v)
    return out


def _parse_reqs(lines, context):
    reqs = []
    for line in lines or []:
        try:
            reqs.append(Requirement(line))
        except InvalidRequirement:
            log.debug("%s: skipping invalid requirement %r", context, line)
    return reqs


class StaticIndex(PackageIndex):
    """Index backed by a mapping of ``{package: {version: [requirement, ...]}}``.

    File format (YAML or JSON)::

        packages:
          requests:
            "2.31.0":
              requires: ["urllib3>=1.21.1,<3", "idna>=2.5,<4"]
              requires_python: ">=3.7"
            "2.30.0": ["urllib3>=1.21.1,<3"]      # short form: just the list
    """

    def __init__(self, packages):
        self._data = {}  # type: Dict[str, Dict[Version, dict]]
        for name, releases in (packages or {}).items():
            canon = canonicalize_name(name)
            bucket = self._data.setdefault(canon, {})
            for ver, meta in (releases or {}).items():
                if isinstance(meta, list) or meta is None:
                    meta = {"requires": meta or []}
                bucket[Version(str(ver))] = meta

    @classmethod
    def from_file(cls, path):
        if not os.path.isfile(path):
            raise ResolutionError("offline index not found: {}".format(path))
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) if path.endswith((".yaml", ".yml")) else json.load(fh)
        return cls((data or {}).get("packages", {}))

    def versions(self, name):
        return list(self._data.get(canonicalize_name(name), {}).keys())

    def dependencies(self, name, version):
        meta = self._data.get(canonicalize_name(name), {}).get(Version(str(version)))
        if meta is None:
            raise ResolutionError("{}=={} not in offline index".format(name, version))
        return _parse_reqs(meta.get("requires", []), "{}=={}".format(name, version))

    def requires_python(self, name, version):
        meta = self._data.get(canonicalize_name(name), {}).get(Version(str(version))) or {}
        rp = meta.get("requires_python")
        return SpecifierSet(rp) if rp else None


class PyPIIndex(PackageIndex):
    """PyPI JSON API client with an in-memory and optional on-disk cache."""

    DEFAULT_URL = "https://pypi.org/pypi"

    def __init__(self, base_url=None, cache_dir=None, timeout=15.0):
        self.base_url = (base_url or os.environ.get("ENVFORGE_PYPI_URL") or self.DEFAULT_URL).rstrip("/")
        self.cache_dir = cache_dir
        self.timeout = timeout
        self._project_cache = {}
        self._release_cache = {}
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    # -- HTTP + cache -------------------------------------------------------

    def _cache_path(self, url):
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.cache_dir, digest + ".json")

    def _get_json(self, url):
        cache = self._cache_path(url)
        if cache and os.path.isfile(cache):
            with open(cache, "r", encoding="utf-8") as fh:
                return json.load(fh)
        log.debug("GET %s", url)
        req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": "env-forge"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise ResolutionError("PyPI request failed ({}): {}".format(exc.code, url))
        except (urllib.error.URLError, OSError) as exc:
            raise ResolutionError("cannot reach package index {}: {}".format(self.base_url, exc))
        if cache:
            tmp = cache + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, cache)
        return data

    def _project(self, name):
        canon = canonicalize_name(name)
        if canon not in self._project_cache:
            data = self._get_json("{}/{}/json".format(self.base_url, canon))
            if data is None:
                raise ResolutionError("package '{}' not found on index".format(name))
            self._project_cache[canon] = data
        return self._project_cache[canon]

    def _release(self, name, version):
        key = (canonicalize_name(name), str(version))
        if key not in self._release_cache:
            data = self._get_json("{}/{}/{}/json".format(self.base_url, key[0], key[1]))
            if data is None:
                raise ResolutionError("{}=={} not found on index".format(name, version))
            self._release_cache[key] = data
        return self._release_cache[key]

    # -- PackageIndex -------------------------------------------------------

    def versions(self, name):
        releases = self._project(name).get("releases", {})
        usable = []
        for ver, files in releases.items():
            # skip releases with no files or where every file was yanked
            if not files or all(f.get("yanked") for f in files):
                continue
            usable.append(ver)
        return _parse_versions(usable)

    def dependencies(self, name, version):
        info = self._release(name, version).get("info", {})
        return _parse_reqs(info.get("requires_dist"), "{}=={}".format(name, version))

    def requires_python(self, name, version):
        files = self._project(name).get("releases", {}).get(str(version), [])
        for f in files:
            rp = f.get("requires_python")
            if rp:
                try:
                    return SpecifierSet(rp)
                except InvalidSpecifier:
                    return None
        return None


def build_index(spec, root, cache_dir=None):
    """Create an index from the manifest's ``index`` value."""
    if spec in (None, "", "pypi"):
        return PyPIIndex(cache_dir=cache_dir)
    if spec.startswith(("http://", "https://")):
        return PyPIIndex(base_url=spec, cache_dir=cache_dir)
    path = spec if os.path.isabs(spec) else os.path.join(root, spec)
    return StaticIndex.from_file(path)
