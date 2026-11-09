"""Per-platform setup guidance: where to create tokens and which scopes
are required, with verified sources (checked Nov 2026)."""

GUIDES = {
    "github": {
        "title": "GitHub",
        "token_url": "https://github.com/settings/tokens?type=beta (fine-grained) "
                     "or https://github.com/settings/tokens (classic)",
        "token_note": "Classic tokens are simplest for mirroring.",
        "scopes": [
            ("repo", "Full control of private repositories — read + write + push "
                     "(required for private repos)"),
            ("workflow", "Only if you want .github/workflows files mirrored too"),
            ("read:org", "Only if you mirror organization repositories"),
        ],
        "minimum": "repo",
        "alt_auth": "If the gh CLI is installed and logged in "
                    "(gh auth login), gitsync can use it instead of a token.",
        "env": "GITHUB_TOKEN",
    },
    "gitlab": {
        "title": "GitLab",
        "token_url": "https://gitlab.com/-/user_settings/personal_access_tokens "
                     "(self-hosted: https://<your-host>/-/user_settings/personal_access_tokens)",
        "token_note": "One scope covers everything gitsync needs.",
        "scopes": [
            ("api", "Complete read/write API access — list repos, create "
                    "projects, push (required)"),
            ("write_repository", "Implied by api; only needed if you prefer a "
                                 "narrower token for git-over-HTTP"),
        ],
        "minimum": "api",
        "alt_auth": "",
        "env": "GITLAB_TOKEN",
    },
    "forgejo": {
        "title": "Forgejo",
        "token_url": "<your-forgejo-url>/user/settings/applications "
                     "(e.g. http://localhost:3002/user/settings/applications)",
        "token_note": "Forgejo requires BOTH scopes — write:user is needed "
                      "because repo creation goes through /user/repos.",
        "scopes": [
            ("write:repository", "Create repos, push code (required)"),
            ("write:user", "Create repositories under your account via the API "
                           "(required — repo creation lives under /user/*)"),
            ("read:user", "Optional — lets gitsync show your username"),
        ],
        "minimum": "write:repository + write:user",
        "alt_auth": "",
        "env": "FORGEJO_TOKEN",
    },
    "gitea": {
        "title": "Gitea",
        "token_url": "<your-gitea-url>/user/settings/applications",
        "token_note": "Same scope model as Forgejo (Gitea >= 1.22 granular scopes).",
        "scopes": [
            ("write:repository", "Create repos, push code (required)"),
            ("write:user", "Create repositories via the API (required)"),
        ],
        "minimum": "write:repository + write:user",
        "alt_auth": "",
        "env": "GITEA_TOKEN",
    },
    "bitbucket": {
        "title": "Bitbucket",
        "token_url": "https://id.atlassian.com/manage-profile/security/api-tokens "
                     "(create an Atlassian API token with Bitbucket access)",
        "token_note": "Sent as a Bearer token. You also need your workspace "
                      "name (the owner in bitbucket.org/<workspace>/<repo>).",
        "scopes": [
            ("Bitbucket: repositories (read+write)",
             "List repos, create repositories, push"),
        ],
        "minimum": "Atlassian API token with Bitbucket repository read+write",
        "alt_auth": "",
        "env": "BITBUCKET_TOKEN",
    },
    "generic": {
        "title": "Generic git server (no API)",
        "token_url": "",
        "token_note": "No API needed — give a URL template like "
                      "https://git.example.com/{owner}/{name}.git. Works as a "
                      "push TARGET only (it cannot list repos, so it cannot be "
                      "a sync source). Codeberg.org also works here, or via the "
                      "forgejo adapter with url=https://codeberg.org.",
        "scopes": [],
        "minimum": "none (optional token embedded in the URL)",
        "alt_auth": "",
        "env": "",
    },
    "local": {
        "title": "Local folder",
        "token_url": "",
        "token_note": "No token needed — just pick a folder. Bare mirrors are "
                      "created there; use it as an offline backup drive.",
        "scopes": [],
        "minimum": "none",
        "alt_auth": "",
        "env": "",
    },
}


def guide(ptype):
    return GUIDES.get(ptype, GUIDES["local"])


def format_guide(ptype, indent="  "):
    g = guide(ptype)
    lines = [g["title"]]
    if g["token_url"]:
        lines.append("%sCreate token : %s" % (indent, g["token_url"]))
    if g["token_note"]:
        lines.append("%sNote         : %s" % (indent, g["token_note"]))
    if g["scopes"]:
        lines.append("%sRequired     :" % indent)
        for scope, why in g["scopes"]:
            lines.append("%s  - %-20s %s" % (indent, scope, why))
        lines.append("%sMinimum      : %s" % (indent, g["minimum"]))
    if g["alt_auth"]:
        lines.append("%sAlternative  : %s" % (indent, g["alt_auth"]))
    return "\n".join(lines)
