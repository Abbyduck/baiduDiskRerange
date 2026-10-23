"""Read-only descriptions of complete packages, using saved inventory evidence.

This catalog has no cloud mutation or deletion-service dependencies at runtime.
Boundary knowledge here is for introduction/review only, not a delete allowlist.
"""
import collections
import hashlib
import html
import json
import re
from pathlib import Path

from analyze_inventory import file_kind
from dedup_live_index import atomic_json
from package_boundaries import CONTAINERS,infer_roots,package_owner,protected

BASE=Path(__file__).resolve().parents[1]
from project_paths import DATA_DIR

OUT = DATA_DIR
OXFORD='/01恩恩学习资料/小孩资源/牛津树'
EN='恩恩学习资料 / 英文';ZH='恩恩学习资料 / 中文';MATH='恩恩学习资料 / 数学'
MIX='恩恩学习资料 / 综合资料待核对'
MULTI='恩恩学习资料 / 综合资料（中英共用）'

# Interpretations below name only the series/topics evidenced by file names.
# Actual media counts and representative contents are derived separately.
RULES=[
 (r'泛听泛读书单',EN,'英语泛听泛读综合书库','汇集绘本、分级读物、桥梁书及章节书的听读配套；书单在这里是资源合集。','按书名或系列名找到音频与电子书，作为长期选书、配书和泛听的资料库。','综合听读'),
 (r'张宇高数','大人学习资料 / 数学','高等数学课程与练习','含基础班、强化班和习题课等成人高数资料。','供成人按课程阶段查找讲课视频、讲义与习题材料。','成人课程'),
 (r'沈[奕弈]斐|松弛父母','大人学习资料 / 家庭教育','家庭教育与关系课程','主题包括父母角色、家庭教育及关系；合集还包含夫妻关系、社会学等分支。','家长按具体问题查找课程或讲义，作为家庭教育与个人学习资料。','家庭教育'),
 (r'启蒙问Susan|父母必看.*Susan|盖兆泉|A130',EN,'家长英语启蒙指导','面向家长讲解英语启蒙的定位、方法和执行方式，服务于孩子学英语。','供家长查阅启蒙方法，与孩子正在使用的课程和绘本配套。','家长指导'),
 (r'Susan|susan|su妈妈妈妈',EN,'Susan 儿童英语启蒙与绘本带读','包含闪卡指认、名词/动词/形容词、基础句型、绘本讲读等相关材料，具体分支见本包目录。','按课程名称或绘本名称找到讲读、原声音频及卡片；家长指导与儿童材料一起保留。','Susan'),
 (r'超妈|家卫|幼小衔接|数感|数理思维',MATH,'数学与思维课程','以数感、计算、图形、数学思维或配套练习为主题；合集可能跨多个级别与学段。','按课程名称和级别查找视频、课件与练习，把同一套的配套资料一起使用。','数学课程'),
 (r'Number.?blocks|数字积木|/Season 01-02',MATH,'数字积木数学动画及配套','包含数字积木的动画或相关音频、字幕、闪卡与练习；以实际格式统计为准。','作为数学主题故事与配套活动资源，按季或集数找材料。','数字积木'),
 (r'牛津树|Oxford Reading Tree|/L10-12',EN,'牛津树分级阅读配套','围绕牛津树不同级别、版本的书本、音频、讲读视频或练习资料；不同份数保留来源。','按书名、级别和版本查找绘本及对应听读资料。','牛津树'),
 (r'Little.?fox|LittleFox|Little Fox|Bat and Friends|Dino Buddies|Tire Town School|Rocket.?girl|火箭女孩|西游记 Journey',EN,'Little Fox 英文故事与动画','包含英文分级故事、动画或配套原文、音频；火箭女孩与英文西游记也按系列保留。','按故事系列及集数找到听、看、读的对应材料。','Little Fox'),
 (r'松果妈妈.*(?:自拼|自然拼)|自然拼读|字母迷宫|basic_code_frieze',EN,'自然拼读课程与练习','围绕字母音、拼读及解码练习，可能含课程、码表、解码书和活动素材。','按原课程进度找到讲课和配套材料，版本先分别记录。','自然拼读'),
 (r'奇幻积木|Wonderblocks',EN,'奇幻积木英文动画','当前文件标题出现 Go、Stop、Hop、Moo 等英文主题；本包为动画视频。','按主题或集数查找英文动画，作为儿童英文动画资源。','动画'),
 (r'布鲁伊|Bluey',EN,'布鲁伊动画与配套','围绕同一动画系列，可能收录不同语言、字幕及打印素材；具体语言分支见目录。','按季、集数和语言版本查找动画、音频或配套台词。','布鲁伊'),
 (r'Peppa|peppa|小猪佩奇|peppa爱听|佩奇英文',EN,'小猪佩奇动画、听力与配套','围绕小猪佩奇的分季音频、视频或讲读、图卡资料；不同版本分别保留。','按季、集数和故事名查找熟悉的听读内容，与熏听入口对应。','小猪佩奇'),
 (r'古力|Gooloo|W.?play|Wplay|Big Muzzy|/悦悦',EN,'古力、Muzzy 或主题词汇英语资源','含动画、词汇音频、图卡或家长拓展；悦悦目录下实际是 Big Muzzy 与古力两组英语资料。','按课程名或词汇主题找视频与音频，相关图卡和拓展继续随包保留。','古力与Muzzy'),
 (r'培生儿童情[景境]口语|情景400|60张闪卡',EN,'日常情境口语与闪卡配套','围绕生活句型、情境故事或闪卡；文件可能是音频，也可能是点读包。','配合对应故事书、闪卡或设备，按情境和书名查找朗读材料。','培生与口语'),
 (r'培生|Biscuit|饼干狗|悠游|all aboard|企鹅12本|super hammy|Super Hammy|Animal Antics|ican read|I Can Read|牛蛙|学乐小读者|外研社小书虫|漫威分级',EN,'英语分级阅读与配书资源','按原系列及级别组织的故事阅读、音频或点读配套；音轨数与册数并不相同。','按书名与级别找到对应阅读或朗读文件，作为系列阅读资料。','分级阅读'),
 (r'高频词|字母歌|字母童谣',EN,'字母与高频词资源','围绕字母、常见词或对应例句的音频、讲读或练习。','按字母或原系列顺序查找词汇配套。','字母与词汇'),
 (r'丽声.*百科|Be an Expe[cr]t|BE AN EXPECT|Expert课程|Expert带读|Helper职业|Hello World|Nature Stories|Booklife|小芽大树|水先生|The Big Book|Find Out|神奇校车',EN,'英文科普与主题阅读','书名或主题涉及自然、动物、职业、身体、植物等；有些包同时含电子书、音频和带读。','按主题或具体书名配书、找音频，作为英语中的科普阅读资源。','英文科普'),
 (r'Ask the StoryBots|问问故事机器人|Do You Know|Okido|小小科学探索家 英语版',EN,'英文科普动画与配套','以知识问答、日常原理或自然科学为主题的英文动画，部分含台词和其他语言版本。','按知识主题或集数查找英文视频及配套音频。','科普动画'),
 (r'小小科学探索家 国语版|土豆逗|白泽|斑马百科',ZH,'中文科普与认知故事','目录和标题涉及自然、身体、地理、安全、制造等科普主题；按本包的子系列区分。','按孩子感兴趣的问题或系列名称查找中文讲解、故事或动画。','中文科普'),
 (r'三国|怀沙|古诗|汉字国王|北大派派|凯叔|葫芦兄弟',ZH,'中文故事、识字或国学资料','包括相应系列的中文讲读、故事、古诗或识字内容；大型合集按原子系列保存。','按故事名、篇目或课程名查找中文阅读与听故事资料。','中文阅读'),
 (r'小龙老师',ZH,'中文故事与音乐欣赏','文件名包含听故事音频，以及柴科夫斯基、肖邦、舒伯特、贝多芬等乐曲名。','作为故事与音乐欣赏的主题资料，按曲目或故事名查找。','故事与音乐'),
 (r'Pip.?and.?Posy|pip and posy|波西[和与]皮普|Q-Play',EN,'波西和皮普绘本、动画与带读','同系列可能分别是绘本朗读、动画音频或 Q-Play 带读及拓展，不按同名直接合并。','按具体书名或动画集名，找到当前需要的版本。','波西和皮普'),
 (r'Maisy|maisy|小鼠波波',EN,'Maisy 小鼠波波绘本配套','包含初体验、故事书等系列的朗读音频或电子书，不同套数与朗读版本分别记录。','按书名和系列查找生活主题绘本及配套音频。','Maisy'),
 (r'Fly.?Guy|女巫|Winnie|比利|Billy|mini monsters|MINI MONSTERS|威猛机器人|Mighty Robot|Dog Man|方块兔|Super Rabbit|Goose|小鹅|Scaredy|scaredy|Pete.?the.?[Cc]at|Bear and bird|salina yoon|Flubby|flubby|Supertato|土豆超人|Knuffle Bunny|古纳什|BrownBear|大卫不可以|猜猜我是谁|苏斯博士|DearZoo|Bunny|我妈妈|我爸爸|蜡笔怪|纽约公共图书馆|Katie|凯蒂|大威廉姆斯|心智麦田|小火柴|Little Match|Nate The Great|桥梁书|睡衣小英雄|NeeNaw|哈利波特|Harry Potter|特洛伊|奥德赛|小猪小象|HOW to音频|Little Monkey',EN,'英文绘本、故事与听读配套','围绕标题所示书系的电子书、朗读、讲读或动画；故事名、册次和版本是主要查找线索。','按书名找到相应听读材料；合集中的不同系列保留原目录。','英文故事'),
 (r'sss儿歌|Super Simple|Good morning|Good night|自制歌单|/已整理的',EN,'英语儿歌与日常歌单','以儿歌、日常主题歌曲或已有播放编排为主；具体内容按歌名查找。','作为日常英语歌曲与熏听的素材来源，保留已有编排。','儿歌与歌单'),
]

