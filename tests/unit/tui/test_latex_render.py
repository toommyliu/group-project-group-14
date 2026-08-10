from opengrader.tui.latex_render import render_response_latex_png, response_has_latex


def test_detects_inline_and_display_math():
    assert response_has_latex(r"The derivative is $\frac{dy}{dx} = 2x$.")
    assert response_has_latex(r"$$\int_0^1 x^2 \,dx = \tfrac{1}{3}$$")


def test_no_latex_in_plain_text():
    assert not response_has_latex("The mitochondria is the powerhouse of the cell.")
    assert not response_has_latex("")
    assert not response_has_latex(None)


def test_renders_valid_latex_to_png(tmp_path):
    dest = tmp_path / "q1.png"
    ok = render_response_latex_png(r"Answer: $\frac{1}{2} + \sqrt{x^2 + 1}$", dest)
    assert ok is True
    assert dest.exists()
    # PNG magic number.
    assert dest.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_plain_text_is_not_rendered(tmp_path):
    dest = tmp_path / "q1.png"
    assert render_response_latex_png("just prose, no math", dest) is False
    assert not dest.exists()


def test_unparseable_latex_fails_cleanly_without_writing(tmp_path):
    dest = tmp_path / "q1.png"
    # Unbalanced brace / bad command mathtext cannot parse.
    ok = render_response_latex_png(r"$\frac{1}{$", dest)
    assert ok is False
    assert not dest.exists()  # no half-written file
