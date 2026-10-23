"""Build local evidence and a proposed classification; never changes Netdisk."""
import collections
import html
import json
import re
import sqlite3
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
from project_paths import DATA_DIR

OUT = DATA_DIR
PHOTO_ROOTS = ['/06照片', '/来自：iPhone']
SCREEN_ROOTS = ['/tmp抖音', '/4月30号抖音tmp', '/云一朵知识问答']
CHILD = '01_儿童学习资源'
EN = CHILD + '/01_英语'
ADULT = '02_成人学习与家庭教育'
GROWTH = '03_孩子与家庭成长档案'
PHOTOS = '04_照片与家庭视频'
LIFE = '05_生活与兴趣'
MEDIA = '06_影视与音乐'
TECH = '07_技术项目与软件'
CHECK = '98_待核对'

# Match series before generic source folder names. A series has one master home.
SERIES = [
 (r'盖兆泉|英语学习规划师|启蒙问Susan|父母必看|Susan手把手', ADULT+'/01_家庭教育/英语启蒙方法', '英语启蒙方法与家长课程'),
 (r'沈[奕弈]斐|松弛父母', ADULT+'/01_家庭教育/沈奕斐', '家庭教育与亲子关系课程'),
 (r'张宇高数', ADULT+'/03_学科进修/高等数学', '高等数学课程'),
 (r'家卫老师合集/(?:1\.|2\.|10\.)', CHILD+'/03_数学与思维/家卫数学', '小学或初中数学思维课程'),
 (r'家卫老师合集', ADULT+'/01_家庭教育/家卫_学习方法与自驱力', '学习方法、自驱力与家庭教育课程'),
 (r'好大一颗羽毛球|好小一颗羽毛球|獾乐工作室', CHILD+'/06_艺术与动手/积木搭建_羽毛球', '积木搭建手册与材料清单'),
 (r'小龙老师', CHILD+'/06_艺术与动手/小龙老师_音乐与故事', '儿童音乐欣赏与故事音频'),
 (r'小家越住越大', LIFE+'/02_居家与收纳/小家越住越大', '家居收纳与空间设计图书'),
 (r'甜品|泡芙|曲奇|菜谱', LIFE+'/01_烹饪与烘焙', '烘焙课程、配方与参考图'),
 (r'刻字机|cameo5|Windows用SS_', LIFE+'/03_手工与设备/Cameo刻字机', '刻字机教程或配套软件'),
 (r'牛津词典', ADULT+'/02_英语提升/词典与工具', '英语词典学习资源'),
 (r'Number\s*blocks|Numberblocks|数字积木|Wonderblocks|奇幻积木', CHILD+'/03_数学与思维/数字积木与奇幻积木', '英文数学启蒙动画及配套资源'),
 (r'超妈|数感|思维超能力|加减法|凑十|破十|口算|数学启蒙|三角形|爱数学', CHILD+'/03_数学与思维/数感与数学启蒙', '儿童数感、数学练习或思维课程'),
 (r'必背古诗|古诗词|唐诗|诗词来了', CHILD+'/02_中文/02_国学与诗词/诗词与跟读', '中文古诗词、跟读或讲解资源'),
 (r'北大派派|小古文', CHILD+'/02_中文/02_国学与诗词/北大派派', '中文小古文与传统文化课程'),
 (r'汉字国王|洪恩识字|小羊上山', CHILD+'/02_中文/03_识字与阅读', '识字或中文分级阅读资源'),
 (r'凯叔.*历史|凯叔讲历史', CHILD+'/02_中文/04_历史故事/凯叔历史', '儿童历史故事音频'),
 (r'三国演义|狐狸家.*三国', CHILD+'/02_中文/01_绘本与故事/三国演义', '三国演义故事与讲读'),
 (r'凯叔.*西游|E007.凯叔西游', CHILD+'/02_中文/01_绘本与故事/凯叔西游记', '凯叔西游记故事音频'),
 (r'凯叔', CHILD+'/02_中文/01_绘本与故事/凯叔故事', '凯叔故事系列，保留原系列与集数'),
 (r'葫芦兄弟|宝宝巴士|火火兔儿歌', CHILD+'/02_中文/05_动画与儿歌', '中文动画、儿歌或早教配套'),
 (r'土豆逗|斑马百科', CHILD+'/04_科学与通识/中文科普', '中文科普故事或百科课程'),
 (r'粤语|C682', CHILD+'/05_粤语', '粤语儿歌、早教或动画资源'),
 (r'牛津树|Oxford Reading Tree|\bORT\b', EN+'/01_分级阅读/牛津树', '牛津树分级阅读及配套带读'),
 (r'丽声.*百科|丽声百科', EN+'/06_科普与主题阅读/丽声英语百科', '丽声英语百科分级及带读'),
 (r'Be an Expe[cr]t|BE AN EXPECT|Expert赠品|Expert课程|rainforest opposites', EN+'/06_科普与主题阅读/Be an Expert', 'Be an Expert 科普主题书及带读'),
 (r'Helper职业|Dentists Help|Teachers Help|Vets Help|Builders Help|Farmers Help|Doctors Help|Police Officers Help|Chefs Help|Mail Carriers Help|Firefighters Help', EN+'/06_科普与主题阅读/Helper职业探索', '职业主题英文阅读音频'),
 (r'Hello World', EN+'/06_科普与主题阅读/Hello World', 'Hello World 英文科普绘本及音频'),
 (r'YAKKA DEE|Yakka Dee', EN+'/03_动画与儿歌/Yakka Dee', 'Yakka Dee 英语启蒙资源；压缩包内部未展开'),
 (r'Little Monkey Read Aloud', EN+'/02_绘本与桥梁书/单册绘本_Little Monkey', 'Little Monkey 绘本朗读音频'),
 (r'Pip\s*and\s*Posy|波西[和与]皮普|pip and posy', EN+'/02_绘本与桥梁书/波西和皮普', '波西和皮普绘本、动画与带读'),
 (r'Peppa|佩[奇琪]|Muddy.Puddles|Grandpa.Pig|Daddy.Puts|Pancakes_笔记|Ballet.Lesson_笔记|The.School.Play|My.Birthday.Party_笔记|Shopping_笔记|Thunderstorm_笔记|Daddy.s.Movie.Camera', EN+'/03_动画与儿歌/小猪佩奇', '小猪佩奇多季动画、音频及笔记'),
 (r'古力|Gooloo', EN+'/03_动画与儿歌/古力', '古力动画、音频及拓展'),
 (r'Muzzy', EN+'/05_综合课程/Muzzy', 'Muzzy 英语启蒙课程及笔记'),
 (r'培生朗文迪士尼|Disney', EN+'/01_分级阅读/培生朗文迪士尼', '培生朗文迪士尼分级资源'),
 (r'培生.*(?:情境|情景).*400|培生儿童情景口语', EN+'/05_综合课程/培生情景口语400句', '培生情景口语音频与配套'),
 (r'培生亲子故事屋|培生幼儿英语|培生预备基础提高级|(?:预备|基础|拓展)级-小蝌蚪', EN+'/01_分级阅读/培生幼儿英语', '培生幼儿英语或亲子故事屋配套'),
 (r'/悠游(?:/|成长计划)', EN+'/01_分级阅读/悠游成长计划', '悠游成长计划分级资源'),
 (r'all aboard reading|企鹅12本音频', EN+'/02_绘本与桥梁书/All Aboard Reading', 'All Aboard Reading 绘本音频'),
 (r'女巫温妮|温妮女巫|Winnie.*Witch|毛毛虫 女巫', EN+'/02_绘本与桥梁书/女巫温妮', '女巫温妮不同朗读或点读版本'),
 (r'饼干狗|Biscuit', EN+'/01_分级阅读/饼干狗Biscuit', '饼干狗分级绘本及带读'),
 (r'海尼曼|Heinemann', EN+'/01_分级阅读/海尼曼', '海尼曼分级阅读音频'),
 (r'学乐小读者', EN+'/01_分级阅读/学乐小读者', '学乐小读者分级资源与点读配套'),
 (r'丽声妙想', EN+'/01_分级阅读/丽声妙想', '丽声妙想英文绘本配套'),
 (r'兰登mp3', EN+'/01_分级阅读/兰登', '兰登分级阅读音频，版本按原目录保留'),
 (r'比利[与和]小怪[兽物]|Billy.*[Mm]ini.*[Mm]onster', EN+'/02_绘本与桥梁书/比利和小怪兽', '比利和小怪兽图书音频与点读'),
 (r'威猛机器人|Mighty Robot', EN+'/02_绘本与桥梁书/威猛机器人', '威猛机器人桥梁书及音频'),
 (r'松果妈妈.*(?:自拼|自然拼)|自然拼读|basic_code_frieze|Alphablocks|字母积木', EN+'/04_拼读与词汇/自然拼读', '自然拼读课程、码表或练习资料'),
 (r'学乐高频词|Susan高频词|字母迷宫|字母童谣|魔性字母歌', EN+'/04_拼读与词汇/字母与高频词', '字母、高频词或认读练习'),
 (r'Little\s*fox|Littlefox|Bat and Friends|Dino Buddies|Tire Town School|火箭女孩|Rocket\s*girl', EN+'/05_综合课程/Little Fox', 'Little Fox 分级故事、动画及音频'),
 (r'W\s*play|Wplay|词汇妙趣国', EN+'/05_综合课程/W-play', 'W-play 词汇与主题启蒙资料'),
 (r'NeeNaw', EN+'/05_综合课程/NeeNaw', 'NeeNaw 英语启蒙课程包'),
 (r'Susan|su妈妈妈妈', EN+'/05_综合课程/Susan', 'Susan 英语课程、开口卡或配套资料'),
 (r'sss儿歌|Super Simple Songs|Super Simple|碰[碰砰]狐|Pinkfong|chuchu|Jack Hartmann|Jack Harmann|English Singsing', EN+'/03_动画与儿歌/英语儿歌与互动', '英语儿歌、律动与互动启蒙'),
 (r'小猪小象|Elephant.*Piggie', EN+'/02_绘本与桥梁书/小猪小象', '小猪小象绘本及带读'),
 (r'Maisy|小鼠波波', EN+'/02_绘本与桥梁书/小鼠波波Maisy', '小鼠波波绘本音频或点读包'),
 (r'Goose|小鹅咕茜', EN+'/02_绘本与桥梁书/Goose小鹅咕茜', 'Goose 小鹅咕茜绘本音频'),
 (r'心智麦田', EN+'/02_绘本与桥梁书/心智麦田', '心智麦田双语标题绘本音频'),
 (r'土豆超人|Supertato', EN+'/02_绘本与桥梁书/土豆超人', '土豆超人绘本、动画及音频'),
 (r'哈利波特|Harry Potter', EN+'/02_绘本与桥梁书/哈利波特', '哈利波特英文有声书'),
 (r'Fly\s*guy|Fly Guyo', EN+'/02_绘本与桥梁书/Fly Guy', 'Fly Guy 绘本音频'),
 (r'方块兔|Super Rabbit|Mega Mole Girl', EN+'/02_绘本与桥梁书/方块兔Super Rabbit Boy', '方块兔英文桥梁书PDF或音频'),
 (r'Pete the cat|皮特猫', EN+'/02_绘本与桥梁书/Pete the Cat', 'Pete the Cat 绘本与配套资源'),
 (r'super hammy|超级哈米', EN+'/01_分级阅读/Super Hammy', 'Super Hammy 分级阅读资源'),
 (r'Dog Man|狗狗神探', EN+'/02_绘本与桥梁书/Dog Man狗狗神探', 'Dog Man 系列有声书'),
 (r'外研社小书虫', EN+'/01_分级阅读/外研社小书虫', '外研社小书虫分级阅读配套'),
 (r'漫威分级', EN+'/01_分级阅读/漫威分级', '漫威主题分级阅读音频'),
 (r'小火柴|Little Match', EN+'/02_绘本与桥梁书/盖世小火柴', '盖世小火柴童话图书配套音频'),
 (r'Ask the StoryBots|问问故事机器人', CHILD+'/04_科学与通识/问问故事机器人', '英文科普动画及音频'),
 (r"Let.s Find Out|小芽大树|Booklife IB|Nature Stories|水先生", EN+'/06_科普与主题阅读/其他科普绘本', '英文科普、自然或主题阅读音频'),
 (r'Journey-to-the-West', EN+'/02_绘本与桥梁书/Journey to the West', '英文西游记分级故事资源'),
 (r'Animal Antics|ican read', EN+'/01_分级阅读/其他分级系列', '儿童英语分级阅读音频或配套'),
 (r'神奇校车|Magic School Bus', EN+'/06_科普与主题阅读/神奇校车', '神奇校车科普阅读音频'),
 (r'Messy Goes To Okido|Do You Know|小小科学探索家|从太空看地球', CHILD+'/04_科学与通识/科普动画与纪录片', '科普动画或纪录片，保留语言版本'),
 (r'布鲁伊|Bluey', EN+'/03_动画与儿歌/布鲁伊', '布鲁伊动画资源；粤语版以源路径语言为准'),
 (r'BBC.s All Time Top|英文原版书合集|开言英语|大天使听说', ADULT+'/02_英语提升', '成人英语阅读或听说课程'),
 (r'老友记|/movies(?:/|$)', MEDIA+'/01_电影与剧集', '电影或电视剧资源'),
 (r'PIANO CD COPY', MEDIA+'/02_音乐/钢琴音乐', '钢琴音乐压缩包，包内未解压'),
]
SERIES = [(re.compile(p,re.I),target,meaning) for p,target,meaning in SERIES]


