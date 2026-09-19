#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Git Data API 推送器 —— 本机 git 通道不通时的绕行方案。

用途
    `git push` 因网络不可达失败时（典型症状：`CONNECT tunnel failed, response 502`
    或 `Failed to connect to github.com port 443`），用 GitHub **Git Data API**
    逐层重建本地 HEAD 这条提交并推进远端分支：

        blob(base64 上传) → tree(以父树为基底) → commit → PATCH ref

    作者 / 提交时间 / 提交信息**全部照抄本地**，因此**远端 commit 哈希与本地完全一致**，
    历史不分叉、不需要事后 fetch+reset。已验证可用于本项目多次发版。

为什么本机 git 会不通
    WorkBuddy 环境代理只放行 `api.github.com`；`github.com` 的 git-over-HTTPS 会被拒。
    另外 git **不读** Windows 系统代理，走 Clash 时须显式指定：
        git -c http.proxy=http://127.0.0.1:7897 push      # 用前先确认 Clash 已启动

用法
    python publish-via-api.py                          # 默认仓库/分支
    python publish-via-api.py --repo owner/name --work D:\\path --branch main
    python publish-via-api.py --dry-run                # 只校验，不写远端

前置
    gh CLI 已登录（认证走 gh 自己的凭据存储，本脚本不接触 token）。
    远端目标分支当前必须指向本地 HEAD 的父提交（否则会拒绝执行，避免误造分叉）。

实现坑（踩过）
    Windows 下**不能**用 shell=True 调 git —— cmd.exe 会吃掉 `^`，
    导致 `HEAD^{tree}` 这类写法失效；必须用参数数组调用。
"""
import argparse
import base64
import json
import subprocess
import sys


def make_git(work):
    def git(*args, binary=False):
        r = subprocess.run(["git"] + list(args), cwd=work, capture_output=True)
        if r.returncode != 0:
            print("git 失败:", r.stderr.decode("utf-8", "replace")[:300])
            sys.exit(1)
        return r.stdout if binary else r.stdout.decode("utf-8", "replace").strip()

    return git


def make_api(repo):
    def api(method, path, payload=None):
        cmd = ["gh", "api", "-X", method, "repos/%s/%s" % (repo, path)]
        if payload is None:
            r = subprocess.run(cmd, capture_output=True, text=True)
        else:
            cmd += ["--input", "-"]
            r = subprocess.run(cmd, input=json.dumps(payload),
                               capture_output=True, text=True)
        if r.returncode != 0:
            print("API 失败:", r.stderr.strip()[:400])
            sys.exit(1)
        return json.loads(r.stdout) if r.stdout.strip() else {}

    return api


def main():
    ap = argparse.ArgumentParser(description="用 Git Data API 原样重建提交并推进远端分支")
    ap.add_argument("--repo", default="Ma6302/wireguard-setup-scripts",
                    help="owner/name（默认本项目仓库）")
    ap.add_argument("--work", default=r"D:\Download\wireguard-setup-scripts",
                    help="本地仓库工作目录")
    ap.add_argument("--branch", default="main", help="目标分支（默认 main）")
    ap.add_argument("--dry-run", action="store_true", help="只做校验，不写远端")
    args = ap.parse_args()

    git = make_git(args.work)
    api = make_api(args.repo)

    head = git("rev-parse", "HEAD")
    parent = git("rev-parse", "HEAD~1")
    local_tree = git("rev-parse", "HEAD^{tree}")
    parent_tree = git("rev-parse", "HEAD~1^{tree}")
    author_iso = git("show", "-s", "--format=%aI", "HEAD")
    committer_iso = git("show", "-s", "--format=%cI", "HEAD")
    author = git("show", "-s", "--format=%an <%ae>", "HEAD")
    raw_commit = git("cat-file", "commit", "HEAD", binary=True)
    message = raw_commit.split(b"\n\n", 1)[1].decode("utf-8", "replace")
    changed = [x for x in git("diff", "--name-only", "HEAD~1", "HEAD").splitlines() if x]

    print("仓库        :", args.repo, "→ 分支", args.branch)
    print("工作目录    :", args.work)
    print("本地 HEAD   :", head)
    print("父提交      :", parent)
    print("本地 tree   :", local_tree)
    print("父 tree     :", parent_tree)
    print("作者/时间   : %s %s" % (author, author_iso))
    print("提交信息    : %r" % message[:60])
    print("变更文件    : %d 个" % len(changed))

    # 前置检查：远端分支必须正好指向本地父提交，否则拒绝（避免误造分叉）
    ref = api("GET", "git/ref/heads/%s" % args.branch)
    remote_sha = ref.get("object", {}).get("sha", "")
    print("远端分支现指:", remote_sha)
    if remote_sha != parent:
        print("\n拒绝执行：远端 %s 指向 %s，而本地父提交是 %s。"
              % (args.branch, remote_sha[:12] or "(空)", parent[:12]))
        print("请先确认远端与本地是否同步（git fetch && git log --oneline -3）；")
        print("若远端已含本地 HEAD（无需重推），或本地需先 rebase —— 都不要用本脚本硬推。")
        sys.exit(3)

    if args.dry_run:
        print("\n[dry-run] 前置检查通过，未写任何远端对象。")
        return

    entries = []
    for path in changed:
        mode = git("ls-tree", "HEAD", path).split()[0]
        local_blob = git("rev-parse", "HEAD:%s" % path)
        raw = git("cat-file", "blob", local_blob, binary=True)
        res = api("POST", "git/blobs", {"encoding": "base64",
                                        "content": base64.b64encode(raw).decode()})
        same_blob = res["sha"] == local_blob
        print("  blob %-38s %s %s" % (path, res["sha"][:12], "✓" if same_blob else "✗"))
        if not same_blob:
            print("    blob 哈希不一致，内容有偏差，终止")
            sys.exit(2)
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": res["sha"]})

    res = api("POST", "git/trees", {"base_tree": parent_tree, "tree": entries})
    tree_sha = res["sha"]
    print("远端 tree   :", tree_sha, "✓" if tree_sha == local_tree else "✗（与本地不同）")
    if tree_sha != local_tree:
        print("tree 哈希不一致，终止（不推进 ref）")
        sys.exit(2)

    name, email = author.split(" <")
    person = {"name": name, "email": email.rstrip(">")}
    res = api("POST", "git/commits", {
        "message": message, "tree": tree_sha, "parents": [parent],
        "author": dict(person, date=author_iso),
        "committer": dict(person, date=committer_iso),
    })
    new_sha = res["sha"]
    same = new_sha == head
    print("远端 commit :", new_sha, "✓ 与本地完全一致" if same else "✗ 与本地不同")
    if not same:
        print("commit 哈希不一致，终止（不推进 ref，避免分叉）")
        sys.exit(2)

    res = api("PATCH", "git/refs/heads/%s" % args.branch,
              {"sha": new_sha, "force": False})
    print("%s 现指向 :" % args.branch, res.get("object", {}).get("sha", new_sha))
    print("\n结果: 哈希一致，本地无需任何收尾操作")


if __name__ == "__main__":
    main()
