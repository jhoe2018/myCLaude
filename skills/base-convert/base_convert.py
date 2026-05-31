"""进制转换与运算工具 — 支持任意进制(2-36)的互转和四则运算。"""

from __future__ import annotations

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _int_to_base(n: int, base: int) -> str:
    """整数转任意进制字符串。"""
    if n == 0:
        return "0"
    result: list[str] = []
    sign = ""
    if n < 0:
        sign = "-"
        n = -n
    while n > 0:
        result.append(DIGITS[n % base])
        n //= base
    return sign + "".join(reversed(result))


def _frac_to_base(f: float, base: int, precision: int = 10) -> str:
    """纯小数部分转任意进制字符串。"""
    result: list[str] = []
    for _ in range(precision):
        f *= base
        result.append(DIGITS[int(f)])
        f -= int(f)
        if f == 0:
            break
    return "".join(result)


def to_decimal(value_str: str, from_base: int) -> float:
    """将任意进制字符串转为十进制数值（支持小数）。"""
    negative = False
    if value_str.startswith("-"):
        negative = True
        value_str = value_str[1:]

    if "." in value_str:
        int_part, frac_part = value_str.split(".")
        int_val = 0
        for ch in int_part:
            int_val = int_val * from_base + DIGITS.index(ch.upper())
        frac_val = 0.0
        for i, ch in enumerate(frac_part, 1):
            frac_val += DIGITS.index(ch.upper()) * (from_base ** -i)
        result = int_val + frac_val
    else:
        result = float(int(value_str, from_base))

    return -result if negative else result


def convert(value_str: str, from_base: int, to_base: int | None = None) -> dict[str, str] | str:
    """将任意进制字符串转换为指定进制。

    Args:
        value_str: 数值字符串
        from_base: 源进制(2-36)
        to_base: 目标进制(2-36)，None 时返回常用进制对照表

    Returns:
        如果 to_base 为 None，返回 {'bin','oct','dec','hex'} 字典；
        否则返回目标进制字符串。
    """
    dec_val = to_decimal(value_str, from_base)
    int_part = int(dec_val)
    frac_part = abs(dec_val - int_part)

    if to_base is None:
        return {
            "bin": bin(int_part),
            "oct": oct(int_part),
            "dec": str(dec_val),
            "hex": hex(int_part),
        }

    if to_base == 10:
        return str(dec_val)

    result = _int_to_base(int_part, to_base)
    if frac_part > 0:
        result += "." + _frac_to_base(frac_part, to_base)
    return result


def arithmetic(a: str, op: str, b: str, base: int) -> str:
    """在指定进制下进行四则运算，返回同进制结果。

    Args:
        a: 左操作数（base进制字符串）
        op: 运算符 (+, -, *, /)
        b: 右操作数（base进制字符串）
        base: 运算所在的进制
    """
    dec_a = to_decimal(a, base)
    dec_b = to_decimal(b, base)

    if op == "+":
        result = dec_a + dec_b
    elif op == "-":
        result = dec_a - dec_b
    elif op == "*":
        result = dec_a * dec_b
    elif op == "/":
        if dec_b == 0:
            raise ZeroDivisionError("除数不能为零")
        result = dec_a / dec_b
    else:
        raise ValueError(f"不支持的运算符: {op}，仅支持 + - * /")

    if isinstance(result, float) and result != int(result):
        int_part = int(result)
        frac_part = abs(result - int_part)
        int_str = _int_to_base(int_part, base)
        return int_str + "." + _frac_to_base(frac_part, base)
    else:
        return _int_to_base(int(result), base)


def auto_parse(expr: str) -> dict | str | None:
    """自动解析混合格式输入。

    支持:
        - "FF 16 2"       纯转换
        - "FF + 1 16"     运算
        - "0xFF" "0b1010" "0o77"  前缀识别
        - "255"       默认十进制，打印全进制对照
    """
    expr = expr.strip()

    # 前缀识别
    if expr.startswith("0x") or expr.startswith("0X"):
        dec_val = int(expr, 16)
        return format_all(dec_val)
    if expr.startswith("0b") or expr.startswith("0B"):
        dec_val = int(expr, 2)
        return format_all(dec_val)
    if expr.startswith("0o") or expr.startswith("0O"):
        dec_val = int(expr, 8)
        return format_all(dec_val)

    parts = expr.split()
    if not parts:
        return None

    # 运算符检测
    ops = {"+", "-", "*", "/"}
    op_idx = -1
    op_found = None
    for i, p in enumerate(parts):
        if p in ops:
            op_idx = i
            op_found = p
            break

    if op_found and 1 <= op_idx <= len(parts) - 2:
        # 格式: A op B [base]
        a_val = parts[op_idx - 1]
        b_val = parts[op_idx + 1]
        base = int(parts[op_idx + 2]) if op_idx + 2 < len(parts) else 10
        result = arithmetic(a_val, op_found, b_val, base)
        return {
            "expression": f"{a_val} {op_found} {b_val} (base {base})",
            "result": result,
            "decimal": to_decimal(result, base) if base != 10 else float(result),
        }
    elif len(parts) >= 2 and parts[-1].isdigit():
        # 格式: value from_base [to_base]
        to_base = int(parts[-1]) if parts[-1].isdigit() else None
        from_base = int(parts[-2]) if len(parts) >= 3 and parts[-2].isdigit() else None
        if len(parts) >= 3 and parts[-2].isdigit() and parts[-1].isdigit():
            # value from_base to_base
            value_str = " ".join(parts[:-2])
            return convert(value_str, int(parts[-2]), int(parts[-1]))
        elif len(parts) >= 2 and parts[-1].isdigit():
            # value from_base
            value_str = " ".join(parts[:-1])
            return convert(value_str, int(parts[-1]), None)
    elif len(parts) == 1:
        # 单数字 -> 默认十进制，打印全表
        try:
            dec_val = int(parts[0])
            return format_all(dec_val)
        except ValueError:
            pass

    return None


def format_all(dec_val: float) -> dict:
    """将一个十进制数格式化为二/八/十/十六进制对照。"""
    int_part = int(dec_val)
    return {
        "bin": bin(int_part),
        "oct": oct(int_part),
        "dec": str(dec_val),
        "hex": hex(int_part),
    }


def main():
    import sys

    if len(sys.argv) < 2:
        print("用法: python base_convert.py <表达式>")
        print("示例:")
        print("  python base_convert.py FF 16 2        # 十六进制FF -> 二进制")
        print("  python base_convert.py 255 10 16      # 十进制255 -> 十六进制")
        print("  python base_convert.py FF + 1 16      # 十六进制运算")
        print("  python base_convert.py 0xFF           # 前缀识别")
        print("  python base_convert.py 255            # 打印所有常用进制")
        sys.exit(1)

    expr = " ".join(sys.argv[1:])
    result = auto_parse(expr)

    if result is None:
        print(f"无法解析: {expr}")
        sys.exit(1)

    if isinstance(result, dict):
        for k, v in result.items():
            print(f"  {k}: {v}")
    else:
        print(f"  {result}")


if __name__ == "__main__":
    main()
