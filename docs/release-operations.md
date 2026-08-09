# Release operations

The four **Release Artifacts** are built from the version in `pyproject.toml` on
Windows 2022 / Python 3.11 with the locked `uv.lock` and explicit PyInstaller
specs. The localhost diagnostic bridge is development-only and is not included.

## Agent Component staging

1. On the local Windows 11 development machine with TouchDesigner 2025.32050,
   build and validate `td-agent.tox` against the exact source commit. The local
   machine is not an Actions runner.
2. Save the machine-readable validation result with `"validated": true`, then
   run `scripts/prepare_agent_stage.py`. Keep its three output files outside the
   Actions checkout.
3. ZIP the three prepared files, record the ZIP SHA-256, and Base64-encode the
   ZIP. Dispatch **Stage Agent Component** with the exact source commit, Release
   Version, Base64 payload, and ZIP digest. The GitHub-hosted Windows runner
   decodes and validates the stage without launching or depending on
   TouchDesigner. `scripts/dispatch_agent_stage.ps1` performs the archive,
   digest, encoding, dispatch, and temporary-file cleanup without placing the
   payload in the repository.
4. Record the run ID, artifact ID, artifact digest, head SHA, version, and expiry
   from the job summary. Restaging always creates a new artifact ID.

Remove the local stage directory and encoded payload after the immutable Actions
artifact identity is recorded. TouchDesigner validation remains local
development evidence; hosted CI verifies only source, evidence, checksum,
version, and packaging contracts that do not require TouchDesigner.

## Repository policy gate

Before publication, an administrator must configure:

- `develop` and `main` branch rulesets: block deletion, direct/force push;
  require resolved conversations, one approval, and both CI jobs. `develop`
  additionally requires the local TouchDesigner evidence status; `main`
  requires promotion from `develop` and the packaging/staging evidence.
- A `v*` tag ruleset that blocks updates and deletion of published version
  tags. Initial creation remains available to the manually approved Release
  workflow, which uses its repository-scoped short-lived `GITHUB_TOKEN`.
- A protected `release` environment with human approval. The workflow receives
  `contents: write` only for its publish job and needs no long-lived Release
  credential or additional GitHub App.
- Immutable Releases in repository settings. Administrators retain emergency
  bypass only with a recorded reason.

The current configuration can be audited with `gh api repos/tedliou/td-cli/rulesets`.
The environment approval, exact-main validation, immutable staged artifact,
draft-first upload, and remote digest checks remain mandatory publication gates.

## Publish and recover

Dispatch **Publish Release** from `main` with the exact main commit, staged
artifact ID, and staged artifact digest. The workflow validates identity and
expiry, builds all executables, packages the four ZIPs, creates an annotated tag
with the environment-approved short-lived `GITHUB_TOKEN`, and uploads to a draft.
A rerun skips matching assets and replaces mismatches only while draft.
Publication is a one-way immutable step; correct published mistakes with a new
SemVer.

Stable install/upgrade:

```powershell
irm https://github.com/tedliou/td-cli/releases/latest/download/install.ps1 | iex
```

Stable uninstall:

```powershell
irm https://github.com/tedliou/td-cli/releases/latest/download/uninstall.ps1 | iex
```

For inspection-first use, download the script and `SHA256SUMS`, compare its
published digest, inspect it, then execute it. The executables are unsigned in
Prototype v0.1.2; checksum verification does not bypass PowerShell, SmartScreen,
or Defender policy.
