class Gitsync < Formula
  desc "Multi-platform git repository sync tool (GitHub, GitLab, Forgejo, local)"
  homepage "https://github.com/SeaXen/gitsync"
  url "https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.14.tar.gz"
  # Template formula: update sha256 upon release tag creation via:
  #   curl -fsSL https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.14.tar.gz | sha256sum
  sha256 "57b7505655c6e9c11a2b51b215a613a11d9c9f4772a3e9cda891ad26eea08b5a"
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
