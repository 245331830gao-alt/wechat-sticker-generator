#!/usr/bin/env python3
"""Plan, encode, audit and package image-generated WeChat sticker candidates.
The host supplies image generation; no network, model or credentials are used here.
"""
import argparse
import csv
import hashlib
import html
import json
import re
import tempfile
from datetime import date
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / 'references/profile.json'
SEEDS = [
 ('我替你尴尬','捂脸','手抬到脸上，再从指缝偷看'),
 ('你认真的','怀疑','瞪眼、张嘴、抬起双手'),
 ('我服了','无语','两手抬起，再放下叹气'),
 ('你先统一口径','反问','抬起食指，左右摇指'),
 ('等一下','稍等','手从下方抬到胸前，掌心向前'),
 ('脑子在加载','思考','抬手摸太阳穴，皱眉思索'),
 ('略略略','搞怪','先张嘴，舌尖出现，再伸舌'),
 ('懒得理你','白眼','眼睛看向上方，再收回目光'),
 ('您先请','客气','抬起手臂，掌心朝上请对方先来'),
 ('无话可说','无语','张嘴又闭嘴，最后托腮'),
 ('哭给你看','委屈','嘴角下垂，出现泪水，抬手擦泪'),
 ('拿捏了','得意','挑眉，抱起双臂，最后得意微笑'),
 ('累了','疲惫','肩膀下垂，弯腰，再托腮休息'),
 ('你礼貌吗','反问','抬起手指，再朝前方反问'),
 ('溜了','告别','侧身、屈膝、迈步，再跑走'),
 ('你可真行','调侃','抬手竖起拇指，再勉强微笑'),
 ('听不懂','疑问','歪头，抬手挠头'),
 ('好好好','敷衍','手掌合拢拍掌，再冷脸分开'),
 ('火冒三丈','生气','握拳，张嘴，耳鼻嘴多处逐步冒烟'),
 ('先吃一口','吃饭','拿起点心，送到嘴边，咬下一口'),
 ('辛苦自己了','安慰','张开双臂，再抱住自己'),
 ('我就看看','围观','手抬向额头，微微前探，观察'),
 ('这就离谱','震惊','睁大双眼，身体后仰，举起双手'),
 ('睡了再说','晚安','眼睛半闭，低头，双手托住下巴')]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def chinese_name(value, prefix=''):
    value = ''.join(re.findall(r'[\u4e00-\u9fff]', value))
    require(bool(value), '名称必须包含中文')
    require(not prefix or re.fullmatch(r'[\u4e00-\u9fff]+', prefix), '命名前缀须为中文或留空')
    return value if not prefix or value.startswith(prefix) else prefix + value


def tsv(rows):
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), delimiter='\t')
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def png_bytes(image, limit):
    for colors in (None, 256, 128, 64, 32):
        candidate = image if colors is None else image.quantize(colors, method=Image.Quantize.FASTOCTREE)
        out = BytesIO()
        candidate.save(out, 'PNG', optimize=True)
        if out.tell() <= limit:
            return out.getvalue()
    raise ValueError(f'PNG超过目标{limit}字节；请简化画面，不能静默降尺寸')


def jpeg_bytes(image, limit):
    for quality in (92, 85, 76, 66, 54, 42):
        out = BytesIO()
        image.convert('RGB').save(out, 'JPEG', quality=quality, optimize=True)
        if out.tell() <= limit:
            return out.getvalue()
    raise ValueError(f'横幅超过目标{limit}字节，请简化画面')


