# Platforms and tokens

Every provider is an adapter with the same surface: list repos, create a
repo, produce a clone URL, and (optionally) delete. Any platform can be a
source, a target, or both.

## Platforms & tokens

The wizard prints this before asking for anything — here it is for reference:

| Platform | Create token at | Required scopes | Notes |
|---|---|---|---|
| **GitHub** | [github.com/settings/tokens](https://github.com/settings/tokens) | `repo` | or the `gh` CLI (no token). Org repos need `read:org` **and** `"affiliation": "owner,organization_member"` in the platform config — the scope alone does nothing |
| **GitLab** | [gitlab.com/-/user_settings/personal_access_tokens](https://gitlab.com/-/user_settings/personal_access_tokens) | `api` | self-hosted: `<host>/-/user_settings/...` |
| **Forgejo** | `<url>/user/settings/applications` | `write:repository` **+** `write:user` | `write:user` is required because repo creation goes through `/user/repos` |
| **Gitea** | `<url>/user/settings/applications` | `write:repository` + `write:user` | same model as Forgejo (1.22+) |
| **Bitbucket** | [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens) | Atlassian API token, Bitbucket repo read+write | sent as Bearer; needs your workspace name |
| **Local folder** | — | none | just pick a directory |
| **Any other git server** | — | optional | use the `generic` type with a URL template |

**Codeberg.org** works too: use the `forgejo` adapter with `url: https://codeberg.org`,
or `generic` with a URL template.

Tokens are stored in `~/.config/gitsync/config.json` (mode `0600`). They can
also come from `GITHUB_TOKEN`, `GITLAB_TOKEN`, `FORGEJO_TOKEN`, `GITEA_TOKEN`,
`BITBUCKET_TOKEN`.

---

[← back to the README](../README.md)
