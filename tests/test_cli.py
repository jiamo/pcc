import shutil
import subprocess

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
    assert (
        "Error: --jobs requires --separate-tus, --depends-on, or --system-link"
        in result.output
    )


def test_depends_on_supports_file_with_dependency_make_goal(tmp_path):
    dep_dir = tmp_path / "lib"
    dep_dir.mkdir()
    (dep_dir / "helper.c").write_text("int helper(void) { return 41; }\n")
    (dep_dir / "ignored.c").write_text("int ignored(void) { return 99; }\n")
    (dep_dir / "Makefile").write_text(
        "lib: helper.o\n"
        "\tcc -o lib helper.o\n\n"
        "helper.o: helper.c\n"
        "\tcc -c -o helper.o helper.c\n\n"
        "ignored.o: ignored.c\n"
        "\tcc -c -o ignored.o ignored.c\n"
    )
    main_path = tmp_path / "main.c"
    main_path.write_text(
        "int helper(void);\n"
        "int main(void) { return helper() == 41 ? 0 : 1; }\n"
    )

    result = CliRunner().invoke(
        main,
        ["--depends-on", f"{dep_dir}=lib", str(main_path)],
    )

    assert result.exit_code == 0


def test_jobs_allowed_with_depends_on(tmp_path):
    dep_dir = tmp_path / "lib"
    dep_dir.mkdir()
    (dep_dir / "helper.c").write_text("int helper(void) { return 41; }\n")
    (dep_dir / "Makefile").write_text(
        "lib: helper.o\n"
        "\tcc -o lib helper.o\n\n"
        "helper.o: helper.c\n"
        "\tcc -c -o helper.o helper.c\n"
    )
    main_path = tmp_path / "main.c"
    main_path.write_text(
        "int helper(void);\n"
        "int main(void) { return helper() == 41 ? 0 : 1; }\n"
    )

    result = CliRunner().invoke(
        main,
        ["--jobs", "2", "--depends-on", f"{dep_dir}=lib", str(main_path)],
    )

    assert result.exit_code == 0


def test_system_link_supports_depends_on_multi_input(tmp_path):
    helper_path = tmp_path / "helper.c"
    helper_path.write_text("int helper(void) { return 41; }\n")
    main_path = tmp_path / "main.c"
    main_path.write_text(
        "int helper(void);\n"
        "int main(void) { return helper() == 41 ? 0 : 1; }\n"
    )

    result = CliRunner().invoke(
        main,
        ["--system-link", "--depends-on", str(helper_path), str(main_path)],
    )

    assert result.exit_code == 0