def under(path, base):
    return path == base or path.startswith(base + '/')


def classify(path, isdir=False):
    parts = path.strip('/').split('/')
    for base in PHOTO_ROOTS:
        if under(path,base):
            if base == '/06照片':
                group = '/'.join(parts[:2]) if len(parts)>1 else parts[0]
                label=parts[1] if len(parts)>1 else '原照片目录'
                if label.lower().endswith(('.zip','.rar','.7z')):
                    target=PHOTOS+'/原片与归档包'
                elif label.startswith('来自：'):
                    target=PHOTOS+'/设备备份/'+label.removeprefix('来自：')+'_来源06照片'
                else:
                    target=PHOTOS+'/原有相册/'+label
            else:
                group, target = base.strip('/'), PHOTOS+'/设备备份/iPhone_来源根目录'
            return target, '照片或家庭影像组；仅统计目录与文件元数据，未逐张查看', '照片组', '高', group
    for base in SCREEN_ROOTS:
        if under(path,base):
            return LIFE+'/04_截图与收藏/'+base.strip('/'), '按来源文件夹保留的截图或图片收藏；未逐张识别', '图片收藏组', '中', base
    if under(path,'/crawl amazon'):
        return TECH+'/01_项目与开发学习/Amazon图书采集', '图书采集项目代码、数据表或配套产品图片', '技术项目', '高', '/crawl amazon'
    if under(path,'/05技术') or path=='/nfc_log.txt':
        return TECH+'/01_项目与开发学习', '开发学习、项目归档、配置或日志', '技术项目', '中', '/05技术' if parts[0]=='05技术' else path
    if under(path,'/07软件'):
        return TECH+'/02_软件与设备工具', '软件安装包、点读码制作工具或设备工具', '软件工具', '高', '/07软件'
    if parts[0]=='apps':
        return '保留原路径/apps', '应用生成目录；建议保留原路径', '应用目录', '高', '/apps'
    if parts[0] in ('08恩恩录音','录音'):
        return GROWTH+'/恩恩/朗读与声音记录', '孩子朗读、歌唱或成长录音', '成长记录', '中', '/'+parts[0]
    if re.search(r'2016届毕业典礼|2021-11-16粱燕珊',path):
        return PHOTOS+'/待确认事件名称的家庭视频', '毕业典礼或具名个人视频，事件归属需确认', '家庭视频', '中', path
    # Explicit language location wins over globally matching an English series.
    if '粤语动画片' in path or '/粤语/' in path:
        return CHILD+'/05_粤语/动画与早教', '粤语动画、电影或早教资料', '粤语学习', '高', '/'.join(parts[:3])
    for pattern,target,meaning in SERIES:
        if pattern.search(path):
            return target, meaning, target.split('/')[0], '高', ''
    if parts[0]=='来自：AI笔记':
        return CHECK+'/01_未匹配课程的AI笔记', 'AI笔记原始包；仅凭时间戳或通用文件名无法确认课程', '待核对笔记', '低', '/来自：AI笔记'
    if parts[0] in ('来自：简单听记','文档附件'):
        return CHECK+'/02_录音与附件待核对', '录音或附件导出，主题需确认', '待核对附件', '低', '/'+parts[0]
    if path.endswith('_chat.txt'):
        return ADULT+'/01_家庭教育/社群资料与聊天归档', '育儿或英语启蒙社群聊天导出；内容未全文阅读', '社群归档', '中', path
    if parts[0] in ('来自：GenClaw','百度云解压'):
        return CHECK+'/03_空目录与解压暂存', '应用或解压暂存目录，是否保留待确认', '暂存目录', '低', '/'+parts[0]
    point_audio_base='/我的资源/小蝌蚪资源/MP3格式mp4格式'
    if under(path,point_audio_base) and path!=point_audio_base:
        book=parts[3] if len(parts)>3 else parts[-1]
        if book=='小蝌蚪JOJO':
            return CHILD+'/02_中文/05_动画与儿歌/小蝌蚪JOJO', 'JOJO 儿歌或早教目录内的配套资源；保留码号', '中文启蒙', '中', point_audio_base+'/'+book
        if any(word in book for word in ['DK','百科','我们的身体','揭秘','种子的旅行']):
            return CHILD+'/04_科学与通识/点读科普绘本/'+book, '科普认知绘本的点读配套；语种待核对', '科普点读资源', '中', point_audio_base+'/'+book
        if re.search(r'[A-Za-z]{3}',book) or '英文' in book or '迪士尼美语' in book:
            return EN+'/02_绘本与桥梁书/其他点读绘本/'+book, '按英文目录名推断的绘本配套；保留原码号与层级', '英文点读资源', '中', point_audio_base+'/'+book
        return CHILD+'/02_中文/01_绘本与故事/'+book, '按中文书名推断的绘本点读配套；语种与版本仍需核对', '中文点读资源', '中', point_audio_base+'/'+book
    if '点读包' in path or any(x in path for x in ['小蝌蚪资源','学乐bnf','盖世点读包']):
        return CHILD+'/90_点读资源待配书', '点读资源；书名或设备适配尚未匹配，保留码号和原层级', '点读资源待配书', '中', '/'.join(parts[:3])
    if '/小孩资源/3岁以后/' in path or '/小孩资源/3岁/' in path:
        unit = parts[3] if len(parts)>3 else parts[-1]
        if unit in ('MP3音频','绘本音频'):
            return EN+'/02_绘本与桥梁书/待归系列单册', '儿童阅读音频，书名沿用文件名，系列待匹配', '英语阅读单册', '中', '/'.join(parts[:4])
        return EN+'/02_绘本与桥梁书/'+unit, '按所在儿童阅读资源组判断，系列名称沿用原目录', '儿童阅读资源', '中', '/'.join(parts[:4])
    if '/泛听泛读书单（新）/' in path:
        unit = parts[2] if len(parts)>2 else parts[-1]
        return EN+'/02_绘本与桥梁书/'+unit, '英语泛听泛读书单中的独立图书系列', '英语阅读资源', '中', '/'.join(parts[:3])
    if parts[0]=='英语动画整理（0-6岁）':
        unit = parts[2] if len(parts)>2 else parts[-1]
        return EN+'/03_动画与儿歌/'+unit, '按英语动画资源目录判断，保留原系列', '英语动画资源', '中', '/'.join(parts[:3])
    if path.startswith('/01恩恩学习资料/中文/'):
        return CHILD+'/02_中文/其他阅读与故事', '中文学习或故事资源', '中文学习资源', '中', '/'.join(parts[:3])
    if path.startswith('/01恩恩学习资料/数学/'):
        return CHILD+'/03_数学与思维/其他数学资源', '儿童数学学习资源', '数学学习资源', '中', '/'.join(parts[:3])
    if parts[0]=='我的资源':
        unit = parts[1] if len(parts)>1 else parts[0]
        if re.search(r'启动|startup|升级|安装视频',unit,re.I):
            return TECH+'/02_软件与设备工具/点读设备', '点读设备固件或使用教程；具体型号需确认', '设备工具', '中', '/'.join(parts[:2])
        if re.search(r'\.zip$|\.rar$|\.bin$',unit,re.I):
            return CHECK+'/04_压缩包与设备文件待核对', '压缩包或二进制设备文件，仅有标题信息，内部未检查', '待核对资源', '低', '/'.join(parts[:2])
        if len(parts)==2 and not isdir:
            return EN+'/02_绘本与桥梁书/待归系列单册', '按独立文件标题判断的儿童阅读配套，系列待匹配', '阅读配套单册', '中', path
        return EN+'/02_绘本与桥梁书/'+unit, '按所在资源库及系列标题推断，需核对语言或用途', '资源待复核', '中', '/'.join(parts[:2])
    if parts[0]=='02英语学习':
        return ADULT+'/02_英语提升/'+(parts[1] if len(parts)>1 else '其他'), '英语学习资料，按原课程包保留配套', '英语学习资源', '中', '/'.join(parts[:2])
    if parts[0]=='00熏听':
        return EN+'/07_播放与书单索引/'+(parts[1] if len(parts)>1 else '其他'), '既有熏听编排；保留播放顺序，以清单索引到主资源', '熏听编排', '中', '/'.join(parts[:2])
    if parts[0]=='01恩恩学习资料':
        if '/自制歌单/' in path:
            return EN+'/07_播放与书单索引/自制歌单', '既有熏听歌单，保留编排和顺序', '熏听编排', '中', '/01恩恩学习资料/小孩资源/自制歌单'
        if '/已整理的/英文儿歌/' in path:
            return EN+'/03_动画与儿歌/其他英文儿歌', '英文儿歌音频', '英语儿歌', '中', '/01恩恩学习资料/小孩资源/已整理的'
        return CHILD+'/98_待识别系列', '儿童学习资料；从现有标题尚不能可靠确定细类', '待识别儿童资源', '低', '/'.join(parts[:3])
    if parts[0]=='tmp':
        return CHECK+'/05_其他暂存', '暂存资源，用途需进一步确认', '暂存资源', '低', '/tmp'
    return CHECK+'/06_其他资源', '仅凭文件名和目录尚不能可靠判断用途', '待核对资源', '低', '/'+parts[0]


