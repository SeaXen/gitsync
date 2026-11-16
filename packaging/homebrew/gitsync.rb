class Gitsync < Formula
  desc "Multi-platform git repository sync tool (GitHub, GitLab, Forgejo, local)"
  homepage "https://github.com/SeaXen/gitsync"
  url "https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.13.tar.gz"
  # Template formula: update sha256 upon release tag creation via:
  #   curl -fsSL https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.13.tar.gz | sha256sum
  sha256 "7263b62eb1bb13a257d63385c398fc9588d6e29e608e24868cf99b8ca6a80a9f"
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
