#!/usr/bin/env python3
"""icloudpd 的独立打包入口。

iCloudPD Studio 需要 icloudpd 作为下载引擎，但它本身是命令行程序。这里把
icloudpd 打成一个独立可执行文件随应用分发，用户就不必再装 Python 和 pip。

用 runpy 复现「命令行直接运行 icloudpd」的效果：icloudpd 的 __main__.py 是
执行式入口而非函数，runpy 能原样保留它的参数解析、退出码与输出格式，上游
逻辑一行都不用改。
"""
from __future__ import annotations

import runpy
import sys


def main() -> int:
    sys.argv = ["icloudpd", *sys.argv[1:]]
    try:
        runpy.run_module("icloudpd", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
