import pytest

from tests.video_generation.test_hayale_durable import module


def test_separator_normalization_preserves_relative_identity():
    normalize = module('durable').normalize_output_subfolder
    assert normalize('shots\\shot-001') == normalize('shots/shot-001')
    assert normalize('') == ''


@pytest.mark.parametrize('path', ['../escape', 'ok/../../escape', 'C:\\private', 'C:relative',
                                 '/root/absolute', '\\\\host\\share', 'bad\x00path'])
def test_absolute_and_traversal_provenance_is_rejected(path):
    with pytest.raises(ValueError, match='relative'):
        module('durable').normalize_output_subfolder(path)
