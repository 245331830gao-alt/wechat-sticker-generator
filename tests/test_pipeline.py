"""Codec/packaging tests with generated toy shapes, not model quality tests."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'scripts/stickers.py'
spec = importlib.util.spec_from_file_location('stickers', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
FONT = next((p for p in [os.environ.get('WECHAT_TEST_FONT',''), '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'] if p and Path(p).is_file()), None)


class Names(unittest.TestCase):
    def test_optional_prefix(self):
        self.assertEqual(mod.chinese_name('吐槽日常'), '吐槽日常')
        self.assertEqual(mod.chinese_name('吐槽日常','星星'), '星星吐槽日常')
        self.assertEqual(mod.chinese_name('星星吐槽','星星'), '星星吐槽')


@unittest.skipUnless(FONT, 'Set WECHAT_TEST_FONT to a valid font file')
class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.items = []
        for i, (text,_,_) in enumerate(mod.SEEDS[:8]):
            frames = []
            for j in range(4):
                im = Image.new('RGBA',(120,120))
                d = ImageDraw.Draw(im)
                d.ellipse((18+i,16,90+i,100),fill=(60+i*20,110,180,255))
                d.rectangle((10+j*14,35,28+j*14,55),fill='white')
                p = self.root/f'{i}-{j}.png'
                im.save(p)
                frames.append(str(p))
            self.items.append({'caption':text,'keywords':['测试'],'scene':'测试编码输出','action':'独立绘制的图形测试帧','motion_steps':['起势','推进','展开','收势'],'frames':frames})

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable,str(SCRIPT),*args],capture_output=True,text=True)

    def manifest(self, mode, prefix=''):
        items = json.loads(json.dumps(self.items))
        if mode == 'static':
            for item in items:
                item['frames'] = item['frames'][-1:]
        return {'title':'测试日常','name_prefix':prefix,'mode':mode,'intro':'图形编码测试，不代表模型生成效果。','character':'程序绘制的圆形与手势方块，仅测试编码。','items':items}

    def write_plan(self, m):
        p = self.root/'plan.json'
        p.write_text(json.dumps(m,ensure_ascii=False))
        return p

    def test_modes_and_prefix(self):
        for mode,prefix in [('dynamic',''),('static',''),('static','星星')]:
            with self.subTest(mode=mode,prefix=prefix):
                plan = self.write_plan(self.manifest(mode,prefix))
                out = self.root/(mode+prefix)/'成品'
                result = self.run_cli('build','--manifest',str(plan),'--output',str(out),'--font',FONT)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(self.run_cli('validate','--directory',str(out)).returncode,0)
                names = [p.name for p in (out/'主图').iterdir()]
                self.assertEqual(len(names),8)
                self.assertTrue(all('麻花' not in n for n in names))
                if prefix:
                    self.assertTrue(all(n[3:].startswith(prefix) for n in names))
                bundle = next(out.parent.glob('*.zip'))
                bundle.unlink()
                self.assertNotEqual(self.run_cli('validate','--directory',str(out)).returncode,0)

    def test_failure_does_not_leave_partial_pack(self):
        m = self.manifest('dynamic')
        m['items'][-1]['durations_ms'] = [20]
        p = self.write_plan(m)
        out = self.root/'bad'/'成品'
        result = self.run_cli('build','--manifest',str(p),'--output',str(out),'--font',FONT)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(out.exists())
        self.assertEqual(list(out.parent.iterdir()),[])

    def test_duplicate_art_rejected(self):
        m = self.manifest('static')
        m['items'][1]['frames'] = m['items'][0]['frames']
        p = self.write_plan(m)
        out = self.root/'duplicate'/'成品'
        result = self.run_cli('build','--manifest',str(p),'--output',str(out),'--font',FONT)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
