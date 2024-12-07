"""GitLab CI pipeline generator."""

from env_forge.ci.base import CIGenerator


class GitLabCIGenerator(CIGenerator):
    name = "gitlab"
    default_path = ".gitlab-ci.yml"

    def build(self):
        python = self.python_versions[0]
        pipeline = {
            "stages": ["verify", "test"],
            "variables": {
                "PIP_CACHE_DIR": "$CI_PROJECT_DIR/.cache/pip",
                "ENVFORGE_HOME": "$CI_PROJECT_DIR/.cache/envforge",
            },
            "default": {
                "image": "python:{}".format(python),
                "cache": {
                    "key": {"files": ["envforge.lock"]},
                    "paths": [".cache/pip", ".cache/envforge"],
                },
                "before_script": [self.install],
            },
            "lock-check": {
                "stage": "verify",
                "script": ["envforge validate", "envforge lock --check"],
            },
        }

        test_job = {
            "stage": "test",
            "needs": ["lock-check"],
            "parallel": {"matrix": [{
                "COMPONENT": self.components,
                "PYTHON_VERSION": self.python_versions,
            }]},
            "image": "python:$PYTHON_VERSION",
            "variables": {"ENVFORGE_PYTHON_OVERRIDE": "$PYTHON_VERSION"},
            "script": ["envforge test \"$COMPONENT\" --ephemeral"],
            "artifacts": {
                "when": "always",
                "paths": [".envforge/reports/"],
                "expire_in": "1 week",
            },
        }
        if self.affected_only:
            # only run a component's job when files under its path (or its deps) change
            test_job["rules"] = [{
                "if": "$CI_PIPELINE_SOURCE == \"merge_request_event\"",
                "changes": ["envforge.yaml", "envforge.lock"] + self._all_paths(),
            }, {"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"}]
        if self.uses_docker():
            test_job["services"] = ["docker:dind"]
            test_job["variables"].update({
                "DOCKER_HOST": "tcp://docker:2375",
                "DOCKER_TLS_CERTDIR": "",
            })
            test_job["before_script"] = [
                "apt-get update -qq && apt-get install -y -qq docker.io >/dev/null",
                self.install,
            ]
        pipeline["test"] = test_job
        return pipeline

    def _all_paths(self):
        out = []
        for comp in self.manifest.components.values():
            path = comp.path.rstrip("/")
            out.append("{}/**/*".format(path) if path not in ("", ".") else "**/*")
        return sorted(set(out))
