class Gitsync < Formula
  desc "Multi-platform git repository sync tool (GitHub, GitLab, Forgejo, local)"
  homepage "https://github.com/SeaXen/gitsync"
  url "https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.18.tar.gz"
  # Template formula: update sha256 upon release tag creation via:
  #   curl -fsSL https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.18.tar.gz | sha256sum
  sha256 "ec3c4577f818bf9c40672bba0727ba102a8a5077dd020015ac94ddd21ded3a1e"
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
