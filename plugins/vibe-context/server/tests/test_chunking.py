import pytest

from vibecontext.ingest.chunking.code import chunk_code
from vibecontext.ingest.chunking.text import chunk_blocks
from vibecontext.ingest.chunking.tokens import count_tokens
from vibecontext.ingest.parsers.base import Block


def sentence(n: int) -> str:
    return f"Sentence number {n} talks about the billing rules of the platform."


def test_text_chunks_respect_limit_and_overlap():
    blocks = [Block(sentence(i)) for i in range(40)]
    chunks = chunk_blocks(blocks, max_tokens=60, overlap=20)
    assert len(chunks) > 1
    assert all(c.token_count <= 60 for c in chunks)
    for previous, current in zip(chunks, chunks[1:]):
        # The next chunk opens with the last paragraph of the previous one.
        assert current.text.split("\n\n")[0] == previous.text.split("\n\n")[-1]


def test_oversized_paragraph_is_split_by_sentence():
    paragraph = " ".join(sentence(i) for i in range(30))
    chunks = chunk_blocks([Block(paragraph)], max_tokens=50, overlap=0)
    assert len(chunks) > 1
    assert all(c.token_count <= 50 for c in chunks)
    assert " ".join(c.text for c in chunks) == paragraph


def test_single_huge_word_is_hard_split():
    blob = "A" * 5000
    chunks = chunk_blocks([Block(blob)], max_tokens=100, overlap=0)
    assert "".join(c.text for c in chunks) == blob
    assert all(c.token_count <= 100 for c in chunks)


def test_heading_starts_new_chunk_without_overlap():
    blocks = [Block(sentence(i), heading="A") for i in range(6)]
    blocks += [Block("# B", heading="B", starts_section=True), Block(sentence(99), heading="B")]
    chunks = chunk_blocks(blocks, max_tokens=200, overlap=40)
    assert chunks[-1].text.startswith("# B")
    assert chunks[-1].meta["heading"] == "B"
    assert "Sentence number 5" not in chunks[-1].text


def test_short_sections_are_packed_together():
    blocks = []
    for name in ("A", "B", "C"):
        blocks += [Block(f"# {name}", heading=name, starts_section=True), Block("tiny", heading=name)]
    assert len(chunk_blocks(blocks, max_tokens=200, overlap=0)) == 1


def test_chunk_meta_lists_pages():
    blocks = [Block(sentence(i), page=1 + i // 3) for i in range(6)]
    chunks = chunk_blocks(blocks, max_tokens=500, overlap=0)
    assert chunks[0].meta["pages"] == [1, 2]


PYTHON = '''import os


class Billing:
    """Charges customers."""

    def charge(self, customer):
{charge_body}

    def refund(self, customer):
{refund_body}


def helper():
    return os.getcwd()
'''


def big_body(prefix: str) -> str:
    return "\n".join(f"        {prefix}_{i} = customer.compute('{prefix}', {i})" for i in range(25))


def test_code_splits_big_class_into_methods():
    source = PYTHON.format(charge_body=big_body("charge"), refund_body=big_body("refund"))
    chunks = chunk_code(source, "python", max_tokens=400)
    symbols = [c.meta.get("symbols") for c in chunks]
    assert ["Billing.charge"] in symbols or any("Billing.charge" in (s or []) for s in symbols)
    assert any("Billing.refund" in (s or []) for s in symbols)
    assert all(c.token_count <= 400 for c in chunks)
    # Chunks cover the file in order with no gaps and no overlap.
    assert "".join(c.text for c in chunks) == source
    assert chunks[0].meta["start_line"] == 1


def test_small_code_file_is_one_chunk_with_all_symbols():
    source = PYTHON.format(charge_body="        return 1", refund_body="        return 2")
    chunks = chunk_code(source, "python", max_tokens=480)
    assert len(chunks) == 1
    assert chunks[0].meta["symbols"] == ["Billing", "helper"]


def test_unstructured_language_uses_line_windows():
    source = "\n".join(f"key_{i}: value number {i}" for i in range(200)) + "\n"
    chunks = chunk_code(source, "yaml", max_tokens=100)
    assert len(chunks) > 1
    assert "".join(c.text for c in chunks) == source
    assert all("symbols" not in c.meta for c in chunks)
    assert all(count_tokens(c.text) <= 100 for c in chunks)


def test_minified_line_is_hard_split():
    source = "var a=" + "1+" * 3000 + "1;"
    chunks = chunk_code(source, "javascript", max_tokens=200)
    assert "".join(c.text for c in chunks) == source
    assert all(c.token_count <= 200 for c in chunks)


@pytest.mark.parametrize(
    "language, header, statement, footer",
    [
        ("typescript", "export function session() {\n", "  const value = compute();\n", "}\n"),
        ("python", "def session():\n", "    value = compute()\n", "    return value\n"),
    ],
)
def test_large_function_splits_without_crashing(language, header, statement, footer):
    # Regression: native tree-sitter nodes outliving their parent segfaulted the backend.
    source = header + statement * 1200 + footer
    chunks = chunk_code(source, language, max_tokens=480)
    assert len(chunks) > 5
    assert "".join(c.text for c in chunks) == source
