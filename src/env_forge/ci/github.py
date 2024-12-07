"""GitHub Actions workflow generator."""

from env_forge.ci.base import CIGenerator


class GitHubActionsGenerator(CIGenerator):
    name = "github"
    default_path = ".github/workflows/envforge.yml"

    def _setup_steps(self, python):
        return [
            {"uses": "actions/checkout@v4", "with": {"fetch-depth": 0}},
            {"uses": "actions/setup-python@v5",
             "with": {"python-version": python, "cache": "pip"}},
            {"name": "Install envforge", "run": self.install},
            {"name": "Cache package index metadata",
             "uses": "actions/cache@v4",
             "with": {"path": "~/.cache/envforge",
                      "key": "envforge-${{ runner.os }}-${{ hashFiles('envforge.lock') }}"}},
        ]

    def build(self):
        first_py = self.python_versions[0]
        jobs = {}

        jobs["lock-check"] = {
            "name": "Lockfile is up to date",
            "runs-on": "ubuntu-latest",
            "steps": self._setup_steps(first_py) + [
                {"name": "Validate manifest", "run": "envforge validate"},
                {"name": "Check lockfile", "run": "envforge lock --check"},
            ],
        }

        needs = ["lock-check"]
        if self.affected_only:
            jobs["plan"] = {
                "name": "Detect affected components",
                "runs-on": "ubuntu-latest",
                "needs": ["lock-check"],
                "outputs": {"components": "${{ steps.affected.outputs.components }}"},
                "steps": self._setup_steps(first_py) + [{
                    "id": "affected",
                    "name": "Compute affected components",
                    "run": (
                        "base=\"origin/${{ github.base_ref || github.event.repository.default_branch }}\"\n"
                        "echo \"components=$(envforge affected --base \"$base\" --json)\" "
                        ">> \"$GITHUB_OUTPUT\""
                    ),
                }],
            }
            needs = ["plan"]
            component_axis = "${{ fromJSON(needs.plan.outputs.components) }}"
        else:
            component_axis = self.components

        test_job = {
            "name": "test ${{ matrix.component }} (py${{ matrix.python }}, ${{ matrix.os }})",
            "needs": needs,
            "runs-on": "${{ matrix.os }}",
            "strategy": {
                "fail-fast": False,
                "matrix": {
                    "component": component_axis,
                    "python": self.python_versions,
                    "os": self.os_list,
                },
            },
            "env": {"ENVFORGE_PYTHON_OVERRIDE": "${{ matrix.python }}"},
            "steps": self._setup_steps("${{ matrix.python }}") + [
                {"name": "Provision and test",
                 "run": "envforge test ${{ matrix.component }} --ephemeral"},
                {"name": "Upload test summary",
                 "if": "always()",
                 "uses": "actions/upload-artifact@v4",
                 "with": {
                     "name": "envforge-${{ matrix.component }}-py${{ matrix.python }}-${{ matrix.os }}",
                     "path": ".envforge/reports/",
                     "if-no-files-found": "ignore",
                 }},
            ],
        }
        if self.affected_only:
            test_job["if"] = "${{ needs.plan.outputs.components != '[]' }}"
        jobs["test"] = test_job

        return {
            "name": "envforge",
            "on": {
                "push": {"branches": self.branches},
                "pull_request": {},
                "workflow_dispatch": {},
            },
            "concurrency": {
                "group": "envforge-${{ github.ref }}",
                "cancel-in-progress": True,
            },
            "jobs": jobs,
        }