CUSTOM={
 '/00熏听/绘本':('绘本熏听 · Pumpkin Soup','当前这一组只有 1 个音频，文件名为 71.pumpkin soup.mp3，保留在“牛六”分支中；仅凭分支简称不能确认书系。','从现有熏听入口找到这段英文绘本音频，保留已有分组。'),
 '/我的资源/HOW to音频':('HOW to 绘本音视频资料','当前有 10 个音频和 10 个视频；音频书名涉及捉龙、种恐龙、当超级英雄、寻找埃及宝藏等，视频文件名是编号。','按英文书名找故事音频，视频先随整包保留，尚未逐本核对音视频对应关系。'),
 '/我的资源/培生封面图':('培生系列封面配图','实际是 192 张配图，分为亲子故事屋基础/进阶、情景400句上下、词汇妙趣屋三辑。','按系列查找封面图，作为找书、辨认材料或配合已有播放入口的辅助素材。'),
 '/我的资源/【全集】Susan五件套 视频+音频':('Susan 五件套 · 词卡与语境音视频','包含名词卡、动词卡、形容词卡、万物贴、启蒙贴贴卡，区分引导输出和常速语境，另有父母指导。','按手头的词卡或生活主题找到对应示范与音频，家长指导继续随包保留。'),
 '/我的资源/【全集】Susan高频词 视频+音频':('Susan 高频词 · 视频、原声与跟读','一级目录区分视频、原声音频、跟读音频，并有高频词 PDF 和父母指导；包内还有同名分支。','按高频词找到讲解、原声或跟读版本；先保留内部编排与来源。'),
 '/我的资源/su妈妈妈妈':('Susan 综合配套（现名：su妈妈妈妈）','包含 PDF 卡片、名词与动词的音视频、开口神器、打卡表、名词动词点读包和形容词资料。','按课程或词卡名称查找打印、听读与家长执行配套，作为综合资料库记录。'),
 '/02英语学习/susan教英语':('Susan 教英语 · 儿童启蒙综合包','含闪卡指认与动词动作演示、Start With Susan A/B 基础句型、第一桶金常速语境、绘本带读、牛津树1—3阶配套和父母指导。','家长按当前用的词卡、句型或绘本查找对应视频、音频、思维导图与打印材料。'),
 '/英语动画整理（0-6岁）':('英语动画整理 · 多系列启蒙资源库','按原目录分为儿歌音频、不同年龄标签的动画、人物传记和综合资料；内部可见 Cocomelon、Peppa、Muzzy、Alphablocks 等，并带字幕、绘本和词卡。','按动画或课程名称检索不同系列；原目录年龄标签仅作为来源信息，不能替代内容与使用阶段判断。'),
 '/我的资源/小蝌蚪资源':('小蝌蚪资源 · 点读与配书综合库','包含多个品牌与格式的点读包，以及电子书、音频、视频、配图和工具；书目跨英文阅读、中文故事与认知，牛津树只是其中一支。','按纸质书名或系列找点读及听读配套，保留原码号与目录；暂按中英共用的综合资料库介绍。'),
 '/01恩恩学习资料/点读包/海豚点读包':('海豚点读包 · 中英绘本与认知配书库','书目包括幼儿认知、行为习惯、小鸡球球、中文故事与国学，也包括培生词汇、口语、分级和自然拼读。实际以点读包为主。','配合对应纸质书查找点读资料，按中英共用资料库保留，不把某个英语系列当作全包主题。'),
 '/01恩恩学习资料/点读包/05-外X社点读包':('外研社点读配套综合库（现名：05-外X社点读包）','一级目录包括丽声、悠游、多维阅读、大猫、亲子口语、教材、词典与双语读物；当前全部为点读包。','按书名、系列或原码号寻找点读配套；含教材、双语和阅读资料，先作为配书综合库保留。'),
 '/01恩恩学习资料/点读包/复制到小蝌蚪':('小蝌蚪待用配套组（现名：复制到小蝌蚪）','主要分支为培生词汇妙趣屋、基础词汇、情景口语及 tmp分享的点读包；后者另含中文绘本、BookLife 练习和教学视频、升级文件。','按手头的书和课程找点读与练习配套；用途跨英语和其他配书资料，先保持这一份的完整来源。'),
 '/01恩恩学习资料/点读包/学乐bnf点读包':('学乐 BNF 点读资源组','文件名可见 Buddy Readers、Clifford、Crabby、Decodable、David Shannon 等系列；这一份有 82 个点读包。','按系列与对应书名找到点读文件，保留原码号和文件名。'),
 '/01恩恩学习资料/点读包/学乐点读包':('学乐点读资源组','文件名可见 Buddy Readers、Clifford、Crabby、Decodable、David Shannon 等系列；这一份有 83 个点读包。','按系列与对应书名找到点读文件；与 82 个文件的另一份先分别记录，不按数量直接判断重复。'),
 '/01恩恩学习资料/小孩资源/牛津树/pdf':('牛津树 PDF 绘本组（现名：pdf）','这组是级别 3—6 的 PDF 绘本文件；原文件夹名没有说明书系，所在牛津树分类及书名提供了依据。','按级别和书名查找电子绘本，音频需要在同主题的其他资料组中定位。'),
 '/01恩恩学习资料/小孩资源/牛津树/音效':('牛津树朗读音频组（现名：音效）','目录包括 Stage 1—2、3—5、6—9、10+—12+ 与原版 MP3；实际包含大量书本朗读音频。','按书名和级别寻找原版朗读或较高级别的广播剧材料。'),
 '/01恩恩学习资料/英文/pdf和音频':('Hello World 绘本与音频（现名：pdf和音频）','第一层是 NY076.Hello World30本，实际有 30 个阅读文件与 10 个音频文件；尚未逐册核对配对关系。','按科普主题书名查找绘本及已有音频。'),
 '/01恩恩学习资料/英文/动画片':('Little Fox 三套故事合集（现名：动画片）','第一层包含 Bat and Friends、Dino Buddies、Tire Town School，内部还出现 The Big Green Forest；含故事书、音频、视频和词汇资料。','按故事系列和集数查找完整的听看读配套。'),
 '/01恩恩学习资料/中文/4岁':('怀沙说《三国演义》资料组（现名：4岁）','第一层实际是怀沙说《三国演义》，包含第一季与第二季的有声内容。文件夹年龄名不是内容简介。','按季和章节查找中文名著讲读，暂不依据原文件夹年龄名判断使用阶段。'),
 '/01恩恩学习资料/小孩资源/3岁以后/MP3音频':('比利和小怪兽音频组（现名：MP3音频）','音频文件名出现 ALL ABOUT THE MINI MONSTERS、Monsters in the Dark、Monsters at Christmas 等。','按书名查找比利和小怪兽系列的整本音频。'),
 '/01恩恩学习资料/小孩资源/3岁以后/栗子妈妈思维导图第一期音频':('科普主题思维导图配套音频','文件名包含 Planting a Lemon、Germs、Journey of Food and Water、Mr Tree’s Life 等自然与身体主题。','配合对应的科普绘本或思维导图，按主题书名找音频。'),
 '/我的资源/2089':('粤语早教合集（现名：2089）','内部为 C682粤语早教，包含粤语儿歌、故事、认知主题视频及音频。','按儿歌、故事或认知主题查找粤语资料。'),
 '/我的资源/培生儿童情景口语400句':('培生儿童情景口语400句 · 点读包','当前这份实际是上、下两部分的点读包，并非独立 MP3 音频目录。','配合相应纸质材料和点读设备使用，按上下册查找。'),
 '/我的资源/ROB&OT 资料包':('ROB&OT 绘本配套资料包','含点读包、翻译、阅读指导，另有名为“西班牙语小书”的分支。标题不足以确定全包语种。','先作为绘本配套整体保留，待确认原版语种与对应书目后再定大类。'),
 '/我的资源/牛津词典10':('牛津高阶词典第10版 · 词典数据','实际是 MDX、MDD 词典数据及 CSS 样式文件，不是牛津树分级阅读。','供兼容的词典阅读器查询英文词汇与释义。'),
 '/我的资源/01家卫老师合集':('家卫老师数学与学习方法合集','一级目录包含小学数学思维、初中数学思维、高效学习、数理思维等；材料跨多个学段，并有大量课件与练习。','作为按课程与学段查找的数学教学资料库，先记录为长期储备与家长参考。'),
 '/我的资源/泛听泛读书单（新）':('泛听泛读书单（新） · 英语有声阅读综合库','一级目录按书系分组，包括饼干狗、Maisy、波西和皮普、牛津树、驯龙大师、神奇树屋、纽伯瑞作品等。以音频为主，同时有电子书和少量其他配套。','它的作用是提供“按书名选材料”的英语听读资源库；保留整包，通过索引找到想听或想读的系列。'),
 '/我的资源/松果妈妈自然拼读课':('松果妈妈自然拼读课 · 原名称版本','包含松果自拼课、动物城与解码书、Anima Phonics 教材练习及活动素材；视频和学习配套并存。','按课程名称与阶段找到课程、解码阅读和活动配套，和另一份先分别记录。'),
 '/我的资源/松果妈妈自然拼读课(1)':('松果妈妈自然拼读课 · 带(1)版本','第一层内容与另一份相近，但当前文件统计多出 126 个，其中视频多 122 个、阅读文件多 4 个；这不是已核实的完整包含结论。','保留版本差异说明，后续再单独核验同课次与配套完整性。'),
}