def test_system_link_supports_link_arg_archive(tmp_path):
    cc = shutil.which("cc") or shutil.which("clang") or shutil.which("gcc")
    ar = shutil.which("ar")
    assert cc is not None
    assert ar is not None

    helper_c = tmp_path / "helper.c"
    helper_o = tmp_path / "helper.o"
    helper_a = tmp_path / "libhelper.a"
    main_path = tmp_path / "main.c"

    helper_c.write_text("int helper(void) { return 41; }\n")
    main_path.write_text(
        "int helper(void);\n"
        "int main(void) { return helper() == 41 ? 0 : 1; }\n"
    )

    subprocess.run(
        [cc, "-c", "-o", str(helper_o), str(helper_c)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [ar, "rcs", str(helper_a), str(helper_o)],
        check=True,
        capture_output=True,
        text=True,
    )

    result = CliRunner().invoke(
        main,
        ["--system-link", f"--link-arg={helper_a}", str(main_path)],
    )

    assert result.exit_code == 0


def test_prepare_cmd_and_ensure_make_goal_support_fresh_dependency_project(tmp_path):
    dep_dir = tmp_path / "lib"
    dep_dir.mkdir()
    configure_sh = dep_dir / "configure.sh"
    helper_c = dep_dir / "helper.c"
    main_path = tmp_path / "main.c"

    configure_sh.write_text(
        "#!/bin/sh\n"
        "cat > config.h <<'EOF'\n"
        "#define VALUE 41\n"
        "EOF\n"
        "cat > Makefile <<'EOF'\n"
        "CPPFLAGS = -I.\n"
        "OBJS = helper.o\n"
        "libhelper.a: $(OBJS)\n"
        "\tar rcs libhelper.a $(OBJS)\n\n"
        "helper.o: helper.c config.h\n"
        "\tcc $(CPPFLAGS) -c -o helper.o helper.c\n"
        "EOF\n"
    )
    configure_sh.chmod(0o755)
    helper_c.write_text(
        "#include \"config.h\"\n"
        "int helper(void) { return VALUE; }\n"
    )
    main_path.write_text(
        "int helper(void);\n"
        "int main(void) { return helper() == 41 ? 0 : 1; }\n"
    )

    result = CliRunner().invoke(
        main,
        [
            "--prepare-cmd",
            f"cd {dep_dir} && ./configure.sh",
            "--ensure-make-goal",
            f"{dep_dir}=libhelper.a",
            "--system-link",
            "--depends-on",
            f"{dep_dir}=libhelper.a",
            str(main_path),
        ],
    )

    assert result.exit_code == 0


def test_cpp_arg_supports_single_file_define(tmp_path):
    main_path = tmp_path / "main.c"
    main_path.write_text(
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int main(void) { return VALUE == 42 ? 0 : 1; }\n"
    )

    result = CliRunner().invoke(
        main,
        ["--cpp-arg=-DVALUE=42", str(main_path)],
    )

    assert result.exit_code == 0


def test_cpp_arg_supports_depends_on_multi_input(tmp_path):
    helper_path = tmp_path / "helper.c"
    helper_path.write_text(
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int helper(void) { return VALUE; }\n"
    )
    main_path = tmp_path / "main.c"
    main_path.write_text(
        "int helper(void);\n"
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int main(void) { return helper() == VALUE ? 0 : 1; }\n"
    )

    result = CliRunner().invoke(
        main,
        ["--cpp-arg=-DVALUE=41", "--depends-on", str(helper_path), str(main_path)],
    )

    assert result.exit_code == 0


def test_cpp_arg_supports_sources_from_make_directory(tmp_path):
    (tmp_path / "helper.c").write_text(
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int helper(void) { return VALUE; }\n"
    )
    (tmp_path / "main.c").write_text(
        "int helper(void);\n"
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int main(void) { return helper() == VALUE ? 0 : 1; }\n"
    )
    (tmp_path / "ignored.c").write_text("int ignored(void) { return 99; }\n")
    (tmp_path / "Makefile").write_text(
        "app: helper.o main.o\n"
        "\tcc -o app helper.o main.o\n\n"
        "helper.o: helper.c\n"
        "\tcc -c -o helper.o helper.c\n\n"
        "main.o: main.c\n"
        "\tcc -c -o main.o main.c\n\n"
        "ignored.o: ignored.c\n"
        "\tcc -c -o ignored.o ignored.c\n"
    )

    result = CliRunner().invoke(
        main,
        ["--cpp-arg=-DVALUE=41", "--sources-from-make", "app", str(tmp_path)],
    )

    assert result.exit_code == 0


def test_cpp_arg_supports_sources_from_make_directory_with_separate_tus(tmp_path):
    (tmp_path / "helper.c").write_text(
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int helper(void) { return VALUE; }\n"
    )
    (tmp_path / "main.c").write_text(
        "int helper(void);\n"
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int main(void) { return helper() == VALUE ? 0 : 1; }\n"
    )
    (tmp_path / "ignored.c").write_text("int ignored(void) { return 99; }\n")
    (tmp_path / "Makefile").write_text(
        "app: helper.o main.o\n"
        "\tcc -o app helper.o main.o\n\n"
        "helper.o: helper.c\n"
        "\tcc -c -o helper.o helper.c\n\n"
        "main.o: main.c\n"
        "\tcc -c -o main.o main.c\n\n"
        "ignored.o: ignored.c\n"
        "\tcc -c -o ignored.o ignored.c\n"
    )

    result = CliRunner().invoke(
        main,
        [
            "--cpp-arg=-DVALUE=41",
            "--separate-tus",
            "--jobs",
            "2",
            "--sources-from-make",
            "app",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0


def test_sources_from_make_infers_cpp_args_from_compile_commands(tmp_path):
    (tmp_path / "helper.c").write_text(
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int helper(void) { return VALUE; }\n"
    )
    (tmp_path / "main.c").write_text(
        "int helper(void);\n"
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int main(void) { return helper() == VALUE ? 0 : 1; }\n"
    )
    (tmp_path / "Makefile").write_text(
        "CPPFLAGS = -DVALUE=41\n"
        "app: helper.o main.o\n"
        "\tcc -o app helper.o main.o\n\n"
        "helper.o: helper.c\n"
        "\tcc $(CPPFLAGS) -c -o helper.o helper.c\n\n"
        "main.o: main.c\n"
        "\tcc $(CPPFLAGS) -c -o main.o main.c\n"
    )

    result = CliRunner().invoke(
        main,
        ["--sources-from-make", "app", str(tmp_path)],
    )

    assert result.exit_code == 0


def test_depends_on_make_goal_infers_cpp_args_from_compile_commands(tmp_path):
    dep_dir = tmp_path / "lib"
    dep_dir.mkdir()
    (dep_dir / "helper.c").write_text(
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int helper(void) { return VALUE; }\n"
    )
    (dep_dir / "Makefile").write_text(
        "CPPFLAGS = -DVALUE=41\n"
        "lib: helper.o\n"
        "\tcc -o lib helper.o\n\n"
        "helper.o: helper.c\n"
        "\tcc $(CPPFLAGS) -c -o helper.o helper.c\n"
    )
    main_path = tmp_path / "main.c"
    main_path.write_text(
        "int helper(void);\n"
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int main(void) { return helper() == VALUE ? 0 : 1; }\n"
    )

    result = CliRunner().invoke(
        main,
        ["--depends-on", f"{dep_dir}=lib", str(main_path)],
    )

    assert result.exit_code == 0


def test_explicit_cpp_arg_overrides_make_inferred_cpp_arg(tmp_path):
    (tmp_path / "helper.c").write_text(
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int helper(void) { return VALUE; }\n"
    )
    (tmp_path / "main.c").write_text(
        "int helper(void);\n"
        "#ifndef VALUE\n"
        "#error missing VALUE\n"
        "#endif\n"
        "int main(void) { return helper() == 41 ? 0 : 1; }\n"
    )
    (tmp_path / "Makefile").write_text(
        "CPPFLAGS = -DVALUE=40\n"
        "app: helper.o main.o\n"
        "\tcc -o app helper.o main.o\n\n"
        "helper.o: helper.c\n"
        "\tcc $(CPPFLAGS) -c -o helper.o helper.c\n\n"
        "main.o: main.c\n"
        "\tcc $(CPPFLAGS) -c -o main.o main.c\n"
    )

    result = CliRunner().invoke(
        main,
        ["--sources-from-make", "app", "--cpp-arg=-DVALUE=41", str(tmp_path)],
    )

    assert result.exit_code == 0