def frame_source(spec, base):
    if isinstance(spec, str):
        spec = {'path': spec}
    require(isinstance(spec, dict) and spec.get('path'), 'frames需有效图片路径')
    path = Path(spec['path'])
    if not path.is_absolute():
        path = base / path
    with Image.open(path) as image:
        image = image.convert('RGBA')
    if 'crop' in spec:
        require(not any(k in spec for k in ('row', 'col', 'rows', 'cols')), 'crop不能与图集索引混用')
        box = spec['crop']
        require(len(box) == 4 and 0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height, 'crop越界或无效')
        image = image.crop(tuple(box))
    elif any(k in spec for k in ('row', 'col', 'rows', 'cols')):
        rows, cols = spec.get('rows', 1), spec.get('cols', 1)
        rr, cc = spec.get('row', 0), spec.get('col', 0)
        require(all(isinstance(v, int) for v in (rows, cols, rr, cc)) and rows > 0 and cols > 0 and 0 <= rr < rows and 0 <= cc < cols, '图集行列索引越界')
        w, h = image.size
        image = image.crop((round(w*cc/cols), round(h*rr/rows), round(w*(cc+1)/cols), round(h*(rr+1)/rows)))
    require(image.width > 0 and image.height > 0, '空图片')
    return image


def visible_hash(image):
    # Ignore hidden RGB under fully transparent pixels.
    rgba = Image.new('RGBA', image.size)
    rgba.alpha_composite(image)
    return hashlib.sha256(rgba.tobytes()).hexdigest()


