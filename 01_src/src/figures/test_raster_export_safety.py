"""Raster scientific content must never silently disappear from SVG."""
import pytest

from src.figures.exporter import ExportError, flatten_svg


@pytest.mark.parametrize('href', ['data:image/png;base64,aGVsbG8=', 'https://example.invalid/image.png'])
def test_svg_image_is_explicitly_refused_until_supported(href):
    source = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
              f'<image href="{href}" width="100" height="100"/></svg>')
    with pytest.raises(ExportError) as error:
        flatten_svg(source)
    assert error.value.args[0] == 'unsupported_raster_svg'
