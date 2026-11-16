"""Network interface routing tests for the embedded remote Linux probe."""
import ast
import io
import unittest
from unittest.mock import patch

from app.probe import PROBE_SCRIPT
from app.charts import draw_chart


def _probe_net_totals():
    """Load only net_totals from the actual embedded collector to avoid accessing real /proc."""
    source = PROBE_SCRIPT.replace('__SERVICES_LITERAL__', '[]')
    tree = ast.parse(source)
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == 'net_totals')
    namespace = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'probe_net_totals', 'exec'), namespace)
    return namespace['net_totals']


class NetworkInterfaceTests(unittest.TestCase):
    def test_network_counter_not_sum_of_all_interfaces(self):
        route = (
            'Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n'
            'docker0 00000000 00000000 0001 0 0 800 00000000 0 0 0\n'
            'enp0s3 00000000 00000000 0003 0 0 100 00000000 0 0 0\n'
            'veth0 00000000 00000000 0003 0 0 500 00000000 0 0 0\n'
        )

        def fake_open(name, *args, **kwargs):
            if name == '/proc/net/route':
                return io.StringIO(route)
            if name == '/sys/class/net/enp0s3/statistics/rx_bytes':
                return io.StringIO('1234567')
            if name == '/sys/class/net/enp0s3/statistics/tx_bytes':
                return io.StringIO('987654')
            raise FileNotFoundError(name)

        with patch('builtins.open', side_effect=fake_open):
            self.assertEqual(_probe_net_totals()(),
                             {'rx': 1234567, 'tx': 987654, 'interface': 'enp0s3'})

    def test_network_no_route_returns_unknown(self):
        route = 'Iface Destination Gateway Flags RefCnt Use Metric Mask\n'
        with patch('builtins.open', return_value=io.StringIO(route)):
            self.assertEqual(_probe_net_totals()(),
                             {'rx': None, 'tx': None, 'interface': None})

    def test_chart_four_panels_png(self):
        rows = [
            {'ok': 1, 'ts': 1794700000 + i * 300, 'cpu': i + 0.1,
             'memory': 20 + i * 0.3, 'disk': 50 + i,
             'disk_used': 5_000_000_000 + i * 5_000_000,
             'disk_total': 10_000_000_000}
            for i in range(7)
        ]
        png = draw_chart(rows, '24h', 'Sweden')
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertLess(len(png), 3_000_000)


if __name__ == '__main__':
    unittest.main()
