from click.testing import CliRunner

from pcc.pcc import main


def test_help_shows_jobs_default_8():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--jobs INTEGER RANGE" in result.output
    assert "[default: 8;" in result.output


def test_jobs_requires_separate_tus(tmp_path):
    (tmp_path / "main.c").write_text("int main(void) { return 0; }\n")

    result = CliRunner().invoke(main, ["--jobs", "2", str(tmp_path)])

    assert result.exit_code == 1
    assert "Error: --jobs requires --separate-tus" in result.output
