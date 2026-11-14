class Gitsync < Formula
  desc "Multi-platform git repository sync tool (GitHub, GitLab, Forgejo, local)"
  homepage "https://github.com/SeaXen/gitsync"
  url "https://github.com/SeaXen/gitsync/archive/refs/tags/v2.1.7.tar.gz"
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
