#!/usr/bin/env python3
"""
对比度检查工具
计算两个颜色的WCAG 2.1对比度

用法:
    python contrast_check.py --bg #21262d --fg #c9d1d9
    python contrast_check.py --bg f9f --fg 000000
"""
import argparse
import sys


def hex_to_rgb(hex_color):
    """hex颜色转RGB元组"""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join([c*2 for c in hex_color])
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def relative_luminance(rgb):
    """计算相对亮度"""
    def channel(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(bg_hex, fg_hex):
    """计算对比度"""
    bg_lum = relative_luminance(hex_to_rgb(bg_hex))
    fg_lum = relative_luminance(hex_to_rgb(fg_hex))
    lighter = max(bg_lum, fg_lum)
    darker = min(bg_lum, fg_lum)
    return (lighter + 0.05) / (darker + 0.05)


def main():
    parser = argparse.ArgumentParser(description='WCAG对比度检查')
    parser.add_argument('--bg', required=True, help='背景色hex')
    parser.add_argument('--fg', required=True, help='前景色hex')
    args = parser.parse_args()

    ratio = contrast_ratio(args.bg, args.fg)

    print(f"背景: {args.bg}")
    print(f"前景: {args.fg}")
    print(f"对比度: {ratio:.2f}:1")
    print()

    if ratio >= 7:
        print("✅ AAA级（最佳）")
    elif ratio >= 4.5:
        print("✅ AA级（合格）")
    elif ratio >= 3:
        print("⚠️  A级（大号文字可用）")
    else:
        print("❌ 不合格（不可用）")
        sys.exit(1)


if __name__ == '__main__':
    main()
