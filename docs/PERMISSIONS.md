# Token scopes — what each platform needs

Verified against official docs, November 2026. Create the **most restrictive
token** that meets the need; gitsync only ever lists repos, creates repos and
pushes code.

---

## GitHub

**Create:** https://github.com/settings/tokens (classic) or
https://github.com/settings/tokens?type=beta (fine-grained)

| Scope | Why |
|---|---|
| `repo` | **Required.** Read/write private repositories — clone + push |
| `workflow` | Only if `.github/workflows/` files must be mirrored |
| `read:org` | Only if you mirror organization repositories |

**Minimum:** `repo`
**Alternative:** if the `gh` CLI is installed and logged in
(`gh auth login`), set `"auth": "gh"` and gitsync uses it — no token needed.

---

## GitLab (gitlab.com or self-managed)

**Create:** https://gitlab.com/-/user_settings/personal_access_tokens
(self-hosted: `https://<host>/-/user_settings/personal_access_tokens`)

| Scope | Why |
|---|---|
| `api` | **Required.** List projects, create projects, push |
| `write_repository` | Narrower alternative for git-over-HTTP only |

**Minimum:** `api`
**Note:** gitsync reads a GitLab *token* only. There is no `glab` CLI path —
paste a personal access token with the `api` scope.

---

## Forgejo (any version, any host)

**Create:** `<your-forgejo-url>/user/settings/applications`
(e.g. `http://localhost:3002/user/settings/applications`)

| Scope | Why |
|---|---|
| `write:repository` | **Required.** Create repos, push code |
| `write:user` | **Required.** Repo creation goes through `/user/repos`, which needs write:user |
| `read:user` | Optional — lets gitsync display your username |

**Minimum:** `write:repository` + `write:user`

> Note: a token with only `write:repository` can push to repos that already
> exist but **cannot create new ones** — that is why both scopes are needed.

---

## Gitea (1.22+)

**Create:** `<your-gitea-url>/user/settings/applications`

Same scope model as Forgejo:

| Scope | Why |
|---|---|
| `write:repository` | **Required.** Create repos, push code |
| `write:user` | **Required.** API repo creation |

**Minimum:** `write:repository` + `write:user`

---

## Local folder

No token, no scopes. Just pick a directory — gitsync creates bare mirrors
there. Ideal for an offline backup drive.

---

## Environment variables (instead of config file tokens)

| Variable | Platform |
|---|---|
| `GITHUB_TOKEN` | GitHub |
| `GITLAB_TOKEN` | GitLab |
| `FORGEJO_TOKEN` | Forgejo |
| `GITEA_TOKEN` | Gitea |

---

## Security notes

- The config file (`~/.config/gitsync/config.json`) is written mode **0600**.
- Tokens are embedded in the git URLs the tool uses, so no ambient
  credential helper is required. Anything that can read the config file can
  use your tokens — protect the file and the machine accordingly.
- Rotate tokens periodically and revoke them at the same URL you created them.
- For Forgejo/Gitea you can restrict a token to **specific repositories**,
  but note that scoped tokens only support `read:repository`,
  `write:repository`, `read:issue`, `write:issue` — repo *creation* needs
  an all-repositories token.
