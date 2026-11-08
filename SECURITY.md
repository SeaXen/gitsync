# Security Policy

## Reporting a Vulnerability

Please **do not** open a public issue for security problems. Instead:

1. Use GitHub's private vulnerability reporting:
   **Security → Report a vulnerability** on this repository, or
2. Contact the maintainer privately.

You'll get an acknowledgement within 72 hours and an assessment after that.

## How gitsync Handles Credentials

gitsync is a local tool that mirrors repositories **using your own
credentials**. Understanding where they live matters:

| Credential | Where it lives | Permissions |
|---|---|---|
| Platform tokens (GitHub/GitLab/Forgejo/Gitea/Bitbucket) | `~/.config/gitsync/config.json` | `0600` |
| Discord webhook / Telegram bot token | same file | `0600` |
| GitHub CLI token | `~/.config/gh/hosts.yml` (managed by `gh`) | `0600` |
| GitLab CLI token | `~/.config/glab-cli/config.yml` (managed by `glab`) | `0600` |
| Git credentials for localhost targets | `~/.git-credentials` (managed by git) | `0600` |

**Nothing is ever sent anywhere except the platforms you configure** —
there is no telemetry, no analytics and no third-party endpoints in this
tool.

### Design notes

- Tokens are embedded in the git URLs the tool uses, so no ambient
  credential helper is required. Anything that can read your config file
  can use your tokens — protect the file and the machine accordingly.
- Tokens are read from the environment (`GITHUB_TOKEN`, `GITLAB_TOKEN`,
  `FORGEJO_TOKEN`, `GITEA_TOKEN`, `BITBUCKET_TOKEN`) when set, which lets
  you keep them out of the config file entirely.
- The config file is created with mode `0600` and the installer never
  copies it anywhere.
- All subprocess and network calls are bounded by explicit timeouts.

### Recommended token scopes

Use the **most restrictive** token that works — `gitsync scopes` prints
the exact minimum per platform. For Forgejo/Gitea you can further restrict
a token to specific repositories (note that repo *creation* needs an
all-repositories token).

### Rotating a token

```bash
gitsync platforms edit <name> --token <new-token>
gitsync platforms test <name>
```

Then revoke the old one at the provider.
