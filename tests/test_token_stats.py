from embed.token_stats import analyse, embed_input, split_inputs


class WordEncoding:
    """An injected deterministic encoder makes unit tests independent of downloads."""
    def encode(self, text, **kwargs):
        return text.split()


def clause(text, title='Title'):
    return dict(id='x:1', doc='x', title=title, text=text)


def test_long_clause_sentence_boundaries():
    c = clause(('word ' * 3999 + 'end. ') * 5)
    chunks = split_inputs(c, WordEncoding())
    # Title is 1 token; each sentence is 4,000. Two fit, three do not.
    assert len(chunks) == 3
    assert [len(WordEncoding().encode(x)) for x in chunks] == [8001, 8001, 4001]
    assert all(x.startswith('Title\n') for x in chunks)
    r = analyse([c], WordEncoding())
    assert r['option_b']['vectors'] == 3
    assert r['option_b']['extra_vectors'] == 2
    assert r['overall']['gt_8191'] == 1


def test_long_sentence_fallback_and_parity():
    c = clause(' '.join(['word'] * 20000))
    chunks = split_inputs(c, WordEncoding())
    assert len(chunks) == 3
    assert sum(len(x.split())-1 for x in chunks) == 20000
    assert all(len(x.split()) <= 8191 for x in chunks)
    assert embed_input(clause('body')) == 'Title\nbody'
    small = analyse([clause('body')], WordEncoding())
    assert small['largest'][0]['tokens'] == 2
    assert small['overall']['lt_8'] == 1
    assert small['option_b']['vectors'] == 1