def file_kind(name):
    ext = PurePosixPath(name).suffix.lower()
    for extensions,label in [
        (['.mp3','.m4a','.wav','.wma','.flac','.aac','.m4b'], '音频'),
        (['.mp4','.mov','.mkv','.flv','.avi','.wmv','.m4v','.ts','.rmvb','.webm','.mpg','.mpeg','.vob'], '视频'),
        (['.pdf','.epub','.mobi','.azw3'], '电子书或阅读资料'),
        (['.axb','.nob','.via','.bnf','.bnl','.dab','.dp','.dar'], '点读包'),
        (['.fcb'], 'AI笔记包'),
        (['.jpg','.jpeg','.png','.gif','.heic','.webp','.bmp','.raf'], '配套图片'),
        (['.zip','.rar','.7z','.bz2'], '压缩包'),
        (['.iso'], '光盘镜像'),
        (['.srt','.ass','.vtt','.ttml'], '字幕'),
        (['.ppt','.pptx'], '课件或演示文稿'),
        (['.swf'], 'Flash动画或互动课件'),
        (['.exe','.msi','.apk'], '安装包'),
        (['.py','.js','.html','.htm','.css','.ipynb','.php','.bat'], '代码或网页'),
        (['.xlsx','.xls','.csv'], '表格或数据'),
        (['.txt','.doc','.docx','.md','.wps'], '文本或文档'),
        (['.m3u','.m3u8','.cue'], '播放列表或音轨索引'),
        (['.livp'], '实况照片包'),
        (['.dll','.jar','.ico','.manifest','.inf'], '软件安装组件'),
        (['.ini','.reg','.xml','.dtd','.properties','.iml','.qss'], '配置或结构化数据'),
        (['.bak','.downloading','.下载','.nobtmp'], '备份或临时文件'),
        (['.psd'], '设计源文件'),
        (['.mdx','.mdd','.dic','.aff','.locale'], '词典或语言支持数据'),
    ]:
        if ext in extensions:return label
    return '其他格式'+('（'+ext+'）' if ext else '（无扩展名）')


