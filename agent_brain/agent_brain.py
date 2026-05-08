#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent_brain.py — 小云的个人知识库大脑 (v2.0)
=======================================================
统一管理所有技能(Skill)和记忆(Memory)的 SQLite 数据库。
支持：
  python agent_brain.py init         初始化数据库
  python agent_brain.py index       索引所有技能+记忆
  python agent_brain.py scan <任务>  扫描相关技能
  python agent_brain.py status      查看数据库状态
  python agent_brain.py record <任务描述> [技能名]   记录任务执行
  python agent_brain.py recall <关键词>  搜索记忆
  python agent_brain.py stats       技能使用统计
  python agent_brain.py rebuild     完全重建数据库
"""
import os, sys, sqlite3, re, time
from pathlib import Path
from datetime import datetime

# ── 路径配置 ─────────────────────────────────────────────
HOME        = Path.home()
SKILLS_DIR  = HOME / ".workbuddy" / "skills"
MEMORY_DIRS = [
    HOME / ".workbuddy" / "memory",
    HOME / "WorkBuddy" / "20260414214847" / ".workbuddy" / "memory",
]
DB_PATH = HOME / ".workbuddy" / "agent_brain" / "brain.db"
SCRIPT_DIR = HOME / ".workbuddy" / "agent_brain"

# ── UTF-8 兼容 ─────────────────────────────────────────────
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ── 正则预编译 ─────────────────────────────────────────────
RE_M = re.MULTILINE
RE_I = re.IGNORECASE
SKILL_NAME_RE = re.compile(r'^name:\s*"?([^"\n]+)"?', RE_M)
TRIGGER_RE    = re.compile(r'(?:触发词|trigger_keywords|trigger_words)[:\s]*([^\n]+)', RE_M | RE_I)
CATEGORY_RE   = re.compile(r'(?:category|分类)[:\s]*"?([^"\n]+)"?', RE_M | RE_I)
AGENT_CREATED = re.compile(r'agent_created:\s*(true|false|1|0)', RE_M | RE_I)
ALLOWED_RE    = re.compile(r'allowed_tools[:\s]*([^\n]+)', RE_M)
DESC_RE       = re.compile(r'description[:\s]*"?([^"\n]+)"?', RE_M | RE_I)

# ══════════════════════════════════════════════════════════
#  数据库初始化
# ══════════════════════════════════════════════════════════
def init_db(force=False):
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    if force and DB_PATH.exists():
        DB_PATH.unlink()
        print("[INFO] 已删除旧数据库，准备重建")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 技能表
    cur.execute("""CREATE TABLE IF NOT EXISTS skills (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT    UNIQUE NOT NULL,
        category      TEXT    DEFAULT 'general',
        description   TEXT    DEFAULT '',
        file_path     TEXT,
        trigger_words TEXT    DEFAULT '',
        tags          TEXT    DEFAULT '',
        allowed_tools TEXT    DEFAULT '',
        agent_created INTEGER DEFAULT 0,
        last_used     TEXT,
        use_count     INTEGER DEFAULT 0,
        created_at    TEXT    DEFAULT (datetime('now')),
        updated_at    TEXT    DEFAULT (datetime('now')),
        raw_meta      TEXT    DEFAULT ''
    )""")

    # 记忆表
    cur.execute("""CREATE TABLE IF NOT EXISTS memories (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        category   TEXT    DEFAULT 'general',
        keywords   TEXT    DEFAULT '',
        content    TEXT,
        source     TEXT    UNIQUE,
        importance INTEGER DEFAULT 3,
        use_count  INTEGER DEFAULT 0,
        created_at TEXT    DEFAULT (datetime('now'))
    )""")

    # 任务历史表
    cur.execute("""CREATE TABLE IF NOT EXISTS task_history (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        task_desc    TEXT,
        skills_used  TEXT    DEFAULT '',
        outcome      TEXT    DEFAULT '',
        notes        TEXT    DEFAULT '',
        duration_sec INTEGER,
        created_at   TEXT    DEFAULT (datetime('now'))
    )""")

    conn.commit()
    conn.close()
    print(f"[OK] 数据库已初始化: {DB_PATH}")

# ══════════════════════════════════════════════════════════
#  创建 FTS 表（独立调用）
# ══════════════════════════════════════════════════════════
def _create_fts_tables():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS skills_fts")
    cur.execute("""CREATE VIRTUAL TABLE skills_fts USING fts5(
        name, description, trigger_words, tags, category
    )""")
    cur.execute("DROP TABLE IF EXISTS memories_fts")
    cur.execute("""CREATE VIRTUAL TABLE memories_fts USING fts5(
        keywords, content, category
    )""")
    conn.commit()
    conn.close()

# ══════════════════════════════════════════════════════════
#  辅助：解析 SKILL.md frontmatter
# ══════════════════════════════════════════════════════════
def parse_skill_md(skill_dir: Path) -> dict:
    """解析技能目录中的 SKILL.md，返回元数据 dict"""
    md_file = skill_dir / "SKILL.md"
    if not md_file.exists():
        md_file = skill_dir / "README.md"
    if not md_file.exists():
        return {}

    try:
        raw = md_file.read_text(encoding='utf-8', errors='replace')
    except Exception:
        try:
            raw = md_file.read_text(encoding='gbk', errors='replace')
        except Exception:
            return {}

    # 提取 YAML frontmatter
    frontmatter = ""
    content = raw
    if raw.startswith('---'):
        end = raw.find('\n---', 4)
        if end > 0:
            frontmatter = raw[3:end]
            content = raw[end+4:]

    # 解析字段
    name_m = SKILL_NAME_RE.search(frontmatter) or SKILL_NAME_RE.search(raw[:500])
    if not name_m:
        name_m = re.search(r'^#\s+(.+)', content, RE_M)
    if not name_m:
        name = skill_dir.name
    else:
        name = name_m.group(1).strip()

    # 触发词
    sample = frontmatter or raw[:2000]
    triggers_m = TRIGGER_RE.search(sample)
    triggers = triggers_m.group(1).strip() if triggers_m else ''

    # 分类
    cat_m = CATEGORY_RE.search(frontmatter or '')
    category = cat_m.group(1).strip() if cat_m else guess_category(skill_dir.name)

    # agent_created
    ac_m = AGENT_CREATED.search(frontmatter or '')
    agent_created = 1 if (ac_m and ac_m.group(1).lower() in ('true', '1')) else 0

    # allowed_tools
    at_m = ALLOWED_RE.search(frontmatter or '')
    allowed_tools = at_m.group(1).strip() if at_m else ''

    # 描述
    desc_m = DESC_RE.search(frontmatter or '')
    if desc_m:
        description = desc_m.group(1).strip()
    else:
        lines = [l.strip() for l in content.split('\n') if l.strip() and not l.strip().startswith('#')]
        description = ' '.join(lines[:3])[:200]

    # 标签
    tags = guess_tags(name, description, category)

    return {
        'name':          str(name).strip(),
        'category':      str(category).strip(),
        'description':   str(description).strip(),
        'file_path':     str(md_file),
        'trigger_words': str(triggers),
        'tags':          ','.join(tags),
        'allowed_tools': str(allowed_tools),
        'agent_created': agent_created,
        'raw_meta':      frontmatter[:500],
    }

# 分类映射
CATEGORY_MAP = {
    'stock': '金融交易', 'trading': '金融交易', 'finance': '金融交易',
    'market': '市场分析', 'macro': '宏观分析', 'futures': '期货',
    'gold': '贵金属', 'news': '新闻资讯', 'browser': '浏览器自动化',
    'document': '文档办公', 'code': '开发工具', 'github': '开发工具',
    'system': '系统工具', 'social': '社交', 'twitter': '社交',
    'wechat': '社交', 'data': '数据分析', 'database': '数据分析',
    'video': '多媒体', 'image': '多媒体', 'voice': '多媒体',
    'office': '办公套件', 'word': '办公套件', 'pptx': '办公套件',
    'xlsx': '办公套件', 'pdf': '文档办公', 'weather': '生活服务',
    'calendar': '日程管理', 'memory': '记忆系统', 'skill': '技能工具',
    'automation': '自动化', 'desktop': '系统工具', 'cron': '自动化',
    'spider': '数据采集', 'ai': 'AI工具', 'Agent': 'AI工具',
    'hermes': '系统集成', 'qqbot': '系统集成',
}

def guess_category(name: str) -> str:
    n = name.lower()
    for kw, cat in CATEGORY_MAP.items():
        if kw in n:
            return cat
    return '通用工具'

def guess_tags(name: str, desc: str, cat: str) -> list:
    text = (name + ' ' + desc + ' ' + cat).lower()
    tags = set()
    for kw in CATEGORY_MAP.keys():
        if kw in text:
            tags.add(kw)
    if not tags:
        tags.add('general')
    return list(tags)

# 停用词（关键词提取）
STOP_WORDS = set('的了是在有和就也不这那一个上与而于但为或者以及可以比如例如等例如比如包括等于没有如果因为所以被把被从对就关于还也'.split())

def extract_keywords(text: str, top_n=15) -> str:
    words = re.findall(r'[\w\u4e00-\u9fff]{2,}', text)
    freq = {}
    for w in words:
        if w not in STOP_WORDS and not w.isdigit():
            freq[w] = freq.get(w, 0) + 1
    top = sorted(freq.items(), key=lambda x: -x[1])[:top_n]
    return ','.join(w for w, _ in top)

# ══════════════════════════════════════════════════════════
#  索引技能
# ══════════════════════════════════════════════════════════
def index_skills(verbose=True):
    if not SKILLS_DIR.exists():
        print(f"[WARN] 技能目录不存在: {SKILLS_DIR}"); return 0, 0

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 清空旧数据（重建模式）
    cur.execute("DELETE FROM skills")
    cur.execute("DELETE FROM skills_fts")

    indexed = 0
    skipped = 0

    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        meta = parse_skill_md(skill_dir)
        if not meta or not meta.get('name'):
            skipped += 1
            if verbose:
                print(f"  [SKIP] {skill_dir.name} (无法解析)")
            continue

        name = meta['name']

        try:
            cur.execute("""INSERT INTO skills
                (name, category, description, file_path, trigger_words,
                 tags, allowed_tools, agent_created, raw_meta)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (name, meta['category'], meta['description'], meta['file_path'],
                 meta['trigger_words'], meta['tags'], meta['allowed_tools'],
                 meta['agent_created'], meta['raw_meta']))
            skill_id = cur.lastrowid

            # 同步 FTS
            cur.execute("""INSERT INTO skills_fts(rowid, name, description, trigger_words, tags, category)
                VALUES (?,?,?,?,?,?)""",
                (skill_id, name, meta['description'], meta['trigger_words'],
                 meta['tags'], meta['category']))

            indexed += 1
            if verbose:
                flag = '🏷️' if meta['agent_created'] else '  '
                print(f"  {flag} [新建] {name:<35s} [{meta['category']}]")
        except sqlite3.IntegrityError:
            skipped += 1
            if verbose:
                print(f"  [SKIP] {name} (名称冲突)")
        except Exception as e:
            skipped += 1
            if verbose:
                print(f"  [FAIL] {name}: {e}")

    conn.commit()
    conn.close()
    print(f"\n[完成] 索引技能: 成功={indexed} 跳过={skipped}")
    return indexed, skipped

