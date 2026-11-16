class Gitsync < Formula
  desc "Multi-platform git repository sync tool (GitHub, GitLab, Forgejo, local)"
  homepage "https://github.com/SeaXen/gitsync"
  url "https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.20.tar.gz"
  # Template formula: update sha256 upon release tag creation via:
  #   curl -fsSL https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.20.tar.gz | sha256sum
  sha256 "12e54775e3cf2acef2cdb41aab5a77d8385a13dfa487626b929923f208942002"
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