def semantic(x):
    path=x['path'];title=x['title'];context=path+' '+' '.join(y['name'] for y in x['top'])
    result={'category':MIX,'kind':'资料主题待核对','summary':'现有名称不足以可靠说明主题，下面保留能确认的文件形式与目录线索。',
        'purpose':'补充课程名、书名或使用对象后，再确定用途与大类。','family':'待核对','confidence':'待核对'}
    # Prefer the package's own title/path to incidental titles inside it.
    for source in (path,context):
        match=next((rule for rule in RULES if re.search(rule[0],source,re.I)),None)
        if match:
            _,category,kind,summary,purpose,family=match
            result.update(category=category,kind=kind,summary=summary,purpose=purpose,family=family,confidence='目录证据较明确');break
    # Explicit content/location exceptions precede generic English names.
    if '粤语' in path or title=='2089':
        result.update(category=ZH,kind='粤语动画、儿歌与认知资源',summary='按目录标注为粤语的动画、儿歌、故事或认知资料；系列与语言版本一起保留。',purpose='按故事、动画名或主题查找粤语输入材料。',family='粤语',confidence='目录证据较明确')
    if path.startswith('/02英语学习/') and not re.search('susan',path,re.I):
        descriptions={
          "BBC's All Time Top 100 Best Novels":('英文小说 PDF 资料库','包含 1984、A Christmas Carol、Animal Farm 等英文小说文件；“Top 100”是原目录标题。','成人按作品名查找英文原版阅读材料。'),
          '大天使听说三合一（更新至106节）':('英语发音、拼读与口语课程','一级目录为全音标、自然拼读、口语进阶，以及讲义、手帐、词频表等。','成人按发音或口语学习主题找课程及讲义。'),
          '开言英语298元效率礼包':('开言英语发音与文化课程','含地道美语发音课、欧美文化双语公开课及音频和文稿。','成人查找英语发音与文化话题的课程、音频及讲义。'),
          '英文原版书':('英文原版书压缩包与双语剧本','有英文原版书合集压缩包，以及神探夏洛克中英文剧本。压缩包内部未展开。','成人按作品或剧集查找原文、译文与阅读材料。'),
        }
        k,s,u=descriptions.get(title,('成人英语资料','按现有成人英语目录及文件名记录。','供成人查找课程或阅读材料。'))
        result.update(category='大人学习资料 / 英文',kind=k,summary=s,purpose=u,family='成人英文',confidence='目录证据较明确')
    if path in ('/08恩恩录音','/录音'):
        result.update(category='孩子与家庭成长档案',kind='孩子与家庭声音记录',summary='实际文件名包含恩恩的 quiz、Super Hammy 跟读或月光光等录音标题。',purpose='保留原录音与名称，作为声音与学习过程的成长记录。',family='成长记录',confidence='目录证据较明确')
    if protected(path) and not path.startswith('/00熏听/'):
        result.update(category='照片与家庭影像',kind='按原相册或设备保存的照片组',summary='仅按来源文件夹统计照片、实况照片包与视频；没有逐张识别照片内容。',purpose='作为家庭相册或设备备份保留，后续按事件与来源整理。',family='照片组',confidence='已按文件夹整体记录')
    if path.startswith('/05技术/') or path=='/crawl amazon':
        detail={'php':'PHP 学习或开发资料','playwright':'Playwright 自动化与网页资料','python learning':'Python 学习课程或项目','stash':'Stash 配置与使用资料','crawl amazon':'Amazon 图书采集项目、图片与数据表'}
        result.update(category='大人学习资料 / 技术',kind=detail.get(title,'技术资料'),summary='内容包含 '+('项目代码、图书数据表及抓取的书目图片。' if title=='crawl amazon' else '对应技术主题的课程、项目或配置，具体见目录线索。'),purpose='按技术名称或项目名称检索学习资料、代码与配套文件。',family='技术',confidence='目录证据较明确')
    if title in ('oid铺码软件','dab转mp3拆分','刻字机视频-cameo5'):
        result.update(category='工具与设备资料',kind='点读制作/转换工具' if title!='刻字机视频-cameo5' else '刻字机操作教程',summary='实际是工具、安装/压缩包、说明或设备演示材料，不能只依据父目录把它当成儿童课程。',purpose='按工具和设备名查找操作教程或制作配套。',family='设备工具',confidence='目录证据较明确')
    if title in ('甜品','菜谱'):
        result.update(category='生活与兴趣',kind='烘焙课程与食谱',summary='目录和文件名涉及蛋糕、曲奇、泡芙、面包等，含课程视频、食谱或制作图片。',purpose='按甜品名称查找做法、配方与课后资料。',family='烘焙',confidence='目录证据较明确')
    if path=='/movies':
        result.update(category='影视与兴趣',kind='影视剧及英文学习附属资料',summary='包含小谢尔顿、权游、求婚大作战、请回答1988、金秘书等；权游下另有小说、词汇和看剧笔记。',purpose='以剧名查找影视和配套笔记，整体用途跨娱乐与英文学习。',family='影视',confidence='目录证据较明确')
    if title=='老友记':result.update(category='大人学习资料 / 英文',kind='老友记分季视频',summary='当前是按季组织的剧集视频文件，未看到独立讲义或字幕文件。',purpose='按季和集数查找英文剧集；是否用于学习由你决定。',family='成人英文',confidence='目录证据较明确')
    if path in ('/4月30号抖音tmp','/tmp抖音','/云一朵知识问答','/文档附件'):
        result.update(category='待核对 / 截图与附件',kind='截图、收藏或导出附件',summary='目前可确认主要是图片。名称或通用截图文件名不足以判断具体知识主题，未逐张查看。',purpose='先按来源整体保留，待确认主题后再归学习或生活类。',family='待核对',confidence='待核对')
    if path=='/来自：AI笔记':
        result.update(category='恩恩学习资料 / 综合配套笔记',kind='英文与数学课程配套笔记',summary='文件名可对应 Muzzy、SSS 儿歌、佩奇、牛津树和数字积木；以 FCB 笔记包为主，另有 PDF。',purpose='按原课程或集名找笔记；先建立关联索引，保留导出包原结构。',family='笔记',confidence='文件名可关联，包内正文未读取')
    if path=='/来自：简单听记':result.update(category='待核对 / 录音附件',kind='来源明确、主题未明的录音',summary='当前只有按日期命名的录音，不能由时间戳判断是课程、会议还是家庭录音。',purpose='确认录音主题后，再关联到课程或家庭档案。',family='待核对',confidence='待核对')
    if path=='/百度云解压':result.update(category=EN,kind='佩奇音频解压残留资料组',summary='目录名标注“佩奇英文版264个”，实际当前只有 1 个 MP3 和 2 张封面图；不能按目录标题视为完整264集。',purpose='先记录为来源待核对的佩奇片段，暂不作为完整课程主包。',family='小猪佩奇',confidence='文件内容数量可确认，完整性待核对')
    if title=='ROB&OT 资料包':result.update(category=MIX,kind='绘本点读、翻译与阅读指导',family='待核对',confidence='语种待核对')
    if title=='牛津词典10':result.update(category='大人学习资料 / 英文',kind='英文词典数据',family='成人英文',confidence='目录证据较明确')
    if title in ('小蝌蚪资源','海豚点读包','05-外X社点读包','复制到小蝌蚪'):
        result.update(category=MULTI,kind='跨书系点读与配书资源库',family='点读综合库',confidence='目录证据较明确')
    if title in ('学乐bnf点读包','学乐点读包'):
        result.update(category=EN,kind='学乐英语阅读点读配套',family='分级阅读',confidence='目录证据较明确')
    if title=='盖世点读包':
        result.update(category=MIX,kind='童话与汽车书点读配套',summary='当前是两个 BIN 文件，分别属于“小火柴经典童话寓言故事”和“呜呜汽车书10本”；未解析文件内部，不能仅凭名称确认全包语种。',purpose='按对应书系查找设备配套，保留原文件名；具体适用设备需结合原说明确认。',family='点读配套',confidence='主题可关联，语种与内部内容待核对')
    if path=='/英语动画整理（0-6岁）':result.update(category=EN,kind='多系列英语动画与启蒙综合库',family='动画综合库',confidence='目录证据较明确')
    if path=='/00熏听/绘本':result.update(category=EN,kind='英文绘本音频',family='英文故事',confidence='标题可确认，原分组简称待核对')
    if title=='栗子妈妈思维导图第一期音频':result.update(category=EN,kind='英文科普思维导图配套音频',family='英文科普',confidence='文件名证据较明确')
    if title=='培生封面图':result.update(kind='书系封面与播放辅助配图')
    if path=='/01恩恩学习资料/小孩资源/自制歌单':
        result.update(category=MULTI,kind='中英文与粤语混合儿歌歌单',summary='歌名包括 Baby Shark、Twinkle Twinkle Little Star、两只老虎、月光光、氹氹转等；这是跨语种的个人播放组合。',purpose='保留现有播放编排，按歌名找常用歌曲，作为中英与粤语共用的歌单资料。',family='儿歌与歌单',confidence='文件名证据较明确')
    if title=='从太空看地球2019':result.update(category=MIX,kind='地球与太空主题纪录片',summary='四个视频标题为太空新视角、地球上的奇特形状、色彩缤纷的地球、变化中的地球。语种未由文件名确认。',purpose='作为地球与太空认知影片保存，确认语种与使用对象后再定大类。',family='科普纪录片',confidence='主题可确认，语种待核对')
    if title=='好大一颗羽毛球｜獾乐工作室':result.update(category='生活与兴趣',kind='积木羽毛球模型搭建资料',summary='实际包含乐高版、国产积木版搭建手册和材料清单。',purpose='按模型版本查找积木搭建步骤与备件清单。',family='搭建与手工',confidence='目录证据较明确')
    if result['family']=='Susan' and re.search(r'精讲|\d+视频|DearZoo|我妈妈&我爸爸',title):
        result.update(kind='Susan 绘本讲读与原声配套',summary=f'围绕标题中的绘本或阅读级别提供讲读配套；这一份实际有 {x["kinds"].get("视频",0)} 个视频、{x["kinds"].get("音频",0)} 个音频，另有父母必读等文档。',purpose='按正在读的绘本名称找讲读视频和原声音频，随书使用，保留这一份的配套关系。')
    if result['family']=='英文故事' and x['kinds'].get('音频',0)==x['files']:
        result.update(summary='这一份全部为标题所示书系的英文故事音频，文件按书名、册次或音轨排列；单个音轨不等同于一本书。',purpose='按书名、册次或音轨名称找到已有故事的听读音频，作为配书与泛听资料。')
    if path in CUSTOM:
        result['displayTitle'],result['summary'],result['purpose']=CUSTOM[path]
    else:result['displayTitle']=title
    return result