def unit_for(path):
    parts=path.strip('/').split('/')
    containers=[
      '/我的资源/小蝌蚪资源/MP3格式mp4格式',
      '/我的资源/泛听泛读书单（新）', '/我的资源/01家卫老师合集',
      '/02英语学习/susan教英语', '/01恩恩学习资料/小孩资源/3岁',
      '/01恩恩学习资料/小孩资源/3岁以后', '/01恩恩学习资料/小孩资源/儿童动画片',
      '/01恩恩学习资料/小孩资源', '/01恩恩学习资料/英文',
      '/01恩恩学习资料/中文', '/01恩恩学习资料/数学',
      '/01恩恩学习资料/tmp', '/01恩恩学习资料/点读包',
      '/01恩恩学习资料/粤语动画片', '/英语动画整理（0-6岁）/A级0-2岁（磨耳朵儿歌音频）',
      '/英语动画整理（0-6岁）/B级2-4岁（动画）', '/英语动画整理（0-6岁）/C级4-6岁（动画）',
      '/英语动画整理（0-6岁）/D 6-8岁', '/英语动画整理（0-6岁）/E 综合',
      '/06照片', '/我的资源','/00熏听','/02英语学习','/05技术','/07软件',
    ]
    for parent in containers:
        if under(path,parent) and path!=parent:
            take=len(parent.strip('/').split('/'))+1
            return '/'+ '/'.join(parts[:take])
    return '/'+parts[0]


