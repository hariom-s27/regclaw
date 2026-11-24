"""Unit and regression tests for S10c3 results extraction, rendering, and checking."""

import copy
import hashlib
import json
from pathlib import Path
import pytest

from scripts import s10c3_render_results as R
from tests.test_s10c1_analysis import world, invoke


@pytest.fixture
def fake_env(tmp_path_factory):
    """Sets up a complete fake environment with fake analysis.json and fake receipts in tmp_path."""
    obj = world(tmp_path_factory)
    code, lines = invoke(obj)
    assert code == 0, f"invoke failed: {lines}"

    analysis_file = obj.root / "A.json"
    assert analysis_file.exists()
    analysis_bytes = analysis_file.read_bytes()
    analysis_sha = hashlib.sha256(analysis_bytes).hexdigest()

    receipts_dir = obj.root / "eval" / "s10" / "manifests"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    cand_doc = {
        "format": "s10c-candidates-receipt.v1",
        "counts": {"http_attempts": 23, "ledger_start": 0, "ledger_total": 23},
        "global_start": 0,
        "global_end": 23,
    }
    pilot_doc = {
        "format": "s10c-run-receipt.v1",
        "counts": {
            "records": 4,
            "planned_records": 4,
            "http_attempts": 6,
            "call_totals": {"global_start": 23, "global_end": 29, "total": 29},
        },
    }
    (receipts_dir / "s10c_candidates_run.json").write_text(json.dumps(cand_doc, indent=2) + "\n", encoding="utf-8")
    (receipts_dir / "s10c_pilot_run.json").write_text(json.dumps(pilot_doc, indent=2) + "\n", encoding="utf-8")

    # Copy templates and allowlist to tmp_path for isolated test operations
    repo_root = Path(__file__).resolve().parents[1]
    template_src = repo_root / "scripts" / "s10c3_results_template.md"
    block_template_src = repo_root / "scripts" / "s10c3_results_block_template.md"
    allowlist_src = repo_root / "scripts" / "s10c3_number_allowlist.json"

    test_dir = obj.root / "test_run"
    test_dir.mkdir()

    template_copy = test_dir / "template.md"
    template_copy.write_text(template_src.read_text(encoding="utf-8"), encoding="utf-8")

    block_template_copy = test_dir / "block_template.md"
    block_template_copy.write_text(block_template_src.read_text(encoding="utf-8"), encoding="utf-8")

    allowlist_copy = test_dir / "allowlist.json"
    allowlist_copy.write_text(allowlist_src.read_text(encoding="utf-8"), encoding="utf-8")

    fake_results_md = test_dir / "results.md"
    fake_results_md.write_text("# Existing Results\n\nSome earlier content.\n", encoding="utf-8")

    return {
        "root": obj.root,
        "test_dir": test_dir,
        "analysis_file": analysis_file,
        "analysis_sha": analysis_sha,
        "receipts_dir": receipts_dir,
        "template": template_copy,
        "block_template": block_template_copy,
        "allowlist": allowlist_copy,
        "results_md": fake_results_md,
    }


def test_sha_refusal(fake_env):
    """extract refuses when --expect-sha does not match analysis.json bytes."""
    bad_sha = "0" * 64
    out_file = fake_env["test_dir"] / "numbers.json"
    code = R.extract_cmd(
        str(fake_env["analysis_file"]),
        bad_sha,
        str(fake_env["receipts_dir"]),
        str(out_file),
    )
    assert code != 0
    assert not out_file.exists()


def test_missing_key_refusal(fake_env):
    """extract refuses when a required key is missing from analysis.json."""
    raw = json.loads(fake_env["analysis_file"].read_text(encoding="utf-8"))
    del raw["primary"]
    corrupted_analysis = fake_env["test_dir"] / "corrupt_A.json"
    corrupted_bytes = json.dumps(raw).encode("utf-8")
    corrupted_analysis.write_bytes(corrupted_bytes)
    corrupted_sha = hashlib.sha256(corrupted_bytes).hexdigest()

    out_file = fake_env["test_dir"] / "numbers_missing.json"
    code = R.extract_cmd(
        str(corrupted_analysis),
        corrupted_sha,
        str(fake_env["receipts_dir"]),
        str(out_file),
    )
    assert code != 0
    assert not out_file.exists()


def test_write_once_refusal(fake_env):
    """extract refuses when target output file already exists."""
    out_file = fake_env["test_dir"] / "numbers_existing.json"
    out_file.write_text("existing\n", encoding="utf-8")

    code = R.extract_cmd(
        str(fake_env["analysis_file"]),
        fake_env["analysis_sha"],
        str(fake_env["receipts_dir"]),
        str(out_file),
    )
    assert code != 0
    assert out_file.read_text(encoding="utf-8") == "existing\n"