def load_saved():
    scan=json.loads((OUT/'实时索引/latest-scan.json').read_text(encoding='utf-8'))
    if scan.get('status')!='complete' or not re.fullmatch(r'scan-[a-f0-9]+',scan['directory']):raise ValueError('Incomplete saved scan.')
    state=json.loads((OUT/'实时索引/known-deletions.json').read_text(encoding='utf-8'))
    removed=set(state.get('removed',[]));pending=set(state.get('pending',[]));excluded=removed|pending
    records=[]
    for i in range(1,scan['pages']+1):records.extend(json.loads((OUT/'实时索引'/scan['directory']/f'page-{i:05d}.json').read_text(encoding='utf-8'))['items'])
    assert len(records)==scan['entries']
    records=[x for x in records if not any('/'.join(x['path'].split('/')[:n]) in excluded for n in range(2,len(x['path'].split('/'))+1))]
    return scan,state,records


def main():
    scan,state,records=load_saved()
    containers={**CONTAINERS,OXFORD:'用户确认：自己建立的牛津树分类'}
    roots=infer_roots([x['path'] for x in records if x['isdir']],containers)
    profiles={p:{'id':'B'+hashlib.sha256(p.encode()).hexdigest()[:10],'path':p,'title':p.rsplit('/',1)[-1],
        'files':0,'folders':0,'bytes':0,'kinds':collections.Counter(),'examples':collections.defaultdict(list),
        'top':[],'directoryNames':set(),'boundary':b,'entry':'熏听常用入口' if p.startswith('/00熏听/') else '资料库',
        'boundaryNote':'用户确认整包，保持内部结构' if p=='/我的资源/泛听泛读书单（新）' else
          '位于用户自建的牛津树分类下；介绍这份资料，不据此确认原始转存边界' if p.startswith(OXFORD+'/') else
          '使用中的熏听资料组，保留原位置与编排' if p.startswith('/00熏听/') else
          '依据分类容器推断的整包或资料组；原转存边界尚未逐份确认'} for p,b in roots.items()}
    loose=collections.defaultdict(list);by_path={x['path']:x for x in records}
    for r in records:
        owner=package_owner(r['path'],roots)
        if not owner:
            if not r['isdir']:loose[r['path'].rsplit('/',1)[0] or '/'].append(r)
            continue
        x=profiles[owner];rel=r['path'][len(owner)+1:]
        if r['isdir']:
            if r['path']!=owner:x['folders']+=1;x['directoryNames'].add(r['name'])
        else:
            x['files']+=1;x['bytes']+=r['size'];kind=file_kind(r['name']);x['kinds'][kind]+=1
            if (not protected(owner) or owner.startswith('/00熏听/')) and len(x['examples'][kind])<4:x['examples'][kind].append(rel)
        if r['path'].rsplit('/',1)[0]==owner:
            x['top'].append({'name':r['name'],'isdir':r['isdir']})
    packages=[];empty=[]
    for p,x in profiles.items():
        x['top'].sort(key=lambda y:(-y['isdir'],y['name']))
        x['kinds']=dict(x['kinds']);x['examples']=dict(x['examples']);x['directoryNames']=sorted(x['directoryNames'])
        if not x['files']:empty.append({'path':p,'note':'当前索引没有文件，不视为有内容的资源包'});continue
        x.update(semantic(x));x['components']=[t['name'] for t in x['top'] if t['isdir']]
        x['contentLine']='、'.join(f'{n:,} 个{k}' for k,n in sorted(x['kinds'].items(),key=lambda a:-a[1])[:6])
        if not x['components']:x['contentDetail']='文件以单册、单集或曲目名称直接排列。'
        else:x['contentDetail']='第一层可见：'+'；'.join(x['components'][:7])+('等。' if len(x['components'])>7 else '。')
        x['notes']=[]
        if x['path'].startswith('/02英语学习/') and x['family']=='Susan':x['notes'].append('虽然目前在“02英语学习”，内容主要服务儿童英语启蒙，建议大类按恩恩英文记录。')
        if x['family']=='Susan' or '超妈' in p:x['notes'].append('和其他同主题包在索引中放在一起比较；保留每份来源与版本，本轮不合并文件。')
        if x['path'].startswith(OXFORD+'/'):x['family']='牛津树';x['category']=EN
        if x['kinds'].get('压缩包'):x['notes'].append('压缩包按外部名称记录，内部未展开，未据此确认内容齐全。')
        if protected(p) and not p.startswith('/00熏听/'):
            x['top']=[t for t in x['top'] if t['isdir']];x['examples']={};x['directoryNames']=[]
        x['titleClues']=list(dict.fromkeys(Path(t['name']).stem for t in x['top'] if not t['isdir'] and not re.search(r'公众号|微信|扫码|淘宝|\.DS_Store|desktop.ini|Thumbs.db',t['name'],re.I) and len(t['name'])<160))[:5] if not (protected(p) and not p.startswith('/00熏听/')) else []
        packages.append(x)
    # Loose files are accounted for separately; photos are grouped by source.
    loose_groups=[]
    for parent,rows in loose.items():
        kinds=collections.Counter(file_kind(x['name']) for x in rows)
        photo=protected(parent) and not parent.startswith('/00熏听')
        loose_groups.append({'parent':parent,'files':len(rows),'bytes':sum(x['size'] for x in rows),'kinds':dict(kinds),
            'photoGroup':photo,'note':'按照片来源整体记录，不列单张照片' if photo else '分类容器中的散文件，不冒充完整转存包',
            'items':[] if photo else [{'name':x['name'],'path':x['path'],'kind':file_kind(x['name'])} for x in rows]})
    assert sum(x['files'] for x in packages)+sum(x['files'] for x in loose_groups)==sum(not x['isdir'] for x in records)
    prefs={'childAge':3,'retrieval':'主要按资源名称查找','frequentEntry':'/00熏听','defaultSaveInbox':'/我的资源',
        'workflow':['确认整包边界','了解内容并判断大类','整包归位','之后再细分英文'],
        'confirmedContainers':{OXFORD:'用户自建分类'},'confirmedWholePackages':['/我的资源/泛听泛读书单（新）'],
        'topicGrouping':['超妈数学：多份与版本先聚合介绍，保留来源','Susan：不同课程与资料包先聚合介绍，按儿童启蒙内容判断'],
        'adultEnglish':'用户说明：成人英文学习在英文资料；不把所有英文内容归成人',
        'cloudMutationsAuthorizedByThisCatalog':False}
    atomic_json(OUT/'用户整理偏好与边界说明.json',prefs)
    focuses=focus_sections(packages,records,by_path)
    data={'meta':{'packageCount':len(packages),'emptyGroups':len(empty),'indexedFiles':sum(not x['isdir'] for x in records),
        'sourceScan':scan,'knownRemovedCount':len(state.get('removed',[])),'pendingExcludedCount':len(state.get('pending',[])),
        'evidence':'依据完整目录与文件清单、名称、格式及配套结构；没有逐个阅读正文或播放媒体，照片仅按组记录。',
        'phase':'了解整包内容与大类；未执行移动、重命名或新增删除'},
        'preferences':prefs,'focus':focuses,'packages':packages,'looseGroups':loose_groups,'empty':empty,
        'categories':dict(collections.Counter(x['category'] for x in packages)),
        'families':dict(collections.Counter(x['family'] for x in packages))}
    atomic_json(OUT/'整包内容介绍.json',data)
    write_markdown(data)
    template=(BASE/'baidu-mcp/package-catalog-template.html').read_text(encoding='utf-8')
    (OUT/'整包内容介绍.html').write_text(template.replace('__DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c')),encoding='utf-8')
    print(json.dumps({'packages':len(packages),'categories':data['categories'],'uncertain':[x['displayTitle'] for x in packages if x['confidence']=='待核对'],
        'coveredFiles':data['meta']['indexedFiles'],'looseFiles':sum(x['files'] for x in loose_groups)},ensure_ascii=False))


