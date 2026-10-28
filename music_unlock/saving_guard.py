"""Reject candidate source that loses the reviewed desktop-saving integration.

Only owned saving definitions are pinned. Unrelated upstream decoder, metadata,
API or comment changes remain mergeable. Update these fingerprints deliberately
when changing the protected implementation and its regression tests together.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

# Generated from the reviewed definitions, ignoring formatting and comments.
_EXPECTED: dict[str, dict[str, str]] = {'desktop_saves.py': {'WinFormsSaveDialog': '303c6b5cb8464b9fdbed17bccb60b0471d680032ed2af0fb2194a0e7a4dcd4ac',
                      'DesktopSaveBridge': '071833ee4d1eb2572dd7fbdece25279efe57414b4a6b23d52d8e297166617094'},
 'windows_launcher.py': {'run_desktop': 'e1aa4c6706576834d28d425b5cf976871b1592beb1ccb89d8dca554c7c5cca7b'},
 'web/server.py': {'ExportError': '581bc5c7778e0e556fe1f8237f7ebbd6d2a46753f93b2939a23e66b5808a2c60',
                   'result_export': '2495f28b6c18666fc9e7467746a85f2790540b7c7b1237af2958cd70fe2ca84a',
                   'write_archive': '4d47cb5f432a187398c8e43e8f23dbee1bf611c49a6b8843f61360fbcf218295',
                   '_close_response': 'cf87c76c402ff2e79966d99bf18e940e21b1d61bff710ba2774881c1e3d83462',
                   'begin_update_restart': 'e17c8059a1b6914ef0338158a2976a58d788c5440279bece4064950eef753505',
                   'cancel_update_restart': 'cbc85223011c1bc836a38806bfa6996e6c87efef0649a8cb5df4ec082bded6b1',
                   '_enter_conversion': '1d0f8efb1c84e5b29dabb4a249aba84f7cf5ff2cf26bc57ed9a6a3d0aaaeff08',
                   '_leave_conversion': '46d55def5b0ddd63e38317d2843afd67d4ae98071a5a1209439579fd1c4d0edb',
                   '_cleanup_expired': '2e3766ba8d18febcbc6237d5cc93f435f7a3bbc33058ecb15df740c8d7921fc2',
                   'download': '2462cd0a26ab4e286616c104bb821c8e1d6e9779f7e5f287287c23e85dcfab74',
                   'zip_batch': '4ae4d24cd6eba4236736b2e9b6ffb5848f649c1ed38efc73f82eda46d1ca03e8',
                   'clear': 'ed8864e302749a430a0f99b77142f41d89a783296694bfe3b05bbea4d1e51491'}}


def validate_saving(root: Path) -> None:
    root = root.resolve(strict=True)
    for name, expected in _EXPECTED.items():
        try:
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError('文件越过候选目录')
            tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=name)
            definitions = {}
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in expected:
                    if node.name in definitions:
                        raise ValueError('托管定义重复')
                    definitions[node.name] = hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
            if definitions != expected:
                raise ValueError('托管实现已改变')
        except (OSError, SyntaxError, ValueError) as exc:
            raise ValueError(f'本地保存定制缺失或不兼容，停止构建候选版本：{name}（{exc}）') from exc
