"""CI/CD integration: pipeline generators and affected-component detection."""

from env_forge.ci.base import CIGenerator
from env_forge.ci.github import GitHubActionsGenerator
from env_forge.ci.gitlab import GitLabCIGenerator
from env_forge.errors import EnvForgeError

GENERATORS = {
    GitHubActionsGenerator.name: GitHubActionsGenerator,
    GitLabCIGenerator.name: GitLabCIGenerator,
}


def get_generator(name, manifest):
    try:
        return GENERATORS[name](manifest)
    except KeyError:
        raise EnvForgeError("unknown CI provider '{}' (available: {})".format(
            name, ", ".join(sorted(GENERATORS))))


__all__ = ["CIGenerator", "GENERATORS", "get_generator"]