def focus_sections(packages,records,by_path):
    ps={x['path']:x for x in packages}
    ox=[x for x in packages if x['path'].startswith(OXFORD+'/')]
    math=[x for x in packages if '超妈' in x['path']]
    su=[x for x in packages if x['family']=='Susan' or 'Susan' in x['path'] or 'susan' in x['path']]
    library=ps.get('/我的资源/泛听泛读书单（新）')
    def refs(xs):return [{'id':x['id'],'title':x['displayTitle'],'path':x['path'],'files':x['files'],'kinds':x['kinds']} for x in xs]
    math_components=[]
    for x in math:
        if x['path'].endswith('/超妈数学'):
            for name in x['components']:
                prefix=x['path']+'/'+name;k=collections.Counter();files=0
                for r in records:
                    if not r['isdir'] and r['path'].startswith(prefix+'/'):k[file_kind(r['name'])]+=1;files+=1
                math_components.append({'title':name,'files':files,'kinds':dict(k),'path':prefix})
    return [
      {'key':'牛津树','title':'牛津树：先作为你建立的分类','intro':f'这个分类下有 {len(ox)} 份非空资料组，分别是大全套、L10—12、PDF、群友剪切音频、视频学习和音效音频。先分别介绍，Week、Level 等内部目录继续随所属资料保存。','category':EN,'refs':refs(ox),'components':[]},
      {'key':'超妈数学','title':'超妈数学：三处资料先放在同一主题下看','intro':f'索引识别到 {len(math)} 处整包/资料组。综合包里有数感 L1/L2、思维 L1/L2、数学绘本与直播课；另有独立的思维 L1 和数感 L1，不能按同名直接合并。','category':MATH,'refs':refs(math),'components':math_components},
      {'key':'Susan','title':'Susan：按孩子英文启蒙理解，区分资料用途','intro':'现有内容包括综合启蒙包、词卡与基础句型、高频词、逐本绘本带读，以及给家长看的启蒙方法。成人英文目录里的 Susan 也主要属于这组儿童启蒙配套。','category':EN,'refs':refs(su),'components':[]},
      {'key':'泛听泛读','title':'泛听泛读书单：按书名选材料的英语有声阅读库','intro':(f'你确认这是整包转存。当前有 {len(library["components"])} 个一级书系目录、{library["files"]:,} 个文件，其中音频 {library["kinds"].get("音频",0):,} 个。覆盖绘本、分级、桥梁与章节书；这是一套跨阶段储备库，可按书名检索，不按年龄一次全部安排。' if library else '该包当前不在有效索引中。'),
       'category':EN,'refs':refs([library]) if library else [],'components':[{'title':n,'note':'书单包内系列，仅介绍，不视为独立移动单元'} for n in (library['components'] if library else [])]},
    ]


