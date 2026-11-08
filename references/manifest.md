# 计划与脚本接口

依赖Python 3.10+和Pillow。字体用可读中文的TTF/OTF/TTC，脚本不下载模型或字体。

生成空计划：`python scripts/stickers.py plan --reference /图.png --output /制作/制作计划.json --mode dynamic --count 16 --title 吐槽日常 --theme 日常吐槽`

生成计划后须填每项frames并根据主题改写caption、keywords、scene、action、motion_steps；不要交付空frames计划冒充完成。

```json
{
  "title":"吐槽日常",
  "name_prefix":"",
  "mode":"dynamic",
  "reference":"/绝对路径/图.png",
  "theme":"日常吐槽",
  "intro":"把说不出口的无语，交给一个表情。",
  "character":"按实际参考图填写形象、性格与识别点",
  "author":"",
  "rights":{"status":"pending","holder":"","source":"用户上传参考图","note":"待实际材料"},
  "platform":{"status":"pending","source":"https://sticker.weixin.qq.com/","checked_at":"2026-11-07"},
  "visual_review":{"status":"pending","notes":"尚未逐张检查"},
  "items":[
    {
      "caption":"我替你尴尬",
      "keywords":["尴尬","捂脸"],
      "scene":"熟人聊天时善意调侃",
      "action":"手从胸前抬到脸上，再从指缝偷看",
      "motion_steps":["手在胸前","手抬向额头","手遮住脸","指缝偷看"],
      "frames":[
        {"path":"/图集.png","rows":4,"cols":4,"row":0,"col":0},
        {"path":"/图集.png","rows":4,"cols":4,"row":0,"col":1},
        {"path":"/图集.png","rows":4,"cols":4,"row":0,"col":2},
        {"path":"/图集.png","rows":4,"cols":4,"row":0,"col":3}
      ],
      "durations_ms":[320,160,180,480],
      "return_to_start":true,
      "thumbnail_frame":3
    }
  ],
  "assets":{}
}
```

示例items只展示1项，实际数量须满足profile.counts。静态每项只有1帧，省略motion_steps、durations_ms；静态主图是PNG。单帧可写`"/单图.png"`，或`{"path":"/图.png","crop":[10,20,200,210]}`；crop在原图坐标中，不能与图集参数混用。覆盖asset同样支持帧对象：`assets.cover`、`icon`、`banner`、`appreciation_guide`、`appreciation_thanks`，均不含图内字幕。icon自定义建议直接用头部图；否则脚本仅裁代表图上半部，须目检是否仍清晰。

动态durations_ms逐帧提供（数量必须等于frames，50–2000毫秒）；不提供时，常用4–12个源帧自动安排约2秒动作，默认加中间帧逆序回程以循环。用return_to_start:false表示单向闭环（如跑步，须自己让末帧接起帧）；不会对像素插值或自动改变动作。duration最后落到10毫秒网格。至少4帧，至少3帧可见像素不同；严格的动作正确性由人工目检判断。

所有frames均须透明。主图按主体alpha边界裁净多余留白，再统一包含缩放到安全区；动态全动作使用相同联合边界、同缩放与同锚点，避免逐帧自动裁切引起跳动。尾/耳/手被切掉不是参数检查能可靠发现的，必须目检。

编码超过字节目标时自动递减颜色数，最低32色仍超限会报错；不能只改扩展名、帧时长、尺寸或丢动作来假通过。可简化画面/增加背景透明区后重建，或有官方依据后选新profile。输出目录必须为空，不覆盖旧版；修订时使用新目录。

整套先在临时目录完成编码和检查，通过后才转为最终输出；失败会清理暂存内容。validate同时核对必备说明、每张图片SHA256、ZIP完整性及ZIP与输出目录逐文件一致性。

默认不添加品牌前缀；仅当用户要求时使用plan的`--prefix 自定义`或JSON的`name_prefix`。前缀为中文，可空，计入专辑名字数限制。相同前缀不会重复添加。通用目录与说明文件不会被强行加品牌。
