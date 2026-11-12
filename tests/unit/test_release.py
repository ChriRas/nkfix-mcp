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


def _job(name: str) -> str:
    """The text of one job, up to the next job."""
    parts = re.split(r'\n  ([\w-]+):\n', CI)
    jobs = dict(zip(parts[1::2], parts[2::2], strict=True))
    assert name in jobs, f'Job "{name}" fehlt'
    return jobs[name]


def test_ci_runs_the_contract_tests_against_a_pinned_nebenkostenfix_release():
    job = _job('contract')
    assert 'repository: Nerbry72/NebenkostenFix' in job
    # A tag can be moved; the commit cannot. The comment names the release.
    assert re.search(r'ref: [0-9a-f]{40}  # v\d+\.\d+\.\d+\n', job), 'NebenkostenFix muss an einem Commit hängen'
    assert 'NKF_SOURCE:' in job
    assert 'NKF_PYTHON:' in job
    # In CI a skipped contract test would be green without checking anything.
    assert "NKF_CONTRACT_REQUIRED: '1'" in job
    assert 'pytest tests/contract -rs' in job


def test_contract_job_keeps_the_foreign_code_away_from_token_and_cache():
    job = _job('contract')
    assert 'enable-cache: false' in job
    assert re.search(r'timeout-minutes: \d+', job)
    for name in ('check', 'contract'):
        checkouts = _job(name).count('uses: actions/checkout@')
        assert _job(name).count('persist-credentials: false') == checkouts, name


def test_every_action_is_pinned_to_a_commit():
    uses = re.findall(r'uses: (\S+)', CI)
    assert uses
    for action in uses:
        assert re.fullmatch(r'[\w.-]+/[\w.-]+@[0-9a-f]{40}', action), action