# ══════════════════════════════════════════════════════════
#  索引记忆
# ══════════════════════════════════════════════════════════
def index_memories(verbose=True):
    conn = sqlite3.connect(str(DB_PATH))
    cur  = conn.cursor()

    # 清空旧记忆
    cur.execute("DELETE FROM memories")
    cur.execute("DELETE FROM memories_fts")

    indexed = 0
    skipped = 0

    for mem_dir in MEMORY_DIRS:
        if not mem_dir.exists():
            continue
        if verbose:
            print(f"\n  扫描: {mem_dir}")

        for md_file in sorted(mem_dir.glob("*.md")):
            try:
                raw = md_file.read_text(encoding='utf-8', errors='replace')
            except Exception:
                try:
                    raw = md_file.read_text(encoding='gbk', errors='replace')
                except Exception:
                    skipped += 1; continue

            if len(raw.strip()) < 20:
                skipped += 1; continue

            keywords = extract_keywords(raw)
            category = guess_memory_category(str(md_file))
            content  = raw[:3000]

            try:
                cur.execute("""INSERT INTO memories
                    (category, keywords, content, source, importance)
                    VALUES (?,?,?,?,?)""",
                    (category, keywords, content, str(md_file), 3))
                mem_id = cur.lastrowid

                cur.execute("""INSERT INTO memories_fts(rowid, keywords, content, category)
                    VALUES (?,?,?,?)""",
                    (mem_id, keywords, content, category))

                indexed += 1
                if verbose:
                    print(f"    [OK] {md_file.name}")
            except sqlite3.IntegrityError:
                skipped += 1
            except Exception as e:
                skipped += 1
                if verbose:
                    print(f"    [FAIL] {md_file.name}: {e}")

    conn.commit()
    conn.close()
    print(f"\n[完成] 索引记忆: 成功={indexed} 跳过={skipped}")
    return indexed, skipped

