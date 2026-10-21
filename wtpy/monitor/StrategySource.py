"""Static Python strategy inspection; uploaded modules are never imported here."""
import ast
import json
import re

KINDS = {"BaseCtaStrategy": "CTA", "BaseHftStrategy": "HFT", "BaseSelStrategy": "SEL"}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", value):
        raise ValueError("ID须以英文字母开头，只能包含字母、数字、下划线或短横线，最多40字符")
    return value


def analyze(source):
    if not isinstance(source, str) or len(source.encode("utf-8")) > 262144:
        raise ValueError("Python源码不可超过256KB")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError("Python语法错误，第%d行：%s" % (exc.lineno, exc.msg)) from None
    aliases = dict(KINDS)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("wtpy"):
            for item in node.names:
                if item.name in KINDS:
                    aliases[item.asname or item.name] = KINDS[item.name]
    classes = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        kinds = [aliases.get(base.id) if isinstance(base, ast.Name) else
                 KINDS.get(base.attr) if isinstance(base, ast.Attribute) else None for base in node.bases]
        kind = next((item for item in kinds if item), None)
        if kind is None:
            continue
        constructor = next((item for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "__init__"), None)
        parameters = []
        kwargs = False
        if constructor:
            args = constructor.args
            if args.posonlyargs:
                raise ValueError("策略构造参数须可按名称传入，不支持仅位置参数")
            kwargs = args.kwarg is not None
            positional = args.args[1:]
            defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
            for arg, default in list(zip(positional, defaults)) + list(zip(args.kwonlyargs, args.kw_defaults)):
                row = {"name": arg.arg, "required": default is None, "type": ast.unparse(arg.annotation) if arg.annotation else ""}
                if default is not None:
                    try:
                        row["default"] = ast.literal_eval(default)
                        json.dumps(row["default"], allow_nan=False)
                    except (ValueError, TypeError):
                        row["default_expression"] = ast.unparse(default)
                parameters.append(row)
        else:
            parameters = [{"name": "name", "required": True, "type": "str"}]
        if not kwargs and "name" not in {item["name"] for item in parameters}:
            raise ValueError("策略构造函数须接受name参数，以绑定唯一实例ID")
        classes.append({"name": node.name, "engine_type": kind, "parameters": parameters,
                        "accepts_kwargs": kwargs, "supported": kind in ("CTA", "HFT")})
    if len(classes) != 1:
        raise ValueError("每个文件须直接定义一个WT策略子类，支持CTA、HFT或SEL")
    return classes
