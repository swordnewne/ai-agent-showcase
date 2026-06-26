#!/usr/bin/env python3
"""
Mermaid复杂度检查工具
统计mermaid图中的节点数和层数

用法:
    python complexity_check.py diagram.md
    python complexity_check.py --validate diagram.md
"""
import argparse
import re
import sys


def analyze_mermaid(content):
    """分析mermaid图复杂度"""
    # 提取mermaid代码块
    mermaid_blocks = re.findall(r'```mermaid\n(.*?)```', content, re.DOTALL)

    if not mermaid_blocks:
        # 尝试匹配缩进代码块
        mermaid_blocks = re.findall(r'```mermaid\r?\n(.*?)```', content, re.DOTALL)

    results = []
    for i, block in enumerate(mermaid_blocks):
        lines = block.strip().split('\n')

        # 统计节点
        nodes = set()
        for line in lines:
            # 匹配 graph TD/LR 中的节点定义
            # 格式: A[文本] 或 A{文本} 或 A(文本) 或 A[[文本]]
            matches = re.findall(r'\b([A-Za-z0-9_]+)(?:\[|\(|\{|\[\[)', line)
            nodes.update(matches)

        # 统计边
        edges = 0
        for line in lines:
            if '-->' in line or '-.->' in line or '==>' in line:
                edges += 1

        # 统计层数（按缩进或subgraph）
        subgraphs = [l for l in lines if l.strip().startswith('subgraph')]

        results.append({
            'block': i + 1,
            'nodes': len(nodes),
            'edges': edges,
            'subgraphs': len(subgraphs),
            'lines': len(lines)
        })

    return results


def print_report(results):
    """打印诊断报告"""
    print("=" * 50)
    print("Mermaid 复杂度诊断报告")
    print("=" * 50)

    for r in results:
        print(f"\n图 #{r['block']}:")
        print(f"  节点数: {r['nodes']} {'✅' if r['nodes'] <= 15 else '⚠️ 建议<15'}")
        print(f"  边数:   {r['edges']}")
        print(f"  子图:   {r['subgraphs']}")
        print(f"  行数:   {r['lines']}")

        # 诊断
        issues = []
        if r['nodes'] > 15:
            issues.append("节点过多，建议拆图或抽象")
        if r['nodes'] > 7 and r['subgraphs'] == 0:
            issues.append("单层节点>7且无子图分组，建议用subgraph")
        if r['edges'] > r['nodes'] * 2:
            issues.append("边数过多，可能存在意大利面问题")

        if issues:
            print(f"  问题:")
            for issue in issues:
                print(f"    - {issue}")
        else:
            print(f"  ✅ 复杂度正常")

    print("\n" + "=" * 50)


def main():
    parser = argparse.ArgumentParser(description='Mermaid复杂度检查')
    parser.add_argument('file', help='Markdown文件路径')
    parser.add_argument('--validate', action='store_true', help='严格模式，有问题时退出码1')
    args = parser.parse_args()

    try:
        with open(args.file, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"❌ 文件不存在: {args.file}")
        sys.exit(1)

    results = analyze_mermaid(content)

    if not results:
        print("⚠️  未找到mermaid代码块")
        sys.exit(0)

    print_report(results)

    # 严格模式检查
    if args.validate:
        has_issues = any(
            r['nodes'] > 15 or
            (r['nodes'] > 7 and r['subgraphs'] == 0) or
            r['edges'] > r['nodes'] * 2
            for r in results
        )
        if has_issues:
            sys.exit(1)


if __name__ == '__main__':
    main()