def main():
    c=sqlite3.connect(OUT/'inventory.sqlite')
    c.row_factory=sqlite3.Row
    nodes=[dict(r) for r in c.execute('select * from nodes order by path')]
    states=[dict(r) for r in c.execute('select * from directories order by path')]
    if any(r['status']!='done' for r in states):
        raise SystemExit('Inventory has unfinished directories; do not publish complete report yet.')
    nonphoto=[]; groups={}; units={}; all_names=collections.Counter(); kinds=collections.Counter()
    by_destination=collections.Counter(); size_destination=collections.Counter()
    duplicate_map=collections.defaultdict(list); zero=[]; archives=[]
    directory_stats={r['path']:{'files':0,'folders':0,'bytes':0} for r in states}
    for row in nodes:
        path=row['path']; target,meaning,category,confidence,group=classify(path,bool(row['isdir']))
        if row['isdir']:
            continue
        all_names[PurePosixPath(row['name']).suffix.lower() or '(无扩展名)']+=1
        kind=file_kind(row['name']);kinds[kind]+=1
        by_destination[target]+=1;size_destination[target]+=row['size']
        ancestor=PurePosixPath(path).parent
        while True:
            key=str(ancestor)
            if key in directory_stats:
                directory_stats[key]['files']+=1;directory_stats[key]['bytes']+=row['size']
            if key=='/':break
            ancestor=ancestor.parent
        if row['md5'] and row['size']>0:duplicate_map[(row['md5'],row['size'])].append(path)
        if row['size']==0:zero.append({'path':path,'fsid':row['fsid'],'kind':kind})
        if kind=='压缩包':archives.append({'path':path,'bytes':row['size'],'meaning':meaning,'confidence':confidence})
        if category=='照片组':
            if group not in groups:groups[group]={'source':'/'+group.lstrip('/'),'suggested':target,'files':0,'bytes':0,'extensions':collections.Counter()}
            g=groups[group];g['files']+=1;g['bytes']+=row['size'];g['extensions'][PurePosixPath(path).suffix.lower()]+=1
        else:
            record={'path':path,'name':row['name'],'fsid':row['fsid'],'bytes':row['size'],'kind':kind,
                    'meaning':meaning,'evidence':'文件名、扩展名、上级目录；非逐文件正文阅读',
                    'confidence':confidence,'suggested':target,'sourceUnit':unit_for(path)}
            nonphoto.append(record)
        unit=unit_for(path)
        if unit not in units:units[unit]={'source':unit,'files':0,'bytes':0,'destinations':collections.Counter(),'examples':[],'lowConfidence':0}
        u=units[unit];u['files']+=1;u['bytes']+=row['size'];u['destinations'][target]+=1
        if category=='照片组':
            if not u['examples']:u['examples'].append('照片与家庭影像组（未逐张查看）')
        elif len(u['examples'])<4:u['examples'].append(row['name'])
        if confidence=='低':u['lowConfidence']+=1
    for row in nodes:
        if row['isdir']:
            unit=unit_for(row['path'])
            if unit not in units and directory_stats.get(unit,{}).get('files')==0:
                target=classify(row['path'],True)[0]
                units[unit]={'source':unit,'files':0,'bytes':0,'destinations':collections.Counter({target:0}),'examples':['（当前目录树无文件）'],'lowConfidence':0}
            ancestor=PurePosixPath(row['path']).parent
            while True:
                key=str(ancestor)
                if key in directory_stats:directory_stats[key]['folders']+=1
                if key=='/':break
                ancestor=ancestor.parent
    dup=[{'md5':md5,'size':size,'copies':len(paths),'extraBytes':(len(paths)-1)*size,'paths':paths}
         for (md5,size),paths in duplicate_map.items() if len(paths)>1]
    dup.sort(key=lambda x:x['extraBytes'],reverse=True)
    summary={'complete':True,'directoriesIncludingRoot':len(states),'folders':sum(r['isdir'] for r in nodes),
             'files':sum(not r['isdir'] for r in nodes),'bytes':sum(r['size'] for r in nodes if not r['isdir']),
             'photoGroups':len(groups),'photoGroupFiles':sum(g['files'] for g in groups.values()),
             'nonPhotoFiles':len(nonphoto),'zeroByteFiles':len(zero),'archives':len(archives),
             'emptyLeafDirectories':sum(r['child_files']==0 and r['child_dirs']==0 for r in states),
             'duplicateGroups':len(dup),'duplicateExtraCopies':sum(d['copies']-1 for d in dup),
             'duplicateCandidateExtraBytes':sum(d['extraBytes'] for d in dup),'extensions':all_names,
             'nonPhotoConfidence':collections.Counter(r['confidence'] for r in nonphoto),
             'roots':{r['path']:directory_stats[r['path']] for r in states if r['depth']==1}}
    def save(name,data):
        (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    def save_lines(name,rows):
        with (OUT/name).open('w',encoding='utf-8') as f:
            for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    save('summary.json',summary);save('photo-groups.json',list(groups.values()))
    save('zero-byte-files.json',zero);save('archives.json',archives)
    save_lines('file-understanding.jsonl',nonphoto);save_lines('resource-units.jsonl',list(units.values()))
    save_lines('duplicate-candidates.jsonl',dup)
    save('destination-summary.json',[{'destination':k,'files':n,'bytes':size_destination[k]} for k,n in by_destination.most_common()])
    save('directory-statistics.json',directory_stats)
    with (OUT/'完整目录树.txt').open('w',encoding='utf-8') as f:
        for row in states:
            p=row['path'];s=directory_stats[p]
            f.write(f"{p}\t{s['files']} files\t{s['folders']} folders\t{s['bytes']} bytes\n")
    with (OUT/'资源目录理解与建议.md').open('w',encoding='utf-8') as f:
        f.write('# 资源目录理解与建议\n\n每项来自完整文件清单；按标题及上下文识别，不等于已阅读全部正文。照片按组记录。建议为草案，不是迁移指令。\n\n')
        for source,u in sorted(units.items()):
            f.write(f"## {source}\n\n文件 {u['files']:,} 个，{u['bytes']/1024**3:.2f} GiB。\n\n")
            f.write('示例：'+'；'.join(u['examples'])+'。\n\n')
            for dest,count in u['destinations'].most_common():f.write(f'- `{dest}`：{count:,} 个文件。\n')
            if u['lowConfidence']:f.write(f"\n其中 {u['lowConfidence']} 项需要核对。\n")
            f.write('\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('roots','extensions')},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