def guess_memory_category(source_path: str) -> str:
    p = source_path.lower()
    for kw, cat in {
        'memory': '记忆系统', 'memo': '记忆', 'trade': '交易记录',
        'stock': '股票', 'config': '配置', 'skill': '技能',
        'project': '项目', 'user': '用户', 'system': '系统',
        'brain': '大脑', 'summary': '摘要',
    }.items():
        if kw in p:
            return cat
    return '通用记忆'

# ══════════════════════════════════════════════════════════
#  重建 FTS 表（数据库迁移用）
# ══════════════════════════════════════════════════════════
def rebuild_fts():
    """从 skills/memories 表重建 FTS 索引"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("DROP TABLE IF EXISTS skills_fts")
    cur.execute("""CREATE VIRTUAL TABLE skills_fts USING fts5(
        name, description, trigger_words, tags, category
    )""")

    cur.execute("DROP TABLE IF EXISTS memories_fts")
    cur.execute("""CREATE VIRTUAL TABLE memories_fts USING fts5(
        keywords, content, category
    )""")

    # 从 skills 表回填
    cur.execute("SELECT id, name, description, trigger_words, tags, category FROM skills")
    for row in cur.fetchall():
        try:
            cur.execute("""INSERT INTO skills_fts(rowid, name, description, trigger_words, tags, category)
                VALUES (?,?,?,?,?,?)""", row)
        except Exception:
            pass

    # 从 memories 表回填
    cur.execute("SELECT id, keywords, content, category FROM memories")
    for row in cur.fetchall():
        try:
            cur.execute("""INSERT INTO memories_fts(rowid, keywords, content, category)
                VALUES (?,?,?,?)""", row)
        except Exception:
            pass

    conn.commit()
    conn.close()
    print("[OK] FTS 索引已重建")

# ══════════════════════════════════════════════════════════
#  扫描任务 → 相关技能
# ══════════════════════════════════════════════════════════
def scan_task(query: str, top_n=8, verbose=True) -> list:
    if not DB_PATH.exists():
        print("[ERROR] 数据库未初始化，请先运行: python agent_brain.py init"); return []

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    q = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', query)
    q_terms = ' '.join(q.split())

    results = []
    # 拆词搜索：提取查询中的关键词，分别 LIKE 匹配
    # 中文按每2-4字gram切分，英文按单词切分
    keywords = set()

    # 英文单词
    for w in re.findall(r'[a-zA-Z]{2,}', query):
        keywords.add(w.lower())

    # 中文：2字gram + 重要单字
    chs = ''.join(re.findall(r'[\u4e00-\u9fff]', query))
    if len(chs) >= 2:
        for i in range(len(chs)-1):
            keywords.add(chs[i:i+2])
        if len(chs) >= 3:
            for i in range(len(chs)-2):
                keywords.add(chs[i:i+3])

    # 如果有"分析"、"股票"等高频词，也加入
    important_words = ['分析', '股票', '交易', '选股', '截图', '搜索', '生成',
                     '修复', 'Hermes', '技能', '报告', '市场', '板块']
    for w in important_words:
        if w in query:
            keywords.add(w)

    if not keywords:
        if verbose:
            print("  未提取到有效关键词"); return []

    if verbose:
        print(f"  提取关键词: {list(keywords)[:10]}")

    # 构建 SQL：任一关键词匹配即返回
    like_clauses = []
    params = []
    for kw in keywords:
        like_clauses.append("(name LIKE ? OR description LIKE ? OR trigger_words LIKE ? OR tags LIKE ?)")
        p = f'%{kw}%'
        params.extend([p, p, p, p])

    sql = f"""SELECT id, name, category, description, trigger_words,
                     tags, use_count, last_used, file_path, 0 as rank
               FROM skills
               WHERE {' OR '.join(like_clauses)}
               ORDER BY use_count DESC, name
               LIMIT ?"""
    params.append(top_n)

    try:
        cur.execute(sql, params)
        results = cur.fetchall()
    except Exception as e:
        if verbose:
            print(f"[WARN] 搜索失败: {e}")

    # 如果 LIKE 结果太少，补上 FTS5 搜索
    if len(results) < top_n:
        try:
            cur.execute("""SELECT s.id, s.name, s.category, s.description,
                              s.trigger_words, s.tags, s.use_count, s.last_used, s.file_path,
                              bm25(skills_fts) as rank
                           FROM skills_fts f
                           JOIN skills s ON s.id = f.rowid
                           WHERE skills_fts MATCH ?
                           ORDER BY rank
                           LIMIT ?""", (q_terms + '*', top_n))
            fts_results = cur.fetchall()
            # 合并去重
            existing_ids = {r[0] for r in results}
            for r in fts_results:
                if r[0] not in existing_ids:
                    results.append(r)
        except Exception as e:
            if verbose:
                print(f"[WARN] FTS 查询失败: {e}")

    conn.close()

    if verbose:
        print(f"\n{'='*60}")
        print(f"  🔍 任务扫描: {query}")
        print(f"{'='*60}")
        if results:
            print(f"  找到 {len(results)} 个相关技能:\n")
            for i, r in enumerate(results, 1):
                rank_icon = '⭐' if i == 1 else ('🔹' if i <= 3 else '  ')
                print(f"  {rank_icon} {i}. {r[1]} [{r[2]}]")
                tw = (r[4] or '')[:80]
                if tw:
                    print(f"      触发词: {tw}")
                desc = (r[3] or '')[:100]
                if desc:
                    print(f"      {desc}")
                print(f"      使用: {r[6]}次 | 路径: {r[8]}")
                print()
        else:
            print("  未找到相关技能。尝试用 'python agent_brain.py index' 重建索引")
            print(f"{'='*60}\n")

    return results

# ══════════════════════════════════════════════════════════
#  搜索记忆
# ══════════════════════════════════════════════════════════
def recall_memories(keyword: str, top_n=5, verbose=True) -> list:
    if not DB_PATH.exists():
        print("[ERROR] 数据库未初始化"); return []

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    results = []
    # LIKE 优先（中文友好）
    like_q = f'%{keyword}%'
    try:
        cur.execute("""SELECT id, category, keywords, substr(content,1,200),
                             source, use_count, created_at
                           FROM memories
                           WHERE keywords LIKE ? OR content LIKE ?
                           ORDER BY importance DESC, use_count DESC
                           LIMIT ?""", (like_q, like_q, top_n))
        results = cur.fetchall()
    except Exception as e:
        if verbose:
            print(f"[WARN] 记忆 LIKE 搜索失败: {e}")

    # FTS 补充（英文关键词）
    if len(results) < top_n:
        try:
            cur.execute("""SELECT m.id, m.category, m.keywords, substr(m.content,1,200),
                                 m.source, m.use_count, m.created_at
                               FROM memories_fts f
                               JOIN memories m ON m.id = f.rowid
                               WHERE memories_fts MATCH ?
                               ORDER BY rank
                               LIMIT ?""", (keyword + '*', top_n))
            fts_results = cur.fetchall()
            existing_ids = {r[0] for r in results}
            for r in fts_results:
                if r[0] not in existing_ids:
                    results.append(r)
        except Exception as e:
            if verbose:
                print(f"[WARN] 记忆 FTS 搜索失败: {e}")

    conn.close()

    if verbose:
        print("\n" + '='*60)
        print(f"  🧠 记忆召回: {keyword}")
        print('='*60)
        if results:
            for r in results:
                print(f"\n  [{r[1]}] {Path(r[4]).name}")
                print(f"  关键词: {(r[2] or '')[:60]}")
                snippet = (r[3] or '')[:150]
                print(f"  {snippet}...")
                print(f"  使用:{r[5]}次 | {str(r[6])[:10]}")
        else:
            print("  未找到相关记忆")

    return results

def record_task(task_desc: str, skills_used: str = '', outcome: str = '', notes: str = ''):
    if not DB_PATH.exists():
        print("[ERROR] 数据库未初始化"); return

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("""INSERT INTO task_history (task_desc, skills_used, outcome, notes)
        VALUES (?,?,?,?)""", (task_desc, skills_used, outcome, notes))

    # 更新技能使用次数
    for skill_name in skills_used.split(','):
        skill_name = skill_name.strip()
        if skill_name:
            cur.execute("""UPDATE skills SET use_count=use_count+1, last_used=datetime('now')
                WHERE name=? OR name LIKE ?""", (skill_name, f'%{skill_name}%'))

    conn.commit()
    conn.close()
    print(f"[OK] 任务已记录: {task_desc[:60]}")

# ══════════════════════════════════════════════════════════
#  状态面板
# ══════════════════════════════════════════════════════════
def show_status():
    if not DB_PATH.exists():
        print("[ERROR] 数据库未初始化，请先运行: python agent_brain.py init"); return

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*), SUM(use_count) FROM skills")
    sc, su = cur.fetchone()
    sc = sc or 0; su = su or 0

    cur.execute("SELECT COUNT(*) FROM memories")
    mc = (cur.fetchone() or (0,))[0]

    cur.execute("SELECT COUNT(*) FROM task_history")
    tc = (cur.fetchone() or (0,))[0]

    cur.execute("SELECT category, COUNT(*) as cnt FROM skills GROUP BY category ORDER BY cnt DESC")
    cats = cur.fetchall()

    cur.execute("SELECT name, use_count, last_used FROM skills ORDER BY use_count DESC LIMIT 10")
    top_skills = cur.fetchall()

    cur.execute("SELECT name, use_count FROM skills WHERE agent_created=1 ORDER BY use_count DESC LIMIT 10")
    my_skills = cur.fetchall()

    cur.execute("SELECT name, use_count FROM skills ORDER BY last_used DESC LIMIT 5")
    recent = cur.fetchall()

    conn.close()

    db_size = DB_PATH.stat().st_size // 1024 if DB_PATH.exists() else 0

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         小云个人知识库大脑 (agent_brain v2.0)                 ║
╠══════════════════════════════════════════════════════════════╣
║  📁 数据库: {str(DB_PATH)[:45]:<45s}║
║          大小: {db_size:>6d} KB{' '*(45-len(str(db_size)))}║
║                                                              ║
║  📊 技能统计                                                   ║
║     总技能数:  {sc:>4d}                                        ║
║     总使用次数: {su:>6d}                                     ║
║                                                              ║
║  📝 记忆统计                                                   ║
║     记忆条目:  {mc:>4d}                                        ║
║                                                              ║
║  📜 任务历史                                                   ║
║     记录条数:  {tc:>4d}                                        ║
║                                                              ║""")
    print("║  🏷️  技能分类 (Top 8)                                    ║")
    for cat, cnt in cats[:8]:
        bar = '█' * min(cnt // 2 + 1, 30)
        print(f"║     {cat:<10s}: {cnt:3d} {bar:<30s}║")
    print("║                                                              ║")
    print("║  ⭐ 最常用技能 (Top 5)                                   ║")
    for name, cnt, _ in top_skills[:5]:
        print(f"║     {name:<35s} {cnt:4d}次     ║")
    if my_skills:
        print("║                                                              ║")
        print("║  🔧 我创建的技能 (Top 5)                                 ║")
        for name, cnt in my_skills[:5]:
            print(f"║     {name:<35s} {cnt:4d}次     ║")
    if recent:
        print("║                                                              ║")
        print("║  🕒 最近使用                                                   ║")
        for name, lu in recent:
            print(f"║     {name:<30s} {str(lu)[:10]:<10s}║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

# ══════════════════════════════════════════════════════════
#  技能使用统计
# ══════════════════════════════════════════════════════════
def show_stats():
    if not DB_PATH.exists():
        print("[ERROR] 数据库未初始化"); return

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT name, category, use_count, last_used, agent_created FROM skills ORDER BY use_count DESC")
    rows = cur.fetchall()
    conn.close()

    print(f"\n{'='*65}")
    print(f"  技能使用统计  (共 {len(rows)} 个技能)")
    print(f"{'='*65}\n")
    print(f"  {'排名':<4s} {'技能名称':<36s} {'分类':<10s} {'使用次数':<8s} {'自建':<4s}")
    print(f"  {'-'*60}")
    for i, (name, cat, cnt, lu, ac) in enumerate(rows, 1):
        ac_flag = '✓' if ac else ' '
        lu_str = str(lu)[:10] if lu else '从未'
        print(f"  {i:3d}. {name:<36s} {cat:<10s} {cnt:>5d}次   {ac_flag}  {lu_str}")
    print(f"\n{'='*65}\n")

# ══════════════════════════════════════════════════════════
#  CLI 入口
# ══════════════════════════════════════════════════════════
USAGE = """
agent_brain.py — 小云的个人知识库大脑 v2.0

用法:
  python agent_brain.py init           初始化数据库
  python agent_brain.py index         索引所有技能+记忆（重建索引）
  python agent_brain.py scan <任务>   扫描相关技能
  python agent_brain.py recall <词>   搜索记忆
  python agent_brain.py record <任务> [技能名]  记录任务
  python agent_brain.py status        查看状态面板
  python agent_brain.py stats         技能使用统计
  python agent_brain.py rebuild      完全重建数据库

示例:
  python agent_brain.py init
  python agent_brain.py index
  python agent_brain.py scan "帮我分析A股汇源通信"
  python agent_brain.py scan "修复Hermes QQ Bot"
  python agent_brain.py recall "汇源通信"
  python agent_brain.py status
  python agent_brain.py rebuild
"""

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print(USAGE)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == 'init':
        init_db()
    elif cmd == 'index':
        print(">>> 初始化数据库...")
        init_db()
        print("\n>>> 索引技能...")
        index_skills()
        print("\n>>> 索引记忆...")
        index_memories()
        print("\n✅ 全部完成! 运行 'python agent_brain.py status' 查看状态")
    elif cmd == 'scan':
        if len(args) < 2:
            print("[ERROR] 请提供任务描述: scan <任务描述>"); sys.exit(1)
        scan_task(' '.join(args[1:]))
    elif cmd == 'recall':
        if len(args) < 2:
            print("[ERROR] 请提供关键词: recall <关键词>"); sys.exit(1)
        recall_memories(' '.join(args[1:]))
    elif cmd == 'record':
        if len(args) < 2:
            print("[ERROR] 请提供任务描述: record <任务描述> [技能名]"); sys.exit(1)
        task_desc = args[1]
        skills_used = ' '.join(args[2:]) if len(args) > 2 else ''
        record_task(task_desc, skills_used)
    elif cmd == 'status':
        show_status()
    elif cmd == 'stats':
        show_stats()
    elif cmd == 'rebuild':
        print("[INFO] 完全重建数据库...")
        init_db(force=True)
        print("\n>>> 索引技能...")
        index_skills()
        print("\n>>> 索引记忆...")
        index_memories()
        print("\n✅ 重建完成! 运行 'python agent_brain.py status' 查看状态")
    else:
        print(f"[ERROR] 未知命令: {cmd}")
        print(USAGE)
        sys.exit(1)
