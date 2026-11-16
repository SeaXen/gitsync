class Gitsync < Formula
  desc "Multi-platform git repository sync tool (GitHub, GitLab, Forgejo, local)"
  homepage "https://github.com/SeaXen/gitsync"
  url "https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.16.tar.gz"
  # Template formula: update sha256 upon release tag creation via:
  #   curl -fsSL https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.16.tar.gz | sha256sum
  sha256 "4af1ee0f35affd0e64a2cb8c08e28e20a07abcc236164dfbfaa27937808d82a2"
  license "MIT"

  depends_on "python@3.12"

  def install
    system "python3", "-m", "py_compile", *Dir["gitsync/*.py"]
    libexec.install Dir["*"]
    bin.install_symlink libexec/"bin/gitsync"
  end

  test do
    system "#{bin}/gitsync", "--version"
  end
end