def test_scope_non_ok_refusal(fake_env):
    """extract refuses if any run in an arm has non-ok detail."""
    raw = json.loads(fake_env["analysis_file"].read_text(encoding="utf-8"))
    raw["secondary"]["medium"]["all_runs"]["llm_detail"]["error"] = 1
    corrupted_analysis = fake_env["test_dir"] / "non_ok_A.json"
    corrupted_bytes = json.dumps(raw).encode("utf-8")
    corrupted_analysis.write_bytes(corrupted_bytes)
    corrupted_sha = hashlib.sha256(corrupted_bytes).hexdigest()

    out_file = fake_env["test_dir"] / "numbers_non_ok.json"
    code = R.extract_cmd(
        str(corrupted_analysis),
        corrupted_sha,
        str(fake_env["receipts_dir"]),
        str(out_file),
    )
    assert code != 0
    assert not out_file.exists()


def test_difference_labels_match_keys(fake_env):
    """Verify that difference labels match key names directly without hand negation."""
    out_file = fake_env["test_dir"] / "numbers.json"
    assert R.extract_cmd(
        str(fake_env["analysis_file"]),
        fake_env["analysis_sha"],
        str(fake_env["receipts_dir"]),
        str(out_file),
    ) == 0

    numbers = json.loads(out_file.read_text(encoding="utf-8"))
    vars_dict = R.build_formatted_vars(numbers)

    # Key d_pooled_medium_minus_high -> medium − high
    assert vars_dict["effort_d_pooled_direction"] == "medium − high"
    # Paired comparison direction
    assert vars_dict["paired_direction"] == "high − medium"
    # Lift reduction direction
    assert vars_dict["lift_medium_direction"] == "rules − hybrid"
    assert vars_dict["lift_high_direction"] == "rules − hybrid"


def test_render_determinism(fake_env):
    """render produces identical bytes across repeated runs."""
    numbers_file = fake_env["test_dir"] / "numbers.json"
    assert R.extract_cmd(
        str(fake_env["analysis_file"]),
        fake_env["analysis_sha"],
        str(fake_env["receipts_dir"]),
        str(numbers_file),
    ) == 0

    results_out1 = fake_env["test_dir"] / "results_s10c_dev_1.md"
    results_md1 = fake_env["test_dir"] / "results_1.md"
    results_md1.write_text(fake_env["results_md"].read_text(encoding="utf-8"), encoding="utf-8")

    assert R.render_cmd(
        str(numbers_file),
        str(fake_env["template"]),
        str(fake_env["block_template"]),
        str(results_out1),
        str(results_md1),
    ) == 0

    results_out2 = fake_env["test_dir"] / "results_s10c_dev_2.md"
    results_md2 = fake_env["test_dir"] / "results_2.md"
    results_md2.write_text(fake_env["results_md"].read_text(encoding="utf-8"), encoding="utf-8")

    assert R.render_cmd(
        str(numbers_file),
        str(fake_env["template"]),
        str(fake_env["block_template"]),
        str(results_out2),
        str(results_md2),
    ) == 0

    assert results_out1.read_bytes() == results_out2.read_bytes()
    assert results_md1.read_bytes() == results_md2.read_bytes()


def test_marked_block_replacement(fake_env):
    """Marked block replacement preserves everything outside the S10c markers."""
    numbers_file = fake_env["test_dir"] / "numbers.json"
    assert R.extract_cmd(
        str(fake_env["analysis_file"]),
        fake_env["analysis_sha"],
        str(fake_env["receipts_dir"]),
        str(numbers_file),
    ) == 0

    initial_md = "# Pre-existing Header\n\nContent before marker.\n<!-- S10c:start -->\nOld content\n<!-- S10c:end -->\nContent after marker.\n"
    test_md = fake_env["test_dir"] / "results_markers.md"
    test_md.write_text(initial_md, encoding="utf-8")

    rendered_out = fake_env["test_dir"] / "rendered.md"

    assert R.render_cmd(
        str(numbers_file),
        str(fake_env["template"]),
        str(fake_env["block_template"]),
        str(rendered_out),
        str(test_md),
    ) == 0

    updated_md = test_md.read_text(encoding="utf-8")
    before_marker = updated_md.split("<!-- S10c:start -->")[0]
    after_marker = updated_md.split("<!-- S10c:end -->")[1]

    assert before_marker == "# Pre-existing Header\n\nContent before marker.\n"
    assert after_marker == "\nContent after marker.\n"


