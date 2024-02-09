import unittest
import re

class MockSlurmInfo:
    def __init__(self):
        self._raw = {}

    def add_value(self, key, value, unit_dict):
        unit_dict[key] = value

    def parse_unit_block(self, unit, unitname, prefix, stype):
        lines = unit.split("\n")
        for pair in lines[0].strip().split(' '):
            key, value = pair.split('=', 1)
            if key == unitname:
                current_unit = value
                self._raw[current_unit] = {}
                if stype:
                    self._raw[current_unit]["__prefix"] = prefix
                    self._raw[current_unit]["__type"] = stype
            elif key == "JobName":
                value = re.search(".*JobName=(.*)$", lines[0].strip()).group(1)
                self._raw[current_unit][key] = value
                break
            self.add_value(key, value, self._raw[current_unit])

        for line in [_.strip() for _ in lines[1:]]:
            if not line: continue
            key, value = line.split('=', 1)
            if key in ['Comment', 'Reason', 'Command', 'WorkDir', 'StdErr', 'StdIn', 'StdOut', 'TRES']:
                self.add_value(key, value, self._raw[current_unit])
                continue
            if line.count('=') > 1:
                for pair in line.split(' '):
                    key, value = pair.split('=', 1)
                    if key in ['Dist']:
                        self._raw[current_unit][key] = line.split(f'{key}=', 1)[1]
                        break
                    print(key, '=',  value)
                    self.add_value(key, value, self._raw[current_unit])
            else:
                key, value = line.split('=', 1)
                print(key, '=',  value)
                self.add_value(key, value, self._raw[current_unit])
        return

class TestParseUnitBlock(unittest.TestCase):
    def test_parse_unit_block(self):
        mc = MockSlurmInfo()
        unit = """NodeName=localhost Arch=x86_64 CoresPerSocket=1
CPUAlloc=1 CPUTot=8 CPULoad=0.00
AvailableFeatures=(null)
ActiveFeatures=(null)
Gres=(null)
GresDrain=N/A
NodeAddr=localhost NodeHostName=localhost Version=21.08.5
OS=Linux 5.15.0-92-generic #102-Ubuntu SMP Wed Jan 10 09:33:48 UTC 2024
RealMemory=31000 AllocMem=0 FreeMem=27763 Sockets=8 Boards=1
State=MIXED ThreadsPerCore=1 TmpDisk=0 Weight=1 Owner=N/A MCS_label=N/A
Partitions=LocalQ
BootTime=2024-01-31T12:43:50 SlurmdStartTime=2024-02-02T10:06:16
LastBusyTime=2024-02-02T11:25:56
CfgTRES=cpu=8,mem=31000M,billing=8
AllocTRES=cpu=1
CapWatts=n/a
CurrentWatts=0 AveWatts=0
ExtSensorsJoules=n/s ExtSensorsWatts=0 ExtSensorsTemp=n/s"""
        unitname = "NodeName"
        prefix = "test_prefix"
        stype = "test_type"
        mc.parse_unit_block(unit, unitname, prefix, stype)

        self.assertIn("localhost", mc._raw)
        self.assertEqual(mc._raw["localhost"]["__prefix"], "test_prefix")
        self.assertEqual(mc._raw["localhost"]["__type"], "test_type")
        # Verify the parsing of first line properties
        self.assertEqual(mc._raw["localhost"]["Arch"], "x86_64")
        self.assertEqual(mc._raw["localhost"]["CoresPerSocket"], "1")
        # Verify parsing of other properties
        self.assertEqual(mc._raw["localhost"]["RealMemory"], "31000")
        self.assertEqual(mc._raw["localhost"]["State"], "MIXED")
        self.assertEqual(mc._raw["localhost"]["BootTime"], "2024-01-31T12:43:50")
        self.assertEqual(mc._raw["localhost"]["OS"], "Linux 5.15.0-92-generic #102-Ubuntu SMP Wed Jan 10 09:33:48 UTC 2024")

    def test_parse_unit_block_no_allocTRES(self):
        mc = MockSlurmInfo()
        unit = """NodeName=localhost Arch=x86_64 CoresPerSocket=1
CPUAlloc=1 CPUTot=8 CPULoad=0.00
AvailableFeatures=(null)
ActiveFeatures=(null)
Gres=(null)
GresDrain=N/A
NodeAddr=localhost NodeHostName=localhost Version=21.08.5
OS=Linux 5.15.0-92-generic #102-Ubuntu SMP Wed Jan 10 09:33:48 UTC 2024
RealMemory=31000 AllocMem=0 FreeMem=27763 Sockets=8 Boards=1
State=MIXED ThreadsPerCore=1 TmpDisk=0 Weight=1 Owner=N/A MCS_label=N/A
Partitions=LocalQ
BootTime=2024-01-31T12:43:50 SlurmdStartTime=2024-02-02T10:06:16
LastBusyTime=2024-02-02T11:25:56
CfgTRES=cpu=8,mem=31000M,billing=8
AllocTRES=
CapWatts=n/a
CurrentWatts=0 AveWatts=0
ExtSensorsJoules=n/s ExtSensorsWatts=0 ExtSensorsTemp=n/s"""
        unitname = "NodeName"
        prefix = "test_prefix"
        stype = "test_type"
        mc.parse_unit_block(unit, unitname, prefix, stype)

        self.assertIn("localhost", mc._raw)
        self.assertEqual(mc._raw["localhost"]["__prefix"], "test_prefix")
        self.assertEqual(mc._raw["localhost"]["__type"], "test_type")
        # Verify the parsing of first line properties
        self.assertEqual(mc._raw["localhost"]["Arch"], "x86_64")
        self.assertEqual(mc._raw["localhost"]["CoresPerSocket"], "1")
        # Verify parsing of other properties
        self.assertEqual(mc._raw["localhost"]["RealMemory"], "31000")
        self.assertEqual(mc._raw["localhost"]["State"], "MIXED")
        self.assertEqual(mc._raw["localhost"]["BootTime"], "2024-01-31T12:43:50")
        self.assertEqual(mc._raw["localhost"]["OS"], "Linux 5.15.0-92-generic #102-Ubuntu SMP Wed Jan 10 09:33:48 UTC 2024")

if __name__ == '__main__':
    unittest.main()
