import pcc.evaluater.c_evaluator as c_evaluator

from click.testing import CliRunner

from pcc.evaluater.c_evaluator import CEvaluator
from pcc.pcc import main
from pcc.project import TranslationUnit


def test_evaluate_uses_disk_compile_cache_by_default(tmp_path, monkeypatch):
    cache_dir = tmp_path / "compile-cache"
    monkeypatch.setenv("PCC_COMPILE_CACHE_DIR", str(cache_dir))

    source = "int main(void) { return 7; }\n"

    assert CEvaluator().evaluate(source, optimize=False, use_system_cpp=False) == 7

    def unexpected_cache_miss(unit_name, codestr):
        raise AssertionError(f"unexpected cache miss for {unit_name}")

    monkeypatch.setattr(
        c_evaluator,
        "_compile_preprocessed_translation_unit_artifact",
        unexpected_cache_miss,
    )

    assert CEvaluator().evaluate(source, optimize=False, use_system_cpp=False) == 7


def test_compile_translation_units_recompiles_only_dirty_units(tmp_path, monkeypatch):
    cache_dir = tmp_path / "compile-cache"

    unit_a = TranslationUnit(
        name="a.c",
        path=str(tmp_path / "a.c"),
        source="int helper(void) { return 1; }\n",
    )
    unit_b = TranslationUnit(
        name="b.c",
        path=str(tmp_path / "b.c"),
        source="int helper(void);\nint main(void) { return helper(); }\n",
    )

    compiled = CEvaluator().compile_translation_units(
        [unit_a, unit_b],
        use_system_cpp=False,
        jobs=1,
        cache_dir=str(cache_dir),
    )
    assert len(compiled) == 2

    original_compile = c_evaluator._compile_preprocessed_translation_unit_artifact
    compiled_names = []

    def tracking_compile(unit_name, codestr):
        compiled_names.append(unit_name)
        return original_compile(unit_name, codestr)

    monkeypatch.setattr(
        c_evaluator,
        "_compile_preprocessed_translation_unit_artifact",
        tracking_compile,
    )

    dirty_unit_b = TranslationUnit(
        name="b.c",
        path=str(tmp_path / "b.c"),
        source="int helper(void);\nint main(void) { return helper() + 1; }\n",
    )

    compiled = CEvaluator().compile_translation_units(
        [unit_a, dirty_unit_b],
        use_system_cpp=False,
        jobs=1,
        cache_dir=str(cache_dir),
    )

    assert len(compiled) == 2
    assert compiled_names == ["b.c"]


def test_cli_uses_disk_compile_cache_by_default(tmp_path, monkeypatch):
    cache_dir = tmp_path / "compile-cache"
    main_path = tmp_path / "main.c"
    main_path.write_text("int main(void) { return 0; }\n")

    result = CliRunner().invoke(
        main,
        ["--cache-dir", str(cache_dir), str(main_path)],
    )
    assert result.exit_code == 0, result.output

    def unexpected_cache_miss(unit_name, codestr):
        raise AssertionError(f"unexpected cache miss for {unit_name}")

    monkeypatch.setattr(
        c_evaluator,
        "_compile_preprocessed_translation_unit_artifact",
        unexpected_cache_miss,
    )

    result = CliRunner().invoke(
        main,
        ["--cache-dir", str(cache_dir), str(main_path)],
    )
    assert result.exit_code == 0, result.output


def test_cli_no_cache_bypasses_disk_compile_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "compile-cache"
    main_path = tmp_path / "main.c"
    main_path.write_text("int main(void) { return 0; }\n")

    compiled_names = []
    original_compile = c_evaluator._compile_preprocessed_translation_unit_artifact

    def tracking_compile(unit_name, codestr):
        compiled_names.append(unit_name)
        return original_compile(unit_name, codestr)

    monkeypatch.setattr(
        c_evaluator,
        "_compile_preprocessed_translation_unit_artifact",
        tracking_compile,
    )

    result = CliRunner().invoke(
        main,
        ["--cache-dir", str(cache_dir), "--no-cache", str(main_path)],
    )
    assert result.exit_code == 0, result.output
    assert compiled_names == ["__pcc_eval__.c"]