def test_checker_catches_stray(fake_env):
    """check catches injected stray numbers and passes on clean outputs."""
    numbers_file = fake_env["test_dir"] / "numbers.json"
    assert R.extract_cmd(
        str(fake_env["analysis_file"]),
        fake_env["analysis_sha"],
        str(fake_env["receipts_dir"]),
        str(numbers_file),
    ) == 0

    rendered_out = fake_env["test_dir"] / "results_s10c_dev.md"
    results_md = fake_env["test_dir"] / "results.md"

    assert R.render_cmd(
        str(numbers_file),
        str(fake_env["template"]),
        str(fake_env["block_template"]),
        str(rendered_out),
        str(results_md),
    ) == 0

    # Clean check passes
    assert R.check_cmd(
        str(rendered_out),
        str(results_md),
        str(numbers_file),
        str(fake_env["allowlist"]),
    ) == 0

    # Inject stray number into rendered_out
    corrupt_rendered = fake_env["test_dir"] / "results_s10c_dev_corrupt.md"
    text = rendered_out.read_text(encoding="utf-8")
    corrupt_rendered.write_text(text + "\nStray number: 987654321\n", encoding="utf-8")

    assert R.check_cmd(
        str(corrupt_rendered),
        str(results_md),
        str(numbers_file),
        str(fake_env["allowlist"]),
    ) != 0


def test_checker_list_marker_vs_stray_number(fake_env):
    """The checker fails on a stray '3.5' inside a sentence, and passes on a line starting '3. text'."""
    numbers_file = fake_env["test_dir"] / "numbers.json"
    assert R.extract_cmd(
        str(fake_env["analysis_file"]),
        fake_env["analysis_sha"],
        str(fake_env["receipts_dir"]),
        str(numbers_file),
    ) == 0

    rendered_out = fake_env["test_dir"] / "results_s10c_dev.md"
    results_md = fake_env["test_dir"] / "results.md"

    assert R.render_cmd(
        str(numbers_file),
        str(fake_env["template"]),
        str(fake_env["block_template"]),
        str(rendered_out),
        str(results_md),
    ) == 0

    base_rendered = rendered_out.read_text(encoding="utf-8")

    # 1. Passes on a line starting with '3. text'
    valid_file = fake_env["test_dir"] / "results_valid_list.md"
    valid_file.write_text(base_rendered + "\n\n3. text\n", encoding="utf-8")
    assert R.check_cmd(
        str(valid_file),
        str(results_md),
        str(numbers_file),
        str(fake_env["allowlist"]),
    ) == 0

    # 2. Fails on a stray '3.5' inside a sentence
    invalid_file = fake_env["test_dir"] / "results_stray_float.md"
    invalid_file.write_text(base_rendered + "\n\nHere is a stray 3.5 inside a sentence.\n", encoding="utf-8")
    assert R.check_cmd(
        str(invalid_file),
        str(results_md),
        str(numbers_file),
        str(fake_env["allowlist"]),
    ) != 0


def test_template_keys_resolve(fake_env):
    """Verify that every template field resolves on fake analysis numbers."""
    out_file = fake_env["test_dir"] / "numbers.json"
    assert R.extract_cmd(
        str(fake_env["analysis_file"]),
        fake_env["analysis_sha"],
        str(fake_env["receipts_dir"]),
        str(out_file),
    ) == 0

    numbers = json.loads(out_file.read_text(encoding="utf-8"))
    variables = R.build_formatted_vars(numbers)

    t_text = fake_env["template"].read_text(encoding="utf-8")
    bt_text = fake_env["block_template"].read_text(encoding="utf-8")

    # Both substitute calls must succeed without KeyError
    rendered_t = R.substitute(t_text, variables)
    rendered_bt = R.substitute(bt_text, variables)

    assert "{{ " not in rendered_t and " }}" not in rendered_t
    assert "{{ " not in rendered_bt and " }}" not in rendered_bt


@pytest.mark.skip(reason="Awaiting committed numbers.json from Part C")
def test_re_render_committed():
    """Renders from committed docs/results_s10c_dev.numbers.json and asserts byte equality with committed docs."""
    repo_root = Path(__file__).resolve().parents[1]
    numbers_path = repo_root / "docs" / "results_s10c_dev.numbers.json"
    template_path = repo_root / "scripts" / "s10c3_results_template.md"
    block_template_path = repo_root / "scripts" / "s10c3_results_block_template.md"
    results_dev_path = repo_root / "docs" / "results_s10c_dev.md"
    results_md_path = repo_root / "docs" / "results.md"

    assert numbers_path.exists(), "docs/results_s10c_dev.numbers.json must exist"
    assert results_dev_path.exists(), "docs/results_s10c_dev.md must exist"
    assert results_md_path.exists(), "docs/results.md must exist"

    # Temporary render targets
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dev = Path(tmpdir) / "results_s10c_dev.md"
        tmp_results_md = Path(tmpdir) / "results.md"
        tmp_results_md.write_text(results_md_path.read_text(encoding="utf-8"), encoding="utf-8")

        code = R.render_cmd(
            str(numbers_path),
            str(template_path),
            str(block_template_path),
            str(tmp_dev),
            str(tmp_results_md),
        )
        assert code == 0
        assert tmp_dev.read_bytes() == results_dev_path.read_bytes()
        assert tmp_results_md.read_bytes() == results_md_path.read_bytes()
