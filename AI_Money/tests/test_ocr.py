"""Actual bundled ONNX models, synthetic fixtures. Not a real THS screenshot accuracy claim."""
from PIL import Image,ImageDraw,ImageFont
import pytest
from ths_watch.capture import Reader,CaptureError

@pytest.fixture(scope='module')
def reader():return Reader()

@pytest.mark.parametrize('size',[18,24,35])
def test_actual_model_numeric_cell(reader,size):
    # Windows has Arial, Linux uses DejaVu for this integration fixture.
    import sys
    fontpath='C:/Windows/Fonts/arial.ttf' if sys.platform=='win32' else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    im=Image.new('RGB',(120,45),'black')
    ImageDraw.Draw(im).text((3,2),'9.53',fill='red',font=ImageFont.truetype(fontpath,size))
    lines=reader.lines(im)
    assert len(lines)==1
    assert lines[0][0]=='9.53' and lines[0][1]>.9
