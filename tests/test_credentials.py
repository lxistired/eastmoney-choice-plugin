"""Offline tests: execute only the login function with a fake SDK."""
import ast
import contextlib
import io
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'fetch_monetary_data.py'

class CredentialsTests(unittest.TestCase):
    def run_login(self, env, outcome='success'):
        tree = ast.parse(SOURCE.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'login_emquant')
        calls = []
        def start(options, *args):
            calls.append(options)
            if outcome == 'exception':
                raise RuntimeError(options)
            return types.SimpleNamespace(ErrorCode=0 if outcome == 'success' else 1, ErrorMsg=options)
        sdk = types.SimpleNamespace(start=start)
        namespace = {'os': types.SimpleNamespace(environ=env)}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        output = io.StringIO()
        with patch.dict(sys.modules, {'EmQuantAPI': types.SimpleNamespace(c=sdk)}), contextlib.redirect_stdout(output):
            result = namespace['login_emquant']()
        return result, calls, output.getvalue(), sdk

    def test_missing_configuration_skips_sdk(self):
        for env in ({}, {'EMQUANT_USERNAME': 'offline-user'}, {'EMQUANT_USERNAME': ' ', 'EMQUANT_PASSWORD': 'offline-pass'}):
            result, calls, _, _ = self.run_login(env)
            self.assertFalse(result)
            self.assertEqual(len(calls), 0)

    def test_credentials_read_each_time_and_option_format_preserved(self):
        for suffix in ('one', 'two'):
            env = {'EMQUANT_USERNAME': 'offline-user-' + suffix, 'EMQUANT_PASSWORD': 'offline-pass-' + suffix}
            result, calls, output, sdk = self.run_login(env)
            self.assertTrue(result)
            self.assertTrue(calls == ['ForceLogin=1,UserName=' + env['EMQUANT_USERNAME'] + ',Password=' + env['EMQUANT_PASSWORD']])
            self.assertEqual(sdk.EncodeType, 'utf-8')
            self.assertFalse(any(v in output for v in env.values()))

    def test_errors_never_log_credentials(self):
        env = {'EMQUANT_USERNAME': 'offline-user', 'EMQUANT_PASSWORD': 'offline-pass'}
        for outcome in ('failure', 'exception'):
            result, calls, output, _ = self.run_login(env, outcome)
            self.assertFalse(result)
            self.assertEqual(len(calls), 1)
            self.assertFalse(any(v in output for v in env.values()))

    def test_option_injection_rejected(self):
        for char in (',', '\n', '\r', '\x00'):
            result, calls, _, _ = self.run_login({'EMQUANT_USERNAME': 'offline-user', 'EMQUANT_PASSWORD': 'offline' + char + 'pass'})
            self.assertFalse(result)
            self.assertEqual(len(calls), 0)

    def test_no_literal_credential_assignments(self):
        for path in SOURCE.parent.glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    sensitive = any(isinstance(t, ast.Name) and t.id.lower() in ('username', 'password', 'pwd') for t in node.targets)
                    self.assertFalse(sensitive and bool(node.value.value.strip()), msg=str(path))

if __name__ == '__main__':
    unittest.main()
