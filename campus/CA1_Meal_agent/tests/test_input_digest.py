from eval.input_digest import text_sha256


def test_line_endings_are_portable_but_changed_input_is_detected(tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_bytes(b'{\n  "price": 7000\n}\n')
    second.write_bytes(first.read_bytes().replace(b'\n', b'\r\n'))
    assert text_sha256(first) == text_sha256(second)
    second.write_bytes(second.read_bytes().replace(b'7000', b'8000'))
    assert text_sha256(first) != text_sha256(second)
