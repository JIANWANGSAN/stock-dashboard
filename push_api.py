# -*- coding: utf-8 -*-
"""push_api.py —— 当 github.com 的 git 协议被网络阻断（`git push` 报 502 / Empty reply / 无法连接）时，
用 api.github.com 完成等效推送。

用法：
    python push_api.py                 # 推送本地 HEAD 到 origin/main
    python push_api.py "自定义提交说明"  # 指定提交说明

原理与特点：
    · 以「远端当前 main」为父提交，把「本地 HEAD 的整棵树」通过 Git Data API 上传（blob → tree → commit），
      再更新远端 ref —— 等价于一次 push。
    · blob 内容取自 **git 对象库**（`git cat-file blob`），不是工作区文件，
      因此不会因 Windows 的 CRLF/LF 差异产生假变更。
    · 创建远端提交后，会**在本地重建同一个提交对象并 reset --soft 过去**，
      使本地 HEAD 与远端 main **SHA 完全一致**，不会留下分叉。
    · 无变更（本地 tree == 远端 tree）时直接跳过。

说明：令牌从 `git remote get-url origin` 的 URL 中读取（与 .git/config 一致），本脚本不存储令牌。
"""
import sys, os, re, json, base64, subprocess, urllib.request, urllib.error, datetime

sys.stdout.reconfigure(encoding='utf-8')
BRANCH = 'main'


def sh(args, **kw):
    return subprocess.check_output(args, **kw).decode('utf-8')


def git(*args, input=None, env=None):
    return subprocess.check_output(['git'] + list(args), input=input, env=env).decode('utf-8')


url = sh(['git', 'remote', 'get-url', 'origin']).strip()
m = re.match(r'https://([^@]*)@github\.com/(.+?)(?:\.git)?$', url)
if not m:
    raise SystemExit('❌ 无法从 origin 解析出 GitHub 仓库（当前 remote: %s）' % url.split('@')[-1])
tok, repo = m.group(1).split(':')[-1], m.group(2)
API = 'https://api.github.com/repos/' + repo


def req(path, method='GET', data=None):
    body = json.dumps(data).encode('utf-8') if data is not None else None
    headers = {'Authorization': 'Bearer ' + tok,
               'Accept': 'application/vnd.github+json', 'User-Agent': 'push_api'}
    if body:
        headers['Content-Type'] = 'application/json'
    r = urllib.request.Request(API + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise SystemExit('❌ API %s %s -> %s %s' % (method, path, e.code, e.read().decode()[:300]))


def to_git_date(iso):
    """ISO 8601 → git 的 '<unix秒> <±HHMM>'"""
    dt = datetime.datetime.fromisoformat(iso.replace('Z', '+00:00'))
    off = dt.utcoffset() or datetime.timedelta(0)
    secs = int(off.total_seconds())
    return '%d %+03d%02d' % (int(dt.timestamp()), secs // 3600, abs(secs % 3600) // 60)


ref = req('/git/ref/heads/' + BRANCH)
base_sha = ref['object']['sha']
base_tree = req('/git/commits/' + base_sha)['tree']['sha']
local_tree = git('rev-parse', 'HEAD^{tree}').strip()

if local_tree == base_tree:
    print('✅ 远端与本地内容一致，无需推送（base=%s）' % base_sha[:9])
    subprocess.call(['git', 'update-ref', 'refs/remotes/origin/' + BRANCH, base_sha])
    raise SystemExit(0)

# 1) 上传本地 HEAD 的整棵树（内容取自 git 对象库，避免 CRLF 干扰）
entries = []
for ln in git('-c', 'core.quotepath=false', 'ls-tree', '-r', 'HEAD').splitlines():
    meta, path = ln.split('\t', 1)
    mode, typ, sha = meta.split()
    raw = subprocess.check_output(['git', 'cat-file', 'blob', sha])
    b = req('/git/blobs', 'POST',
            {'content': base64.b64encode(raw).decode('ascii'), 'encoding': 'base64'})
    entries.append({'path': path, 'mode': mode, 'type': 'blob', 'sha': b['sha']})
tree = req('/git/trees', 'POST', {'tree': entries})
if tree['sha'] != local_tree:
    raise SystemExit('❌ 远端树 %s != 本地树 %s（已中止，未改动 ref）' % (tree['sha'], local_tree))
print('  已上传 %d 个文件，树 %s' % (len(entries), local_tree[:9]))

# 2) 创建远端提交
msg = sys.argv[1] if len(sys.argv) > 1 else git('log', '-1', '--format=%B').strip()
name = (sh(['git', 'config', 'user.name']).strip() or 'JIANWANGSAN')
email = (sh(['git', 'config', 'user.email']).strip()
         or '162260734+JIANWANGSAN@users.noreply.github.com')
iso = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()
c = req('/git/commits', 'POST', {
    'message': msg, 'tree': tree['sha'], 'parents': [base_sha],
    'author': {'name': name, 'email': email, 'date': iso},
    'committer': {'name': name, 'email': email, 'date': iso}})

# 3) 让本地 HEAD 与远端 SHA 完全一致（消除分叉）
#    优先 git fetch（链路可用时最稳）；被阻断时用「重建提交对象」的方式兜底
aligned = False
try:
    subprocess.check_call(['git', 'fetch', '--quiet', 'origin', BRANCH],
                          stderr=subprocess.DEVNULL)
    if git('rev-parse', 'origin/' + BRANCH).strip() == c['sha']:
        subprocess.check_call(['git', 'reset', '--soft', c['sha']])
        aligned = True
except Exception:
    pass

if not aligned:
    # GitHub 会规范化 message 的尾部换行；且提交对象里存的是本地时区（如 +0800）而 API 回 UTC，
    # 所以按「时区变体 × 消息变体」逐个试算，命中即对齐。
    _off = datetime.datetime.now().astimezone().utcoffset() or datetime.timedelta(0)
    _secs = int(_off.total_seconds())
    _tzs = ['%+03d%02d' % (_secs // 3600, abs(_secs % 3600) // 60), '+0000']
    _ts = int(datetime.datetime.fromisoformat(
        c['author']['date'].replace('Z', '+00:00')).timestamp())

    def build(msg, tz):
        return ('tree %s\nparent %s\nauthor %s <%s> %d %s\ncommitter %s <%s> %d %s\n\n%s'
                % (c['tree']['sha'], c['parents'][0]['sha'],
                   c['author']['name'], c['author']['email'], _ts, tz,
                   c['committer']['name'], c['committer']['email'], _ts, tz, msg))

    for tz in _tzs:
        for cand in dict.fromkeys([c['message'], c['message'].rstrip('\n'), c['message'] + '\n']):
            obj = build(cand, tz)
            if git('hash-object', '-t', 'commit', '--stdin',
                   input=obj.encode('utf-8')).strip() == c['sha']:
                git('hash-object', '-t', 'commit', '-w', '--stdin', input=obj.encode('utf-8'))
                subprocess.check_call(['git', 'reset', '--soft', c['sha']])
                aligned = True
                break
        if aligned:
            break
if not aligned:
    print('  [warn] 未能让本地对齐远端（远端已更新，本地可用 git fetch + reset --soft 手动对齐）')

# 4) 更新远端 ref
req('/git/refs/heads/' + BRANCH, 'PATCH', {'sha': c['sha'], 'force': True})
subprocess.call(['git', 'update-ref', 'refs/remotes/origin/' + BRANCH, c['sha']])
print('✅ 已推送到远端 %s：%s → %s' % (BRANCH, base_sha[:9], c['sha'][:9]))