def normalize(frames, size):
    w, h = max(f.width for f in frames), max(f.height for f in frames)
    require(all(abs(f.width-w) <= 1 and abs(f.height-h) <= 1 for f in frames), '同一动作的帧尺寸不一致，请对齐后重试')
    padded = []
    boxes = []
    for f in frames:
        require(f.getchannel('A').getextrema()[0] == 0, '主图源帧无透明区域；请先用生图编辑工具去背景')
        box = f.getchannel('A').getbbox()
        require(box is not None, '源帧完全透明')
        p = Image.new('RGBA', (w, h))
        p.alpha_composite(f, (0, 0))
        padded.append(p)
        boxes.append(box)
    box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
    bw, bh = box[2]-box[0], box[3]-box[1]
    scale = min((size[0]-20)/bw, (size[1]-55)/bh)
    dest = (max(1, round(bw*scale)), max(1, round(bh*scale)))
    output = []
    for f in padded:
        cut = f.crop(box).resize(dest, Image.Resampling.LANCZOS)
        p = Image.new('RGBA', tuple(size))
        p.alpha_composite(cut, ((size[0]-dest[0])//2, 5+(size[1]-55-dest[1])//2))
        output.append(p)
    return output


def outline(image, width):
    if width <= 0:
        return image.copy()
    a = image.getchannel('A').filter(ImageFilter.MaxFilter(2*width+1))
    back = Image.new('RGBA', image.size, 'white')
    back.putalpha(a)
    back.alpha_composite(image)
    return back


def caption(image, text, font_path):
    require(bool(text) and len(text) <= 20, '字幕为空或超过20字；请先精简文案')
    image = image.copy()
    d = ImageDraw.Draw(image)
    chosen = None
    for size in range(34, 17, -1):
        f = ImageFont.truetype(str(font_path), size)
        if d.textlength(text, font=f) <= image.width-20:
            chosen = f
            break
    if chosen:
        d.text((image.width//2, image.height-23), text, anchor='mm', font=chosen, fill='#22282d', stroke_width=2, stroke_fill='white')
    else:
        f = ImageFont.truetype(str(font_path), 18)
        half = (len(text)+1)//2
        require(max(d.textlength(t, font=f) for t in (text[:half], text[half:])) <= image.width-20, '字幕仍超宽')
        for y, line in zip((image.height-35, image.height-14), (text[:half], text[half:])):
            d.text((image.width//2, y), line, anchor='mm', font=f, fill='#22282d', stroke_width=2, stroke_fill='white')
    return image


def encode_gif(frames, durations, limit):
    for colors in (192, 128, 96, 64, 48, 32):
        atlas = Image.new('RGB', (frames[0].width, frames[0].height*len(frames)), 'white')
        for j, f in enumerate(frames):
            white = Image.new('RGBA', f.size, 'white')
            white.alpha_composite(f)
            atlas.paste(white.convert('RGB'), (0, j*f.height))
        palette = atlas.quantize(colors)
        encoded = []
        for f in frames:
            white = Image.new('RGBA', f.size, 'white')
            white.alpha_composite(f)
            q = white.convert('RGB').quantize(palette=palette, dither=Image.Dither.NONE)
            mask = f.getchannel('A').point(lambda a: 255 if a < 128 else 0)
            q.paste(255, (0, 0), mask)
            q.info['transparency'] = 255
            encoded.append(q)
        out = BytesIO()
        encoded[0].save(out, 'GIF', save_all=True, append_images=encoded[1:], duration=durations, loop=0, transparency=255, disposal=2, optimize=False)
        if out.tell() <= limit:
            return out.getvalue()
    raise ValueError(f'GIF超过{limit}字节目标；请简化或重绘动作，不可降尺寸或伪造静态GIF')


def inspect_image(path, expected_size=None, max_bytes=None, alpha=False, dynamic=False):
    raw = Path(path).read_bytes()
    with Image.open(BytesIO(raw)) as im:
        if expected_size:
            require(im.size == tuple(expected_size), f'{path.name}尺寸错误')
        if max_bytes:
            require(len(raw) <= max_bytes, f'{path.name}体积超限')
        if alpha:
            require(im.convert('RGBA').getchannel('A').getextrema()[0] == 0, f'{path.name}无透明像素')
        n = getattr(im, 'n_frames', 1)
        total, hashes = 0, set()
        if dynamic:
            require(im.format == 'GIF' and n >= 3 and im.info.get('loop') == 0, f'{path.name}并非循环动态GIF')
            for j in range(n):
                im.seek(j)
                require(im.info.get('duration', 0) >= 50, f'{path.name}帧时长过短')
                total += im.info['duration']
                hashes.add(visible_hash(im.convert('RGBA')))
            require(len(hashes) >= 3, f'{path.name}不同画面不足，疑似静帧伪动图')
        return {'文件': path.name, 'SHA256': hashlib.sha256(raw).hexdigest(), '格式': im.format, '宽': im.width, '高': im.height, '字节': len(raw), '帧数': n, '时长毫秒': total, '含透明像素': alpha}


def make_plan(args):
    profile = load_json(args.profile)
    require(args.count in profile['counts'], '数量不在当前制作配置中')
    require(Path(args.reference).is_file(), '参考图片不存在')
    Image.open(args.reference).verify()
    items = []
    for text, key, action in SEEDS[:args.count]:
        items.append({'caption': text, 'keywords': [key], 'scene': '按角色和本期主题补写', 'action': action, 'motion_steps': [], 'frames': []})
    obj = {'title': chinese_name(args.title, args.prefix), 'name_prefix': args.prefix, 'mode': args.mode, 'reference': str(Path(args.reference).resolve()), 'theme': args.theme,
           'intro': '让日常情绪有个表情，聊天里的无语、开心和小得意，都有人替你表达。',
           'character': '', 'author': '', 'rights': {'status': 'pending', 'holder': '', 'source': '用户上传参考图', 'note': '待实际提供适用材料'},
           'platform': {'status': 'pending', 'source': 'https://sticker.weixin.qq.com/', 'checked_at': str(date.today())},
           'visual_review': {'status': 'pending', 'notes': ''}, 'items': items, 'assets': {}}
    target = Path(args.output)
    require(not target.exists(), '计划文件已存在；请用新路径避免覆盖')
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, obj)
    print(target)


def build_inner(args):
    m = load_json(args.manifest)
    profile = load_json(args.profile)
    name_prefix = m.get('name_prefix', '')
    title = chinese_name(m.get('title', ''), name_prefix)
    require(re.fullmatch(r'[\u4e00-\u9fff]+', title) is not None and len(title) <= profile['title_max_chars'], '专辑名需纯中文，且符合标题字数目标')
    require(m.get('mode') in ('dynamic', 'static'), 'mode必须dynamic或static')
    require(0 < len(m.get('intro', '')) <= profile['intro_max_chars'], '专辑介绍为空或超字数')
    require(bool(m.get('character', '').strip()), '需按参考图填写角色介绍character')
    items = m.get('items', [])
    require(len(items) in profile['counts'], '主图数量不在制作配置中')
    require(len({it.get('caption') for it in items}) == len(items), '表情字幕重复')
    font_path = Path(args.font)
    require(font_path.is_file(), '缺少中文字体文件')
    ImageFont.truetype(str(font_path), 20)
    output = Path(args.output).resolve()
    require(not output.exists() or not any(output.iterdir()), '输出目录非空；请另用新目录')
    base = Path(args.manifest).resolve().parent
    # Validate every source before writing final deliverables.
    prepared = []
    action_hashes = set()
    for item in items:
        require(item.get('keywords') and item.get('scene') and item.get('action'), '每张需含义词、使用场景和动作说明')
        require(item.get('scene') != '按角色和本期主题补写', '请先按实际主题补全使用场景')
        sources = [frame_source(f, base) for f in item.get('frames', [])]
        if m['mode'] == 'dynamic':
            require(4 <= len(sources) <= 24, '动态每张需4—24个实际动作帧')
            steps = item.get('motion_steps', [])
            require(len(steps) == len(sources) and all(isinstance(step, str) and step.strip() for step in steps), 'motion_steps须逐帧说明实际动作')
            require(len(set(steps)) >= 3 and not all(step.strip() in ('抖动','摇摆','缩放','平移','扭曲') for step in steps), '逐帧动作语义重复或仅为整图变换，请填写实际动作阶段')
        else:
            require(len(sources) == 1, '静态每张只能一个源姿态')
        poses = normalize(sources, profile['main_size'])
        if m['mode'] == 'dynamic':
            require(len(set(map(visible_hash, poses))) >= 3, '动态源画面差异不足')
        action_hash = hashlib.sha256(''.join(map(visible_hash, poses)).encode()).hexdigest()
        require(action_hash not in action_hashes, '不同表情重复使用同组姿态；不能只换字幕凑整套')
        action_hashes.add(action_hash)
        prepared.append(poses)
    output.mkdir(parents=True, exist_ok=True)
    def put(name, value):
        p = output / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(value if isinstance(value, bytes) else value.encode('utf-8-sig'))
        return p
    audit, entries, peaks, representatives = [], [], [], []
    for index, (item, poses) in enumerate(zip(items, prepared), 1):
        name = chinese_name(item['caption'], name_prefix)
        prefix = f'{index:02d}-{name}'
        thumb_index = item.get('thumbnail_frame', len(poses)-1)
        require(isinstance(thumb_index, int) and 0 <= thumb_index < len(poses), 'thumbnail_frame越界')
        fs = [caption(outline(f, profile['outline_px']), item['caption'], font_path) for f in poses]
        peak = fs[thumb_index]
        representatives.append(poses[thumb_index])
        peaks.append(peak)
        if m['mode'] == 'dynamic':
            middle_ms = max(50, min(250, int(round(600/max(1,len(fs)-2)/10)*10)))
            ds = item.get('durations_ms', [300]+[middle_ms]*(len(fs)-2)+[500])
            require(len(ds) == len(fs) and all(isinstance(v, (int, float)) and 50 <= v <= 2000 for v in ds), '帧时长数量不匹配或超50—2000ms')
            ds = [int(round(v/10)*10) for v in ds]
            order = list(range(len(fs)))
            if item.get('return_to_start', True):
                order += list(range(len(fs)-2, 0, -1))
            raw = encode_gif([fs[j] for j in order], [ds[j] for j in order], profile['main_max_bytes'])
            ext = '.gif'
        else:
            raw = png_bytes(peak, profile['main_max_bytes'])
            ext = '.png'
        main = '主图/'+prefix+ext
        p = put(main, raw)
        report = inspect_image(p, profile['main_size'], profile['main_max_bytes'], True, m['mode']=='dynamic')
        report['文件'] = main
        audit.append(report)
        thumb = '缩略图/'+prefix+'.png'
        p = put(thumb, png_bytes(peak.resize(tuple(profile['thumb_size']), Image.Resampling.LANCZOS), profile['thumb_max_bytes']))
        report = inspect_image(p, profile['thumb_size'], profile['thumb_max_bytes'], True)
        report['文件'] = thumb
        audit.append(report)
        entries.append({'编号': f'{index:02d}', '表情名称': name, '图内文字': item['caption'], '含义词建议': ' '.join(item['keywords']), '使用场景': item['scene'], '动作说明': item['action'], '逐帧动作': ' → '.join(item.get('motion_steps', [])), '主图文件': main, '缩略图文件': thumb, '主图字节': len(raw)})
    assets = m.get('assets', {})
    def specified(key, default):
        return frame_source(assets[key], base) if key in assets else default.copy()
    def fit(image, size):
        im = image.copy()
        im.thumbnail(tuple(size), Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', tuple(size))
        canvas.alpha_composite(im, ((size[0]-im.width)//2, (size[1]-im.height)//2))
        return canvas
    def scene(size, ids):
        canvas = Image.new('RGBA', size, '#eac98a')
        d = ImageDraw.Draw(canvas)
        d.ellipse((size[0]*.15, size[1]*.12, size[0]*.9, size[1]*.9), fill='#f6dfb2')
        for k, i in enumerate(ids):
            source = representatives[i].crop((0, 0, profile['main_size'][0], profile['main_size'][1]-48))
            bw = int(size[0]/len(ids)-32)
            source = source.resize((bw, max(1, round(source.height*bw/source.width))), Image.Resampling.LANCZOS)
            if source.height > size[1]-25:
                source.thumbnail((bw, size[1]-25), Image.Resampling.LANCZOS)
            canvas.alpha_composite(source, (int((k+.5)*size[0]/len(ids)-source.width/2), (size[1]-source.height)//2))
        return canvas
    cover = fit(specified('cover', representatives[0]), (240, 240))
    icon_default = representatives[0].crop((20, 0, 220, 155))
    icon = fit(specified('icon', icon_default), (50, 50))
    configs = [
        ('专辑素材/封面.png', cover, (240,240), profile['cover_max_bytes'], True),
        ('专辑素材/聊天图标.png', icon, (50,50), profile['icon_max_bytes'], True),
        ('专辑素材/横幅.jpg', fit(specified('banner', scene((750,400), [0, len(items)//2, len(items)-1])), (750,400)), (750,400), profile['banner_max_bytes'], False),
        ('赞赏素材/赞赏引导图.png', fit(specified('appreciation_guide', scene((750,560), [0])), (750,560)), (750,560), profile['guide_max_bytes'], False),
        ('赞赏素材/赞赏致谢图.png', fit(specified('appreciation_thanks', scene((750,750), [len(items)-1])), (750,750)), (750,750), profile['thanks_max_bytes'], False)]
    for name, image, size, limit, alpha in configs:
        if not alpha:
            back = Image.new('RGBA', size, '#eac98a')
            back.alpha_composite(image)
            image = back
        encoded = jpeg_bytes(image, limit) if name.endswith('.jpg') else png_bytes(image, limit)
        p = put(name, encoded)
        report = inspect_image(p, size, limit, alpha)
        report['文件'] = name
        audit.append(report)
    cols, cell = 4, 200
    for bg, name in (('#fff8eb', '浅底总览.jpg'), ('#24272b', '深底总览.jpg')):
        board = Image.new('RGBA', (cols*cell, ((len(peaks)+cols-1)//cols)*cell), bg)
        for i, peak in enumerate(peaks):
            board.alpha_composite(peak.resize((cell,cell), Image.Resampling.LANCZOS), (i%cols*cell, i//cols*cell))
        put(name, jpeg_bytes(board, 800000))
    platform = m.get('platform', {})
    rights = m.get('rights', {})
    visual = m.get('visual_review', {})
    pending = []
    if platform.get('status') != 'verified':
        pending.append('微信当前投稿页面规范待复核，制作目标不是官方审核保证。')
    if rights.get('status') != 'provided':
        pending.append('角色版权、真人肖像或其他适用授权材料待实际提供；不能以署名代替授权。')
    if not m.get('author') or not rights.get('holder'):
        pending.append('实际作者展示名或权利人字段待据实补齐。')
    if visual.get('status') != 'passed':
        pending.append('逐张播放、角色一致性、手部、边缘、字幕、封面图标横幅等人工目检待完成。')
    pending += ['投稿账号资料、专辑重名、赞赏功能与后台额外字段按实际页面处理。', '本包未上传、未送审，无平台审核回执。']
    metadata = f'''{title}｜投稿文案
专辑名：{title}
类型：{'动态GIF' if m['mode']=='dynamic' else '静态PNG'}；{len(items)}款
主题：{m.get('theme','日常聊天')}
专辑介绍：{m['intro']}
角色介绍：{m['character']}
作者展示名：{m.get('author') or '待实际填写'}
版权主体：{rights.get('holder') or '待实际填写'}
参考来源：{rights.get('source','用户上传参考图')}
来源说明：根据参考图实际生成不同姿态，再排版和编码；不声称出自原片。
赞赏引导语：{m.get('appreciation_prompt','喜欢这套表情，欢迎支持创作')}
赞赏致谢语：{m.get('appreciation_thanks','谢谢支持，愿你的每种心情都被接住')}
平台核对来源：{platform.get('source','https://sticker.weixin.qq.com/')}
平台核对日期：{platform.get('checked_at','未核对')}
平台核对状态：{platform.get('status','pending')}
权属材料状态：{rights.get('status','pending')}
以上为投稿拟稿；实际字段名称、字数、可选标签与限制以当前后台为准。
'''
    checklist = title+'｜投稿准备\n\n'+'\n'.join(f'{i+1}. {s}' for i,s in enumerate([
        '登录微信表情开放平台 https://sticker.weixin.qq.com/，查看当前专辑投稿类型和制作要求。',
        '使用投稿文案.txt填写对应字段；不可将待填内容当成已核验事实。',
        '按01起的编号顺序选择主图文件，与缩略图同编号配对。',
        '选择同用途的封面、聊天图标和横幅；赞赏图按实际功能与要求使用。',
        '若后台要求纯数字名，只重命名投稿副本；保留本包中文命名与对应表。',
        '预览深浅背景下的表情，核对人物、文字、动作、白边、排序与配图。',
        '补齐实际授权和账号资料后再自行提交；记录平台审核回执。']+pending))
    guide = f'''{title}｜完整使用说明

一、快速使用
下载整套ZIP并完整解压。打开「完整使用说明.html」查看全套；发送请选「主图」原文件。动态是GIF，静态是PNG；缩略图与总览不能代替动态主图。不要截屏或转成JPEG后再当动图发送。

二、内容和画面
{m['character']}
本期为{len(items)}款{'关键动作姿态循环动图' if m['mode']=='dynamic' else '独立姿态静态图片'}。{'各动作使用不同绘制帧，时长和帧数见参数清单；不承诺等同高帧率连续原片动画。GIF没有声音。' if m['mode']=='dynamic' else '静态图片不包含动画或声音。'} 主图透明背景，主体和文字添加白边以兼顾深浅聊天背景；专辑封面与图标不额外加字幕或白边。

三、文件与用途
主图：实际聊天主图，240×240。
缩略图：120×120 PNG，同编号一一对应。
专辑素材：240×240透明PNG封面、50×50透明PNG图标、750×400不透明JPG横幅。
赞赏素材：750×560 PNG引导图、750×750 PNG致谢图，均为候选素材；未替用户开通赞赏。
投稿文案.txt：专辑、角色、作者、权利人、来源和赞赏拟稿。
表情清单.tsv：逐张名称、含义词、场景、动作及文件对应关系；Excel选择UTF-8与制表符导入。
素材参数清单.tsv：实际尺寸、体积、帧数、时长与透明检查。
投稿准备清单.txt：投稿流程及待补事项。
检查报告.json：文件检查、人工目检、平台核对与授权分开记录。
浅底总览.jpg、深底总览.jpg：静态全套对照；完整HTML中主图按原类型播放。

四、逐张说明
'''
    for e in entries:
        guide += '\n'+e['编号']+' '+e['表情名称']+'\n含义词：'+e['含义词建议']+'\n场景：'+e['使用场景']+'\n动作：'+e['动作说明']+'\n逐帧：'+e['逐帧动作']+'\n主图：'+e['主图文件']+'\n缩略图：'+e['缩略图文件']+'\n'
    guide += '''
五、常见问题
GIF不动：确认没有选PNG缩略图；用浏览器查看，部分系统文件预览器只显示首帧。
网页缺图：完整解压，保持文件夹名称和相对位置，不要单独移动HTML。
透明底变黑：更换支持透明GIF/PNG的查看器，并在实际客户端试发。
能修改文字吗：成品中文字已烧录；需要修改计划并重新生成/编码，不是直接改清单。
可以直接发布吗：先查看投稿准备清单和检查报告。文件检查通过不代表角色授权、官方规范核对或审核通过。
本包不含图像模型、分层工程、三维模型、虚构授权或平台回执。
'''
    put('使用说明.txt', guide)
    put('投稿文案.txt', metadata)
    put('投稿准备清单.txt', checklist)
    put('表情清单.tsv', tsv(entries))
    put('素材参数清单.tsv', tsv(audit))
    report = {'title': title, 'file_check': 'passed', 'mode': m['mode'], 'count': len(items), 'profile': profile,
              'visual_review': visual, 'platform': platform, 'rights': rights, 'pending': pending, 'assets': audit}
    put('检查报告.json', json.dumps(report, ensure_ascii=False, indent=2))
    body = '<h1>'+html.escape(title)+'</h1><p>主图按实际格式显示；浅色、深色两种背景可切换。</p><button onclick="document.body.classList.toggle(\'dark\')">切换深浅背景</button><main>'
    for e in entries:
        body += '<article><img src="'+html.escape(e['主图文件'], quote=True)+'" alt="'+html.escape(e['表情名称'], quote=True)+'"><h3>'+html.escape(e['编号']+' '+e['表情名称'])+'</h3><p>'+html.escape(e['使用场景'])+'</p><p>'+html.escape(e['动作说明'])+'</p></article>'
    body += '</main><h2>专辑配图</h2><aside>'
    for name,_,_,_,_ in configs:
        body += '<figure><img src="'+name+'"><figcaption>'+name+'</figcaption></figure>'
    body += '</aside><h2>完整使用说明</h2><pre>'+html.escape(guide)+'</pre><h2>投稿文案</h2><pre>'+html.escape(metadata)+'</pre><h2>投稿准备</h2><pre>'+html.escape(checklist)+'</pre>'
    put('完整使用说明.html', '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>body{font:16px/1.7 system-ui;background:#fff8eb;color:#27313a;max-width:1100px;margin:30px auto;padding:0 18px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}article,figure{background:#f5ead5;border:1px solid #bdab8c;border-radius:12px;padding:12px}img{max-width:100%;display:block;margin:auto}article img{width:240px}h3{font-size:18px}pre{white-space:pre-wrap;font:inherit}aside img{max-height:280px}.dark{background:#24272b;color:#f6e9d1}.dark article,.dark figure{background:#383b40}button{margin:12px;padding:8px 18px}</style>'+body+'</html>')
    zip_path = output.parent/(title+'投稿资料.zip')
    require(not zip_path.exists(), 'ZIP目标已存在；请选新输出父目录避免覆盖')
    with ZipFile(zip_path, 'w', ZIP_DEFLATED) as z:
        for p in sorted(output.rglob('*')):
            if p.is_file():
                z.write(p, p.relative_to(output).as_posix())
    with ZipFile(zip_path) as z:
        require(z.testzip() is None, 'ZIP完整性检查失败')
    return {'directory': str(output), 'zip': str(zip_path), 'file_check': 'passed', 'pending': pending}



def build(args):
    # Stage the whole package privately. Errors cannot leave a half-built final pack.
    target = Path(args.output).resolve()
    require(not target.exists() or not any(target.iterdir()), '输出目录非空；请另用新目录')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.wechat-build-', dir=target.parent) as temporary:
        staging = Path(temporary)/'成品'
        staged_args = argparse.Namespace(**vars(args))
        staged_args.output = str(staging)
        result = build_inner(staged_args)
        staged_zip = Path(result['zip'])
        final_zip = target.parent/staged_zip.name
        require(not final_zip.exists(), 'ZIP目标已存在；请选新输出父目录避免覆盖')
        if target.exists():
            target.rmdir()  # Empty directory only; never delete user files.
        staging.rename(target)
        staged_zip.rename(final_zip)
        result.update(directory=str(target), zip=str(final_zip))
    print(json.dumps(result, ensure_ascii=False))


def validate(args):
    root = Path(args.directory)
    report = load_json(root/'检查报告.json')
    profile, mode = report['profile'], report['mode']
    mains = sorted((root/'主图').iterdir())
    thumbs = sorted((root/'缩略图').iterdir())
    require(len(mains) == report['count'] == len(thumbs), '主图/缩略图数量错误')
    require(len(mains) in profile['counts'], '数量不在配置中')
    for a,b in zip(mains, thumbs):
        require(a.stem == b.stem, '主图缩略图名称不匹配')
        require(a.suffix == ('.gif' if mode=='dynamic' else '.png'), '主图混用动静类型')
        inspect_image(a, profile['main_size'], profile['main_max_bytes'], True, mode=='dynamic')
        inspect_image(b, profile['thumb_size'], profile['thumb_max_bytes'], True)
    for asset in report['assets']:
        p = root/asset['文件']
        current = inspect_image(p, (asset['宽'],asset['高']), asset['字节'], asset['含透明像素'], mode=='dynamic' and str(p.parent.name)=='主图')
        require(current['字节'] == asset['字节'] and current['SHA256'] == asset['SHA256'], '文件内容发生变化，需重建报告')
    expected_docs = ['使用说明.txt','投稿文案.txt','投稿准备清单.txt','表情清单.tsv','素材参数清单.tsv','完整使用说明.html','浅底总览.jpg','深底总览.jpg']
    require(all((root/n).is_file() for n in expected_docs), '说明或预览资料缺失')
    zip_path = root.parent/(report['title']+'投稿资料.zip')
    require(zip_path.is_file(), '整套ZIP缺失')
    with ZipFile(zip_path) as z:
        require(z.testzip() is None, 'ZIP完整性错误')
        names = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        require(len(z.namelist()) == len(names) and set(z.namelist()) == names, 'ZIP与目录文件清单不一致')
        require(all(z.read(n) == (root/n).read_bytes() for n in names), 'ZIP内容与目录不一致')
    print(json.dumps({'file_check':'passed','count':len(mains),'platform':report['platform'].get('status','pending'),'visual_review':report['visual_review'].get('status','pending')}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan')
    plan.add_argument('--reference', required=True)
    plan.add_argument('--output', required=True)
    plan.add_argument('--mode', choices=('dynamic','static'), default='dynamic')
    plan.add_argument('--count', type=int, default=16)
    plan.add_argument('--title', default='吐槽日常')
    plan.add_argument('--theme', default='日常吐槽')
    plan.add_argument('--prefix', default='', help='可选中文命名前缀，默认不添加品牌')
    plan.add_argument('--profile', default=str(DEFAULT_PROFILE))
    build_p = sub.add_parser('build')
    build_p.add_argument('--manifest', required=True)
    build_p.add_argument('--output', required=True)
    build_p.add_argument('--font', required=True)
    build_p.add_argument('--profile', default=str(DEFAULT_PROFILE))
    valid = sub.add_parser('validate')
    valid.add_argument('--directory', required=True)
    args = parser.parse_args()
    try:
        {'plan':make_plan,'build':build,'validate':validate}[args.command](args)
    except (ValueError,OSError,KeyError,TypeError,BadZipFile) as exc:
        parser.exit(2, '未完成：'+str(exc)+'\n')


if __name__ == '__main__':
    main()