def write_markdown(data):
    lines=['# 整包内容介绍与大类判断','',f'共 {len(data["packages"])} 份整包与资料组；保留每份原路径，不把推断边界冒充转存记录。','',data['meta']['evidence'],'',
        '整理顺序：确认边界 → 了解内容与大类 → 整包归位 → 再细分英文。恩恩3岁，常用内容在00熏听，查找以名称为主。','']
    for f in data['focus']:
        lines+=['## '+f['title'],'',f['intro'],'']
        for r in f['refs']:lines+=['- '+r['title']+'：'+r['path']+f'（{r["files"]:,} 个文件）']
        lines+=['']
    for x in data['packages']:
        lines+=['## '+x['displayTitle'],'','原路径：'+x['path'],'','建议大类：'+x['category'],'',
            '是什么：'+x['summary'],'','实际内容：'+x['contentLine']+'。'+x['contentDetail'],'',
            '什么作用：'+x['purpose'],'','边界说明：'+x['boundaryNote'],'','判断状态：'+x['confidence'],'']
        if x['titleClues']:lines+=['文件名线索：'+'；'.join(x['titleClues']),'']
        if x['notes']:lines+=['整理备注：'+'；'.join(x['notes']),'']
    lines+=['## 分类目录中的散文件','']
    for g in data['looseGroups']:
        lines+=[f'- {g["parent"]}：{g["files"]:,} 个文件。{g["note"]}。']
        if g['items']:lines+=['  '+i['name']+'（'+i['kind']+'）' for i in g['items']]
    (OUT/'整包内容介绍.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':main()
