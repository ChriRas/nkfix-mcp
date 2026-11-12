"""The public repo: README, metadata and CI fit together (plan 09).

The workflow is checked as text: PyYAML is no pinned dependency here.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_URL = 'https://github.com/ChriRas/nkfix-mcp'
PYPROJECT = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
README = (ROOT / 'README.md').read_text(encoding='utf-8')
CI = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')


def test_readme_installs_the_current_version_from_github():
    version = PYPROJECT['project']['version']
    assert f'uv tool install git+{REPO_URL}@v{version}' in README


def test_readme_says_it_is_no_part_of_nebenkostenfix():
    head = ' '.join(README.split('\n## ', 1)[0].split())
    assert 'eigenständiges Projekt' in head
    assert 'nicht vom Maintainer von NebenkostenFix' in head


def test_readme_sends_bug_reports_here():
    assert f'{REPO_URL}/issues' in README


def test_metadata_name_the_author_and_the_repo():
    project = PYPROJECT['project']
    assert project['authors'] == [{'name': 'Christoph Raschke'}]
    assert project['urls'] == {'Repository': REPO_URL, 'Issues': f'{REPO_URL}/issues'}
    # PEP 639: the SPDX expression in `license` replaces the License classifiers.
    assert project['license'] == 'GPL-3.0-or-later'
    assert not [c for c in project['classifiers'] if c.startswith('License ::')]


def test_every_action_is_pinned_to_a_commit():
    uses = re.findall(r'uses: (\S+)', CI)
    assert uses
    for action in uses:
        assert re.fullmatch(r'[\w.-]+/[\w.-]+@[0-9a-f]{40}', action), action
